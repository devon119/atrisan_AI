#
# ABOUT
# Audio Roast Recorder — 全息烘焙法聲音記錄引擎
# 背景執行緒錄音 + 即時 FFT 頻譜計算
# 每個烘焙事件（T0/T1/大理石紋/CHARGE/FC/DROP 等）都帶時間戳存入 events.json
# 最終輸出：audio.wav / spectrum.csv / events.json

import json
import logging
import os
import queue
import threading
import time
import wave
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import numpy as np
from qtpy.QtCore import QObject, Signal

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow

_log = logging.getLogger(__name__)

SAMPLERATE   = 44100
CHANNELS     = 1
BLOCKSIZE    = 2048          # ~46 ms per block
FFT_BANDS    = 8             # number of frequency bands for spectrum.csv
SPECTRUM_HZ  = (80, 300, 600, 1200, 2400, 4800, 9600, 22050)  # band upper edges


class AudioRoastRecorder(QObject):
    """Background audio recorder + FFT analyser for holographic roasting."""

    # emitted every ~BLOCKSIZE samples: list[float] of FFT band energies (dB, length FFT_BANDS)
    spectrumUpdated = Signal(list)
    # emitted when a roast session is fully saved: str path to session folder
    sessionSaved    = Signal(str)
    # emitted on error
    errorSignal     = Signal(str)
    # emitted when acoustic FC is suspected: float = elapsed seconds since recording start
    fcSuggested     = Signal(float)

    # FC detection constants
    _FC_SPIKE_DB       = 7.0   # dB above rolling baseline to trigger
    _FC_BASELINE_INIT  = 100   # blocks to build initial baseline (~4.6 s)
    _FC_MIN_ELAPSED    = 240.0 # don't suggest FC before 4 minutes

    def __init__(self, aw: 'ApplicationWindow') -> None:
        super().__init__()
        self.aw = aw
        self._recording  = False
        self._tts_active = False   # True while TTS is playing — apply speech-band filter
        self._device_idx: Optional[int] = None   # None = system default
        self._session_dir: Optional[str] = None
        self._audio_queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stream = None

        # accumulated data for this session
        self._pcm_frames: list[bytes] = []
        self._spectrum_rows: list[list] = []   # [elapsed_sec, *band_energies]
        self._events: list[dict] = []
        self._start_time: float = 0.0

        # acoustic FC detection state
        self._fc_armed: bool = False
        self._fc_detected: bool = False
        self._fc_baseline_db: float = -60.0
        self._fc_baseline_n: int = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_device(self, device_idx: Optional[int]) -> None:
        self._device_idx = device_idx

    def start(self, session_dir: str) -> bool:
        """Start recording into session_dir. Returns False if already recording."""
        if self._recording:
            return False
        try:
            import sounddevice as sd  # type: ignore
        except ImportError:
            self.errorSignal.emit('sounddevice not installed')
            return False

        os.makedirs(session_dir, exist_ok=True)
        self._session_dir = session_dir
        self._pcm_frames  = []
        self._spectrum_rows = []
        self._events      = []
        self._start_time  = time.time()
        # Reset FC detection for each new recording session
        self._fc_armed    = False
        self._fc_detected = False
        self._fc_baseline_db = -60.0
        self._fc_baseline_n  = 0

        try:
            self._stream = sd.RawInputStream(
                samplerate  = SAMPLERATE,
                blocksize   = BLOCKSIZE,
                device      = self._device_idx,
                channels    = CHANNELS,
                dtype       = 'int16',
                callback    = self._audio_callback,
            )
            self._recording = True
            self._stream.start()
            self._thread = threading.Thread(target=self._process_loop, daemon=True)
            self._thread.start()
            _log.info('AudioRoastRecorder started → %s', session_dir)
            return True
        except Exception as e:  # pylint: disable=broad-except
            self._recording = False
            self.errorSignal.emit(str(e))
            _log.exception(e)
            return False

    def stop(self) -> None:
        """Stop recording and flush files."""
        if not self._recording:
            return
        self._recording = False
        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None
        except Exception as e:  # pylint: disable=broad-except
            _log.exception(e)
        # drain queue
        if self._thread:
            self._thread.join(timeout=3)
        self._flush()

    def record_event(self, name: str, bt: float, et: float, ror: float) -> None:
        """Call from main thread when any event occurs (CHARGE, T0, T1 …)."""
        if not self._recording:
            return
        elapsed = time.time() - self._start_time
        self._events.append({
            'name'   : name,
            'elapsed': round(elapsed, 2),
            'bt'     : round(bt, 1),
            'et'     : round(et, 1),
            'ror'    : round(ror, 2),
        })

    def is_recording(self) -> bool:
        return self._recording

    def tts_start(self) -> None:
        """Call before TTS playback begins — activates speech-band suppression filter."""
        self._tts_active = True

    def tts_stop(self) -> None:
        """Call after TTS playback ends — deactivates filter."""
        self._tts_active = False

    def elapsed(self) -> float:
        if not self._recording:
            return 0.0
        return time.time() - self._start_time

    def arm_fc_detection(self) -> None:
        """Enable acoustic first-crack detection (call after DRY END)."""
        self._fc_armed = True
        self._fc_detected = False
        self._fc_baseline_db = -60.0
        self._fc_baseline_n = 0

    def disarm_fc_detection(self) -> None:
        """Disable acoustic FC detection (call after FC confirmed)."""
        self._fc_armed = False

    @staticmethod
    def list_devices() -> list[tuple[int, str]]:
        """Return [(index, name), …] for available input devices."""
        try:
            import sounddevice as sd  # type: ignore
            result = []
            for i, dev in enumerate(sd.query_devices()):
                if dev['max_input_channels'] > 0:
                    result.append((i, dev['name']))
            return result
        except Exception:  # pylint: disable=broad-except
            return []

    # ── Internal ───────────────────────────────────────────────────────────────

    def _audio_callback(self, indata: bytes, frames: int, _time, _status) -> None:
        self._audio_queue.put((bytes(indata), self._tts_active))

    @staticmethod
    def _apply_speech_filter(samples: np.ndarray) -> np.ndarray:
        """Bandstop filter 300–3400 Hz (speech range) to suppress TTS contamination.
        Preserves low-frequency drum/mechanical sounds and high-frequency cracking."""
        try:
            from scipy.signal import butter, sosfilt  # type: ignore
            sos = butter(4, [300 / (SAMPLERATE / 2), 3400 / (SAMPLERATE / 2)],
                         btype='bandstop', output='sos')
            return sosfilt(sos, samples).astype(np.float32)
        except Exception:  # pylint: disable=broad-except
            return samples  # scipy unavailable — return as-is

    def _process_loop(self) -> None:
        while self._recording or not self._audio_queue.empty():
            try:
                raw, tts_on = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if tts_on:
                samples = self._apply_speech_filter(samples)
            pcm_out = (samples * 32768.0).clip(-32768, 32767).astype(np.int16).tobytes()
            self._pcm_frames.append(pcm_out)
            bands = self._fft_bands(samples)
            elapsed = time.time() - self._start_time
            self._spectrum_rows.append([round(elapsed, 3)] + bands)
            self.spectrumUpdated.emit(bands)

            # Acoustic FC detection: monitor low-freq impact energy (bands 0+1 = 80-600 Hz)
            if self._fc_armed and not self._fc_detected and elapsed >= self._FC_MIN_ELAPSED:
                low_db = (bands[0] + bands[1]) / 2.0
                if self._fc_baseline_n < self._FC_BASELINE_INIT:
                    # Blend into rolling baseline
                    alpha = 1.0 / (self._fc_baseline_n + 1)
                    self._fc_baseline_db = (1 - alpha) * self._fc_baseline_db + alpha * low_db
                    self._fc_baseline_n += 1
                else:
                    # Compare spike against current baseline BEFORE updating it
                    if low_db >= self._fc_baseline_db + self._FC_SPIKE_DB:
                        self._fc_detected = True
                        self._fc_armed = False
                        self.fcSuggested.emit(elapsed)
                    else:
                        # Only update baseline when no spike (don't let cracks drift the baseline up)
                        self._fc_baseline_db = 0.99 * self._fc_baseline_db + 0.01 * low_db

    def _fft_bands(self, samples: np.ndarray) -> list[float]:
        """Compute energy (dB) in each frequency band."""
        n   = len(samples)
        win = np.hanning(n)
        fft = np.abs(np.fft.rfft(samples * win))
        freqs = np.fft.rfftfreq(n, d=1.0 / SAMPLERATE)
        bands: list[float] = []
        prev_hz = 0
        for hz in SPECTRUM_HZ:
            mask = (freqs >= prev_hz) & (freqs < hz)
            energy = float(np.mean(fft[mask] ** 2)) if mask.any() else 0.0
            db = 10.0 * np.log10(energy + 1e-12)
            bands.append(round(db, 2))
            prev_hz = hz
        return bands

    def _flush(self) -> None:
        if not self._session_dir:
            return
        try:
            # audio.wav
            wav_path = os.path.join(self._session_dir, 'audio.wav')
            with wave.open(wav_path, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)  # int16
                wf.setframerate(SAMPLERATE)
                wf.writeframes(b''.join(self._pcm_frames))

            # spectrum.csv
            csv_path = os.path.join(self._session_dir, 'spectrum.csv')
            headers  = ['elapsed_sec'] + [f'band_{i+1}_db' for i in range(FFT_BANDS)]
            with open(csv_path, 'w', encoding='utf-8') as f:
                f.write(','.join(headers) + '\n')
                for row in self._spectrum_rows:
                    f.write(','.join(str(v) for v in row) + '\n')

            # events.json
            evt_path = os.path.join(self._session_dir, 'events.json')
            with open(evt_path, 'w', encoding='utf-8') as f:
                json.dump({'events': self._events}, f, ensure_ascii=False, indent=2)

            _log.info('AudioRoastRecorder saved → %s', self._session_dir)
            self.sessionSaved.emit(self._session_dir)
        except Exception as e:  # pylint: disable=broad-except
            self.errorSignal.emit(str(e))
            _log.exception(e)


def default_session_dir(base_dir: str, title: str = '') -> str:
    """Generate a session folder path like base_dir/20260505_143022_Ethiopia."""
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    suffix = '_' + title.replace(' ', '_')[:20] if title else ''
    return os.path.join(base_dir, stamp + suffix)
