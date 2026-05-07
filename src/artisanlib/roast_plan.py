"""
roast_plan.py — 烘焙計畫對話框
從 devon-coffee-wheel/index.html 移植：烘焙計畫單 + 曲線設計器
"""
from __future__ import annotations

import csv
import io
import json
import math
import urllib.request
from typing import TYPE_CHECKING, Any, Optional

from PyQt6.QtCore import QSettings, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from artisanlib.dialogs import ArtisanDialog

if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow

# ── 烘焙基線 ─────────────────────────────────────────────────────────────────
ROAST_BASELINE: dict[str, dict[str, Any]] = {
    'cinnamon':       {'chargeTemp': 180, 'bt': [195, 200], 'devPct': [10, 14], 'totalMin': [8,  11], 'note': '一爆初即下豆',      'warn': '極淺焙需確保豆心熟透，勿只追求一爆表面訊號'},
    'city':           {'chargeTemp': 185, 'bt': [203, 208], 'devPct': [14, 18], 'totalMin': [10, 12], 'note': '一爆中段',          'warn': 'RoR 曲線維持平穩下降'},
    'city_plus':      {'chargeTemp': 190, 'bt': [208, 213], 'devPct': [17, 20], 'totalMin': [11, 13], 'note': '一爆末尾',          'warn': '最平衡的精品焙度'},
    'full_city':      {'chargeTemp': 195, 'bt': [215, 220], 'devPct': [18, 22], 'totalMin': [12, 14], 'note': '一爆完全結束',      'warn': 'Development 勿超過 22% 否則容易 baked'},
    'full_city_plus': {'chargeTemp': 200, 'bt': [222, 228], 'devPct': [20, 24], 'totalMin': [13, 15], 'note': '二爆邊緣（稀疏爆聲）', 'warn': ''},
    'vienna':         {'chargeTemp': 205, 'bt': [228, 233], 'devPct': [22, 26], 'totalMin': [14, 16], 'note': '二爆初段',          'warn': '豆表微油光'},
    'french':         {'chargeTemp': 210, 'bt': [238, 243], 'devPct': [25, 28], 'totalMin': [15, 17], 'note': '二爆完全',          'warn': '接近碳化邊緣，超過 28% 即過焙'},
}

COUNTRIES = [
    ('none',    '-- 選擇國家 --'),
    ('eth',     '衣索比亞 (Ethiopia)'),
    ('ken',     '肯亞 (Kenya)'),
    ('rwa',     '盧安達 (Rwanda)'),
    ('bdi',     '蒲隆地 (Burundi)'),
    ('tza',     '坦尚尼亞 (Tanzania)'),
    ('uga',     '烏干達 (Uganda)'),
    ('yem',     '葉門 (Yemen)'),
    ('pan',     '巴拿馬 (Panama)'),
    ('gua',     '瓜地馬拉 (Guatemala)'),
    ('crc',     '哥斯大黎加 (Costa Rica)'),
    ('slv',     '薩爾瓦多 (El Salvador)'),
    ('hnd',     '宏都拉斯 (Honduras)'),
    ('nic',     '尼加拉瓜 (Nicaragua)'),
    ('mex',     '墨西哥 (Mexico)'),
    ('jam',     '牙買加藍山 (Jamaica)'),
    ('bra',     '巴西 (Brazil)'),
    ('col',     '哥倫比亞 (Colombia)'),
    ('per',     '秘魯 (Peru)'),
    ('idn',     '印尼 (Sumatra/Java)'),
    ('twn',     '台灣 (Taiwan)'),
    ('chn',     '中國雲南 (Yunnan)'),
    ('hwi',     '夏威夷 (Kona)'),
    ('png',     '巴布亞紐幾內亞 (PNG)'),
    ('vnm',     '越南 (Vietnam)'),
    ('tha',     '泰國 (Thailand)'),
]

ALTITUDES = [
    ('std',  '標準海拔 (1200-1500m)'),
    ('high', '極高海拔 (1800m+)'),
    ('low',  '低海拔 (1000m-)'),
]

VARIETIES = [
    ('standard',     '鐵比卡 (Typica)'),
    ('red_bourbon',  '紅波旁 (Red Bourbon)'),
    ('yellow_bourbon','黃波旁 (Yellow Bourbon)'),
    ('pink_bourbon', '粉紅波旁 (Pink Bourbon)'),
    ('blue_mt',      '藍山種 (Blue Mt.)'),
    ('mandheling',   '曼特寧種 (Mandheling)'),
    ('geisha',       '瑰夏 (Geisha)'),
    ('sl28',         'SL28/SL34 (肯亞)'),
    ('pacamara',     '帕卡瑪拉 (Pacamara)'),
    ('sidra',        '西得拉 (Sidra)'),
    ('java',         '爪哇種 (Java)'),
    ('sudan_rume',   '蘇丹汝梅 (Sudan Rume)'),
    ('maragogype',   '大象豆 (Maragogype)'),
    ('caturra',      '卡杜拉 (Caturra)'),
    ('catuai',       '卡杜艾 (Catuai)'),
    ('pacas',        '帕卡斯 (Pacas)'),
    ('mokka',        '摩卡 (Mokka)'),
    ('wush_wush',    'Wush Wush (衣索比亞古種)'),
    ('laurina',      'Laurina (低咖啡因種)'),
    ('castillo',     'Castillo (哥倫比亞抗病種)'),
]

PROCESSES = [
    ('washed',        '水洗 (Washed)'),
    ('natural',       '日曬 (Natural)'),
    ('honey_light',   '白/黃蜜處理 (Honey)'),
    ('honey_dark',    '紅/黑蜜處理 (Dark Honey)'),
    ('anaerobic_wash','厭氧水洗 (Anaerobic Wash)'),
    ('anaerobic_nat', '厭氧日曬 (Anaerobic Nat)'),
    ('wet_hulled',    '印尼濕剝法 (Giling Basah)'),
    ('carbonic',      '二氧化碳浸漬 (Carbonic Maceration)'),
]

ROASTS = [
    ('cinnamon',       '極淺焙 (Cinnamon)'),
    ('city',           '淺焙 (City)'),
    ('city_plus',      '中淺焙 (City+)'),
    ('full_city',      '中焙 (Full City)'),
    ('full_city_plus', '中深焙 (Full City+)'),
    ('vienna',         '深焙 (Vienna)'),
    ('french',         '極深焙 (French)'),
]

SETTINGS_KEY = 'RoastPlan'

# ── 演算法（移植自 JS _rs_* 函數）─────────────────────────────────────────────

def _rp_adj(s: dict, stage: str, axis: str, delta: float, reason: str) -> None:
    if axis == 'charge':  s['chargeTemp']   += delta
    if axis == 'btLow':   s['btLow']        += delta
    if axis == 'btHigh':  s['btHigh']       += delta
    if axis == 'devLow':  s['devLow']       += delta
    if axis == 'devHigh': s['devHigh']      += delta
    if axis == 'drying':  s['dryingAdjSec'] += delta
    s['adjustments'].append({'stage': stage, 'axis': axis, 'delta': delta, 'reason': reason})


def _rp_stage_variety(s: dict) -> None:
    v = s['cfg'].get('varietyCode', '')
    if v in ('pacamara', 'maragogype'):
        _rp_adj(s, 'Variety', 'drying', +30, '大象豆顆粒巨大，Drying 延長 30s 確保豆心熟透（否則易生心）')
    if v in ('geisha', 'wush_wush', 'sidra', 'laurina'):
        lbl = {'geisha': '瑰夏', 'wush_wush': 'Wush Wush', 'sidra': 'Sidra', 'laurina': 'Laurina'}[v]
        _rp_adj(s, 'Variety', 'devHigh', -3, f'{lbl} 風味細膩：Development 上限降 3%，避免烤壞頂香')
    if v == 'sl28':
        _rp_adj(s, 'Variety', 'devLow', +2, 'SL28 黑醋栗酸質：Development 下限 +2%，讓酸質完整展現')
    if v in ('mandheling', 'java', 'mokka'):
        lbl = {'mokka': 'Mokka', 'java': 'Java', 'mandheling': '曼特寧'}[v]
        _rp_adj(s, 'Variety', 'btHigh', +2, f'{lbl} 需中深焙化開土木/苦甜：BT 上限 +2°C')
    if v == 'blue_mt':
        _rp_adj(s, 'Variety', 'devLow', -2, '藍山風味溫和，Development 短一點保留細緻度')


