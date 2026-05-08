#
# ABOUT
# Excel Roasting Plan Importer
# Auto-detects the plan table and loads BT curve as background profile.

from qtpy.QtWidgets import (
    QApplication, QDialogButtonBox, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QComboBox, QGroupBox, QPushButton,
    QFileDialog, QTableWidget, QTableWidgetItem, QWidget, QHeaderView
)
from qtpy.QtCore import Qt
from artisanlib.dialogs import ArtisanDialog

import logging
import numpy
import os
from typing import Final, TYPE_CHECKING
if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow

_log: Final[logging.Logger] = logging.getLogger(__name__)
_NONE = '-- 無 --'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_time(val) -> 'float | None':
    """Accept seconds (int/float) or mm:ss / h:mm:ss string."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if ':' in s:
        parts = s.split(':')
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except (ValueError, IndexError):
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _row_label(row: list) -> str:
    if not row or row[0] is None:
        return ''
    return str(row[0]).strip()


def _auto_parse(rows: 'list[list]') -> 'dict | None':
    """Try to extract timex + BT from rows automatically.
    Returns dict with keys: timex, temp2, timeindex, info — or None on failure.
    """
    # 1. Find the 時間 row
    time_row_idx = None
    for i, row in enumerate(rows):
        label = _row_label(row).lower()
        if '時間' not in label and 'time' not in label:
            continue
        times = [_parse_time(v) for v in row[1:] if v is not None]
        times = [t for t in times if t is not None]
        if len(times) >= 4:
            time_row_idx = i
            break
    if time_row_idx is None:
        return None

    time_row = rows[time_row_idx]
    # Build (col_index, time_seconds) pairs — skip column 0 (label)
    col_times: list[tuple[int, float]] = []
    for c in range(1, len(time_row)):
        t = _parse_time(time_row[c])
        if t is not None:
            col_times.append((c, t))
    if len(col_times) < 2:
        return None

    # 2. Find the 預計 BT row (skip 實際/blank rows)
    bt_row = None
    for row in rows[time_row_idx + 1:]:
        label = _row_label(row)
        label_l = label.lower()
        if not label or '實際' in label or '備註' in label:
            continue
        if 'bt' in label_l or '豆溫' in label or 'bean' in label_l:
            # verify it has numeric values
            numeric = [row[c] for c, _ in col_times
                       if c < len(row) and isinstance(row[c], (int, float))]
            if len(numeric) >= 4:
                bt_row = row
                break
    if bt_row is None:
        return None

    # 3. Build arrays
    timex: list[float] = []
    temp2: list[float] = []
    for c, t in col_times:
        bt = bt_row[c] if c < len(bt_row) else None
        if bt is None:
            continue
        try:
            temp2.append(float(bt))
            timex.append(t)
        except (ValueError, TypeError):
            continue
    if len(timex) < 2:
        return None

    # 4. timeindex: CHARGE = first point
    timeindex = [0, 0, 0, 0, 0, 0, 0, 0]

    # 5. Build summary string
    info = (f'時間 {len(timex)} 點（{timex[0]:.0f}s → {timex[-1]:.0f}s），'
            f'BT {temp2[0]:.0f} → {temp2[-1]:.0f}°C，'
            f'來源列：「{_row_label(rows[time_row_idx])}」+「{_row_label(bt_row)}」')

    return {'timex': timex, 'temp2': temp2, 'timeindex': timeindex, 'info': info}


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class ExcelImportDialog(ArtisanDialog):
    """Import an Excel roasting plan as a background profile.
    Auto-detects the plan table; falls back to manual mapping on failure.
    """

    def __init__(self, parent: QWidget, aw: 'ApplicationWindow') -> None:
        super().__init__(parent, aw)
        self.aw = aw
        self.setWindowTitle(QApplication.translate('Dialog', '匯入 Excel 烘焙計畫'))
        self.setMinimumWidth(540)
        self._rows: list[list] = []
        self._headers: list[str] = []
        self._filepath: str = ''
        self._auto_result: 'dict | None' = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # File row
        fileRow = QHBoxLayout()
        self._fileLabel = QLabel('尚未選擇檔案')
        self._fileLabel.setWordWrap(True)
        fileRow.addWidget(self._fileLabel, 1)
        browseBtn = QPushButton('瀏覽...')
        browseBtn.clicked.connect(self._browse)
        fileRow.addWidget(browseBtn)
        layout.addLayout(fileRow)

        # Preview table
        self._table = QTableWidget(0, 0)
        self._table.setMaximumHeight(130)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table)

        # Auto-detect result box
        self._autoBox = QGroupBox('自動偵測結果')
        autoLayout = QVBoxLayout(self._autoBox)
        self._autoLabel = QLabel('（尚未選擇檔案）')
        self._autoLabel.setWordWrap(True)
        autoLayout.addWidget(self._autoLabel)
        layout.addWidget(self._autoBox)

        # Manual override (hidden when auto succeeds)
        self._manualBox = QGroupBox('手動欄位對應（自動偵測失敗時使用）')
        manForm = QFormLayout(self._manualBox)
        self._hTimeCombo = QComboBox()
        manForm.addRow('時間列：', self._hTimeCombo)
        self._hBtCombo = QComboBox()
        manForm.addRow('豆溫 BT 列：', self._hBtCombo)
        self._manualBox.setVisible(False)
        layout.addWidget(self._manualBox)

        self._statusLabel = QLabel('')
        self._statusLabel.setWordWrap(True)
        layout.addWidget(self._statusLabel)

        buttonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._okBtn = buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        self._okBtn.setText('載入為背景曲線')
        self._okBtn.setEnabled(False)
        buttonBox.accepted.connect(self._import)
        buttonBox.rejected.connect(self.reject)
        layout.addWidget(buttonBox)

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, '選擇 Excel 烘焙計畫', '',
            'Excel Files (*.xlsx *.xls)')
        if not path:
            return
        self._filepath = path
        self._fileLabel.setText(os.path.basename(path))
        self._load_excel(path)

    def _load_excel(self, path: str) -> None:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            ws = wb.active
            raw = list(ws.iter_rows(values_only=True))
            wb.close()
            self._rows = [list(r) for r in raw if any(v is not None for v in r)]
            if not self._rows:
                self._statusLabel.setText('❌ 檔案為空')
                return
            self._headers = [str(c) if c is not None else f'欄{i+1}'
                             for i, c in enumerate(self._rows[0])]
            self._populate_preview()
            self._run_auto_detect()
        except ImportError:
            self._statusLabel.setText('❌ 需要安裝 openpyxl：pip install openpyxl')
        except Exception as e:  # pylint: disable=broad-except
            self._statusLabel.setText(f'❌ 讀取失敗：{e}')

    def _populate_preview(self) -> None:
        preview = self._rows[:6]
        ncols = max(len(r) for r in preview) if preview else 0
        self._table.setColumnCount(ncols)
        self._table.setRowCount(len(preview))
        self._table.setHorizontalHeaderLabels([str(i) for i in range(ncols)])
        for r, row in enumerate(preview):
            for c, val in enumerate(row):
                item = QTableWidgetItem(str(val) if val is not None else '')
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(r, c, item)

    def _run_auto_detect(self) -> None:
        self._auto_result = _auto_parse(self._rows)
        # Trim metadata rows above the 時間 row so preview shows only plan data
        for i, row in enumerate(self._rows):
            label = _row_label(row).lower()
            if ('時間' in label or 'time' in label):
                times = [_parse_time(v) for v in row[1:] if v is not None]
                if len([t for t in times if t is not None]) >= 4:
                    if i > 0:
                        self._rows = self._rows[i:]
                        self._populate_preview()
                    break
        if self._auto_result:
            self._autoLabel.setText(f'✅ 找到計畫表\n{self._auto_result["info"]}')
            self._manualBox.setVisible(False)
            self._okBtn.setEnabled(True)
            self._statusLabel.setText('')
        else:
            self._autoLabel.setText('⚠️ 無法自動定位計畫表，請手動設定欄位對應')
            self._populate_manual_combos()
            self._manualBox.setVisible(True)
            self._okBtn.setEnabled(True)

    # ------------------------------------------------------------------
    # Manual fallback
    # ------------------------------------------------------------------

    def _populate_manual_combos(self) -> None:
        labels = [str(r[0]) if r and r[0] is not None else f'列{i+1}'
                  for i, r in enumerate(self._rows)]
        opts = [_NONE] + labels
        for combo in (self._hTimeCombo, self._hBtCombo):
            combo.clear()
            combo.addItems(opts)
        for i, label in enumerate(labels):
            ll = label.lower()
            if self._hTimeCombo.currentIndex() == 0 and ('時間' in label or 'time' in ll):
                self._hTimeCombo.setCurrentIndex(i + 1)
            if self._hBtCombo.currentIndex() == 0 and ('bt' in ll or '豆溫' in label):
                self._hBtCombo.setCurrentIndex(i + 1)

    def _manual_parse(self) -> 'dict | None':
        def _ri(combo: QComboBox) -> 'int | None':
            txt = combo.currentText()
            if txt == _NONE:
                return None
            labels = [str(r[0]) if r and r[0] is not None else '' for r in self._rows]
            try:
                return labels.index(txt)
            except ValueError:
                return None

        tri = _ri(self._hTimeCombo)
        bri = _ri(self._hBtCombo)
        if tri is None or bri is None:
            return None
        time_row = self._rows[tri]
        bt_row   = self._rows[bri]
        timex, temp2 = [], []
        for c in range(1, min(len(time_row), len(bt_row))):
            t  = _parse_time(time_row[c])
            bt = bt_row[c]
            if t is None or bt is None:
                continue
            try:
                temp2.append(float(bt))
                timex.append(t)
            except (ValueError, TypeError):
                continue
        if len(timex) < 2:
            return None
        return {'timex': timex, 'temp2': temp2,
                'timeindex': [0, 0, 0, 0, 0, 0, 0, 0], 'info': ''}

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def _import(self) -> None:
        result = self._auto_result or self._manual_parse()
        if not result:
            self._statusLabel.setText('❌ 無法取得有效資料，請確認欄位對應')
            return
        timex = list(result['timex'])
        temp2 = list(result['temp2'])

        # Pre-charge buffer
        pre = [timex[0] - 60.0, timex[0] - 30.0]
        timex = pre + timex
        temp2 = [temp2[0], temp2[0]] + temp2
        charge = len(pre)  # = 2
        n = len(timex)
        temp1 = list(temp2)  # use BT as ET placeholder

        # Infer event indices from BT thresholds
        dry_end_idx = fc_idx = 0
        for i in range(charge + 1, n):
            bt = temp2[i]
            if dry_end_idx == 0 and bt >= 160.0:
                dry_end_idx = i
            if fc_idx == 0 and bt >= 196.0:
                fc_idx = i
                break
        timeindexB = [charge, dry_end_idx, fc_idx, 0, 0, 0, n - 1, 0]

        # Smooth curves using the same method as loadbackground
        try:
            decay_p = not self.aw.qmc.optimalSmoothing
            tb_lin = numpy.linspace(timex[0], timex[-1], n)
            stemp1 = self.aw.qmc.smooth_list(
                timex, temp1, window_len=self.aw.qmc.curvefilter,
                decay_smoothing=decay_p, a_lin=tb_lin)
            stemp2 = self.aw.qmc.smooth_list(
                timex, temp2, window_len=self.aw.qmc.curvefilter,
                decay_smoothing=decay_p, a_lin=tb_lin)
        except Exception:  # pylint: disable=broad-except
            stemp1 = numpy.array(temp1)
            stemp2 = numpy.array(temp2)

        title = os.path.splitext(os.path.basename(self._filepath))[0]
        # Average sampling interval from the actual plan data (exclude pre-charge)
        plan_n = n - charge
        sampling_interval = (timex[-1] - timex[charge]) / (plan_n - 1) if plan_n > 1 else 1.0

        try:
            qmc = self.aw.qmc
            qmc.resetlinecountcaches()
            qmc.deleteAnnoPositions(foreground=False, background=True)
            # Temperature arrays
            qmc.temp1B = temp1
            qmc.temp2B = temp2
            qmc.timeB  = timex
            qmc.abs_timeB = timex[:]
            qmc.temp1BX = []
            qmc.temp2BX = []
            qmc.extratimexB = []
            qmc.stemp1B = stemp1
            qmc.stemp2B = stemp2
            qmc.stemp1BX = []
            qmc.stemp2BX = []
            # RoR delta arrays — reset so smoothETBTBkgnd recomputes them on redraw
            qmc.delta1B = []
            qmc.delta2B = []
            qmc.background_profile_sampling_interval = sampling_interval
            # Event markers
            qmc.timeindexB = timeindexB
            # Metadata
            qmc.backgroundEvents = []
            qmc.backgroundEtypes = []
            qmc.backgroundEvalues = []
            qmc.backgroundEStrings = []
            qmc.backgroundFlavors = [5.0] * 10
            qmc.extraname1B = []
            qmc.extraname2B = []
            qmc.titleB = title           # 直接賦值 Unicode，不需要 encode
            qmc.roastbatchnrB = 0
            qmc.roastbatchprefixB = ''
            qmc.roastbatchposB = 1
            qmc.backgroundpath = self._filepath
            qmc.backgroundUUID = None
            qmc.TP_time_B_loaded = None
            qmc.backgroundprofile = {}   # non-None sentinel
            qmc.backgroundprofile_moved_x = 0
            qmc.backgroundprofile_moved_y = 0
            qmc.background = not qmc.hideBgafterprofileload
            qmc.timealign(redraw=False)
            qmc.redraw()
            self.accept()
        except Exception as e:  # pylint: disable=broad-except
            self._statusLabel.setText(f'❌ 載入失敗：{e}')
