#
# ABOUT
# AI Advisor Simulation Window
# Realistic roast simulation: BT dips to turning point after CHARGE,
# heat/airflow adjustments accumulate as RoR deviation with ~40s thermal lag.
# Embedded matplotlib chart shows BT / ET / RoR curves in real time.

import random
from collections import deque
from typing import TYPE_CHECKING

from PyQt6.QtCore import QTimer, pyqtSlot, Qt
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QGroupBox, QWidget, QSlider,
)
from PyQt6.QtGui import QFont

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from artisanlib.dialogs import ArtisanDialog

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow

# ── Base curves at heat=5, airflow=5 (seconds from CHARGE) ───────────────────
# Negative time = pre-charge warm-up (drum stabilising).
# BT dips to TP (~90°C at 90s) — realistic cold-bean shock.
# 5-min BT ≈ 150°C, 轉黃 at ~420s, FC at ~620s.

_BT_KEYS: list[tuple[float, float]] = [
    (-60, 202), (-30, 199),
    (0,   193),              # CHARGE: drum is ~193°C, cold beans enter
    (30,  138),              # rapid BT drop
    (60,   98),              # still falling
    (90,   90),              # Turning Point (TP) ← minimum ~90°C
    (150, 108),              # BT recovering
    (240, 130),
    (300, 152),              # 5-min target ≈ 150°C
    (390, 163),              # 轉黃（梅納期開始）
    (480, 175),
    (570, 186),
    (620, 196),              # FC start
    (680, 204),
    (730, 210),
    (780, 215),              # drop zone
]
_ET_KEYS: list[tuple[float, float]] = [
    (-60, 232), (-30, 228),
    (0,   222), (30,  210),  # ET also dips at CHARGE, but less severely
    (90,  205), (150, 207),
    (240, 211), (360, 216),
    (480, 220), (570, 224),
    (620, 226), (730, 230),
    (780, 233),
]

_SPEED_INTERVALS: dict[int, int] = {0: 1000, 1: 333, 2: 100}
_CHART_EVERY_N   = 5   # redraw chart every N ticks
_DEFAULT_BEAN_LOAD = 250  # grams


def _lerp(keys: list[tuple[float, float]], t: float) -> float:
    if t <= keys[0][0]:  return keys[0][1]
    if t >= keys[-1][0]: return keys[-1][1]
    for i in range(len(keys) - 1):
        t0, v0 = keys[i]; t1, v1 = keys[i + 1]
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return keys[-1][1]