def _rp_stage_processing(s: dict) -> None:
    p = s['cfg'].get('procCode', '')
    if p in ('natural', 'anaerobic_nat'):
        _rp_adj(s, 'Processing', 'devHigh', -3, '日曬/厭氧日曬果糖含量高，Development 不宜過長以免果糖焦苦')
    if p == 'wet_hulled':
        _rp_adj(s, 'Processing', 'btLow',  +3, '濕剝豆需較高 BT 化開土木調：下限 +3°C')
        _rp_adj(s, 'Processing', 'btHigh', +3, '濕剝豆 BT 上限同步 +3°C')
    roast = s['cfg'].get('roast', '')
    if p == 'washed' and roast in ('cinnamon', 'city'):
        _rp_adj(s, 'Processing', 'devHigh', -2, '水洗 × 淺焙 Development 短，保留明亮酸質')
    if p == 'honey_dark':
        _rp_adj(s, 'Processing', 'devLow', +2, '深蜜發酵度高，Development 略長幫焦糖化完整')


def _rp_stage_altitude(s: dict) -> None:
    alt = s['cfg'].get('alt', '')
    if alt == 'high':
        _rp_adj(s, 'Altitude', 'drying', +30, '高海拔豆密度高，Drying 延長 30s 讓水分均勻排出')
        _rp_adj(s, 'Altitude', 'charge', +3,  '高密度豆入豆溫 +3°C，避免 RoR flick 過大')
    if alt == 'low':
        _rp_adj(s, 'Altitude', 'drying', -20, '低海拔豆密度低，Drying 縮短 20s 避免 baked')


def _rp_stage_machine_size(s: dict) -> None:
    kg = s['cfg'].get('machineKg', '')
    try:
        kg = float(kg)
    except (TypeError, ValueError):
        return
    if kg <= 0:
        return
    if kg < 0.5:
        _rp_adj(s, 'MachineSize', 'charge', -12, f'超小型機 {kg}kg（氣流/熱風）：熱質量極小，入豆溫 -12°C 防瞬熱')
    elif kg < 1.5:
        _rp_adj(s, 'MachineSize', 'charge', -10, f'小型家用機 {kg}kg（Bullet/Hottop 級）：熱質量小，入豆溫 -10°C')
    elif kg < 3:
        _rp_adj(s, 'MachineSize', 'charge', -8,  f'家用中小機 {kg}kg（Olomo 1.5/Bullet 2kg 級）：入豆溫 -8°C')
    elif kg < 5:
        _rp_adj(s, 'MachineSize', 'charge', -4,  f'小商用機 {kg}kg：入豆溫 -4°C')
    elif kg > 15:
        _rp_adj(s, 'MachineSize', 'charge', +3,  f'大型商用機 {kg}kg：熱質量巨大、入豆溫 +3°C 補蓄熱')


def _rp_stage_machine_load(s: dict) -> None:
    try:
        g  = float(s['cfg'].get('batchG', '') or 0)
        kg = float(s['cfg'].get('machineKg', '') or 0)
    except (TypeError, ValueError):
        return
    if not g:
        return
    if kg > 0:
        ratio = g / (kg * 1000)
        pct   = round(ratio * 100)
        if ratio > 0.95:
            _rp_adj(s, 'MachineLoad', 'charge', +7,  f'⚠️ 超滿載 {pct}% ({g}g/{kg}kg 機)：建議降批量；若堅持 charge +7°C')
            _rp_adj(s, 'MachineLoad', 'drying', +30, '超滿載需延長 Drying 讓熱穿透')
        elif ratio > 0.80:
            _rp_adj(s, 'MachineLoad', 'charge', +5,  f'滿載 {pct}% ({g}g/{kg}kg 機)：熱質量壓力大、入豆溫 +5°C')
        elif ratio >= 0.50:
            pass  # 理想區間
        elif ratio >= 0.30:
            _rp_adj(s, 'MachineLoad', 'charge', -2,  f'偏低載 {pct}% ({g}g/{kg}kg 機)：入豆溫 -2°C')
        elif ratio >= 0.15:
            _rp_adj(s, 'MachineLoad', 'charge', -5,  f'低載 {pct}% ({g}g/{kg}kg 機)：易過熱、入豆溫 -5°C')
            _rp_adj(s, 'MachineLoad', 'drying', -15, '低載 Drying 縮短 15s 避免 baked')
        else:
            _rp_adj(s, 'MachineLoad', 'charge', -8,  f'⚠️ 極低載 {pct}% ({g}g/{kg}kg 機)：強烈建議換小機')
    else:
        if g >= 400:
            _rp_adj(s, 'Batch', 'charge', +3, f'大批量 {g}g：熱質量高，入豆溫 +3°C（填機器容量可更精準）')
        elif g <= 150:
            _rp_adj(s, 'Batch', 'charge', -3, f'小批量 {g}g：熱質量低，入豆溫 -3°C（填機器容量可更精準）')


def _rp_stage_ambient(s: dict) -> None:
    try:
        t = float(s['cfg'].get('ambientC', '') or 0)
    except (TypeError, ValueError):
        return
    if t < 18:
        _rp_adj(s, 'Ambient', 'charge', +2, f'室溫 {t}°C 偏冷：入豆溫 +2°C 補散熱損失')
    elif t > 28:
        _rp_adj(s, 'Ambient', 'charge', -2, f'室溫 {t}°C 偏熱：入豆溫 -2°C 防過熱')


def _rp_stage_density(s: dict) -> None:
    try:
        d = float(s['cfg'].get('densityGL', '') or 0)
    except (TypeError, ValueError):
        return
    if d >= 750:
        _rp_adj(s, 'Density', 'drying', +15, f'高密度 {d}g/L：Drying +15s 讓水分均勻排出')
    elif 0 < d <= 650:
        _rp_adj(s, 'Density', 'drying', -15, f'低密度 {d}g/L：Drying -15s 避免過乾 baked')


def _rp_stage_moisture(s: dict) -> None:
    try:
        m = float(s['cfg'].get('moisturePct', '') or 0)
    except (TypeError, ValueError):
        return
    if m > 11.5:
        _rp_adj(s, 'Moisture', 'drying', +20, f'含水率 {m}% 偏高（新豆/雨季）：Drying +20s 充分脫水')
    elif 0 < m < 9.5:
        _rp_adj(s, 'Moisture', 'drying', -20, f'含水率 {m}% 偏低（老豆/乾季）：Drying -20s 防 baked')


def compute_roast_profile(cfg: dict) -> dict:
    base = ROAST_BASELINE.get(cfg.get('roast', ''), ROAST_BASELINE['full_city'])
    s: dict[str, Any] = {
        'cfg': cfg,
        'base': base,
        'chargeTemp':   float(base['chargeTemp']),
        'btLow':        float(base['bt'][0]),
        'btHigh':       float(base['bt'][1]),
        'devLow':       float(base['devPct'][0]),
        'devHigh':      float(base['devPct'][1]),
        'totalMinLow':  float(base['totalMin'][0]),
        'totalMinHigh': float(base['totalMin'][1]),
        'dryingAdjSec': 0.0,
        'adjustments':  [],
    }
    _rp_stage_variety(s)
    _rp_stage_processing(s)
    _rp_stage_altitude(s)
    _rp_stage_machine_size(s)
    _rp_stage_machine_load(s)
    _rp_stage_ambient(s)
    _rp_stage_density(s)
    _rp_stage_moisture(s)
    # finalize
    if s['devLow'] > s['devHigh'] - 1:
        s['devLow'] = max(5.0, s['devHigh'] - 2)
    if s['btLow'] > s['btHigh']:
        s['btLow'] = s['btHigh'] - 2
    s['chargeTemp'] = round(s['chargeTemp'])
    s['btLow']      = round(s['btLow'])
    s['btHigh']     = round(s['btHigh'])
    s['devLow']     = round(s['devLow'])
    s['devHigh']    = round(s['devHigh'])
    return s


def rp_default_density(ctx: dict) -> float:
    v    = ctx.get('varietyCode', '')
    proc = ctx.get('procCode', '')
    alt  = ctx.get('alt', '')
    base = 700.0
    if alt == 'high':  base += 35
    elif alt == 'low': base -= 40
    if v in ('pacamara', 'maragogype'):     base -= 30
    if v in ('geisha', 'sl28', 'wush_wush', 'blue_mt', 'sudan_rume'): base += 15
    if v == 'mokka':                        base += 10
    if v in ('mandheling', 'java'):         base -= 20
    if proc in ('natural', 'anaerobic_nat'): base += 10
    if proc == 'wet_hulled':                base -= 25
    return round(base)


def rp_default_moisture(ctx: dict) -> float:
    proc = ctx.get('procCode', '')
    if proc == 'washed':                                      return 10.3
    if proc in ('natural', 'anaerobic_nat'):                  return 11.0
    if proc in ('honey_light', 'honey_dark'):                 return 10.7
    if proc == 'wet_hulled':                                  return 12.5
    if proc == 'anaerobic_wash':                              return 10.8
    return 10.5


# ── 室溫自動抓取（QThread）────────────────────────────────────────────────────

