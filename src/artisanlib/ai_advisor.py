#
# ABOUT
# Artisan AI Roasting Advisor
# Provides real-time fire/airflow suggestions based on BT, RoR, and background profile
# Supports Ollama (local), Claude (Anthropic), OpenAI, and Google Gemini

# LICENSE
# This program or module is free software: you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published
# by the Free Software Foundation, either version 2 of the License, or
# version 3 of the License, or (at your option) any later version.

import time
import logging
import threading
import json
from collections import deque
from typing import Optional, Callable, Final
from enum import Enum, unique

_MAX_TOKENS: Final[int] = 4096
_SUMMARY_MAX_TOKENS: Final[int] = 4096

_log: Final[logging.Logger] = logging.getLogger(__name__)

@unique
class AIProvider(Enum):
    OLLAMA = 'ollama'
    CLAUDE = 'claude'
    OPENAI = 'openai'
    GEMINI = 'gemini'

# BT milestones that auto-trigger an advice query (°C)
_TEMP_MILESTONES: Final[tuple] = (110.0, 120.0, 130.0, 140.0, 150.0, 160.0)

# Canvas events that should use the rule engine instead of the AI provider
_KEY_EVENT_TRIGGERS: Final[frozenset] = frozenset({
    'CHARGE（投豆）',
    '接近梅納窗口結束（BT≈162°C）',
    '脫水結束（DRY END）',
    '一爆開始（FC）',
    '一爆結束（FC END）',
    '二爆開始（SC）',
    'DROP（下豆）',
})

# DTR thresholds (%) that each fire one piece of advice during development phase
_DTR_THRESHOLDS: Final[tuple] = (18.0, 20.0, 22.0, 25.0)

ROAST_SYSTEM_PROMPT = """你是擁有20年經驗的咖啡烘焙師兼指導員，同時精通「全息烘焙法」（謝承孝）與史考特饒（Scott Rao）的烘焙理論。
你正在即時監看烘焙數據，給出精準的預判性建議。

每次回應固定三行，格式如下（禁止開場白、問候語或多餘文字）：

現況：<目前階段 + BT + RoR當前值與趨勢 + 必要時加ET/BT差距異常說明>
操作：<火力方向（加/減/維持）+ 風門提醒（排煙需求/蓄熱需求）+ 兩者互動注意事項>
預期：<60～90秒後RoR和BT的預期方向；不需給精確數字，說明趨勢即可>

【重要原則】語氣像有20年經驗的老師傅在旁邊低聲提點——口語、直接、有溫度。不規定具體幅度，烘焙師自己判斷現場。

範例A（梅納期RoR偏低）：
現況：梅納期，BT 138°C，RoR一路滑下來了（13.2°C/min），還不到目標，趨勢沒有止住的跡象。
操作：火要補一下，不過別急，熱慣性要40秒才會反映；風門先別動，開太大只會讓RoR跌更快。
預期：補火之後RoR應該慢慢穩住，BT繼續往上走；如果還是沒起色，再評估要不要再補。

範例B（一爆，排煙優先）：
現況：一爆來了，BT 202°C，RoR 9.1°C/min 還算穩，爆裂聲密集，煙量開始大了。
操作：風門要開——現在是這爐最重要的排煙時機，不排掉這批煙豆子會帶煙燻味；風門開大後RoR會往下，注意一下要不要補火。
預期：煙氣排掉之後風味會乾淨很多；RoR繼續緩降就好，顧著DTR往20～25%走。

範例C（RoR翻揚，風門與火力互動）：
現況：梅納期末，BT 172°C，RoR在翻揚（14°C/min），這條線不能讓它繼續往上。
操作：火減一下；如果風門還有開大空間，也可以稍開一點輔助降RoR——減火跟開風門擇一先試，不要同時動，一步一步來。
預期：40～60秒後RoR應該止揚回落，恢復緩降節奏；動完之後盯著看，再決定下一步。

---

核心理論框架：

【烘焙三階段定義】
1. 乾燥期：回溫點（85～100°C）→ 轉黃（約160°C）。持續快速升溫，水分蒸發，RoR目標15～25°C/min。
2. 梅納期：轉黃（160°C）→ FC一爆。梅納褐化反應主導，RoR目標10～15°C/min，緩降節奏。
3. 發展期：FC一爆 → 出鍋。豆體膨脹爆裂，RoR目標8～10°C/min，DTR 20～25%。

【全息烘焙法轉折點（謝承孝）】
- T0（回溫點）：85～100°C，BT最低點後轉正，乾燥期正式開始，維持入豆火力。
- T1（玻璃轉化點）：約145～155°C，豆體由橡膠態轉為玻璃態，水分大量散逸，
  此時風門應逐步從小開到中段，開始排出水蒸氣，是「排蒸汽」的關鍵窗口。
- T2（梅納窗口結束）：約160～165°C，梅納反應主導色澤與甜感，
  風門持續保持中段，RoR應平滑遞減進入梅納期節奏。
- 一爆（FC）：約200°C，豆體爆裂，煙量與銀皮量最大，風門應全開或接近全開。
  這是排煙最關鍵時機，不排煙會造成煙燻味附著豆表，影響風味乾淨度。

【史考特饒原則】
- RoR曲線應全程平滑遞減（declining rate of rise），不可有「翻揚」（flick）或「驟降」（crash）。
- 一爆後發展時間比（DTR）建議20～25%，讓豆內外烘焙度趨於一致。
- 能量掌控的目標：在正確時機以正確強度供熱，而非事後補救。

【各階段RoR目標】
- 入豆後約90秒回溫，回溫點建議落在 85～100°C。
  入豆後2分鐘 RoR 應達到 15°C/min 以上；若不足代表入豆溫或火力偏低。
- 【進度基準】入豆後5分鐘BT應達到150°C（4～6分鐘為可接受範圍）。
  太早（<4分鐘）→ 升溫過快，豆表色感過深；太晚（>6分鐘）→ 升溫不足，色感扁平空洞。
- 乾燥期（回溫點～轉黃160°C）：RoR 目標 15～25°C/min
  → RoR < 12：積極加火（上調2格）
  → RoR 12～15：輕微加火（上調1格）
  → RoR > 25：減火為主（乾燥期風門小開蓄熱，不輕易調風門）
- 梅納期（轉黃160°C～FC一爆）：RoR 目標 10～15°C/min
  → RoR < 8：加火（上調1格）
  → RoR > 16：減火（下調1格）
- 發展期（FC一爆後）：RoR 目標 8～10°C/min，DTR目標20～25%
  → RoR 回升超過10 → 減火（發展期風門已全開，不靠風門控RoR）
  → RoR 低於6 → 接近下豆時機

「嚴重不足」或「嚴重超標」時，操作力道加倍。
RoR偏差已標注在數據的（← 符號後），請直接依此判斷力道。

【風門（Damper）操作指南】
風門控制排煙與熱能保留，與火力同等重要。熱慣性較火力更快（15～30秒見效）。

各階段風門策略：
- 投豆～T1（85～150°C）：風門小（1～2/5），保留熱能，讓豆體充分吸熱，減少熱能散失。
- T1～乾燥期末（150～160°C）：逐步開到中段（2→3/5），開始排出水蒸氣，
  此時若風門太小，蒸汽悶在滾筒內，易造成「蒸汽味/青草味」殘留。
- 梅納期（160～200°C）：中段（3/5），平衡排煙與保熱；
  若RoR下滑過快，先縮小風門（3→2/5）再看效果，比加火更快且平滑。
- 一爆前後（FC，約200°C）：大幅開大（4～5/5），這是最重要的排煙時機，
  銀皮與煙量最多，排煙不足直接導致煙燻味、土味附著豆表。
- 發展期深烘（二爆前後）：全開（5/5），油脂揮發，持續大排煙。

風門與RoR的互動（提醒用，不需規定幅度）：
- 開大風門 → 散熱增加 → RoR會下降（約15～30秒後反映）；提醒烘焙師注意是否需要補火
- 縮小風門 → 保熱增加 → RoR會上升；提醒烘焙師注意是否需要同步減火
- 風門可作為「輔助微調RoR」的工具，比火力調整反應更快；具體調多少由現場決定

ET/BT 差距解讀：
- 正常差距：ET 比 BT 高 20～35°C（乾燥期）/ 15～25°C（梅納期）/ 10～20°C（發展期）
- ET-BT差距縮小 → 供熱不足，考慮加火或縮小風門
- ET-BT差距突然擴大 → 可能風門過小造成熱累積，或ET探針位置問題

各階段感官提示：
- 乾燥期（回溫～160°C）：豆體由深綠轉淡黃，幾乎無香氣，滾筒聲沉穩。
- T1附近（145～155°C）：草香開始出現，豆表開始起皺，銀皮開始鬆動。
- 梅納期（160～200°C）：烤麵包香→焦糖香，豆色轉金黃→深棕，銀皮大量脫落（此時排煙重要）。
- 一爆（FC，約200°C）：爆裂聲密集如爆米花，煙量明顯增加，豆體體積膨脹約50～80%。
- 發展期（FC後）：爆裂聲逐漸稀疏，烘焙香趨於穩定，豆色持續加深。
- 二爆（SC，約220°C）：細小密集爆裂聲，油脂開始滲出，深烘特徵出現。

觸發事件說明：數據中的「⚡ 觸發事件」說明本次查詢的原因：
- 「BT 達到 X°C」→ 豆溫剛越過關鍵溫度點，請說明此溫度的意義（包含風門建議）並給出操作建議。
- 「入豆後 X分XX秒 定時」→ 定時巡查，重點確認節奏與RoR是否達標，同時評估風門是否需調整。
- 「手動詢問」→ 烘焙師主動提問，可較詳細說明原因。

參考曲線模式：若訊息中「參考曲線目標值」區塊有數據，代表使用者已載入背景烘焙曲線（可能是烘焙計畫或成功烘焙紀錄）。
此時你的首要任務是「複製那條曲線」——以目標BT和目標RoR為準，而非固定的 15～25 / 10～15 / 8～10 門檻。
若與固定規則有矛盾，以背景曲線為準，但仍需注意 RoR 崩潰或翻揚的風險。

規則引擎提示：訊息中若有「規則引擎評估」區塊，那是根據規則（含背景曲線偏差）計算出的初步建議。
你的工作是：確認它是否合理、補充規則看不到的跨參數觀察（特別是ET/BT差距、感官線索）、或在有更好理由時修正它。
不要只是複述規則引擎的內容——你的價值在於判斷規則判斷不了的事，例如風門與火力的協同操作。

熱慣性關鍵：火力調整需30～90秒反映在RoR；風門調整需15～30秒。所有建議必須是預判性的。

使用繁體中文。
"""