class AIAdvisorSimDialog(ArtisanDialog):
    """Simulated roast window with physics model + live chart."""

    _DEFAULT_HEAT    = 5
    _DEFAULT_AIRFLOW = 5

    def __init__(self, parent: QWidget, aw: 'ApplicationWindow') -> None:
        super().__init__(parent, aw)
        self.aw = aw
        self.setWindowTitle('模擬烘豆器')
        self.setMinimumWidth(460)

        # simulation state
        self._sim_time:   float = 0.0
        self._charge_at:  float | None = None
        self._timex:      list[float] = []
        self._temp2:      list[float] = []
        self._timeindex:  list[int]   = [-1, 0, 0, 0, 0, 0, 0, 0]
        self._ror_buffer: deque[float] = deque(maxlen=60)
        self._bt_prev:    deque[float] = deque(maxlen=30)

        # physics — BT = base_curve(t) + deviation
        self._bt_deviation: float = 0.0   # accumulated °C offset from base curve
        self._ror_adj:      float = 0.0   # current effective RoR adjustment (°C/min)
        self._et:           float = 0.0   # ET state (with lag)
        self._noise_bt:     float = 0.0

        # controls
        self._heat:      int   = self._DEFAULT_HEAT
        self._airflow:   int   = self._DEFAULT_AIRFLOW
        self._bean_load: int   = _DEFAULT_BEAN_LOAD  # grams

        # FC exothermic state
        self._fc_exo_elapsed: float = 0.0  # seconds since FC

        # chart data
        self._chart_times: list[float] = []   # minutes from CHARGE
        self._chart_bt:    list[float] = []
        self._chart_et:    list[float] = []
        self._chart_ror:   list[float] = []
        self._tp_marked:   bool = False
        self._tick_count:  int  = 0

        # events
        self._event_near_t1: bool = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)

        # ── Chart ──
        self._fig = Figure(figsize=(4.8, 2.6), dpi=88)
        self._fig.patch.set_facecolor('#141414')
        self._fig.subplots_adjust(left=0.10, right=0.88, top=0.93, bottom=0.14)

        self._ax_bt  = self._fig.add_subplot(111)
        self._ax_ror = self._ax_bt.twinx()

        for ax in (self._ax_bt, self._ax_ror):
            ax.set_facecolor('#141414')
            for spine in ax.spines.values():
                spine.set_color('#333')
            ax.tick_params(colors='#666', labelsize=8)

        self._ax_bt.set_ylabel('°C', color='#aaa', fontsize=8)
        self._ax_ror.set_ylabel('RoR °C/min', color='#4fc3f7', fontsize=8)
        self._ax_bt.set_xlabel('min', color='#666', fontsize=8)
        self._ax_bt.set_ylim(70, 240)
        self._ax_ror.set_ylim(-5, 25)
        self._ax_ror.tick_params(colors='#4fc3f7', labelsize=8)

        self._line_bt,  = self._ax_bt.plot([], [], color='#ff6b6b', lw=1.8, label='BT')
        self._line_et,  = self._ax_bt.plot([], [], color='#ffd93d', lw=1.5, label='ET')
        self._line_ror, = self._ax_ror.plot([], [], color='#4fc3f7', lw=1.2,
                                             linestyle='--', label='RoR')
        self._ax_bt.legend(loc='upper left', fontsize=7,
                           facecolor='#222', edgecolor='#444', labelcolor='#ccc')
        # event marker vertical lines (added dynamically)
        self._vlines: dict[str, tuple] = {}  # label → (axvline, text)

        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setMinimumHeight(200)
        root.addWidget(self._canvas)

        # ── LCD row ──
        lcd_box = QGroupBox('即時數據')
        grid = QGridLayout(lcd_box)
        for col, name in enumerate(['BT', 'ET', 'RoR', '計時']):
            h = QLabel(name)
            h.setStyleSheet('color:#888;font-size:9pt;')
            grid.addWidget(h, 0, col)
        self._lcd_bt   = self._lcd(grid, 1, 0)
        self._lcd_et   = self._lcd(grid, 1, 1)
        self._lcd_ror  = self._lcd(grid, 1, 2)
        self._lcd_time = self._lcd(grid, 1, 3)
        root.addWidget(lcd_box)

        # ── 風火控制 ──
        ctrl_box = QGroupBox('風火控制')
        ctrl_grid = QGridLayout(ctrl_box)

        self._heat_slider, self._heat_label = self._make_control(
            ctrl_grid, 0, '🔥 火力', '#ff8c00',
            lambda v: self._on_heat_changed(v))
        self._air_slider, self._air_label = self._make_control(
            ctrl_grid, 1, '💨 風門', '#4fc3f7',
            lambda v: self._on_airflow_changed(v))

        # bean load row
        from PyQt6.QtWidgets import QSpinBox as _QSpin
        ctrl_grid.addWidget(QLabel('⚖️ 豆量'), 2, 0)
        self._bean_spin = _QSpin()
        self._bean_spin.setRange(100, 500)
        self._bean_spin.setValue(_DEFAULT_BEAN_LOAD)
        self._bean_spin.setSuffix(' g')
        self._bean_spin.setSingleStep(50)
        self._bean_spin.valueChanged.connect(self._on_bean_load_changed)
        ctrl_grid.addWidget(self._bean_spin, 2, 1, 1, 4)

        self._effect_label = QLabel('')
        self._effect_label.setStyleSheet('color:#888;font-size:9pt;')
        ctrl_grid.addWidget(self._effect_label, 3, 0, 1, 5)
        root.addWidget(ctrl_box)

        # ── Stage ──
        self._stage = QLabel('等待開始…')
        self._stage.setStyleSheet(
            'QLabel{background:#1a1a1a;color:#ccc;padding:4px;border-radius:3px;}'
        )
        root.addWidget(self._stage)

        # ── Speed ──
        spd_row = QHBoxLayout()
        spd_row.addWidget(QLabel('速度：'))
        self._speed_combo = QComboBox()
        self._speed_combo.addItems(['1× 正常', '3× 快速', '10× 測試'])
        self._speed_combo.setCurrentIndex(2)
        self._speed_combo.currentIndexChanged.connect(self._on_speed_change)
        spd_row.addWidget(self._speed_combo)
        spd_row.addStretch()
        root.addLayout(spd_row)

        # ── Event buttons ──
        ev_row = QHBoxLayout()
        self._btn_charge = QPushButton('CHARGE 投豆')
        self._btn_fc     = QPushButton('FC 一爆')
        self._btn_drop   = QPushButton('DROP 出豆')
        self._btn_charge.clicked.connect(self._on_charge)
        self._btn_fc.clicked.connect(self._on_fc)
        self._btn_drop.clicked.connect(self._on_drop)
        for b in (self._btn_charge, self._btn_fc, self._btn_drop):
            b.setEnabled(False)
            ev_row.addWidget(b)
        root.addLayout(ev_row)

        # ── Start / Stop / Ask AI ──
        ctrl_row = QHBoxLayout()
        self._btn_start = QPushButton('▶ 開始模擬')
        self._btn_stop  = QPushButton('■ 停止')
        self._btn_ask   = QPushButton('📣 立即詢問 AI')
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_ask.clicked.connect(self._on_ask_ai)
        self._btn_stop.setEnabled(False)
        self._btn_ask.setEnabled(False)
        ctrl_row.addWidget(self._btn_start)
        ctrl_row.addWidget(self._btn_stop)
        ctrl_row.addWidget(self._btn_ask)
        root.addLayout(ctrl_row)

    def _lcd(self, grid: QGridLayout, row: int, col: int) -> QLabel:
        val = QLabel('---')
        val.setFont(QFont('Courier', 14, QFont.Weight.Bold))
        val.setStyleSheet('color:#90ee90;')
        grid.addWidget(val, row, col)
        return val

    def _make_control(self, grid: QGridLayout, row: int,
                      title: str, color: str, cb) -> 'tuple[QSlider, QLabel]':
        grid.addWidget(QLabel(title), row, 0)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(1, 9)
        slider.setValue(5)
        slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        slider.setTickInterval(1)
        slider.valueChanged.connect(cb)
        grid.addWidget(slider, row, 1)
        minus = QPushButton('－')
        minus.setFixedWidth(30)
        minus.clicked.connect(lambda: slider.setValue(slider.value() - 1))
        grid.addWidget(minus, row, 2)
        lbl = QLabel('5')
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setFixedWidth(24)
        lbl.setStyleSheet(f'font-weight:bold;font-size:13pt;color:{color};')
        grid.addWidget(lbl, row, 3)
        plus = QPushButton('＋')
        plus.setFixedWidth(30)
        plus.clicked.connect(lambda: slider.setValue(slider.value() + 1))
        grid.addWidget(plus, row, 4)
        return slider, lbl

    # ── Control slots ─────────────────────────────────────────────────────────

    @pyqtSlot(int)
    def _on_heat_changed(self, v: int) -> None:
        self._heat = v
        self._heat_label.setText(str(v))
        self._update_effect_hint()

    @pyqtSlot(int)
    def _on_airflow_changed(self, v: int) -> None:
        self._airflow = v
        self._air_label.setText(str(v))
        self._update_effect_hint()

    @pyqtSlot(int)
    def _on_bean_load_changed(self, v: int) -> None:
        self._bean_load = v

    def _update_effect_hint(self) -> None:
        import math
        def _nl(delta: float, scale: float) -> float:
            return math.copysign(scale * (abs(delta) ** 0.75), delta)
        et_delta  = (self._heat - 5) * 12 - (self._airflow - 5) * 5
        ror_delta = _nl(self._heat - 5, 0.9) - _nl(self._airflow - 5, 0.45)
        lf = self._bean_load / 250.0
        tau_et = int(12 * lf); tau_bt = int(40 * lf)
        sign_et  = '+' if et_delta  >= 0 else ''
        sign_ror = '+' if ror_delta >= 0 else ''
        self._effect_label.setText(
            f'ET {sign_et}{et_delta:.0f}°C（~{tau_et}s）  '
            f'RoR {sign_ror}{ror_delta:.1f}°/m（~{tau_bt}s）'
        )

    @pyqtSlot(int)
    def _on_speed_change(self, idx: int) -> None:
        if self._timer.isActive():
            self._timer.setInterval(_SPEED_INTERVALS[idx])

    @pyqtSlot()
    def _on_start(self) -> None:
        self._sim_time = 0.0; self._charge_at = None
        self._timex.clear(); self._temp2.clear()
        self._timeindex = [-1, 0, 0, 0, 0, 0, 0, 0]
        self._ror_buffer.clear(); self._bt_prev.clear()
        self._bt_deviation = 0.0; self._ror_adj = 0.0
        self._noise_bt = 0.0; self._event_near_t1 = False
        self._fc_exo_elapsed = 0.0
        self._chart_times.clear(); self._chart_bt.clear()
        self._chart_et.clear(); self._chart_ror.clear()
        self._tp_marked = False; self._tick_count = 0
        self._heat = 5; self._airflow = 5
        self._heat_slider.setValue(5); self._air_slider.setValue(5)
        self._clear_chart()
        if hasattr(self.aw, 'ai_advisor'):
            self.aw.ai_advisor.reset_for_new_roast()
        self._btn_start.setEnabled(False); self._btn_stop.setEnabled(True)
        self._btn_charge.setEnabled(True)
        self._btn_fc.setEnabled(False); self._btn_drop.setEnabled(False)
        self._timer.start(_SPEED_INTERVALS[self._speed_combo.currentIndex()])
        self._stage.setText('機器預熱中… 按 CHARGE 投豆')

    @pyqtSlot()
    def _on_stop(self) -> None:
        self._timer.stop()
        self._btn_start.setEnabled(True); self._btn_stop.setEnabled(False)
        for b in (self._btn_charge, self._btn_fc, self._btn_drop):
            b.setEnabled(False)
        self._stage.setText('已停止')

    @pyqtSlot()
    def _on_charge(self) -> None:
        self._charge_at = self._sim_time
        self._timeindex[0] = max(0, len(self._timex) - 1)
        self._bt_deviation = 0.0; self._ror_adj = 0.0
        self._fc_exo_elapsed = 0.0
        self._et = _lerp(_ET_KEYS, 0.0)   # init ET at CHARGE
        self._ror_buffer.clear(); self._bt_prev.clear()
        self._event_near_t1 = False
        self._chart_times.clear(); self._chart_bt.clear()
        self._chart_et.clear(); self._chart_ror.clear()
        self._tp_marked = False
        self._clear_chart()
        if hasattr(self.aw, 'ai_advisor'):
            self.aw.ai_advisor.reset_for_new_roast()
        self._btn_charge.setEnabled(False); self._btn_fc.setEnabled(True)
        self._btn_ask.setEnabled(True)
        self._stage.setText('已投豆 — 降溫至回溫點…')
        self._stage.setStyleSheet('QLabel{background:#1a2a3a;color:#4fc3f7;padding:4px;border-radius:3px;}')
        self._fire_advice('CHARGE（投豆）')

    @pyqtSlot()
    def _on_fc(self) -> None:
        self._timeindex[2] = max(0, len(self._timex) - 1)
        self._btn_fc.setEnabled(False); self._btn_drop.setEnabled(True)
        self._stage.setText('🔥 一爆 — 發展期')
        self._stage.setStyleSheet('QLabel{background:#3a1a00;color:#ff8c00;padding:4px;border-radius:3px;}')
        fc_min = (self._sim_time - self._charge_at) / 60.0 if self._charge_at else 0.0
        self._add_chart_marker('FC', fc_min, '#ff8c00')
        self._fc_exo_elapsed = 0.0  # start exothermic countdown
        self._fire_advice('一爆開始（FC）')

    @pyqtSlot()
    def _on_drop(self) -> None:
        self._timeindex[6] = max(0, len(self._timex) - 1)
        self._btn_drop.setEnabled(False)
        self._btn_ask.setEnabled(False)
        self._timer.stop()
        self._btn_start.setEnabled(True); self._btn_stop.setEnabled(False)
        self._stage.setText('✅ 出豆完成 — 等待烘後評估…')
        self._stage.setStyleSheet('QLabel{background:#1a3a1a;color:#90ee90;padding:4px;border-radius:3px;}')
        drop_min = (self._sim_time - self._charge_at) / 60.0 if self._charge_at else 0.0
        self._add_chart_marker('DROP', drop_min, '#90ee90')
        if hasattr(self.aw, 'ai_advisor') and self.aw.ai_advisor.enabled:
            self._fire_advice('DROP（下豆）')
            from artisanlib.canvas import _build_roast_summary
            summary = _build_roast_summary(
                self._timeindex, self._timex, self._temp2, 'C',
                False, None, None, None,
                delta2=None,
                session_stats=self.aw.ai_advisor.get_session_stats())
            self.aw.ai_advisor.request_summary(summary)

    # ── Physics ───────────────────────────────────────────────────────────────

    def _update_physics(self, curve_t: float) -> None:
        """Advance ET and BT deviation by 1 simulated second.

        BT = base_curve(t) + deviation
        Thermal lag scales with bean load: lighter load → faster response.
        Non-linear heat effect: extreme settings have diminishing returns.
        FC exothermic: +0.8°C/min RoR boost decaying over ~90s.
        """
        load_factor = self._bean_load / 250.0  # 1.0 at default 250g
        tau_et = 12.0 * load_factor
        tau_bt = 40.0 * load_factor

        # ET: fast response to heat/airflow
        base_et   = _lerp(_ET_KEYS, curve_t)
        et_target = base_et + (self._heat - 5) * 12 - (self._airflow - 5) * 5
        self._et += (et_target - self._et) / max(tau_et, 1.0)

        # Non-linear heat/airflow → RoR: use power curve so extremes have diminishing returns
        def _nl(delta: float, scale: float) -> float:
            import math
            return math.copysign(scale * (abs(delta) ** 0.75), delta)

        target_ror_adj = _nl(self._heat - 5, 0.9) - _nl(self._airflow - 5, 0.45)

        # FC exothermic boost: peaks at +0.8 °C/min at FC, decays over 90s
        if self._timeindex[2] > 0:
            self._fc_exo_elapsed += 1.0
            exo = 0.8 * max(0.0, 1.0 - self._fc_exo_elapsed / 90.0)
            target_ror_adj += exo

        self._ror_adj      += (target_ror_adj - self._ror_adj) / max(tau_bt, 1.0)
        self._bt_deviation += self._ror_adj / 60.0      # per second

    # ── Tick ──────────────────────────────────────────────────────────────────

    @pyqtSlot()
    def _tick(self) -> None:
        self._sim_time += 1.0
        self._tick_count += 1
        curve_t = (self._sim_time - self._charge_at
                   if self._charge_at is not None else self._sim_time - 60.0)

        if self._charge_at is None:
            # Pre-charge: follow base curve only
            self._noise_bt = self._noise_bt * 0.75 + random.gauss(0, 0.5) * 0.25
            bt = _lerp(_BT_KEYS, curve_t) + self._noise_bt
            et = _lerp(_ET_KEYS, curve_t) + random.gauss(0, 0.4)
        else:
            self._update_physics(curve_t)
            self._noise_bt = self._noise_bt * 0.75 + random.gauss(0, 0.3) * 0.25
            bt = _lerp(_BT_KEYS, curve_t) + self._bt_deviation + self._noise_bt
            et = self._et + random.gauss(0, 0.35)

        # RoR
        self._bt_prev.append(bt)
        n = len(self._bt_prev)
        ror_bt = ((self._bt_prev[-1] - self._bt_prev[0]) / (n - 1) * 60.0
                  if n >= 6 else 0.0) + random.gauss(0, 0.10)

        self._timex.append(self._sim_time)
        self._temp2.append(bt)
        self._ror_buffer.append(ror_bt)

        # LCD
        self._lcd_bt.setText(f'{bt:.1f}°C')
        self._lcd_et.setText(f'{et:.1f}°C')
        ror_color = ('#ff6b6b' if ror_bt < 3 or ror_bt > 18 else
                     '#ffd93d' if ror_bt < 5 else '#90ee90')
        self._lcd_ror.setStyleSheet(f'color:{ror_color};font-weight:bold;font-size:14pt;')
        self._lcd_ror.setText(f'{ror_bt:.1f}°/m')

        if self._charge_at is not None:
            elapsed = self._sim_time - self._charge_at
            self._lcd_time.setText(f'{int(elapsed//60)}:{int(elapsed%60):02d}')

            # chart data
            self._chart_times.append(elapsed / 60.0)
            self._chart_bt.append(bt)
            self._chart_et.append(et)
            self._chart_ror.append(ror_bt)

            # auto-stage label
            if ror_bt > 0 and not self._tp_marked and len(self._chart_ror) > 10:
                # past TP when RoR turns positive
                prev = list(self._ror_buffer)
                if len(prev) >= 15 and all(r < 0 or abs(r) < 1.5 for r in prev[:10]):
                    self._tp_marked = True
                    self._stage.setText('回溫點已過 — 乾燥期')
                    self._stage.setStyleSheet('QLabel{background:#1a1a1a;color:#ccc;padding:4px;border-radius:3px;}')
                    tp_min = elapsed / 60.0
                    self._add_chart_marker('TP', tp_min, '#aaaaaa')

        # auto near-T1
        charged = self._timeindex[0] >= 0
        fc_set  = self._timeindex[2] > 0
        dropped = self._timeindex[6] > 0

        if (charged and not fc_set and not dropped
                and not self._event_near_t1 and bt >= 162.0):
            self._event_near_t1 = True
            self._stage.setText('⚡ 接近梅納窗口結束（BT ≈ 162°C）')
            self._stage.setStyleSheet('QLabel{background:#2a2a00;color:#ffd700;padding:4px;border-radius:3px;}')
            t1_min = (self._sim_time - self._charge_at) / 60.0 if self._charge_at is not None else 0.0
            self._add_chart_marker('T1', t1_min, '#ffd700')
            self._fire_advice('接近梅納窗口結束（BT≈162°C）')
        elif charged and not dropped:
            self._fire_advice('')

        # chart redraw
        if self._tick_count % _CHART_EVERY_N == 0 and self._charge_at is not None:
            self._redraw_chart()

    # ── Chart ─────────────────────────────────────────────────────────────────

    def _add_chart_marker(self, label: str, x_min: float, color: str) -> None:
        """Add a labelled vertical line at x_min (minutes from CHARGE)."""
        if label in self._vlines:
            old_vl, old_txt = self._vlines[label]
            old_vl.remove()
            old_txt.remove()
        vl = self._ax_bt.axvline(x=x_min, color=color, lw=1.2, linestyle=':', alpha=0.8)
        txt = self._ax_bt.text(x_min + 0.02, self._ax_bt.get_ylim()[1] - 10,
                               label, color=color, fontsize=7, va='top')
        self._vlines[label] = (vl, txt)

    def _clear_chart(self) -> None:
        for line in (self._line_bt, self._line_et, self._line_ror):
            line.set_data([], [])
        for vl, txt in self._vlines.values():
            vl.remove()
            txt.remove()
        self._vlines.clear()
        self._ax_bt.set_xlim(0, 1)
        self._canvas.draw_idle()

    def _redraw_chart(self) -> None:
        xs = self._chart_times
        if not xs:
            return
        self._line_bt.set_data(xs, self._chart_bt)
        self._line_et.set_data(xs, self._chart_et)
        self._line_ror.set_data(xs, self._chart_ror)

        x_max = max(xs[-1] + 0.5, 2.0)
        self._ax_bt.set_xlim(0, x_max)

        # auto-scale BT/ET y-axis (keep floor at 60 to show full TP dip)
        all_temps = self._chart_bt + self._chart_et
        if all_temps:
            self._ax_bt.set_ylim(min(60.0, min(all_temps) - 10), max(all_temps) + 15)

        self._canvas.draw_idle()

    # ── AI advice ─────────────────────────────────────────────────────────────

    @pyqtSlot()
    def _on_ask_ai(self) -> None:
        if not hasattr(self.aw, 'ai_advisor') or not self.aw.ai_advisor.enabled:
            return
        bt  = self._temp2[-1] if self._temp2 else 0.0
        ror = list(self._ror_buffer)[-1] if self._ror_buffer else 0.0
        et  = self._et if self._charge_at is not None else _lerp(_ET_KEYS, self._sim_time - 60.0)
        self.aw.ai_advisor.force_query(
            bt=bt, et=et, ror_bt=ror,
            timeindex=self._timeindex, timex=self._timex, mode='C',
            ror_values=list(self._ror_buffer),
        )

    def _fire_advice(self, trigger: str) -> None:
        if not hasattr(self.aw, 'ai_advisor') or not self.aw.ai_advisor.enabled:
            return
        if trigger:  # show AI querying in stage label temporarily
            pass  # status shown via aiQuerySignal in main window
        bt  = self._temp2[-1] if self._temp2 else 0.0
        ror = list(self._ror_buffer)[-1] if self._ror_buffer else 0.0
        et  = (self._et if self._charge_at is not None
               else _lerp(_ET_KEYS, self._sim_time - 60.0))
        self.aw.ai_advisor.request_advice(
            bt=bt, et=et, ror_bt=ror, ror_et=2.5,
            timeindex=self._timeindex,
            timex=self._timex,
            mode='C',
            ror_values=list(self._ror_buffer),
            force_trigger=trigger,
        )