class AmbientFetchThread(QThread):
    result_ready = pyqtSignal(float)
    error        = pyqtSignal(str)

    def run(self) -> None:
        try:
            # Step 1: IP 地理定位
            with urllib.request.urlopen('http://ip-api.com/json/?fields=lat,lon', timeout=6) as r:
                geo = json.loads(r.read())
            lat = round(geo['lat'], 2)
            lon = round(geo['lon'], 2)
            # Step 2: open-meteo 現在溫度
            url = (f'https://api.open-meteo.com/v1/forecast'
                   f'?latitude={lat}&longitude={lon}&current=temperature_2m')
            with urllib.request.urlopen(url, timeout=6) as r:
                data = json.loads(r.read())
            t = data['current']['temperature_2m']
            self.result_ready.emit(round(float(t)))
        except Exception as e:
            self.error.emit(str(e))


# ── 錨點行 Widget ─────────────────────────────────────────────────────────────

class AnchorWidget(QFrame):
    """Compact anchor: title + 2×2 grid (time|temp / fire|damper)."""
    changed = pyqtSignal()

    def __init__(self, label: str,
                 t: float, temp: float, fire: float, damper: float,
                 t_min: float, t_max: float,
                 temp_min: float, temp_max: float,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet('QFrame { background: #fafafa; border-radius: 4px; }')

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(6, 4, 6, 6)
        vbox.setSpacing(4)

        title = QLabel(label)
        title.setStyleSheet('font-weight: bold; font-size: 13px; color: #5d4037;')
        title.setWordWrap(True)
        vbox.addWidget(title)

        self._t_spin      = self._make_spin(t,      t_min,    t_max,    0.1)
        self._temp_spin   = self._make_spin(temp,    temp_min, temp_max, 1.0)
        self._fire_spin   = self._make_spin(fire,    0,        100,      5.0)
        self._damper_spin = self._make_spin(damper,  0,        100,      5.0)

        g = QGridLayout()
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(2)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)

        def lbl(text: str) -> QLabel:
            l = QLabel(text)
            l.setStyleSheet('font-size: 12px; color: #555;')
            return l

        g.addWidget(lbl('⏱ 時間'),  0, 0)
        g.addWidget(self._t_spin,    0, 1)
        g.addWidget(lbl('🌡️ 溫度'),  0, 2)
        g.addWidget(self._temp_spin, 0, 3)
        g.addWidget(lbl('🔥 火力'),  1, 0)
        g.addWidget(self._fire_spin, 1, 1)
        g.addWidget(lbl('💨 風門'),  1, 2)
        g.addWidget(self._damper_spin, 1, 3)

        vbox.addLayout(g)

        for spin in (self._t_spin, self._temp_spin, self._fire_spin, self._damper_spin):
            spin.valueChanged.connect(self.changed)

    @staticmethod
    def _make_spin(val: float, lo: float, hi: float, step: float) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setSingleStep(step)
        sb.setValue(val)
        sb.setDecimals(1)
        sb.setMinimumWidth(62)
        return sb

    @property
    def values(self) -> dict:
        return {
            't':      self._t_spin.value(),
            'temp':   self._temp_spin.value(),
            'fire':   self._fire_spin.value(),
            'damper': self._damper_spin.value(),
        }

    def set_temp(self, v: float) -> None:
        self._temp_spin.blockSignals(True)
        self._temp_spin.setValue(v)
        self._temp_spin.blockSignals(False)

    def set_t(self, v: float) -> None:
        self._t_spin.blockSignals(True)
        self._t_spin.setValue(v)
        self._t_spin.blockSignals(False)


# ── 自訂中間調整點 Widget ──────────────────────────────────────────────────────

class CustomEventWidget(QFrame):
    removed  = pyqtSignal(object)   # emits self
    changed  = pyqtSignal()

    def __init__(self, t: float, fire: float, damper: float, t_max: float,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.Box)
        self.setStyleSheet('QFrame { background: #fffbf0; border: 1px solid #ffca28; }')

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(4)

        def spin(val: float, lo: float, hi: float, step: float, suffix: str = '') -> QDoubleSpinBox:
            sb = QDoubleSpinBox()
            sb.setRange(lo, hi)
            sb.setSingleStep(step)
            sb.setValue(val)
            sb.setDecimals(1)
            if suffix:
                sb.setSuffix(suffix)
            sb.setFixedWidth(76)
            return sb

        self._t      = spin(t,      0.2, t_max, 0.1, ' 分')
        self._fire   = spin(fire,   0,   100,   5,   ' %')
        self._damper = spin(damper, 0,   100,   5,   ' %')

        row.addWidget(QLabel('⏱'))
        row.addWidget(self._t)
        row.addWidget(QLabel('🔥'))
        row.addWidget(self._fire)
        row.addWidget(QLabel('💨'))
        row.addWidget(self._damper)

        btn_rm = QPushButton('✕')
        btn_rm.setFixedWidth(26)
        btn_rm.clicked.connect(lambda: self.removed.emit(self))
        row.addWidget(btn_rm)

        for sb in (self._t, self._fire, self._damper):
            sb.valueChanged.connect(self.changed)

    @property
    def values(self) -> dict:
        return {'t': self._t.value(), 'fire': self._fire.value(), 'damper': self._damper.value()}


# ── 主對話框 ──────────────────────────────────────────────────────────────────