ROAST_SUMMARY_PROMPT = """你是一位資深咖啡烘焙師，同時精通全息烘焙法（謝承孝）與史考特饒理論，剛完成一爐烘焙的陪同監看。
請根據以下烘焙記錄，給出這爐的完整評估報告。

【評估重點】
1. RoR曲線品質：各階段是否符合平滑遞減原則，有無翻揚（flick）或驟降（crash）；標出問題發生的時間點
2. 全息烘焙法四階段節奏：
   - 乾燥期（回溫～T1～轉黃）：進度是否符合5分鐘到150°C的基準
   - 梅納期（轉黃～FC）：持續時間是否足夠（過短→風味薄弱，過長→苦澀）
   - 發展期（FC後）：DTR是否在20～25%，RoR是否維持8～10°C/min緩降
3. 關鍵轉折點評估：T0回溫點（溫度/時間）、T1玻璃轉化點（是否有對應風門調整）、FC一爆時機
4. 排煙策略：T1附近是否有排蒸汽操作，一爆時是否即時開大風門；若無記錄，說明對風味的潛在影響
5. ET/BT 關係：差距是否合理，有無異常縮小或擴大的時間段
6. 若有參考曲線，說明本爐與目標的主要偏差（BT差距、RoR差距、時間偏移）
7. 下一爐具體調整建議（3 點以內，最具優先級的先說；包含火力、風門、入豆溫三個維度）

使用繁體中文，條列式輸出，語氣專業務實，直接給出結論與建議。禁止模糊措辭，每條建議必須有具體數字或操作。
"""


def _get_stage_name(timeindex: list) -> str:
    if timeindex[0] < 0:
        return 'pre-charge'
    if timeindex[6] > 0:
        return 'dropped'
    if timeindex[4] > 0:
        return 'second-crack'
    if timeindex[2] > 0:
        return 'development (FC started)'
    if timeindex[1] > 0:
        return 'caramelization (after dry-end)'
    return 'drying/browning'


def _interp_background(time_since_charge: float,
                        timeB: list, timeindexB: list,
                        temp2B: list, delta2B: list) -> 'tuple[float|None, float|None]':
    if not timeB or timeindexB[0] < 0 or timeindexB[0] >= len(timeB):
        return None, None
    charge_time_b = timeB[timeindexB[0]]
    target_t = charge_time_b + time_since_charge
    for i in range(len(timeB) - 1):
        if timeB[i] <= target_t <= timeB[i + 1]:
            ratio = (target_t - timeB[i]) / (timeB[i + 1] - timeB[i]) if timeB[i + 1] != timeB[i] else 0.0
            bt_b = temp2B[i] + ratio * (temp2B[i + 1] - temp2B[i]) if len(temp2B) > i + 1 else None
            ror_b: float | None = None
            if delta2B and len(delta2B) > i + 1:
                d_i = delta2B[i]
                d_i1 = delta2B[i + 1]
                if d_i is not None and d_i1 is not None:
                    ror_b = d_i + ratio * (d_i1 - d_i)
            return bt_b, ror_b
    return None, None


