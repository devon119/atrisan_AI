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
from PyQt6.QtCore import QObject, pyqtSignal

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
    spectrumUpdated = pyqtSignal(list)
    # emitted when a roast session is fully saved: str path to session folder
    sessionSaved    = pyqtSignal(str)
    # emitted on error
    errorSignal     = pyqtSignal(str)

    def __init__(self, aw: 'ApplicationWindow') -> None:
        super().__init__()
        self.aw = aw
        self._recording  = False
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

    def elapsed(self) -> float:
        if not self._recording:
            return 0.0
        return time.time() - self._start_time

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
        self._audio_queue.put(bytes(indata))

    def _process_loop(self) -> None:
        while self._recording or not self._audio_queue.empty():
            try:
                raw = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            self._pcm_frames.append(raw)
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            bands = self._fft_bands(samples)
            elapsed = time.time() - self._start_time
            self._spectrum_rows.append([round(elapsed, 3)] + bands)
            self.spectrumUpdated.emit(bands)

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
