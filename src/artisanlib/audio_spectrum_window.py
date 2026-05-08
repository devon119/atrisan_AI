# pylint: disable=attribute-defined-outside-init,arguments-renamed
#
# ABOUT
# Audio Spectrum Window — 烘焙過程即時頻譜視窗（浮動，可拖移）
# 顯示高頻/低頻能量比值曲線，事件發生時自動畫垂直線
# 純觀察用，不需要操作

from collections import deque
from typing import TYPE_CHECKING

from qtpy.QtCore import Slot, Qt
from qtpy.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from artisanlib.dialogs import ArtisanDialog

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow

MAX_POINTS = 600   # ~10 min at 1 point/sec


class AudioSpectrumWindow(ArtisanDialog):
    """Floating window showing real-time audio spectrum during roasting."""

    def __init__(self, aw: 'ApplicationWindow') -> None:
        super().__init__(parent=aw, aw=aw)
        self.aw = aw
        self.setWindowTitle('音訊頻譜')
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.resize(480, 280)

        # rolling data
        self._times: deque[float]  = deque(maxlen=MAX_POINTS)
        self._ratios: deque[float] = deque(maxlen=MAX_POINTS)
        self._elapsed: float = 0.0

        # event markers: list of (elapsed_sec, label)
        self._event_markers: list[tuple[float, str]] = []

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # status bar
        top = QHBoxLayout()
        self._lbl_status = QLabel('待機')
        self._lbl_status.setStyleSheet('color: #888; font-size: 11px;')
        top.addWidget(self._lbl_status)
        top.addStretch()
        btn_clear = QPushButton('清除')
        btn_clear.setFixedWidth(52)
        btn_clear.clicked.connect(self._clear)
        top.addWidget(btn_clear)
        layout.addLayout(top)

        # chart
        self._fig  = Figure(figsize=(5, 2.5), tight_layout=True)
        self._ax   = self._fig.add_subplot(111)
        self._canvas = FigureCanvasQTAgg(self._fig)
        layout.addWidget(self._canvas)

        self._setup_axes()

    def _setup_axes(self) -> None:
        ax = self._ax
        ax.set_facecolor('#1a1a1a')
        self._fig.patch.set_facecolor('#1a1a1a')
        ax.tick_params(colors='#aaa', labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor('#444')
        ax.set_xlabel('秒', color='#aaa', fontsize=8)
        ax.set_ylabel('高頻/低頻', color='#aaa', fontsize=8)
        ax.set_ylim(-0.5, 2.0)
        self._line, = ax.plot([], [], color='#4fc3f7', linewidth=1.2)
        # store vline references for cleanup
        self._vlines: list[tuple] = []   # (axvline, text)

    # ── Slots (called from main thread via signal) ───────────────────────────

    @Slot(list)
    def on_spectrum(self, bands: list[float]) -> None:
        """Receive FFT band energies, compute hi/lo ratio and update chart."""
        if len(bands) < 4:
            return
        self._elapsed += 1.0   # approximate — refined by actual elapsed from recorder
        # bands: [80, 300, 600, 1200, 2400, 4800, 9600, 22050] Hz
        lo = sum(10 ** (b / 10) for b in bands[:2]) + 1e-12   # <300 Hz
        hi = sum(10 ** (b / 10) for b in bands[4:6]) + 1e-12  # 2400–9600 Hz
        ratio = hi / lo

        self._times.append(self._elapsed)
        self._ratios.append(ratio)
        self._update_chart()
        self._lbl_status.setText(f'錄音中  {int(self._elapsed)}s  比值 {ratio:.3f}')

    @Slot(str, float)
    def on_event(self, name: str, elapsed: float) -> None:
        """Mark an event on the chart."""
        self._event_markers.append((elapsed, name))
        self._redraw_vlines()
        self._canvas.draw_idle()

    def on_stop(self) -> None:
        self._lbl_status.setText('記錄已停止')
        self._elapsed = 0.0

    # ── Internal ─────────────────────────────────────────────────────────────

    def _update_chart(self) -> None:
        xs = list(self._times)
        ys = list(self._ratios)
        self._line.set_data(xs, ys)
        if xs:
            self._ax.set_xlim(max(0, xs[-1] - MAX_POINTS), xs[-1] + 5)
        self._canvas.draw_idle()

    def _redraw_vlines(self) -> None:
        # remove old
        for vl, tx in self._vlines:
            try:
                vl.remove()
                tx.remove()
            except Exception:  # pylint: disable=broad-except
                pass
        self._vlines = []
        # redraw all
        ys = list(self._ratios)
        top_y = max(ys[-20:], default=1.0) if ys else 1.0
        for elapsed, name in self._event_markers:
            vl = self._ax.axvline(elapsed, color='#ffcc02', linewidth=0.8, linestyle='--', alpha=0.7)
            tx = self._ax.text(elapsed + 1, top_y * 0.9, name,
                               color='#ffcc02', fontsize=7, rotation=90, va='top')
            self._vlines.append((vl, tx))

    def _clear(self) -> None:
        self._times.clear()
        self._ratios.clear()
        self._event_markers = []
        for vl, tx in self._vlines:
            try:
                vl.remove()
                tx.remove()
            except Exception:  # pylint: disable=broad-except
                pass
        self._vlines = []
        self._line.set_data([], [])
        self._canvas.draw_idle()
        self._elapsed = 0.0

    def closeEvent(self, event) -> None:  # type: ignore[override]
        event.accept()