class RoastPlanDlg(ArtisanDialog):

    def __init__(self, aw: 'ApplicationWindow') -> None:
        super().__init__(parent=aw, aw=aw)
        self.aw = aw
        self.setWindowTitle('烘焙計畫')
        self.setMinimumWidth(720)
        self.resize(960, 720)
        self.setStyleSheet('font-size: 13px;')

        self._profile: Optional[dict]  = None
        self._custom_event_widgets: list[CustomEventWidget] = []
        self._ambient_thread: Optional[AmbientFetchThread] = None

        self._build_ui()
        self._load_machine_kg()
        self._recompute()

    # ── 建立 UI ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # Tab widget
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_config_tab(),   '⚙️ 配置')
        self._tabs.addTab(self._build_curve_tab(),    '📈 曲線設計')
        self._tabs.addTab(self._build_stats_tab(),    '📊 統計分析')

        # Corner widget：四個按鈕貼在 tab bar 右上角，永遠可見
        corner = QWidget()
        corner_row = QHBoxLayout(corner)
        corner_row.setContentsMargins(0, 2, 4, 2)
        corner_row.setSpacing(4)

        btn_reset = QPushButton('🔄 重設')
        btn_csv   = QPushButton('📄 CSV')
        btn_xlsx  = QPushButton('📊 XLSX')
        btn_close = QPushButton('關閉')
        for b in (btn_reset, btn_csv, btn_xlsx, btn_close):
            b.setFixedHeight(24)
            b.setStyleSheet('font-size: 11px; padding: 0 8px;')
            corner_row.addWidget(b)

        btn_reset.clicked.connect(self._reset_anchors)
        btn_csv.clicked.connect(self._export_csv)
        btn_xlsx.clicked.connect(self._export_xlsx)
        btn_close.clicked.connect(self.close)

        self._tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        outer.addWidget(self._tabs, 1)

    def _build_config_tab(self) -> QWidget:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        vbox = QVBoxLayout(inner)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(8)
        vbox.addWidget(self._build_bean_group())
        vbox.addWidget(self._build_measure_group())
        vbox.addWidget(self._build_profile_group(), 1)

        scroll.setWidget(inner)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)
        return w

    def _build_curve_tab(self) -> QWidget:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        vbox = QVBoxLayout(inner)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(8)
        vbox.addWidget(self._build_anchor_group())
        vbox.addWidget(self._build_custom_events_group())
        vbox.addWidget(self._build_chart_widget())
        vbox.addStretch()

        scroll.setWidget(inner)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)
        return w

    def _build_stats_tab(self) -> QWidget:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        h = QHBoxLayout(inner)
        h.setContentsMargins(8, 8, 8, 8)
        h.setSpacing(10)

        # Left: 4 charts
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self._build_stats_widget())
        h.addWidget(left, 6)

        # Right: algo targets + text explanation
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        rv.addWidget(self._build_algo_visual_widget())
        rv.addWidget(self._build_stats_text_widget(), 1)
        h.addWidget(right, 4)

        scroll.setWidget(inner)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)
        return w

    # ── 豆子配置 ──────────────────────────────────────────────────────────────

    def _build_bean_group(self) -> QGroupBox:
        grp = QGroupBox('豆子配置')
        grid = QGridLayout(grp)
        grid.setSpacing(4)

        def combo(options: list) -> QComboBox:
            cb = QComboBox()
            for code, label in options:
                cb.addItem(label, code)
            return cb

        self._cb_country  = combo(COUNTRIES)
        self._cb_altitude = combo(ALTITUDES)
        self._cb_variety  = combo(VARIETIES)
        self._cb_proc     = combo(PROCESSES)
        self._cb_roast    = combo(ROASTS)

        # default: city_plus
        self._cb_roast.setCurrentIndex(2)

        grid.addWidget(QLabel('產區'),  0, 0); grid.addWidget(self._cb_country,  0, 1)
        grid.addWidget(QLabel('海拔'),  0, 2); grid.addWidget(self._cb_altitude, 0, 3)
        grid.addWidget(QLabel('品種'),  1, 0); grid.addWidget(self._cb_variety,  1, 1)
        grid.addWidget(QLabel('處理法'), 1, 2); grid.addWidget(self._cb_proc,     1, 3)
        grid.addWidget(QLabel('焙度'),  2, 0); grid.addWidget(self._cb_roast,    2, 1)

        for cb in (self._cb_country, self._cb_altitude, self._cb_variety,
                   self._cb_proc, self._cb_roast):
            cb.currentIndexChanged.connect(self._on_config_changed)

        return grp

    def _build_measure_group(self) -> QGroupBox:
        grp = QGroupBox('實測參數')
        grid = QGridLayout(grp)
        grid.setSpacing(8)
        grid.setContentsMargins(10, 10, 10, 10)

        def dspin(lo: float, hi: float, step: float, val: float, suffix: str = '') -> QDoubleSpinBox:
            sb = QDoubleSpinBox()
            sb.setRange(lo, hi)
            sb.setSingleStep(step)
            sb.setValue(val)
            sb.setSpecialValueText('—')
            if suffix:
                sb.setSuffix(suffix)
            return sb

        self._sp_machine = dspin(0, 200, 0.5, 0, ' kg')
        self._sp_batch   = dspin(0, 5000, 10, 0, ' g')
        self._sp_ambient = dspin(0, 50, 0.5, 0, ' °C')
        self._sp_density = dspin(0, 1000, 5, 0, ' g/L')
        self._sp_moisture= dspin(0, 20, 0.1, 0, ' %')

        # batch: required — highlight when zero
        self._sp_batch.setStyleSheet('background: #ffebee;')

        btn_geo = QPushButton('📍 自動')
        btn_geo.setFixedWidth(62)
        btn_geo.clicked.connect(self._fetch_ambient)
        self._btn_geo = btn_geo

        grid.addWidget(QLabel('🔥 機器容量（記住一次就好）'), 0, 0)
        grid.addWidget(self._sp_machine, 0, 1)
        grid.addWidget(QLabel('⚖️ 本次豆重 *必填'), 0, 2)
        grid.addWidget(self._sp_batch,   0, 3)

        row1_ambient = QHBoxLayout()
        row1_ambient.addWidget(self._sp_ambient)
        row1_ambient.addWidget(btn_geo)
        ambient_wrap = QWidget()
        ambient_wrap.setLayout(row1_ambient)

        grid.addWidget(QLabel('🌡️ 室溫'), 1, 0)
        grid.addWidget(ambient_wrap,       1, 1)
        grid.addWidget(QLabel('⚖️ 豆密度（留空估算）'), 1, 2)
        grid.addWidget(self._sp_density,   1, 3)

        grid.addWidget(QLabel('💧 含水率（留空估算）'), 2, 0)
        grid.addWidget(self._sp_moisture,  2, 1)

        for sb in (self._sp_machine, self._sp_batch, self._sp_ambient,
                   self._sp_density, self._sp_moisture):
            sb.valueChanged.connect(self._on_measure_changed)

        return grp

    def _build_profile_group(self) -> QGroupBox:
        grp = QGroupBox('🎯 演算法推算目標參數')
        h = QHBoxLayout(grp)
        h.setSpacing(12)
        h.setContentsMargins(8, 8, 8, 8)

        # Left: gauge chart
        self._profile_fig = Figure(figsize=(4, 4), tight_layout=True)
        self._profile_canvas = FigureCanvasQTAgg(self._profile_fig)
        self._profile_canvas.setMinimumHeight(200)
        self._profile_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        h.addWidget(self._profile_canvas, 4)

        # Right: text labels
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(10)
        self._lbl_profile = QLabel('請先填入豆重')
        self._lbl_profile.setWordWrap(True)
        self._lbl_profile.setStyleSheet('font-size: 16px; line-height: 1.8;')
        self._lbl_adj = QLabel('')
        self._lbl_adj.setWordWrap(True)
        self._lbl_adj.setStyleSheet('font-size: 14px; color: #444; line-height: 1.7;')
        rv.addWidget(self._lbl_profile)
        rv.addWidget(self._lbl_adj)
        rv.addStretch()
        h.addWidget(right, 6)

        return grp

    def _build_anchor_group(self) -> QGroupBox:
        grp = QGroupBox('錨點設定')
        grid = QGridLayout(grp)
        grid.setSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        self._anchor_widgets: dict[str, AnchorWidget] = {}
        # row 0: 3 anchors, row 1: 2 anchors (centred)
        specs = [
            ('charge',     '🚪 入豆 Charge',      0,    0.5,  100, 230, 0, 0),
            ('turnPoint',  '🔽 Turn Point 回溫點', 0.5,  4.0,   60, 120, 0, 1),
            ('dryEnd',     '💨 乾燥結束',           3.0,  10.0, 120, 170, 0, 2),
            ('firstCrack', '💥 一爆時點',           6.0,  18.0, 180, 210, 1, 0),
            ('drop',       '🎯 下豆時溫',           8.0,  25.0, 160, 260, 1, 1),
        ]
        for key, lbl, t_min, t_max, temp_min, temp_max, row, col in specs:
            w = AnchorWidget(lbl, t=0, temp=150, fire=50, damper=50,
                             t_min=t_min, t_max=t_max,
                             temp_min=temp_min, temp_max=temp_max)
            w.changed.connect(self._on_anchor_changed)
            self._anchor_widgets[key] = w
            grid.addWidget(w, row, col)

        return grp

    def _build_custom_events_group(self) -> QGroupBox:
        self._grp_custom = QGroupBox('🎛️ 自訂中間調整點')
        vbox = QVBoxLayout(self._grp_custom)
        vbox.setSpacing(3)

        hdr = QHBoxLayout()
        hdr.addStretch()
        btn_add = QPushButton('+ 新增調整點')
        btn_add.clicked.connect(self._add_custom_event)
        hdr.addWidget(btn_add)
        vbox.addLayout(hdr)

        self._custom_events_layout = QVBoxLayout()
        self._custom_events_layout.setSpacing(3)
        vbox.addLayout(self._custom_events_layout)

        self._lbl_no_custom = QLabel('尚無自訂點。')
        self._lbl_no_custom.setStyleSheet('color: #888; font-size: 10px;')
        self._custom_events_layout.addWidget(self._lbl_no_custom)

        return self._grp_custom

    def _build_chart_widget(self) -> QGroupBox:
        grp = QGroupBox('BT + RoR 曲線')
        vbox = QVBoxLayout(grp)
        vbox.setContentsMargins(4, 4, 4, 4)
        self._fig = Figure(figsize=(7, 3.4), tight_layout=True)
        self._ax_bt  = self._fig.add_subplot(111)
        self._ax_ror = self._ax_bt.twinx()
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._canvas.setMinimumHeight(240)
        vbox.addWidget(self._canvas)
        return grp

    def _build_stats_widget(self) -> QGroupBox:
        grp = QGroupBox('📊 統計視覺化')
        vbox = QVBoxLayout(grp)
        vbox.setContentsMargins(6, 6, 6, 6)
        self._stats_fig = Figure(figsize=(6, 5), tight_layout=True)
        self._stats_canvas = FigureCanvasQTAgg(self._stats_fig)
        self._stats_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._stats_canvas.setMinimumHeight(360)
        vbox.addWidget(self._stats_canvas)
        return grp

    def _build_algo_visual_widget(self) -> QGroupBox:
        grp = QGroupBox('🎯 演算法推算目標')
        vbox = QVBoxLayout(grp)
        vbox.setContentsMargins(6, 6, 6, 6)
        self._algo_fig = Figure(figsize=(4, 2.4), tight_layout=True)
        self._algo_canvas = FigureCanvasQTAgg(self._algo_fig)
        self._algo_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._algo_canvas.setMinimumHeight(160)
        self._algo_canvas.setMaximumHeight(220)
        vbox.addWidget(self._algo_canvas)
        return grp

    def _build_stats_text_widget(self) -> QGroupBox:
        grp = QGroupBox('📋 階段說明與調整軌跡')
        vbox = QVBoxLayout(grp)
        vbox.setContentsMargins(6, 6, 6, 6)
        inner_scroll = QScrollArea()
        inner_scroll.setWidgetResizable(True)
        inner_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._stats_text_lbl = QLabel('...')
        self._stats_text_lbl.setWordWrap(True)
        self._stats_text_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._stats_text_lbl.setStyleSheet('font-size: 12px; padding: 2px;')
        self._stats_text_lbl.setTextFormat(Qt.TextFormat.RichText)
        inner_scroll.setWidget(self._stats_text_lbl)
        vbox.addWidget(inner_scroll)
        return grp

    # ── 初始化錨點值 ──────────────────────────────────────────────────────────

    def _reset_anchors(self) -> None:
        if self._profile is None:
            return
        p = self._profile
        total_min = (p['totalMinLow'] + p['totalMinHigh']) / 2

        vals = {
            'charge':     {'t': 0,               'temp': p['chargeTemp'],                  'fire': 90, 'damper': 40},
            'turnPoint':  {'t': 1.5,             'temp': 90,                               'fire': 65, 'damper': 50},
            'dryEnd':     {'t': total_min * 0.45, 'temp': 150,                             'fire': 50, 'damper': 70},
            'firstCrack': {'t': total_min * 0.78, 'temp': 196,                             'fire': 35, 'damper': 85},
            'drop':       {'t': total_min,        'temp': (p['btLow'] + p['btHigh']) / 2,  'fire': 0,  'damper': 100},
        }
        for key, w in self._anchor_widgets.items():
            w.blockSignals(True)
            w._t_spin.setValue(round(vals[key]['t'], 1))
            w._temp_spin.setValue(round(vals[key]['temp'], 1))
            w._fire_spin.setValue(vals[key]['fire'])
            w._damper_spin.setValue(vals[key]['damper'])
            w.blockSignals(False)
        self._update_chart_and_stats()

    # ── 事件處理 ──────────────────────────────────────────────────────────────

    @pyqtSlot()
    def _on_config_changed(self) -> None:
        self._recompute()

    @pyqtSlot()
    def _on_measure_changed(self) -> None:
        # batch: highlight if empty
        if self._sp_batch.value() > 0:
            self._sp_batch.setStyleSheet('')
        else:
            self._sp_batch.setStyleSheet('background: #ffebee;')
        # persist machineKg
        kg = self._sp_machine.value()
        if kg > 0:
            QSettings().setValue(f'{SETTINGS_KEY}/machineKg', kg)
        self._recompute()

    @pyqtSlot()
    def _on_anchor_changed(self) -> None:
        self._update_chart_and_stats()

    def _add_custom_event(self) -> None:
        drop_t = self._anchor_widgets['drop'].values['t']
        default_t = max(2.0, drop_t * 0.5)
        cev = CustomEventWidget(t=default_t, fire=50, damper=60, t_max=drop_t)
        cev.removed.connect(self._remove_custom_event)
        cev.changed.connect(self._update_chart_and_stats)
        self._custom_event_widgets.append(cev)
        self._custom_events_layout.addWidget(cev)
        self._lbl_no_custom.setVisible(False)
        self._update_chart_and_stats()

    def _remove_custom_event(self, w: CustomEventWidget) -> None:
        if w in self._custom_event_widgets:
            self._custom_event_widgets.remove(w)
        self._custom_events_layout.removeWidget(w)
        w.deleteLater()
        self._lbl_no_custom.setVisible(len(self._custom_event_widgets) == 0)
        self._update_chart_and_stats()

    # ── 演算法重算 + 錨點初始化 ───────────────────────────────────────────────

    def _build_ctx(self) -> dict:
        def combo_val(cb: QComboBox) -> str:
            return cb.currentData() or ''

        def spin_str(sb: QDoubleSpinBox) -> str:
            v = sb.value()
            return str(v) if v > 0 else ''

        country_idx = self._cb_country.currentIndex()
        country_txt = self._cb_country.currentText() if country_idx > 0 else ''
        variety_txt = self._cb_variety.currentText()
        proc_txt    = self._cb_proc.currentText()
        roast_txt   = self._cb_roast.currentText()

        return {
            'country':      country_txt,
            'countryCode':  combo_val(self._cb_country),
            'variety':      variety_txt,
            'varietyCode':  combo_val(self._cb_variety),
            'proc':         proc_txt,
            'procCode':     combo_val(self._cb_proc),
            'roast':        combo_val(self._cb_roast),
            'roastLabel':   roast_txt,
            'alt':          combo_val(self._cb_altitude),
            'batchG':       spin_str(self._sp_batch),
            'machineKg':    spin_str(self._sp_machine),
            'ambientC':     spin_str(self._sp_ambient),
            'densityGL':    spin_str(self._sp_density),
            'moisturePct':  spin_str(self._sp_moisture),
        }

    def _recompute(self) -> None:
        ctx = self._build_ctx()
        density_used  = float(ctx['densityGL'])  if ctx['densityGL']  else rp_default_density(ctx)
        moisture_used = float(ctx['moisturePct']) if ctx['moisturePct'] else rp_default_moisture(ctx)
        full_cfg = dict(ctx)
        full_cfg['densityGL']   = str(density_used)
        full_cfg['moisturePct'] = str(moisture_used)
        self._profile = compute_roast_profile(full_cfg)
        self._update_profile_display(density_used, moisture_used)
        # only reset anchors if batch > 0 (profile meaningful)
        if float(ctx.get('batchG') or 0) > 0 or self._profile:
            self._reset_anchors()

    def _update_profile_display(self, density_used: float, moisture_used: float) -> None:
        p = self._profile
        if p is None:
            return
        has_batch = float(self._build_ctx().get('batchG') or 0) > 0
        if not has_batch:
            self._lbl_profile.setText('⚠️ 請先填入豆重，目標參數才有意義。')
            self._lbl_adj.setText('')
            return
        self._draw_target_gauges(self._profile_fig, self._profile_canvas)

        hints = []
        ctx = self._build_ctx()
        if not ctx['densityGL']:
            hints.append(f'密度估算 <b>{density_used} g/L</b>')
        if not ctx['moisturePct']:
            hints.append(f'含水率估算 <b>{moisture_used}%</b>')

        hint_str = ''
        if hints:
            hint_str = f'<span style="color:#0288d1;">💡 未填欄位自動估算：{"、".join(hints)}</span><br>'

        warn_str = ''
        if p['base'].get('warn'):
            warn_str = f'<br><span style="color:#c62828;">⚠️ {p["base"]["warn"]}</span>'

        drying_str = ''
        if p['dryingAdjSec']:
            sign = '延長' if p['dryingAdjSec'] > 0 else '縮短'
            drying_str = f'｜Drying 相對基準 {sign} {abs(p["dryingAdjSec"])}s'

        self._lbl_profile.setText(
            f'{hint_str}'
            f'🔥 入豆溫 <b>{p["chargeTemp"]}°C</b>　'
            f'🎯 BT 終溫 <b>{p["btLow"]}-{p["btHigh"]}°C</b>　'
            f'⏱ Dev <b>{p["devLow"]}-{p["devHigh"]}%</b>　'
            f'⏳ 總時 <b>{p["totalMinLow"]}-{p["totalMinHigh"]} 分</b>'
            f'{drying_str}{warn_str}'
        )
        self._lbl_profile.setTextFormat(Qt.TextFormat.RichText)

        adjs = p.get('adjustments', [])
        if adjs:
            lines = ['<b>推理軌跡：</b>']
            for a in adjs:
                sign = '+' if a['delta'] > 0 else ''
                lines.append(f'[{a["stage"]}] {sign}{a["delta"]} → {a["reason"]}')
            self._lbl_adj.setText('<br>'.join(lines))
            self._lbl_adj.setTextFormat(Qt.TextFormat.RichText)
        else:
            self._lbl_adj.setText('本次配置無特殊調整，使用焙度基線參數。')

    # ── 圖表 + 統計 ───────────────────────────────────────────────────────────

    def _get_anchor_points(self) -> list[tuple[float, float]]:
        keys = ['charge', 'turnPoint', 'dryEnd', 'firstCrack', 'drop']
        pts = [(self._anchor_widgets[k].values['t'],
                self._anchor_widgets[k].values['temp']) for k in keys]
        return sorted(pts, key=lambda x: x[0])

    def _get_all_fire_events(self) -> list[dict]:
        evts = []
        for k in ('charge', 'turnPoint', 'dryEnd', 'firstCrack', 'drop'):
            v = self._anchor_widgets[k].values
            evts.append({'t': v['t'], 'fire': v['fire'], 'damper': v['damper']})
        for ce in self._custom_event_widgets:
            evts.append(ce.values)
        return sorted(evts, key=lambda x: x['t'])

    @staticmethod
    def _interp_bt(bt_pts: list[tuple], t: float) -> float:
        if t <= bt_pts[0][0]:  return bt_pts[0][1]
        if t >= bt_pts[-1][0]: return bt_pts[-1][1]
        for i in range(len(bt_pts) - 1):
            if bt_pts[i][0] <= t <= bt_pts[i+1][0]:
                r = (t - bt_pts[i][0]) / (bt_pts[i+1][0] - bt_pts[i][0] or 1e-9)
                return bt_pts[i][1] + r * (bt_pts[i+1][1] - bt_pts[i][1])
        return bt_pts[-1][1]

    def _update_chart_and_stats(self) -> None:
        bt_pts = self._get_anchor_points()

        # RoR at midpoints
        ror_pts: list[tuple] = []
        for i in range(1, len(bt_pts)):
            dt = bt_pts[i][0] - bt_pts[i-1][0]
            dT = bt_pts[i][1] - bt_pts[i-1][1]
            if dt <= 0:
                continue
            ror = round((dT / dt) * 10) / 10
            mid_t = (bt_pts[i][0] + bt_pts[i-1][0]) / 2
            ror_pts.append((mid_t, ror))

        # Custom event markers
        custom_markers = []
        for ce in self._custom_event_widgets:
            cv = ce.values
            bt_here = self._interp_bt(bt_pts, cv['t'])
            custom_markers.append((cv['t'], bt_here, cv['fire'], cv['damper']))

        # Draw
        self._ax_bt.cla()
        self._ax_ror.cla()

        bt_x  = [p[0] for p in bt_pts]
        bt_y  = [p[1] for p in bt_pts]
        ror_x = [p[0] for p in ror_pts]
        ror_y = [p[1] for p in ror_pts]

        self._ax_bt.plot(bt_x, bt_y, 'o-', color='#d32f2f', linewidth=2.5, markersize=7, label='BT 曲線')
        self._ax_bt.axhline(196, color='#f57c00', linestyle='--', linewidth=1, alpha=0.7, label='一爆 ~196°C')
        if ror_x:
            self._ax_ror.plot(ror_x, ror_y, 'd--', color='#0288d1', linewidth=1.8, markersize=5, label='RoR (°C/min)')

        for (t, bt, fire, damper) in custom_markers:
            self._ax_bt.scatter([t], [bt], marker='D', color='#ff9800', s=70, zorder=5)
            self._ax_bt.annotate(f'🔥{fire}% 💨{damper}%', (t, bt),
                                 fontsize=7, ha='center', va='bottom', color='#e65100')

        self._ax_bt.set_xlabel('時間 (分)', fontsize=9)
        self._ax_bt.set_ylabel('°C', fontsize=9, color='#d32f2f')
        self._ax_ror.set_ylabel('RoR (°C/min)', fontsize=9, color='#0288d1')
        self._ax_bt.set_xlim(0, max(bt_pts[-1][0] + 1, 1))
        self._ax_bt.set_ylim(80, 265)
        self._ax_ror.set_ylim(-5, 40)
        self._ax_bt.tick_params(labelsize=8)
        self._ax_ror.tick_params(labelsize=8)

        lines1, lbl1 = self._ax_bt.get_legend_handles_labels()
        lines2, lbl2 = self._ax_ror.get_legend_handles_labels()
        self._ax_bt.legend(lines1 + lines2, lbl1 + lbl2, fontsize=8, loc='upper left')
        self._ax_bt.grid(True, alpha=0.3)

        self._canvas.draw()

        # Stats
        self._update_stats(bt_pts, ror_pts)

    def _draw_target_gauges(self, fig: Figure, canvas: FigureCanvasQTAgg) -> None:
        p = self._profile
        if p is None:
            return
        fig.clear()
        fig.patch.set_facecolor('#fafafa')
        configs = [
            ('入豆溫',  p['chargeTemp'],  p['chargeTemp'],  160, 220, '°C', '#ff7043'),
            ('BT 終溫', p['btLow'],       p['btHigh'],      190, 252, '°C', '#ef5350'),
            ('Dev%',    p['devLow'],      p['devHigh'],       5,  30, '%',  '#66bb6a'),
            ('總時',    p['totalMinLow'], p['totalMinHigh'],  6,  20, '分', '#42a5f5'),
        ]
        gs = fig.add_gridspec(4, 1, hspace=1.1, top=0.93, bottom=0.04, left=0.20, right=0.82)
        for i, (label, lo, hi, slo, shi, unit, color) in enumerate(configs):
            ax = fig.add_subplot(gs[i])
            ax.set_facecolor('#fafafa')
            span = shi - slo
            ax.barh(0, span, left=slo, height=0.5, color='#e0e0e0', zorder=1)
            w = max(hi - lo, span * 0.04)
            ax.barh(0, w, left=lo, height=0.5, color=color, alpha=0.85, zorder=2)
            val_str = f'{lo}{unit}' if lo == hi else f'{lo}–{hi}{unit}'
            ax.text(shi + span * 0.03, 0, val_str, va='center', fontsize=9.5,
                    fontweight='bold', color=color)
            ax.set_xlim(slo - span * 0.02, shi + span * 0.22)
            ax.set_ylim(-0.45, 0.45)
            ax.set_yticks([])
            ax.set_xticks([])
            ax.set_ylabel(label, fontsize=9, rotation=0, ha='right', va='center', labelpad=4)
            for spine in ax.spines.values():
                spine.set_visible(False)
        canvas.draw()

    def _update_algo_visual(self) -> None:
        self._draw_target_gauges(self._algo_fig, self._algo_canvas)

    def _update_stats_text(self, drying_pct: float, maillard_pct: float, dev_pct: float,
                            ror_dry: float, ror_mai: float, ror_dev: float) -> None:
        p = self._profile
        if p is None:
            return

        def pct_status(val: float, lo: float, hi: float) -> str:
            if val < lo:
                return f'<span style="color:#e65100">偏短 {val:.1f}%（建議 {lo}–{hi}%）</span>'
            if val > hi:
                return f'<span style="color:#c62828">偏長 {val:.1f}%（建議 {lo}–{hi}%）</span>'
            return f'<span style="color:#2e7d32">✓ {val:.1f}%</span>'

        def ror_status(val: float, lo: float, hi: float) -> str:
            if val < lo:
                return f'<span style="color:#e65100">偏慢 {val} °C/min（理想 {lo}–{hi}）</span>'
            if val > hi:
                return f'<span style="color:#c62828">偏快 {val} °C/min（理想 {lo}–{hi}）</span>'
            return f'<span style="color:#2e7d32">✓ {val} °C/min</span>'

        dev_ok    = p['devLow'] <= dev_pct <= p['devHigh']
        dev_color = '#2e7d32' if dev_ok else '#c62828'
        dev_icon  = '✓' if dev_ok else '⚠'

        flick_ok  = ror_dry >= ror_mai >= ror_dev
        flick_msg = '✓ RoR 依序遞減，曲線健康' if flick_ok else '⚠ RoR 未全程遞減，注意 flick/baked'
        flick_col = '#2e7d32' if flick_ok else '#c62828'

        adjs = p.get('adjustments', [])
        axis_map = {'charge': '入豆溫', 'btLow': 'BT低', 'btHigh': 'BT高',
                    'devLow': 'Dev低', 'devHigh': 'Dev高', 'drying': 'Drying'}

        rows = [
            '<b style="color:#5d4037">🔥 烘焙階段分析</b>',
            f'Drying：{pct_status(drying_pct, 38, 45)}（理想 38–45%）',
            f'Maillard：{pct_status(maillard_pct, 32, 42)}（理想 32–42%）',
            f'Development：<span style="color:{dev_color}">{dev_icon} {dev_pct:.1f}%（目標 {p["devLow"]}–{p["devHigh"]}%）</span>',
            '',
            '<b style="color:#5d4037">📈 各段平均 RoR</b>',
            f'乾燥段：{ror_status(ror_dry, 15, 25)}',
            f'梅納段：{ror_status(ror_mai, 10, 15)}',
            f'發展段：{ror_status(ror_dev,  8, 10)}',
            f'<span style="color:{flick_col}"><b>{flick_msg}</b></span>',
        ]

        if adjs:
            rows += ['', '<b style="color:#5d4037">🔧 演算法調整軌跡</b>']
            for a in adjs:
                sign = '+' if a['delta'] > 0 else ''
                al   = axis_map.get(a['axis'], a['axis'])
                rows.append(f'<span style="color:#555">[{a["stage"]}] {al} {sign}{a["delta"]}</span> {a["reason"]}')
        else:
            rows += ['', '<span style="color:#888">本次無特殊調整，使用焙度基線。</span>']

        base = p['base']
        if base.get('warn'):
            rows += ['', f'<span style="color:#c62828">⚠️ {base["warn"]}</span>']

        self._stats_text_lbl.setText('<br>'.join(rows))

    def _update_stats(self, bt_pts: list, ror_pts: list) -> None:
        aw = self._anchor_widgets
        anchors = {k: aw[k].values for k in aw}
        total_min    = anchors['drop']['t']
        drying_min   = anchors['dryEnd']['t']
        maillard_min = anchors['firstCrack']['t'] - anchors['dryEnd']['t']
        dev_min      = anchors['drop']['t'] - anchors['firstCrack']['t']

        def safe_pct(part: float, whole: float) -> float:
            return round(part / whole * 100, 1) if whole > 0 else 0.0

        drying_pct   = safe_pct(drying_min,   total_min)
        maillard_pct = safe_pct(maillard_min, total_min)
        dev_pct      = safe_pct(dev_min,      total_min)

        def ror_phase(t0: float, t1: float, temp0: float, temp1: float) -> float:
            dt = t1 - t0
            return round((temp1 - temp0) / dt * 10) / 10 if dt > 0 else 0.0

        ror_dry = ror_phase(anchors['turnPoint']['t'], anchors['dryEnd']['t'],
                            anchors['turnPoint']['temp'], anchors['dryEnd']['temp'])
        ror_mai = ror_phase(anchors['dryEnd']['t'], anchors['firstCrack']['t'],
                            anchors['dryEnd']['temp'], anchors['firstCrack']['temp'])
        ror_dev = ror_phase(anchors['firstCrack']['t'], anchors['drop']['t'],
                            anchors['firstCrack']['temp'], anchors['drop']['temp'])

        p = self._profile
        if p is None:
            return

        # fire/damper operation sequence
        phase_names = {'charge': '入豆', 'turnPoint': '回溫', 'dryEnd': '乾燥末',
                       'firstCrack': '一爆', 'drop': '下豆'}
        all_evts = sorted(
            [{'label': phase_names[k], 'kind': 'anchor', **aw[k].values} for k in aw] +
            [{'label': '自訂', 'kind': 'custom', **ce.values} for ce in self._custom_event_widgets],
            key=lambda x: x['t']
        )

        # ── 畫圖 ──────────────────────────────────────────────────────────────
        self._stats_fig.clear()
        # 4 subplots: phase bar, RoR comparison, fire timeline, damper timeline
        gs = self._stats_fig.add_gridspec(4, 1, hspace=0.55,
                                          top=0.95, bottom=0.06,
                                          left=0.10, right=0.97)
        ax_phase  = self._stats_fig.add_subplot(gs[0])
        ax_ror    = self._stats_fig.add_subplot(gs[1])
        ax_fire   = self._stats_fig.add_subplot(gs[2])
        ax_damper = self._stats_fig.add_subplot(gs[3])

        # ── ① 階段時間占比（橫向堆疊條形）─────────────────────────────────────
        ax_phase.set_title('烘焙階段占比  (Drying / Maillard / Development)', fontsize=11, pad=6)
        phases_data = [
            ('Drying',      drying_pct,   '#ffa726'),
            ('Maillard',    maillard_pct, '#66bb6a'),
            ('Development', dev_pct,      '#ef5350'),
        ]
        left = 0.0
        for label, pct, color in phases_data:
            ax_phase.barh(0, pct, left=left, height=0.55, color=color, alpha=0.85)
            if pct > 3:
                ax_phase.text(left + pct / 2, 0, f'{label}\n{pct:.1f}%',
                              ha='center', va='center', fontsize=9.5,
                              fontweight='bold', color='white')
            left += pct
        # ideal markers: Drying ~38-45%, Maillard ~32-42%, Dev 18-22%
        ideal_ranges = [(0, 38, 45, '#ffa726'), (38, 70, 87, '#66bb6a'), (78, 96, 100, '#ef5350')]
        for _, lo, hi, color in ideal_ranges:
            ax_phase.axvspan(lo, hi, alpha=0.12, color=color)
        ax_phase.set_xlim(0, 100)
        ax_phase.set_yticks([])
        ax_phase.set_xlabel('占比 (%)', fontsize=9)
        ax_phase.set_ylim(-0.5, 0.5)
        # dev target band
        if p['devLow'] <= dev_pct <= p['devHigh']:
            ax_phase.set_title(ax_phase.get_title() + f'  ✓ Dev 達標', fontsize=11)
        else:
            ax_phase.set_title(ax_phase.get_title() + f'  ⚠ Dev 目標 {p["devLow"]}-{p["devHigh"]}%', fontsize=11, color='#c62828')

        # ── ② RoR 各段對照（柱狀 + 理想區間底色）────────────────────────────
        ax_ror.set_title('各段平均 RoR (°C/min)  vs  Scott Rao 理想範圍', fontsize=11, pad=6)
        phase_labels = ['Drying', 'Maillard', 'Development']
        actual_ror   = [ror_dry, ror_mai, ror_dev]
        ideal_lo     = [15, 10, 8]
        ideal_hi     = [25, 15, 10]
        colors_ror   = ['#ffa726', '#66bb6a', '#ef5350']
        x = [0, 1, 2]
        for i, (xi, val, lo, hi, col) in enumerate(zip(x, actual_ror, ideal_lo, ideal_hi, colors_ror)):
            # ideal band
            ax_ror.bar(xi, hi - lo, bottom=lo, width=0.5, color=col, alpha=0.18, zorder=1)
            # actual bar
            ok = lo <= val <= hi
            bar_col = col if ok else '#c62828'
            ax_ror.bar(xi, val, width=0.35, color=bar_col, alpha=0.9, zorder=2)
            ax_ror.text(xi, val + 0.4, f'{val}', ha='center', va='bottom', fontsize=10, fontweight='bold')
            # ideal range text
            ax_ror.text(xi, lo - 1.2, f'理想\n{lo}-{hi}', ha='center', va='top',
                        fontsize=8, color='#888')
        ax_ror.set_xticks(x)
        ax_ror.set_xticklabels(phase_labels, fontsize=10)
        ax_ror.set_ylabel('°C/min', fontsize=9)
        ax_ror.set_ylim(0, max(max(actual_ror) + 5, 30))
        ax_ror.grid(axis='y', alpha=0.3)
        # flick check
        flick_ok = all(actual_ror[i] >= actual_ror[i+1] for i in range(len(actual_ror)-1))
        flick_txt = '✓ RoR 依序遞減 (always-declining)' if flick_ok else '⚠ RoR 未遞減 — flick/baked 警訊'
        ax_ror.set_title(ax_ror.get_title(), fontsize=11, pad=6)
        ax_ror.text(0.99, 0.97, flick_txt, transform=ax_ror.transAxes,
                    ha='right', va='top', fontsize=9,
                    color='#2e7d32' if flick_ok else '#c62828',
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8))

        # ── ③ 火力時間軸（階梯線）────────────────────────────────────────────
        ax_fire.set_title('火力操作時間軸', fontsize=11, pad=6)
        evt_t    = [e['t']      for e in all_evts]
        evt_fire = [e['fire']   for e in all_evts]
        evt_damp = [e['damper'] for e in all_evts]
        evt_lbl  = [e['label']  for e in all_evts]
        ax_fire.step(evt_t, evt_fire, where='post', color='#d32f2f', linewidth=2.2)
        ax_fire.fill_between(evt_t, evt_fire, step='post', alpha=0.15, color='#d32f2f')
        for t, v, lbl in zip(evt_t, evt_fire, evt_lbl):
            ax_fire.annotate(f'{lbl}\n{v:.0f}%', (t, v), textcoords='offset points',
                             xytext=(0, 6), ha='center', fontsize=8, color='#b71c1c')
        ax_fire.set_ylim(0, 115)
        ax_fire.set_ylabel('火力 (%)', fontsize=9, color='#d32f2f')
        ax_fire.set_xlim(0, total_min + 0.5)
        ax_fire.set_xlabel('時間 (分)', fontsize=9)
        ax_fire.grid(axis='both', alpha=0.25)

        # ── ④ 風門時間軸（階梯線）────────────────────────────────────────────
        ax_damper.set_title('風門操作時間軸', fontsize=11, pad=6)
        ax_damper.step(evt_t, evt_damp, where='post', color='#0288d1', linewidth=2.2)
        ax_damper.fill_between(evt_t, evt_damp, step='post', alpha=0.15, color='#0288d1')
        for t, v, lbl in zip(evt_t, evt_damp, evt_lbl):
            ax_damper.annotate(f'{lbl}\n{v:.0f}%', (t, v), textcoords='offset points',
                               xytext=(0, 6), ha='center', fontsize=8, color='#01579b')
        ax_damper.set_ylim(0, 115)
        ax_damper.set_ylabel('風門 (%)', fontsize=9, color='#0288d1')
        ax_damper.set_xlim(0, total_min + 0.5)
        ax_damper.set_xlabel('時間 (分)', fontsize=9)
        ax_damper.grid(axis='both', alpha=0.25)

        self._stats_canvas.draw()

        # Right panel updates
        self._update_algo_visual()
        self._update_stats_text(drying_pct, maillard_pct, dev_pct, ror_dry, ror_mai, ror_dev)

    # ── 室溫自動取得 ──────────────────────────────────────────────────────────

    def _fetch_ambient(self) -> None:
        self._btn_geo.setEnabled(False)
        self._btn_geo.setText('取得中…')
        thread = AmbientFetchThread(self)
        thread.result_ready.connect(self._on_ambient_result)
        thread.error.connect(self._on_ambient_error)
        thread.finished.connect(thread.deleteLater)
        self._ambient_thread = thread
        thread.start()

    @pyqtSlot(float)
    def _on_ambient_result(self, t: float) -> None:
        self._sp_ambient.setValue(t)
        self._btn_geo.setText(f'✓ {t}°C')
        self._btn_geo.setEnabled(True)

    @pyqtSlot(str)
    def _on_ambient_error(self, _msg: str) -> None:
        self._btn_geo.setText('取得失敗')
        self._btn_geo.setEnabled(True)

    # ── 持久化機器容量 ────────────────────────────────────────────────────────

    def _load_machine_kg(self) -> None:
        val = QSettings().value(f'{SETTINGS_KEY}/machineKg', 0.0)
        try:
            v = float(val)
            if v > 0:
                self._sp_machine.setValue(v)
        except (TypeError, ValueError):
            pass

    # ── CSV 匯出 ──────────────────────────────────────────────────────────────

    def _build_table_data(self) -> tuple[list, list, list, list]:
        """Returns (times, bt_row, fire_row, damper_row)"""
        bt_pts    = self._get_anchor_points()
        fire_evts = self._get_all_fire_events()
        drop_t    = bt_pts[-1][0]
        step      = 0.5
        max_t     = max(16.0, drop_t)
        times: list[float] = []
        t = 0.0
        while t <= max_t + 0.001:
            times.append(round(t, 2))
            t += step

        def fmt_t(m: float) -> str:
            mins = int(m)
            secs = round((m - mins) * 60)
            return f'{mins}:{secs:02d}'

        def bt_val(t: float) -> str:
            if t > drop_t + 0.01: return ''
            return str(round(self._interp_bt(bt_pts, t)))

        def fire_damper(t: float) -> tuple:
            if t > drop_t + 0.01: return ('', '')
            f = d = None
            for ev in fire_evts:
                if ev['t'] <= t + 0.01:
                    f = ev['fire']
                    d = ev['damper']
            return (str(int(f)) if f is not None else '', str(int(d)) if d is not None else '')

        time_labels = [fmt_t(t) for t in times]
        bt_row      = [bt_val(t)         for t in times]
        fire_row    = [fire_damper(t)[0] for t in times]
        damper_row  = [fire_damper(t)[1] for t in times]

        return time_labels, bt_row, fire_row, damper_row

    def _export_csv(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        filename, _ = QFileDialog.getSaveFileName(self, '匯出 CSV', '烘豆計畫.csv', '*.csv')
        if not filename:
            return

        ctx = self._build_ctx()
        p   = self._profile or {}
        time_labels, bt_row, fire_row, damper_row = self._build_table_data()

        rows: list[list] = [
            [f'烘豆計畫表 - {ctx.get("country","")} · {ctx.get("variety","")} · {ctx.get("roastLabel","")}'],
            [f'入豆溫 {p.get("chargeTemp","")}°C · BT {p.get("btLow","")}-{p.get("btHigh","")}°C · '
             f'Dev {p.get("devLow","")}-{p.get("devHigh","")}% · 總時 {p.get("totalMinLow","")}-{p.get("totalMinHigh","")} 分'],
            [],
            ['時間 (分:秒)', *time_labels],
            ['預計 BT (°C)', *bt_row],
            ['預計 火力 (%)', *fire_row],
            ['預計 風門 (%)', *damper_row],
            [],
            ['── 以下留白，烘豆當下手寫記錄 ──'],
            ['實際 BT (°C)', *([''] * len(time_labels))],
            ['實際 火力 (%)', *([''] * len(time_labels))],
            ['實際 風門 (%)', *([''] * len(time_labels))],
            ['實際 RoR (°C/min)', *([''] * len(time_labels))],
            ['備註 / 現象', *([''] * len(time_labels))],
        ]

        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerows(rows)

    # ── XLSX 匯出 ─────────────────────────────────────────────────────────────

    def _export_xlsx(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        except ImportError:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, '缺少套件', 'openpyxl 未安裝，請先安裝。')
            return

        filename, _ = QFileDialog.getSaveFileName(self, '匯出 XLSX', '烘豆計畫.xlsx', '*.xlsx')
        if not filename:
            return

        ctx = self._build_ctx()
        p   = self._profile or {}
        time_labels, bt_row, fire_row, damper_row = self._build_table_data()
        total_cols = 1 + len(time_labels)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = '烘豆計畫'

        thin  = Side(style='thin', color='999999')
        bdr   = Border(top=thin, left=thin, bottom=thin, right=thin)

        def hdr_cell(ws_row: Any, text: str, bg: str = 'FF5D4037', fg: str = 'FFFFFFFF', bold: bool = True) -> None:
            cell = ws_row
            cell.value     = text
            cell.font      = Font(bold=bold, size=12, color=fg)
            cell.fill      = PatternFill('solid', fgColor=bg)
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=False)

        def style_row(row_num: int, bg: str, bold: bool = False) -> None:
            for c in range(1, total_cols + 1):
                cell = ws.cell(row=row_num, column=c)
                cell.font      = Font(bold=bold, size=10, color='FF3E2723')
                cell.fill      = PatternFill('solid', fgColor=bg)
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.border    = bdr
            ws.cell(row=row_num, column=1).alignment = Alignment(horizontal='left', vertical='center')

        # Title header
        ws.append([f'烘豆計畫表 — {ctx.get("country","")} · {ctx.get("variety","")} · {ctx.get("roastLabel","")}'])
        ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=total_cols)
        hdr_cell(ws.cell(ws.max_row, 1), ws.cell(ws.max_row, 1).value or '')
        ws.row_dimensions[ws.max_row].height = 22

        ws.append([f'入豆溫 {p.get("chargeTemp","")}°C · BT {p.get("btLow","")}-{p.get("btHigh","")}°C · '
                   f'Dev {p.get("devLow","")}-{p.get("devHigh","")}% · '
                   f'總時 {p.get("totalMinLow","")}-{p.get("totalMinHigh","")} 分'])
        ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=total_cols)
        ws.cell(ws.max_row, 1).font      = Font(size=10, color='FF3E2723')
        ws.cell(ws.max_row, 1).alignment = Alignment(horizontal='center')
        ws.row_dimensions[ws.max_row].height = 16

        ws.append([])

        # Column header
        ws.append(['時間 (分:秒)', *time_labels])
        ws.row_dimensions[ws.max_row].height = 18
        style_row(ws.max_row, 'FF8D6E63', bold=True)
        for c in range(1, total_cols + 1):
            ws.cell(ws.max_row, c).font = Font(bold=True, size=10, color='FFFFFFFF')

        # Plan rows
        ws.append(['預計 BT (°C)', *bt_row])
        style_row(ws.max_row, 'FFFFF3E0')

        ws.append(['預計 火力 (%)', *fire_row])
        style_row(ws.max_row, 'FFFFEBEE')

        ws.append(['預計 風門 (%)', *damper_row])
        style_row(ws.max_row, 'FFE3F2FD')

        ws.append([])
        ws.append(['── 以下留白，烘豆當下手寫記錄實際值 ──'])
        ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=total_cols)
        ws.cell(ws.max_row, 1).font      = Font(italic=True, size=10, color='FF888888')
        ws.cell(ws.max_row, 1).alignment = Alignment(horizontal='center')

        for label in ('實際 BT (°C)', '實際 火力 (%)', '實際 風門 (%)', '實際 RoR (°C/min)', '備註 / 現象'):
            ws.append([label, *([''] * len(time_labels))])
            style_row(ws.max_row, 'FFFFFFFF')

        # Column widths
        ws.column_dimensions['A'].width = 20
        for col_idx in range(2, total_cols + 1):
            col_letter = openpyxl.utils.get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = 7

        # Embed chart image
        ws.append([])
        ws.append(['📈 預計烘焙曲線（BT + RoR）'])
        ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=total_cols)
        hdr_cell(ws.cell(ws.max_row, 1), ws.cell(ws.max_row, 1).value or '')
        ws.row_dimensions[ws.max_row].height = 22
        chart_anchor_row = ws.max_row

        try:
            buf = io.BytesIO()
            self._fig.savefig(buf, format='png', dpi=120, bbox_inches='tight')
            buf.seek(0)
            img = openpyxl.drawing.image.Image(buf)
            img.width  = min(800, total_cols * 56)
            img.height = 380
            ws.add_image(img, f'A{chart_anchor_row + 1}')
            for _ in range(22):
                ws.append([])
        except Exception:
            pass

        wb.save(filename)

        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(self, '匯出完成', f'已儲存：{filename}')