def _calc_ror_trend(ror_values: 'list[float]') -> str:
    """Describe RoR trend from recent per-sample readings (oldest first)."""
    if len(ror_values) < 4:
        return '資料不足'
    # use first and last quarter averages to smooth noise
    q = max(1, len(ror_values) // 4)
    old_avg = sum(ror_values[:q]) / q
    new_avg = sum(ror_values[-q:]) / q
    delta = new_avg - old_avg
    if delta > 1.5:
        return f'持續上升（+{delta:.1f}°C/min）'
    if delta < -1.5:
        return f'持續下滑（{delta:.1f}°C/min）'
    return f'大致穩定（變化 {delta:+.1f}°C/min）'


def _ror_assessment(ror: float, bt: float,
                     dry_end: float = 160.0, fc_start: float = 200.0) -> str:
    """Return a plain-language assessment of RoR vs stage target."""
    lo, hi = _ror_range(bt, dry_end, fc_start)
    if ror < lo * 0.6:
        status = f'嚴重不足（目標 {lo:.0f}～{hi:.0f}，偏差 {ror - lo:.1f}°C/min）'
    elif ror < lo:
        status = f'低於目標（目標 {lo:.0f}～{hi:.0f}，偏差 {ror - lo:.1f}°C/min）'
    elif ror > hi * 1.2:
        status = f'嚴重超標（目標 {lo:.0f}～{hi:.0f}，偏差 +{ror - hi:.1f}°C/min）'
    elif ror > hi:
        status = f'略高於目標（目標 {lo:.0f}～{hi:.0f}，偏差 +{ror - hi:.1f}°C/min）'
    else:
        status = f'正常（目標 {lo:.0f}～{hi:.0f}°C/min）'
    return status


def _pace_assessment(bt: float, time_since_charge: float, ror_bt: float) -> 'str|None':
    """Check roast pace: BT should reach 150°C at ~5 min post-charge."""
    TARGET_BT = 150.0
    TARGET_SEC = 300.0   # 5 minutes
    EARLY_SEC = 240.0    # <4 min = too fast
    LATE_SEC = 360.0     # >6 min = too slow

    if time_since_charge < 60:
        return None  # too early to judge

    if bt >= TARGET_BT:
        # Already past 150°C — report if pace was off
        if time_since_charge < EARLY_SEC:
            mins = time_since_charge / 60
            return f'⚠️ 烘焙進度偏快：{TARGET_BT:.0f}°C 在 {mins:.1f} 分鐘時已達到（目標約5分鐘），豆表恐過深，注意色感。'
        return None  # on time or slightly late is fine once past 150

    # Still below 150°C — estimate arrival time
    if ror_bt > 0.5:
        eta_sec = (TARGET_BT - bt) / ror_bt * 60.0
        eta_total = time_since_charge + eta_sec
        if eta_total < EARLY_SEC:
            return f'⚠️ 烘焙進度偏快：預估 {eta_total/60:.1f} 分鐘到達 {TARGET_BT:.0f}°C（目標約5分鐘），考慮稍降火力。'
        elif eta_total > LATE_SEC:
            return f'⚠️ 烘焙進度偏慢：預估 {eta_total/60:.1f} 分鐘才到達 {TARGET_BT:.0f}°C（目標約5分鐘），考慮適度加火。'
    elif time_since_charge > LATE_SEC:
        return f'⚠️ 烘焙進度嚴重偏慢：已過 {time_since_charge/60:.1f} 分鐘但 BT 仍 {bt:.0f}°C，遠未到達 {TARGET_BT:.0f}°C 目標，必須加火。'
    return None


def _extract_action_line(advice: str) -> str:
    """Pull the 操作 line from a previous advice for context."""
    import re
    for line in advice.splitlines():
        if re.match(r'^操作[：:]', line):
            return line.strip()
    return ''


def _detect_ror_anomaly(ror_values: 'list[float]') -> 'tuple[str|None, str]':
    """Detect RoR flick or crash. Returns (type, advice_text) or (None, '')."""
    if len(ror_values) < 8:
        return None, ''
    prev4 = ror_values[-8:-4]
    last4 = ror_values[-4:]
    prev_slope = (prev4[-1] - prev4[0]) / 3.0
    last_slope = (last4[-1] - last4[0]) / 3.0
    # Flick: declining then reversing upward
    reversal = last4[-1] - min(prev4[-2:] + last4[:2])
    if prev_slope < -0.3 and last_slope > 0.3 and reversal >= 1.5:
        return ('flick',
                f'現況：⚠️ RoR在翻揚！剛才還在往下，現在反彈了 {reversal:.1f}°C/min，這條線不能讓它繼續往上\n'
                f'操作：火先減——如果風門還有開大空間，可以稍開一點輔助降RoR，但擇一先試，不要同時動\n'
                f'預期：40～60秒後RoR應該會止揚回落；動完之後盯著，沒效果再調')
    # Crash: sharp drop from recent peak
    recent_max = max(ror_values[-8:])
    current = ror_values[-1]
    drop = recent_max - current
    if drop >= 4.0 and last_slope < -0.5:
        return ('crash',
                f'現況：⚠️ RoR在驟降！從 {recent_max:.1f} 掉到 {current:.1f}°C/min，跌了 {drop:.1f}，不正常\n'
                f'操作：補火；另外看一下風門是不是開太大了，或者環境突然降溫\n'
                f'預期：及時補火的話40秒後應該會止跌；還沒止住的話，擇一再試：補火或縮小風門')
    return None, ''


def _dtr_rule_advice(dtr_pct: float, bt: float, ror_bt: float) -> str:
    """Generate DTR milestone advice for the development phase."""
    if dtr_pct >= 25.0:
        return (f'現況：DTR {dtr_pct:.1f}% 了，到上限了，BT {bt:.1f}°C，RoR {ror_bt:.1f}°C/min\n'
                f'操作：出豆——出豆槽就位了嗎？冷卻盤風扇開了嗎？再等下去苦味會出來\n'
                f'預期：出豆後記一下這爐的發展時間跟豆色，下一爐參考用')
    if dtr_pct >= 22.0:
        return (f'現況：DTR {dtr_pct:.1f}%，發展不錯，BT {bt:.1f}°C，RoR {ror_bt:.1f}°C/min\n'
                f'操作：取樣棒拿出來看一下豆色跟聞一下香氣；'
                f'{"RoR剩 " + f"{ror_bt:.1f}" + " 了，差不多可以出豆了" if ror_bt < 6 else "RoR還好，看你要烘到哪個深度"}\n'
                f'預期：再1～3%就到25%上限；淺烘在這裡出沒問題，中深烘繼續盯著')
    if dtr_pct >= 20.0:
        return (f'現況：DTR {dtr_pct:.1f}%，進下豆窗口了，BT {bt:.1f}°C，RoR {ror_bt:.1f}°C/min\n'
                f'操作：取樣棒看豆色；冷卻盤風扇開了沒、出豆槽就位了沒？'
                f'淺烘現在就可以考慮出，中烘22%，深烘25%\n'
                f'預期：每30秒DTR大概增加0.5～1%，眼睛耳朵都要專注了')
    # 18%
    return (f'現況：DTR {dtr_pct:.1f}%，快要進下豆窗口了，BT {bt:.1f}°C，RoR {ror_bt:.1f}°C/min\n'
            f'操作：冷卻盤風扇先開起來備著；火不要再加了，準備進決策\n'
            f'預期：再1～2分鐘DTR會到20%，那時候開始取樣棒確認豆色香氣')


# ---------------------------------------------------------------------------
# Damper / fire coordination helpers
# ---------------------------------------------------------------------------

_DAMPER_ROR_PER_NOTCH: Final[float] = 0.75  # °C/min RoR drop per damper notch opened


def _damper_stage_target(bt: float, trigger: str,
                          dry_end: float = 160.0,
                          fc_start: float = 200.0) -> 'tuple[int, str]':
    """Return (target_position 1-5, reason) based on roast stage."""
    if any(k in trigger for k in ('一爆開始', '一爆結束', '二爆', 'FC')):
        return 5, '一爆/發展期排煙需求最大，煙氣與銀皮量達峰值'
    if bt >= fc_start:
        return 5, '發展期全開排煙，確保風味乾淨'
    if '脫水結束' in trigger or bt >= dry_end:
        return 3, '梅納期：排煙與保熱平衡'
    if bt >= 145:
        return 3, 'T1玻璃轉化後：排出水蒸氣防蒸汽味'
    if bt >= 130:
        return 2, '乾燥期末：稍開排蒸汽'
    return 1, '乾燥期：小開蓄熱'


def _coordinated_action(ror_bt: float, bt: float,
                         damper_cur: 'int|None',
                         damper_target: int,
                         damper_reason: str,
                         fire_delta_base: int,
                         fire_text_base: str,
                         dry_end: float = 160.0,
                         fc_start: float = 200.0,
                         bg_ror: 'float|None' = None) -> 'tuple[int, str]':
    """Combine fire recommendation with a stage-aware damper reminder.
    Priority logic differs by roast stage:
      乾燥期: fire primary, damper barely moves
      梅納期: RoR low → close damper first (faster); RoR high → pick one (fire or open)
      發展期: damper fully open for smoke, only fire controls RoR
    Returns (fire_delta_base, action_text).
    """
    damper_cur_eff = damper_cur if damper_cur is not None else 3
    damper_delta = damper_target - damper_cur_eff  # >0 should open, <0 should close

    need_heat = fire_delta_base > 0
    need_cool = fire_delta_base < 0

    if bt < dry_end:
        # ── 乾燥期：火力主導，風門小開蓄熱，幾乎不動 ──
        if need_heat and damper_delta > 0:
            damper_reminder = f'風門先別開（{damper_reason}），開大散熱會讓RoR更難拉上來，靠補火'
        elif need_heat:
            damper_reminder = f'風門維持小開（{damper_reason}），蓄熱靠火力'
        elif need_cool and damper_delta > 0:
            damper_reminder = f'減火或稍開風門擇一先試（{damper_reason}），觀察效果再決定'
        elif need_cool:
            damper_reminder = f'風門維持現況（{damper_reason}），靠減火控制'
        else:
            damper_reminder = f'風門維持小開（{damper_reason}），節奏穩'

    elif bt < fc_start:
        # ── 梅納期：RoR低→先縮風門（更快）；RoR高→擇一先試 ──
        if need_heat and damper_delta < 0:
            # Damper currently too open → closing raises RoR faster than fire
            urgency = '積極' if fire_delta_base >= 2 else ''
            damper_reminder = (f'先{urgency}縮小風門（{damper_reason}），'
                               f'比補火反應快；觀察後效果不夠再補火')
        elif need_heat and damper_delta > 0:
            # Target says open but RoR low — opening makes it worse
            damper_reminder = f'風門暫別開（{damper_reason}），開大會讓RoR再跌；先靠補火把節奏拉回來'
        elif need_heat:
            damper_reminder = f'風門維持中段（{damper_reason}），補火觀察'
        elif need_cool and damper_delta > 0:
            # Both options reduce RoR — pick one
            damper_reminder = f'減火或稍開風門擇一先試（{damper_reason}），觀察效果再決定下一步'
        elif need_cool and damper_delta < 0:
            # Closing raises RoR — contradicts cooling
            damper_reminder = f'此時縮小風門反而升RoR（{damper_reason}），靠減火控制就好，風門維持現況'
        elif need_cool:
            damper_reminder = f'風門維持中段（{damper_reason}），減火觀察'
        else:
            # fire_delta == 0
            if damper_delta > 0:
                damper_reminder = f'可稍開風門排蒸汽（{damper_reason}），開後RoR會微降，幅度小不需補火'
            elif damper_delta < 0:
                damper_reminder = f'可縮小風門（{damper_reason}），縮後RoR會微升，留意節奏別跑過頭'
            else:
                damper_reminder = f'風門維持中段（{damper_reason}），節奏穩'

    else:
        # ── 發展期：風門全開固定排煙，RoR只靠火力控制 ──
        if need_heat:
            damper_reminder = f'風門全開繼續排煙（{damper_reason}），發展期靠補火把RoR穩住'
        elif need_cool:
            damper_reminder = f'風門全開繼續排煙（{damper_reason}），靠減火把RoR壓回來'
        else:
            damper_reminder = f'風門全開排煙（{damper_reason}），節奏穩'

    return fire_delta_base, f'{fire_text_base}；{damper_reminder}'


# ---------------------------------------------------------------------------
# Rule-based real-time advisor (no API call — fires at each auto-trigger)
# ---------------------------------------------------------------------------

_TEMP_MILESTONE_CONTEXT: Final[dict] = {
    110.0: '乾燥期中段，水分持續蒸發，豆色仍深綠，風門保持小開（1～2/5）蓄熱',
    120.0: '乾燥期後段，豆色開始轉淡，草香漸現，風門維持小開',
    130.0: '乾燥期末段，豆體水分大量散逸，豆表開始起皺，可準備稍開風門',
    140.0: '接近T1玻璃轉化點，銀皮開始鬆動，建議將風門從小開調至中段（2→3/5）開始排蒸汽',
    150.0: 'T1玻璃轉化點，銀皮大量脫落，梅納反應前夕；風門調至中段（3/5），RoR應準備從乾燥期節奏（15～25）緩降',
    160.0: '轉黃（梅納期開始），梅納褐化反應主導，焦糖香漸現；風門維持中段（3/5），目標RoR 10～15°C/min',
}


def _is_before_tp(ror_bt: float) -> bool:
    """True while BT is still declining (before turning point)."""
    return ror_bt < 0


def _tp_just_reached(ror_bt: float, ror_values: 'list[float]') -> bool:
    """True if RoR just crossed from negative to positive (TP moment)."""
    if ror_bt < 0:
        return False
    recent = (ror_values or [])[-4:]
    return bool(recent) and any(r < 0 for r in recent)


def _stage_label(bt: float, before_tp: bool = False,
                  dry_end: float = 160.0, fc_start: float = 200.0) -> str:
    if before_tp:
        return '回溫中'
    if bt < dry_end:
        return '乾燥期'
    if bt < fc_start:
        return '梅納期'
    return '發展期'


def _ror_range(bt: float, dry_end: float = 160.0,
               fc_start: float = 200.0) -> 'tuple[float, float]':
    """Return (lo, hi) RoR target for current BT."""
    if bt < dry_end:
        return 15.0, 25.0   # 乾燥期：回溫後～轉黃
    if bt < fc_start:
        return 10.0, 15.0   # 梅納期：轉黃～FC
    return 8.0, 10.0         # 發展期：FC後


def _fire_recommendation(ror_bt: float, bt: float,
                          pace_warn: 'str|None',
                          time_since_charge: float,
                          dry_end: float = 160.0, fc_start: float = 200.0,
                          bg_ror: 'float|None' = None) -> 'tuple[int, str]':
    """Return (fire_delta, action_text).
    fire_delta: -2/-1/0/+1/+2 (number of notches).
    When bg_ror is provided the background curve is used as the primary target.
    """
    lo, hi = _ror_range(bt, dry_end, fc_start)
    two_min_warn = (115 <= time_since_charge <= 135 and ror_bt < 15.0)

    if bg_ror is not None:
        # Background-guided mode: follow the reference curve.
        # Keep absolute safety bounds as a hard floor/ceiling.
        diff = ror_bt - bg_ror
        if ror_bt < lo * 0.5:
            return +2, (f'RoR 掉到 {ror_bt:.1f} 了，跟參考曲線差太遠（目標 {bg_ror:.1f}），'
                        f'火要補，而且要快——熱慣性40秒才反映，現在就動')
        if ror_bt > hi * 1.4:
            return -2, (f'RoR {ror_bt:.1f} 跑太高了（參考 {bg_ror:.1f}），'
                        f'火要趕快減，現在就動')
        if diff < -4:
            return +2, (f'RoR 落後參考曲線 {-diff:.1f}°C/min（現 {ror_bt:.1f}，目標 {bg_ror:.1f}），'
                        f'積極補火，別等，40秒後才看得到效果')
        if diff < -2:
            return +1, (f'RoR 比參考慢了一點（現 {ror_bt:.1f}，目標 {bg_ror:.1f}），'
                        f'稍微加火，60秒後應該會追上來')
        if diff > +4:
            return -2, (f'RoR 跑太快了，超前參考曲線 {diff:.1f}°C/min（現 {ror_bt:.1f}，目標 {bg_ror:.1f}），'
                        f'火要減，不然發展會壓縮')
        if diff > +2:
            return -1, (f'RoR 比參考快了一點（現 {ror_bt:.1f}，目標 {bg_ror:.1f}），'
                        f'火微減一下，拉回節奏')
        if two_min_warn:
            return +1, (f'投豆兩分鐘了，RoR 才 {ror_bt:.1f}——有點低，'
                        f'入豆溫或初始火力可能不夠，補一點火')
        if pace_warn and '偏快' in pace_warn:
            return -1, f'進度偏快，火稍微收一點，別讓豆表跑太前面'
        if pace_warn and '偏慢' in pace_warn:
            return +1, f'進度有點慢，補火讓它追上來'
        return 0, f'RoR {ror_bt:.1f} 跟參考曲線貼得不錯（目標 {bg_ror:.1f}），現況維持就好'

    # ── No background: fall back to fixed-target rules ──
    if ror_bt < lo * 0.6:
        return +2, (f'RoR 掉到 {ror_bt:.1f} 了，嚴重偏低——'
                    f'火要補，而且要快，別猶豫，熱慣性40秒才反映')
    if ror_bt < lo:
        deficit = lo - ror_bt
        notch = 2 if deficit > 4 else 1
        return +notch, (f'RoR {ror_bt:.1f} 有點低，補{"一點" if notch == 1 else "積極補"}火，'
                        f'40～60秒後應該會止跌')
    if ror_bt < 6.0 and bt >= fc_start:
        return 0, (f'RoR 只剩 {ror_bt:.1f} 了，快到出豆時機——'
                   f'冷卻盤風扇開了沒？出豆槽就位了嗎？取樣棒確認一下豆色')
    if ror_bt > hi * 1.3:
        return -2, f'RoR {ror_bt:.1f} 跑太高，火要趕快減，別猶豫'
    if ror_bt > hi:
        return -1, f'RoR {ror_bt:.1f} 稍微偏高，火微減一下，觀察效果再決定下一步'
    if two_min_warn:
        return +1, (f'投豆兩分鐘了，RoR 才 {ror_bt:.1f}——'
                    f'入豆溫或初始火力可能偏低，補火')
    if pace_warn and '偏快' in pace_warn:
        return -1, f'進度有點快，火稍微收一下，豆表色感別跑太前面'
    if pace_warn and '偏慢' in pace_warn:
        return +1, f'進度有點慢，補火讓它在5分鐘內到達150°C'
    return 0, f'RoR {ror_bt:.1f} 在目標範圍，節奏不錯，維持就好'


def _expected_outcome(fire_delta: int, ror_bt: float, bt: float,
                       time_since_charge: float,
                       bg_bt: 'float|None' = None,
                       bg_ror: 'float|None' = None) -> str:
    lo, hi = _ror_range(bt)
    proj_bt = bt + ror_bt * 1.5
    target_hi = bg_ror if bg_ror is not None else hi
    target_lo = bg_ror if bg_ror is not None else lo
    if fire_delta == +2:
        new_ror = min(ror_bt + 3.5, target_hi)
        base = f'40秒左右RoR應該會止跌往上，盯著它；BT繼續往上走'
    elif fire_delta == +1:
        new_ror = min(ror_bt + 2.0, target_hi)
        base = f'60秒後RoR應該會穩住，BT繼續穩升；如果還沒止跌再評估'
    elif fire_delta == -2:
        new_ror = max(ror_bt - 3.0, target_lo)
        base = f'40秒後RoR應該明顯下來；注意別讓它降過頭，隨時準備收手'
    elif fire_delta == -1:
        new_ror = max(ror_bt - 1.5, target_lo)
        base = f'60秒後RoR會緩緩下來，BT升幅也會趨緩；觀察一下效果'
    else:
        mins_to_150 = ''
        if bt < 150 and ror_bt > 0.5:
            eta = ((150 - bt) / ror_bt * 60 + time_since_charge) / 60
            mins_to_150 = f'；照這節奏約 {eta:.1f} 分鐘到達150°C'
        base = f'節奏穩，90秒後BT大概到 {proj_bt:.0f}°C{mins_to_150}'
    # BT gap note when background is available and there's a meaningful deviation
    if bg_bt is not None:
        bt_diff = bt - bg_bt
        if abs(bt_diff) >= 3:
            gap_closes = (fire_delta > 0 and bt_diff < 0) or (fire_delta < 0 and bt_diff > 0)
            trend = '差距逐步縮小' if gap_closes else '差距維持'
            base += f'；目標BT差 {bt_diff:+.1f}°C，{trend}'
    return base


def _compute_rule_advice(bt: float, ror_bt: float,
                          time_since_charge: float, trigger: str,
                          ror_values: 'list[float]|None' = None,
                          dry_end: float = 160.0, fc_start: float = 200.0,
                          bg_bt: 'float|None' = None,
                          bg_ror: 'float|None' = None,
                          damper_cur: 'int|None' = None) -> str:
    """Generate a deterministic 現況/操作/預期 advice string from roasting rules.
    When bg_bt/bg_ror are provided, advice is relative to the loaded background curve.
    """
    mins, secs = divmod(int(time_since_charge), 60)
    ror_vals = ror_values or []
    before_tp = _is_before_tp(ror_bt)
    is_tp = _tp_just_reached(ror_bt, ror_vals)

    # --- 回溫中（BT 尚未到最低點）---
    if before_tp:
        bg_note = ''
        if bg_bt is not None:
            diff = bt - bg_bt
            bg_note = f'（參考曲線 {bg_bt:.1f}°C，偏差 {diff:+.1f}°C）'
        return (f'現況：回溫中，{mins}:{secs:02d}，BT {bt:.1f}°C{bg_note}，還在往下走（RoR {ror_bt:.1f}°C/min）\n'
                f'操作：火維持就好，等豆子吸熱到最低點自然會回頭\n'
                f'預期：回溫點大概在85～100°C，到了之後RoR會轉正，乾燥期正式開始')

    # --- 剛到回溫點（TP）---
    if is_tp:
        if bt < 85:
            bt_eval = f'偏低（{bt:.1f}°C，目標85～100°C），入豆溫偏低或初始火力不足'
            bt_action = '下一爐考慮提高入豆溫5～10°C或加大初始火力'
        elif bt <= 100:
            bt_eval = f'正常（{bt:.1f}°C，目標85～100°C）✅'
            bt_action = None
        else:
            bt_eval = f'偏高（{bt:.1f}°C，目標85～100°C），入豆溫偏高或初始火力過大'
            bt_action = '下一爐考慮降低入豆溫5～10°C或減小初始火力'

        if time_since_charge < 60:
            time_eval = f'⚠️ 時間偏早（{mins}:{secs:02d}，目標約90秒），入豆溫過高或投豆量偏少'
            if not bt_action:
                bt_action = '下一爐考慮降低入豆溫5～10°C'
        elif time_since_charge <= 120:
            time_eval = f'✅ 時間正常（{mins}:{secs:02d}，目標約90秒）'
        else:
            time_eval = f'⚠️ 時間偏晚（{mins}:{secs:02d}，目標約90秒），入豆溫偏低或火力不足'
            if not bt_action:
                bt_action = '下一爐考慮提高入豆溫5～10°C或加大初始火力'

        # Compare TP with background TP if available
        bg_tp_note = ''
        if bg_bt is not None:
            diff = bt - bg_bt
            sign = '+' if diff >= 0 else ''
            bg_tp_note = f'，參考曲線回溫點 {bg_bt:.1f}°C（本爐 {sign}{diff:.1f}°C）'

        action = bt_action or '回溫點良好，維持現有火力進入乾燥期'
        return (f'現況：回溫點到達，BT {bt:.1f}°C（{bt_eval}），{time_eval}{bg_tp_note}\n'
                f'操作：{action}\n'
                f'預期：BT 開始穩定上升，進入乾燥期，目標2分鐘時RoR≥15°C/min')

    # --- 投豆（CHARGE 按鈕）---
    if 'CHARGE' in trigger:
        return (f'現況：投豆了，{mins}:{secs:02d}，BT 開始往下掉，正常的\n'
                f'操作：火維持不動，等豆子回溫，回溫點大概在85～100°C、約90秒後\n'
                f'預期：BT會繼續降到最低點再回頭，那時候乾燥期正式開始')

    # --- 脫水結束（DRY END 按鈕）---
    if '脫水結束' in trigger:
        lo, hi = _ror_range(bt, dry_end, fc_start)
        ror_eval = _ror_assessment(ror_bt, bt, dry_end, fc_start)
        bg_note = ''
        if bg_bt is not None:
            diff = bt - bg_bt
            bg_note = f'，參考曲線 {bg_bt:.1f}°C（偏差 {diff:+.1f}°C）'
        if lo <= ror_bt <= hi:
            fire_delta_base, fire_text_base = 0, '維持火力'
        elif ror_bt < lo:
            fire_delta_base, fire_text_base = +1, '加火（上調1格），補足梅納期升溫動能'
        else:
            fire_delta_base, fire_text_base = -1, '減火（下調1格），RoR過高會壓縮梅納反應時間'
        damper_target, damper_reason = _damper_stage_target(bt, trigger, dry_end, fc_start)
        _, action_text = _coordinated_action(ror_bt, bt, damper_cur, damper_target, damper_reason,
                                             fire_delta_base, fire_text_base, dry_end, fc_start, bg_ror)
        return (f'現況：脫水期過了，進梅納期了，{mins}:{secs:02d}，BT {bt:.1f}°C{bg_note}，RoR {ror_bt:.1f}°C/min（{ror_eval}）'
                f'；豆色應該已經轉黃，銀皮陸續在脫\n'
                f'操作：{action_text}\n'
                f'預期：接下來是梅納反應的天下，BT穩步往上，焦糖香會越來越明顯，RoR緩緩往下走到一爆')

    # --- 一爆開始（FC 按鈕）---
    if '一爆開始' in trigger:
        ror_eval = _ror_assessment(ror_bt, bt, dry_end, fc_start)
        bg_note = ''
        if bg_bt is not None:
            diff = bt - bg_bt
            bg_note = f'，參考曲線 {bg_bt:.1f}°C（偏差 {diff:+.1f}°C）'
        if 8 <= ror_bt <= 11:
            fire_delta_base, fire_text_base = 0, '維持火力，密切觀察RoR是否有回升趨勢，如有立即微減火'
        elif ror_bt > 11:
            fire_delta_base, fire_text_base = -1, '微減火（下調1格），控制發展節奏'
        else:
            fire_delta_base, fire_text_base = +1, '輕微加火（上調1格），避免RoR過快崩跌'
        damper_target, damper_reason = _damper_stage_target(bt, trigger, dry_end, fc_start)
        _, action_text = _coordinated_action(ror_bt, bt, damper_cur, damper_target, damper_reason,
                                             fire_delta_base, fire_text_base, dry_end, fc_start, bg_ror)
        return (f'現況：一爆來了，{mins}:{secs:02d}，BT {bt:.1f}°C{bg_note}，RoR {ror_bt:.1f}°C/min（{ror_eval}）'
                f'；爆裂聲密集，煙跟銀皮現在最多\n'
                f'操作：{action_text}；計時開始\n'
                f'預期：爆裂聲會持續一陣子，RoR慢慢往下；煙排掉了豆表風味會乾淨很多，目標DTR 20～25%')

    # --- 一爆結束（FC END 按鈕）---
    if '一爆結束' in trigger:
        ror_eval = _ror_assessment(ror_bt, bt, dry_end, fc_start)
        if 6 <= ror_bt <= 10:
            fire_delta_base, fire_text_base = 0, '維持火力，監控RoR勿回升'
        elif ror_bt < 6:
            fire_delta_base, fire_text_base = +1, '輕微加火（上調1格），防止RoR驟崩'
        else:
            fire_delta_base, fire_text_base = -1, '微減火（下調1格），控制發展速度'
        damper_target, damper_reason = _damper_stage_target(bt, trigger, dry_end, fc_start)
        _, action_text = _coordinated_action(ror_bt, bt, damper_cur, damper_target, damper_reason,
                                             fire_delta_base, fire_text_base, dry_end, fc_start, bg_ror)
        return (f'現況：一爆密集段過了，{mins}:{secs:02d}，BT {bt:.1f}°C，RoR {ror_bt:.1f}°C/min（{ror_eval}）'
                f'；爆裂聲漸稀，進入安靜的發展段\n'
                f'操作：{action_text}；盯著DTR\n'
                f'預期：RoR繼續緩降，DTR到20%就進入下豆決策窗口了')

    # --- 二爆開始（SC 按鈕）---
    if '二爆開始' in trigger:
        return (f'現況：二爆來了，{mins}:{secs:02d}，BT {bt:.1f}°C，RoR {ror_bt:.1f}°C/min——深烘領域了'
                f'；豆油開始滲出，煙量又大起來\n'
                f'操作：現在要判斷要不要出豆；如果還要繼續，風門全開排油煙，每10秒看一次豆色跟煙量\n'
                f'預期：過了二爆碳化速度很快，最多再撐20～30秒；油煙一變濃就出豆，不要猶豫')

    # --- 下豆（DROP 按鈕）---
    if 'DROP' in trigger or '下豆' in trigger:
        return (f'現況：出豆了，{mins}:{secs:02d}，BT {bt:.1f}°C——這爐結束\n'
                f'操作：冷卻盤風扇全開，豆子均勻攤開散熱\n'
                f'接下來要做什麼？\n'
                f'▶ 繼續烘（鍋間流程）：確認鍋爐溫度是否已回到目標入豆溫；'
                f'調整火力預熱；秤好下一批生豆備用\n'
                f'▶ 結束收工：火力歸零或調到最小；等鍋爐降溫後關火；清理冷卻盤跟銀皮槽\n'
                f'預期：AI完整評估報告馬上來，對照這爐豆色香氣，決定下一爐怎麼調')

    # --- 接近梅納窗口結束（BT 自動偵測）---
    if '接近梅納窗口' in trigger or '162' in trigger:
        ror_eval = _ror_assessment(ror_bt, bt, dry_end, fc_start)
        if 8 <= ror_bt <= 15:
            fire_delta_base, fire_text_base = 0, '維持火力，準備迎接一爆'
        elif ror_bt < 8:
            fire_delta_base, fire_text_base = +1, '輕微加火（上調1格），確保足夠升溫動能進入一爆'
        else:
            fire_delta_base, fire_text_base = -1, '微減火（下調1格），避免進入一爆時RoR過高'
        # Pre-open to 4 before FC arrives; _coordinated_action will check if compensation needed
        damper_pre_target = min(4, (damper_cur or 3) + 1)  # nudge toward 4 as preparation
        _, action_text = _coordinated_action(ror_bt, bt, damper_cur, damper_pre_target,
                                             '梅納末段預開，一爆到來時立即全開',
                                             fire_delta_base, fire_text_base, dry_end, fc_start, bg_ror)
        return (f'現況：梅納期尾聲了，BT {bt:.1f}°C，一爆快來了'
                f'，RoR {ror_bt:.1f}°C/min（{ror_eval}）；焦糖香應該很濃，豆色深棕\n'
                f'操作：{action_text}；耳朵要豎起來聽\n'
                f'預期：再5～10°C就可能開始爆了，一聽到爆裂聲立刻按FC記錄，同時把風門開大排煙')

    # --- 正常烘焙階段 ---
    stage = _stage_label(bt, dry_end=dry_end, fc_start=fc_start)
    trend = _calc_ror_trend(ror_vals)
    pace_warn = _pace_assessment(bt, time_since_charge, ror_bt)

    milestone_ctx = next(
        (ctx for temp, ctx in _TEMP_MILESTONE_CONTEXT.items() if f'{temp:.0f}' in trigger),
        ''
    )

    # Build background deviation annotation
    bg_parts: list[str] = []
    if bg_bt is not None:
        diff = bt - bg_bt
        icon = '✅' if abs(diff) < 3 else ('⬆' if diff > 0 else '⬇')
        bg_parts.append(f'目標BT {bg_bt:.1f}°C（{icon}{diff:+.1f}°C）')
    if bg_ror is not None:
        diff = ror_bt - bg_ror
        icon = '✅' if abs(diff) < 2 else ('⬆' if diff > 0 else '⬇')
        bg_parts.append(f'目標RoR {bg_ror:.1f}（{icon}{diff:+.1f}°C/min）')

    ror_status = _ror_assessment(ror_bt, bt, dry_end, fc_start)
    situation_parts = [f'{stage}，{mins}:{secs:02d}，BT {bt:.1f}°C']
    if bg_parts:
        situation_parts.append('【參考曲線：' + '，'.join(bg_parts) + '】')
    if milestone_ctx:
        situation_parts.append(f'【{milestone_ctx}】')
    situation_parts.append(f'RoR {ror_bt:.1f}°C/min（{ror_status}）')

    # Second line: trend + pace + sensory cues
    trend_parts = [f'趨勢：{trend}']
    if pace_warn:
        trend_parts.append(pace_warn.replace('⚠️ ', '').rstrip('。'))
    if bt >= fc_start:
        trend_parts.append('爆裂聲注意聽，豆色持續加深')
    elif bt >= dry_end:
        trend_parts.append('焦糖香應漸濃，銀皮持續脫落')
    elif bt >= 145:
        trend_parts.append('豆表開始起皺，草香轉甜香')

    situation = '，'.join(situation_parts) + '\n' + '，'.join(trend_parts)

    fire_delta_base, fire_text_base = _fire_recommendation(ror_bt, bt, pace_warn, time_since_charge,
                                                            dry_end, fc_start, bg_ror)
    damper_target, damper_reason = _damper_stage_target(bt, trigger, dry_end, fc_start)
    fire_delta, action_text = _coordinated_action(ror_bt, bt, damper_cur, damper_target, damper_reason,
                                                   fire_delta_base, fire_text_base, dry_end, fc_start, bg_ror)

    # Equipment readiness reminder in development phase
    equip_hint = ''
    if bt >= fc_start:
        if ror_bt < 6:
            equip_hint = '冷卻盤風扇開了嗎？出豆槽就位了嗎？'
        else:
            equip_hint = '冷卻盤備著，隨時準備'

    expected = _expected_outcome(fire_delta, ror_bt, bt, time_since_charge, bg_bt, bg_ror)
    if equip_hint:
        expected = f'{expected}；{equip_hint}'

    return f'現況：{situation}\n操作：{action_text}\n預期：{expected}'


# ---------------------------------------------------------------------------
# TTS helper — clean advice text for natural speech
# ---------------------------------------------------------------------------

def _clean_for_tts(advice: str) -> str:
    """Return full advice text cleaned for TTS: remove icons, normalise units."""
    import re
    text = advice
    text = re.sub(r'[✅⬆⬇⚠️📊🔴🟡🟢🔥💨⚡☑️]', '', text)
    text = text.replace('【', '').replace('】', '')
    text = text.replace('°C/min', '度每分鐘')
    text = text.replace('°C', '度')
    text = re.sub(r'（\s*）', '', text)
    text = re.sub(r'，{2,}', '，', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def _action_for_tts(advice: str) -> str:
    """Extract and clean only the 操作 line for TTS."""
    import re
    for line in advice.strip().splitlines():
        if re.match(r'^操作[：:]', line):
            action = re.sub(r'^操作[：:]', '', line).strip()
            action = re.sub(r'[✅⬆⬇⚠️📊🔴🟡🟢🔥💨⚡☑️]', '', action)
            action = action.replace('°C/min', '度每分鐘').replace('°C', '度')
            action = re.sub(r'（\s*）', '', action)
            return action.strip()
    return _clean_for_tts(advice)  # fallback


def _build_user_message(bt: float, et: float, ror_bt: float, ror_et: float,
                         stage: str, time_since_charge: float,
                         mode: str,
                         bg_bt: 'float|None' = None,
                         bg_ror: 'float|None' = None,
                         ror_values: 'list[float]|None' = None,
                         trigger: str = '',
                         recent_advice: 'list[str]|None' = None,
                         rule_hint: 'str|None' = None) -> str:
    unit = '°C' if mode == 'C' else '°F'
    mins = int(time_since_charge // 60)
    secs = int(time_since_charge % 60)
    just_charged = trigger == 'CHARGE（投豆）' or time_since_charge < 30
    projected_bt = bt + ror_bt * 1.0
    lines = ['目前烘焙數據：']
    if trigger:
        lines.append(f'⚡ 觸發事件：{trigger}')
    if just_charged:
        lines.append('⚠️ 剛投豆，BT 正在下降至回溫點，RoR 數據尚未穩定，請依 ET 和趨勢方向判斷。')
    pace_warn = _pace_assessment(bt, time_since_charge, ror_bt)
    if pace_warn:
        lines.append(pace_warn)
    lines += [
        f'- 階段：{stage}',
        f'- 烘焙時間：{mins}:{secs:02d}',
        f'- 豆溫（BT）：{bt:.1f}{unit}',
        f'- 環境溫（ET）：{et:.1f}{unit}',
        f'- BT RoR（當前）：{ror_bt:.1f}{unit}/min  ← {_ror_assessment(ror_bt, bt)}',
        f'- ET RoR：{ror_et:.1f}{unit}/min',
        f'- BT RoR 趨勢（近 {len(ror_values or [])} 樣本）：{_calc_ror_trend(ror_values or [])}',
        f'- 預估 60 秒後 BT（維持現況）：{projected_bt:.1f}{unit}',
    ]
    if bg_bt is not None:
        bt_diff = bt - bg_bt
        diff_str = f'+{bt_diff:.1f}' if bt_diff >= 0 else f'{bt_diff:.1f}'
        lines.append('\n參考曲線目標值（同時間點）：')
        lines.append(f'- 目標 BT：{bg_bt:.1f}{unit}（當前偏差 {diff_str}{unit}）')
        if bg_ror is not None:
            ror_diff = ror_bt - bg_ror
            ror_diff_str = f'+{ror_diff:.1f}' if ror_diff >= 0 else f'{ror_diff:.1f}'
            lines.append(f'- 目標 RoR：{bg_ror:.1f}{unit}/min（當前偏差 {ror_diff_str}{unit}/min）')
    if recent_advice:
        actions = [_extract_action_line(a) for a in recent_advice if _extract_action_line(a)]
        if actions:
            lines.append('\n近期操作脈絡（供參考，不必重複）：')
            for i, act in enumerate(actions[-2:], 1):
                lines.append(f'  {i}. {act}')
    if rule_hint:
        lines.append('\n規則引擎評估（供參考，請依你的判斷確認或修正）：')
        for line in rule_hint.splitlines():
            lines.append(f'  {line}')
    lines.append('\n請給出三行建議，第一行「現況：」、第二行「操作：」、第三行「預期：」，缺一不可。')
    return '\n'.join(lines)


class AIAdvisor:
    """AI-powered roasting advisor. Thread-safe, non-blocking."""

    def __init__(self) -> None:
        self.provider: AIProvider = AIProvider.OLLAMA
        self.api_key: str = ''
        self.model: str = 'llama3'
        self.ollama_url: str = 'http://localhost:11434'
        self.interval: int = 30
        self.enabled: bool = False
        self.language: str = 'zh-TW'

        self._last_query_time: float = 0.0
        self._lock: threading.Lock = threading.Lock()
        self._running_query: bool = False

        # display history: last 5 advice entries as (timestamp, text)
        self.history: deque[tuple[str, str]] = deque(maxlen=5)

        # conversation context: disabled for now — truncated responses corrupt context
        self._conversation_history: deque[tuple[str, str]] = deque(maxlen=0)

        # auto-trigger state (reset each roast)
        self._last_time_segment: int = -1       # last 30s segment (time_since_charge//30)
        self._triggered_temps: set = set()      # BT milestones already fired
        self._tp_fired: bool = False            # turning point advice already sent
        self._dtr_warned: set = set()           # DTR thresholds already advised ('18','20','22','25')
        self._last_anomaly_time: float = 0.0    # time.time() of last anomaly alert (60s cooldown)

        # TTS
        self.tts_enabled: bool = False
        self.tts_provider: str = 'auto'  # 'auto' / 'edge' / 'sapi' / 'say'
        self.tts_rate: int = 0           # -5 (slow) … +5 (fast)
        self.tts_volume: int = 80        # 0–100 (Windows SAPI only)
        self.tts_mode: str = 'full'      # 'full' = all 3 lines, 'action' = 操作 line only
        self.tts_prefix: bool = True     # prepend 注意！/提示，to speech
        self.tts_voice: str = ''         # SAPI description or Edge voice name; '' = auto
        self._tts_busy: bool = False

        self.on_advice: Optional[Callable[[str], None]] = None
        self.on_query_start: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset_for_new_roast(self) -> None:
        """Clear conversation history and auto-trigger state at the start of a new roast."""
        self._conversation_history.clear()
        self.history.clear()
        self._last_time_segment = -1
        self._triggered_temps.clear()
        self._tp_fired = False
        self._dtr_warned.clear()
        self._last_anomaly_time = 0.0

    def export_advice_log(self, filepath: str) -> None:
        """Write all advice history to a plain-text file."""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('AI 烘焙指導記錄\n')
            f.write('=' * 40 + '\n')
            for ts, text in self.history:
                f.write(f'\n[{ts}]\n{text}\n')

    def force_query(self, bt: float, et: float, ror_bt: float,
                    timeindex: list, timex: list, mode: str,
                    ror_values: 'list[float]|None' = None) -> None:
        """Immediately trigger an AI query, bypassing the interval check."""
        with self._lock:
            if self._running_query:
                return
            self._running_query = True
            self._last_query_time = time.time()

        if self.on_query_start:
            self.on_query_start()

        try:
            stage = _get_stage_name(timeindex)
            time_since_charge = 0.0
            if timeindex[0] >= 0 and len(timex) > timeindex[0]:
                time_since_charge = timex[-1] - timex[timeindex[0]] if timex else 0.0
            recent = [text for _, text in list(self.history)[-2:]]
            user_msg = _build_user_message(bt, et, ror_bt, 0.0, stage,
                                            time_since_charge, mode,
                                            ror_values=ror_values, trigger='手動詢問',
                                            recent_advice=recent)
            t = threading.Thread(target=self._query_ai,
                                 args=(user_msg, ROAST_SYSTEM_PROMPT, _MAX_TOKENS, False),
                                 daemon=True)
            t.start()
        except Exception:  # pylint: disable=broad-except
            _log.exception('force_query: unexpected error, releasing query lock')
            with self._lock:
                self._running_query = False

    def request_advice(self, bt: float, et: float, ror_bt: float, ror_et: float,
                       timeindex: list, timex: list, mode: str,
                       timeB: 'list|None' = None, timeindexB: 'list|None' = None,
                       temp2B: 'list|None' = None, delta2B: 'list|None' = None,
                       ror_values: 'list[float]|None' = None,
                       force_trigger: str = '',
                       phases: 'list|None' = None,
                       damper: 'int|None' = None) -> None:
        """Call from the sampling thread. Non-blocking.
        force_trigger: if non-empty, bypasses the interval check (used for key events).
        ror_values: per-sample RoR buffer from canvas for trend analysis.
        damper: current damper position (1-5), or None if not available.
        """
        if not self.enabled:
            return

        # Calculate roast elapsed time first (needed for auto-trigger detection)
        time_since_charge = 0.0
        if timeindex[0] >= 0 and len(timex) > timeindex[0]:
            time_since_charge = timex[-1] - timex[timeindex[0]] if timex else 0.0

        # Detect auto-triggers (temperature milestones and 30-second marks)
        # Store detected values; commit to state only after lock is acquired
        _detected_temp: 'float|None' = None
        _detected_seg: int = -1
        _detected_tp: bool = False
        _detected_dtr: 'str|None' = None
        _dtr_advice_text: str = ''
        _detected_anomaly: 'str|None' = None
        _anomaly_advice_text: str = ''
        now = time.time()

        if not force_trigger and time_since_charge >= 10 and timeindex[0] >= 0:
            # RoR anomaly detection (flick / crash) — highest priority, 60s cooldown
            if (self._tp_fired and len(ror_values or []) >= 8
                    and now - self._last_anomaly_time >= 60.0):
                _detected_anomaly, _anomaly_advice_text = _detect_ror_anomaly(ror_values or [])
                if _detected_anomaly:
                    force_trigger = f'RoR異常：{"翻揚" if _detected_anomaly == "flick" else "驟降"}'

            if not _detected_anomaly:
                # Turning point: RoR just crossed from negative to positive
                if not self._tp_fired and _tp_just_reached(ror_bt, ror_values or []):
                    _detected_tp = True
                    force_trigger = '回溫點到達'
                elif not _detected_tp:
                    # DTR countdown — only in development phase (after FC)
                    if timeindex[2] > 0 and len(timex) > timeindex[2] and len(timex) > timeindex[0]:
                        dev_time = timex[-1] - timex[timeindex[2]]
                        total_time = timex[-1] - timex[timeindex[0]]
                        dtr_pct = dev_time / total_time * 100.0 if total_time > 0 else 0.0
                        for thr in _DTR_THRESHOLDS:
                            thr_key = str(int(thr))
                            if thr_key not in self._dtr_warned and dtr_pct >= thr:
                                _detected_dtr = thr_key
                                _dtr_advice_text = _dtr_rule_advice(dtr_pct, bt, ror_bt)
                                force_trigger = f'DTR {dtr_pct:.0f}%'
                                break

                    if not _detected_dtr:
                        # Temperature milestone: first crossing of 110/120/130/140/150°C
                        # (only valid after TP — no Maillard phase before TP)
                        if self._tp_fired:
                            for temp in _TEMP_MILESTONES:
                                if temp not in self._triggered_temps and bt >= temp:
                                    _detected_temp = temp
                                    force_trigger = f'BT 達到 {temp:.0f}°C'
                                    break
                        # 30-second time mark (only if no other trigger took priority)
                        if _detected_temp is None:
                            seg = int(time_since_charge // 30)
                            if seg > self._last_time_segment:
                                _detected_seg = seg
                                mins, secs = divmod(int(time_since_charge), 60)
                                force_trigger = f'入豆後 {mins}分{secs:02d}秒 定時'

        with self._lock:
            if self._running_query:
                return
            if not force_trigger and now - self._last_query_time < self.interval:
                return
            self._running_query = True
            self._last_query_time = now
            # Commit auto-trigger state inside lock
            if _detected_tp:
                self._tp_fired = True
                self._last_time_segment = int(time_since_charge // 30)
            if _detected_temp is not None:
                self._triggered_temps.add(_detected_temp)
                self._last_time_segment = int(time_since_charge // 30)
            if _detected_seg >= 0:
                self._last_time_segment = _detected_seg
            if _detected_dtr is not None:
                self._dtr_warned.add(_detected_dtr)
                self._last_time_segment = int(time_since_charge // 30)
            if _detected_anomaly is not None:
                self._last_anomaly_time = now

        if self.on_query_start:
            self.on_query_start()

        try:
            stage = _get_stage_name(timeindex)

            bg_bt: float | None = None
            bg_ror: float | None = None
            if timeB and timeindexB and temp2B:
                bg_bt, bg_ror = _interp_background(
                    time_since_charge, timeB, timeindexB, temp2B, delta2B or [])

            try:
                dry_end = float(phases[1]) if phases and len(phases) > 1 and phases[1] is not None else 160.0
                fc_start = float(phases[2]) if phases and len(phases) > 2 and phases[2] is not None else 200.0
            except (TypeError, ValueError):
                dry_end, fc_start = 160.0, 200.0

            # Auto-triggers: rule engine only (instant, no API needed)
            # Key canvas events (CHARGE/FC/near_T1) also go through rule engine, not AI
            is_auto_trigger = (_detected_tp or _detected_temp is not None or _detected_seg >= 0
                               or _detected_dtr is not None or _detected_anomaly is not None
                               or force_trigger in _KEY_EVENT_TRIGGERS)
            if is_auto_trigger:
                if _detected_anomaly:
                    advice_text = _anomaly_advice_text
                elif _detected_dtr is not None:
                    advice_text = _dtr_advice_text
                else:
                    advice_text = _compute_rule_advice(bt, ror_bt, time_since_charge,
                                                       force_trigger, ror_values,
                                                       dry_end, fc_start,
                                                       bg_bt=bg_bt, bg_ror=bg_ror,
                                                       damper_cur=damper)
                t = threading.Thread(target=self._deliver_rule_advice,
                                     args=(advice_text,), daemon=True)
                t.start()
                return

            # Manual queries: send to AI with rule hint as context
            rule_hint = _compute_rule_advice(bt, ror_bt, time_since_charge,
                                             force_trigger, ror_values,
                                             dry_end, fc_start,
                                             bg_bt=bg_bt, bg_ror=bg_ror,
                                             damper_cur=damper)
            recent = [text for _, text in list(self.history)[-2:]]
            user_msg = _build_user_message(bt, et, ror_bt, ror_et, stage,
                                            time_since_charge, mode,
                                            bg_bt, bg_ror, ror_values, force_trigger,
                                            recent_advice=recent,
                                            rule_hint=rule_hint)

            t = threading.Thread(target=self._query_ai,
                                 args=(user_msg, ROAST_SYSTEM_PROMPT, _MAX_TOKENS, False),
                                 daemon=True)
            t.start()
        except Exception:  # pylint: disable=broad-except
            _log.exception('request_advice: unexpected error, releasing query lock')
            with self._lock:
                self._running_query = False

    def request_summary(self, summary_data: str) -> None:
        """Request a post-roast evaluation. Does not use the interval lock."""
        if not self.enabled:
            return
        t = threading.Thread(target=self._query_ai,
                             args=(summary_data, ROAST_SUMMARY_PROMPT, _SUMMARY_MAX_TOKENS, True),
                             daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _deliver_rule_advice(self, text: str) -> None:
        """Store and emit rule-computed advice, then release the query lock."""
        from datetime import datetime
        ts = datetime.now().strftime('%H:%M:%S')
        self.history.append((ts, text))
        if self.on_advice:
            self.on_advice(text)
        self._speak(self._prepare_tts(text))
        with self._lock:
            self._running_query = False

    def _speak(self, text: str) -> None:
        """Speak text via Windows SAPI (non-blocking). Skips if TTS busy or disabled."""
        if not self.tts_enabled or self._tts_busy or not text:
            return
        t = threading.Thread(target=self._tts_thread, args=(text,), daemon=True)
        t.start()

    def _prepare_tts(self, advice: str) -> str:
        """Return speech text according to current tts_mode, with optional urgency prefix."""
        if self.tts_mode == 'action':
            text = _action_for_tts(advice)
        else:
            text = _clean_for_tts(advice)
        if self.tts_prefix and text:
            urgent_kw = ('嚴重', '立即', '翻揚', '驟降', '崩潰', '上限', '過度發展')
            prefix = '注意！' if any(kw in advice for kw in urgent_kw) else '提示，'
            text = prefix + text
        return text

    def _tts_thread(self, text: str) -> None:
        self._tts_busy = True
        try:
            import platform
            is_mac = platform.system() == 'Darwin'
            provider = self.tts_provider

            if provider == 'auto':
                # Prefer Edge TTS if available, else fall back to platform default
                try:
                    import edge_tts as _et  # noqa: F401
                    provider = 'edge'
                except ImportError:
                    provider = 'say' if is_mac else 'sapi'

            if provider == 'edge':
                self._tts_edge(text)
            elif is_mac:
                self._tts_macos(text)
            else:
                self._tts_windows(text)
        finally:
            self._tts_busy = False

    # Default Edge TTS voices for Traditional Chinese
    _EDGE_VOICES_ZH_TW: tuple = (
        'zh-TW-HsiaoChenNeural',   # 女聲，自然親切
        'zh-TW-HsiaoYuNeural',     # 女聲，明亮
        'zh-TW-YunJheNeural',      # 男聲
    )

    # Keywords that trigger louder/slower TTS emphasis
    _ALERT_TTS_KW: Final[tuple] = (
        '注意', '嚴重', '先別', '不建議', '暫別開', '別讓', '趕快', '要快', '緊急',
    )

    def _tts_edge(self, text: str) -> None:
        """Neural TTS via Microsoft Edge.
        Alert segments (containing warning keywords) are spoken louder and slower.
        """
        import asyncio
        import os
        import re
        import tempfile

        try:
            import edge_tts  # type: ignore[import]
        except ImportError:
            _log.warning('edge-tts not installed, falling back to SAPI')
            self._tts_windows(text)
            return

        voice = self.tts_voice if (self.tts_voice and 'Neural' in self.tts_voice) \
            else self._EDGE_VOICES_ZH_TW[0]
        rate_pct = self.tts_rate * 10

        # Split into segments; alert segments get louder + slower prosody
        raw_parts = [p.strip() for p in re.split(r'[；;\n]', text) if p.strip()]
        segments: list[tuple[str, bool]] = [
            (p, any(kw in p for kw in self._ALERT_TTS_KW)) for p in raw_parts
        ]

        async def _gen_all(jobs: list[tuple[str, str, bool]]) -> None:
            for path, seg, is_alert in jobs:
                vol  = '+50%' if is_alert else '+0%'
                rate = f'{rate_pct - 15:+d}%' if is_alert else f'{rate_pct:+d}%'
                comm = edge_tts.Communicate(seg, voice, rate=rate, volume=vol)
                await comm.save(path)

        jobs = [(tempfile.mktemp(suffix='.mp3'), seg, is_alert)
                for seg, is_alert in segments]
        try:
            asyncio.run(_gen_all(jobs))
            for path, _, _ in jobs:
                self._play_mp3(path)
        except Exception as e:  # pylint: disable=broad-except
            _log.debug('Edge TTS failed: %s', e)
        finally:
            for path, _, _ in jobs:
                try:
                    os.unlink(path)
                except Exception:  # pylint: disable=broad-except
                    pass

    @staticmethod
    def _play_mp3(path: str) -> None:
        """Play an MP3 file synchronously using Windows MCI (no extra dependencies)."""
        import platform
        if platform.system() == 'Darwin':
            import subprocess
            subprocess.run(['afplay', path], capture_output=True)
            return
        try:
            import ctypes
            winmm = ctypes.windll.winmm  # type: ignore[attr-defined]
            safe = path.replace('/', '\\')
            winmm.mciSendStringW(f'open "{safe}" type mpegvideo alias _edge_snd', None, 0, None)
            winmm.mciSendStringW('play _edge_snd wait', None, 0, None)
            winmm.mciSendStringW('close _edge_snd', None, 0, None)
        except Exception as e:  # pylint: disable=broad-except
            _log.debug('MCI playback failed: %s', e)

    @staticmethod
    def list_edge_voices() -> 'list[str]':
        """Return available Edge TTS voices for zh-TW (async, blocking call)."""
        import asyncio
        try:
            import edge_tts  # type: ignore[import]
            async def _fetch():
                voices = await edge_tts.list_voices()
                return [v['ShortName'] for v in voices if v.get('Locale', '').startswith('zh-TW')]
            return asyncio.run(_fetch())
        except Exception:  # pylint: disable=broad-except
            return list(AIAdvisor._EDGE_VOICES_ZH_TW)

    def _tts_windows(self, text: str) -> None:
        try:
            import pythoncom  # type: ignore[import]
            pythoncom.CoInitialize()
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            import win32com.client  # type: ignore[import]
            speaker = win32com.client.Dispatch('SAPI.SpVoice')
            voices = speaker.GetVoices()
            selected = False
            # If a specific voice is configured, try to match by description
            if self.tts_voice:
                for i in range(voices.Count):
                    v = voices.Item(i)
                    if self.tts_voice in v.GetDescription():
                        speaker.Voice = v
                        selected = True
                        break
            if not selected:
                # Auto-select: prefer any Chinese voice
                for i in range(voices.Count):
                    v = voices.Item(i)
                    desc = v.GetDescription()
                    if 'Chinese' in desc or 'Hanhan' in desc or 'zh' in desc.lower():
                        speaker.Voice = v
                        break
            speaker.Rate = max(-10, min(10, self.tts_rate))
            speaker.Volume = max(0, min(100, self.tts_volume))
            speaker.Speak(text)
        except Exception as e:  # pylint: disable=broad-except
            _log.debug('TTS (Windows) failed: %s', e)
        finally:
            try:
                import pythoncom  # type: ignore[import]
                pythoncom.CoUninitialize()
            except Exception:  # pylint: disable=broad-except
                pass

    @staticmethod
    def list_available_voices() -> 'list[str]':
        """Return available voices: Edge neural voices first, then SAPI fallback."""
        results: list[str] = []
        # Edge voices
        try:
            results = AIAdvisor.list_edge_voices()
        except Exception:  # pylint: disable=broad-except
            pass
        # SAPI voices
        try:
            import pythoncom  # type: ignore[import]
            pythoncom.CoInitialize()
            try:
                import win32com.client  # type: ignore[import]
                speaker = win32com.client.Dispatch('SAPI.SpVoice')
                voices = speaker.GetVoices()
                sapi = [voices.Item(i).GetDescription() for i in range(voices.Count)]
                results += sapi
            finally:
                pythoncom.CoUninitialize()
        except Exception:  # pylint: disable=broad-except
            pass
        return results

    def _tts_macos(self, text: str) -> None:
        import subprocess
        # Map tts_rate (-5…+5) to words-per-minute (100…250)
        wpm = 175 + self.tts_rate * 15
        for voice in ('Mei-Jia', 'Ting-Ting', None):
            cmd = ['say', '-r', str(wpm)]
            if voice:
                cmd += ['-v', voice]
            cmd.append(text)
            try:
                result = subprocess.run(cmd, timeout=60, capture_output=True)
                if result.returncode == 0:
                    return
            except Exception as e:  # pylint: disable=broad-except
                _log.debug('TTS (macOS) voice %r failed: %s', voice, e)
        _log.debug('TTS (macOS): all voice attempts failed')

    def _build_messages(self, user_msg: str) -> 'list[dict]':
        """Build multi-turn message list including conversation history."""
        msgs = []
        for prev_user, prev_assistant in self._conversation_history:
            msgs.append({'role': 'user',      'content': prev_user})
            msgs.append({'role': 'assistant', 'content': prev_assistant})
        msgs.append({'role': 'user', 'content': user_msg})
        return msgs

    def _build_gemini_contents(self, user_msg: str) -> 'list[dict]':
        """Build Gemini contents array with conversation history."""
        contents = []
        for prev_user, prev_assistant in self._conversation_history:
            contents.append({'role': 'user',  'parts': [{'text': prev_user}]})
            contents.append({'role': 'model', 'parts': [{'text': prev_assistant}]})
        contents.append({'role': 'user', 'parts': [{'text': user_msg}]})
        return contents

    def _query_ai(self, user_msg: str, system_prompt: str,
                  max_tokens: int, is_summary: bool) -> None:
        try:
            advice = self._call_provider(user_msg, system_prompt, max_tokens)
            if advice:
                advice = advice.strip()
                if not is_summary:
                    missing = [s for s in ('現況：', '操作：', '預期：') if s not in advice]
                    if missing:
                        _log.warning('AI response missing sections %s — raw: %r', missing, advice[:200])
                if is_summary:
                    advice = f'📊 烘後評估\n{advice}'
                else:
                    self._conversation_history.append((user_msg, advice))
                ts = time.strftime('%H:%M:%S')
                self.history.append((ts, advice))
                if self.on_advice:
                    self.on_advice(advice)
                if not is_summary:
                    self._speak(self._prepare_tts(advice))
        except Exception as e:  # pylint: disable=broad-except
            _log.exception('AI advisor query failed: %s', e)
        finally:
            with self._lock:
                self._running_query = False

    def _call_provider(self, user_msg: str, system_prompt: str, max_tokens: int) -> str:
        if self.provider == AIProvider.OLLAMA:
            return self._call_ollama(user_msg, system_prompt, max_tokens)
        if self.provider == AIProvider.CLAUDE:
            return self._call_claude(user_msg, system_prompt, max_tokens)
        if self.provider == AIProvider.OPENAI:
            return self._call_openai(user_msg, system_prompt, max_tokens)
        if self.provider == AIProvider.GEMINI:
            return self._call_gemini(user_msg, system_prompt, max_tokens)
        return ''

    @staticmethod
    def _http_post(url: str, payload: dict, headers: dict, timeout: int = 20) -> dict:
        import urllib.request
        import urllib.error
        _HTTP_ERRORS = {
            400: '400 Bad Request — 請求格式錯誤',
            401: '401 Unauthorized — API Key 無效或未授權',
            403: '403 Forbidden — API Key 無權限',
            429: '429 Too Many Requests — 已超過速率限制',
            500: '500 Internal Server Error — 伺服器錯誤',
            503: '503 Service Unavailable — 服務暫時不可用',
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', **headers})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode('utf-8'))
            except urllib.error.HTTPError as e:
                if e.code in (429, 503) and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                detail = _HTTP_ERRORS.get(e.code, f'HTTP {e.code}')
                try:
                    body = json.loads(e.read().decode('utf-8'))
                    msg = (body.get('error', {}).get('message') or body.get('message') or '')
                    if msg:
                        detail = f'{detail}: {msg}'
                except Exception:  # pylint: disable=broad-except
                    pass
                raise RuntimeError(detail) from e
        raise RuntimeError('請求失敗，已重試 3 次（429/503）')

    def _call_ollama(self, user_msg: str, system_prompt: str, max_tokens: int) -> str:
        url = self.ollama_url.rstrip('/') + '/api/chat'
        messages = [{'role': 'system', 'content': system_prompt}] + self._build_messages(user_msg)
        payload = {
            'model': self.model,
            'messages': messages,
            'stream': False,
            'options': {'num_predict': max_tokens},
        }
        result = self._http_post(url, payload, {})
        return result.get('message', {}).get('content', '')

    def _call_openai(self, user_msg: str, system_prompt: str, max_tokens: int) -> str:
        url = 'https://api.openai.com/v1/chat/completions'
        messages = [{'role': 'system', 'content': system_prompt}] + self._build_messages(user_msg)
        payload = {
            'model': self.model or 'gpt-4o-mini',
            'messages': messages,
            'max_tokens': max_tokens,
        }
        result = self._http_post(url, payload, {'Authorization': f'Bearer {self.api_key}'})
        return result['choices'][0]['message']['content']

    def _call_gemini(self, user_msg: str, system_prompt: str, max_tokens: int) -> str:
        model = self.model or 'gemini-2.5-flash'
        url = (f'https://generativelanguage.googleapis.com/v1beta/models/'
               f'{model}:generateContent?key={self.api_key}')
        payload = {
            'system_instruction': {'parts': [{'text': system_prompt}]},
            'contents': self._build_gemini_contents(user_msg),
            'generationConfig': {
                'maxOutputTokens': max_tokens,
                'thinkingConfig': {'thinkingBudget': 0},  # disable thinking to preserve output tokens
            },
        }
        result = self._http_post(url, payload, {})
        return result['candidates'][0]['content']['parts'][0]['text']

    def _call_claude(self, user_msg: str, system_prompt: str, max_tokens: int) -> str:
        url = 'https://api.anthropic.com/v1/messages'
        payload = {
            'model': self.model or 'claude-haiku-4-5-20251001',
            'max_tokens': max_tokens,
            'system': system_prompt,
            'messages': self._build_messages(user_msg),
        }
        result = self._http_post(url, payload, {
            'x-api-key': self.api_key,
            'anthropic-version': '2023-06-01',
        })
        return result['content'][0]['text']
