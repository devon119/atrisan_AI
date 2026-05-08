# pylint: disable=reimported
#
# ABOUT
# AI Advisor Configuration Dialog

from qtpy.QtWidgets import (
    QApplication, QDialogButtonBox, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QLineEdit, QComboBox, QSpinBox,
    QCheckBox, QGroupBox, QPushButton, QWidget, QTextEdit,
    QSlider, QRadioButton, QButtonGroup
)
from qtpy.QtCore import Qt
from qtpy.QtCore import Slot, Signal, QUrl
from qtpy.QtGui import QDesktopServices
from artisanlib.dialogs import ArtisanDialog
from artisanlib.ai_advisor import AIProvider

import logging
from typing import Final, TYPE_CHECKING
if TYPE_CHECKING:
    from artisanlib.main import ApplicationWindow

_log: Final[logging.Logger] = logging.getLogger(__name__)

PROVIDER_LABELS = {
    AIProvider.OLLAMA: 'Ollama (Local)',
    AIProvider.CLAUDE: 'Claude (Anthropic)',
    AIProvider.OPENAI: 'OpenAI',
    AIProvider.GEMINI: 'Google Gemini',
}

DEFAULT_MODELS = {
    AIProvider.OLLAMA: ['llama3', 'llama3.1', 'mistral', 'gemma3', 'phi3'],
    AIProvider.CLAUDE: ['claude-haiku-4-5-20251001', 'claude-sonnet-4-6', 'claude-opus-4-7'],
    AIProvider.OPENAI: ['gpt-4o-mini', 'gpt-4o', 'gpt-4-turbo'],
    AIProvider.GEMINI: ['gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-1.5-flash', 'gemini-1.5-pro'],
}

API_KEY_URLS = {
    AIProvider.CLAUDE: 'https://console.anthropic.com/settings/keys',
    AIProvider.OPENAI: 'https://platform.openai.com/api-keys',
    AIProvider.GEMINI: 'https://aistudio.google.com/app/apikey',
}


class AIAdvisorDialog(ArtisanDialog):

    _resultSignal  = Signal(str, str)        # (text, color)
    _modelsSignal  = Signal(list)            # list[str] of model names

    def __init__(self, parent: QWidget, aw: 'ApplicationWindow') -> None:
        super().__init__(parent, aw)
        self.aw = aw
        self.setWindowTitle(QApplication.translate('Dialog', 'AI Roasting Advisor'))
        self.setMinimumWidth(420)
        self._build_ui()
        self._resultSignal.connect(self._set_result)
        self._modelsSignal.connect(self._on_gemini_models)
        self._load_from_advisor()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Enable checkbox
        self.enabledCheck = QCheckBox(QApplication.translate('Label', 'Enable AI Advisor'))
        layout.addWidget(self.enabledCheck)
        self.enabledCheck.toggled.connect(self._on_enabled_toggled)

        # Provider group
        providerGroup = QGroupBox(QApplication.translate('Label', 'AI Provider'))
        provForm = QFormLayout(providerGroup)

        self.providerCombo = QComboBox()
        for p, label in PROVIDER_LABELS.items():
            self.providerCombo.addItem(label, p)
        provForm.addRow(QApplication.translate('Label', 'Provider:'), self.providerCombo)
        self.providerCombo.currentIndexChanged.connect(self._on_provider_changed)

        self.modelCombo = QComboBox()
        self.modelCombo.setEditable(True)
        provForm.addRow(QApplication.translate('Label', 'Model:'), self.modelCombo)

        self.apiKeyEdit = QLineEdit()
        self.apiKeyEdit.setEchoMode(QLineEdit.EchoMode.Password)
        self.apiKeyEdit.setPlaceholderText('sk-...')
        self.apiKeyLabel = QLabel(QApplication.translate('Label', 'API Key:'))

        apiKeyRow = QHBoxLayout()
        apiKeyRow.addWidget(self.apiKeyEdit)
        self.getApiKeyBtn = QPushButton(QApplication.translate('Button', 'Get API Key ↗'))
        self.getApiKeyBtn.setFixedWidth(110)
        self.getApiKeyBtn.clicked.connect(self._open_api_key_page)
        apiKeyRow.addWidget(self.getApiKeyBtn)
        provForm.addRow(self.apiKeyLabel, apiKeyRow)

        self.ollamaUrlEdit = QLineEdit()
        self.ollamaUrlEdit.setPlaceholderText('http://localhost:11434')
        self.ollamaUrlLabel = QLabel(QApplication.translate('Label', 'Ollama URL:'))
        provForm.addRow(self.ollamaUrlLabel, self.ollamaUrlEdit)

        layout.addWidget(providerGroup)

        # Interval + TTS
        intervalGroup = QGroupBox(QApplication.translate('Label', 'Query Settings'))
        intervalForm = QFormLayout(intervalGroup)
        self.intervalSpin = QSpinBox()
        self.intervalSpin.setRange(10, 300)
        self.intervalSpin.setSuffix(QApplication.translate('Label', ' sec'))
        intervalForm.addRow(QApplication.translate('Label', 'Query interval:'), self.intervalSpin)

        layout.addWidget(intervalGroup)

        # TTS group
        ttsGroup = QGroupBox('語音播報')
        ttsForm = QFormLayout(ttsGroup)

        self.ttsCheck = QCheckBox('啟用語音播報')
        ttsForm.addRow(self.ttsCheck)

        # mode radios
        modeRow = QHBoxLayout()
        self._ttsModeFull   = QRadioButton('完整播報（現況／操作／預期）')
        self._ttsModeAction = QRadioButton('僅播操作那行')
        self._ttsModeGroup  = QButtonGroup(self)
        self._ttsModeGroup.addButton(self._ttsModeFull,   0)
        self._ttsModeGroup.addButton(self._ttsModeAction, 1)
        self._ttsModeFull.setChecked(True)
        modeRow.addWidget(self._ttsModeFull)
        modeRow.addWidget(self._ttsModeAction)
        ttsForm.addRow(modeRow)

        # rate slider
        rateRow = QHBoxLayout()
        rateRow.addWidget(QLabel('慢'))
        self.ttsRateSlider = QSlider(Qt.Orientation.Horizontal)
        self.ttsRateSlider.setRange(-5, 5)
        self.ttsRateSlider.setValue(0)
        self.ttsRateSlider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.ttsRateSlider.setTickInterval(1)
        rateRow.addWidget(self.ttsRateSlider)
        rateRow.addWidget(QLabel('快'))
        self.ttsRateLabel = QLabel('0')
        self.ttsRateLabel.setFixedWidth(20)
        rateRow.addWidget(self.ttsRateLabel)
        self.ttsRateSlider.valueChanged.connect(lambda v: self.ttsRateLabel.setText(str(v)))
        ttsForm.addRow('語速：', rateRow)

        # volume slider (Windows only)
        import platform as _plt
        self._volRow = QHBoxLayout()
        self._volRow.addWidget(QLabel('小'))
        self.ttsVolSlider = QSlider(Qt.Orientation.Horizontal)
        self.ttsVolSlider.setRange(0, 100)
        self.ttsVolSlider.setValue(80)
        self.ttsVolSlider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.ttsVolSlider.setTickInterval(10)
        self._volRow.addWidget(self.ttsVolSlider)
        self._volRow.addWidget(QLabel('大'))
        self.ttsVolLabel = QLabel('80')
        self.ttsVolLabel.setFixedWidth(28)
        self._volRow.addWidget(self.ttsVolLabel)
        self.ttsVolSlider.valueChanged.connect(lambda v: self.ttsVolLabel.setText(str(v)))
        self._volWidget = QWidget()
        self._volWidget.setLayout(self._volRow)
        self._volWidget.setVisible(_plt.system() == 'Windows')
        ttsForm.addRow('音量：', self._volWidget)

        # urgency prefix checkbox
        self.ttsPrefixCheck = QCheckBox('朗讀前加「注意！」或「提示，」')
        self.ttsPrefixCheck.setChecked(True)
        ttsForm.addRow(self.ttsPrefixCheck)

        # voice selector (Windows only; populated lazily when TTS group is shown)
        import platform as _plt2
        self._voiceRow = QHBoxLayout()
        self.ttsVoiceCombo = QComboBox()
        self.ttsVoiceCombo.addItem('自動選擇（優先中文）', '')
        self._voiceRow.addWidget(self.ttsVoiceCombo)
        self._refreshVoiceBtn = QPushButton('↻')
        self._refreshVoiceBtn.setFixedWidth(28)
        self._refreshVoiceBtn.setToolTip('重新列出語音')
        self._refreshVoiceBtn.clicked.connect(self._refresh_voices)
        self._voiceRow.addWidget(self._refreshVoiceBtn)
        self._voiceWidget = QWidget()
        self._voiceWidget.setLayout(self._voiceRow)
        self._voiceWidget.setVisible(_plt2.system() == 'Windows')
        ttsForm.addRow('語音：', self._voiceWidget)

        # test button
        self.ttsTestBtn = QPushButton('▶ 測試語音')
        self.ttsTestBtn.clicked.connect(self._test_tts)
        ttsForm.addRow(self.ttsTestBtn)
        layout.addWidget(ttsGroup)

        # Test / List Models / Simulate buttons
        btnRow = QHBoxLayout()
        testBtn = QPushButton(QApplication.translate('Button', 'Test Connection'))
        testBtn.clicked.connect(self._test_connection)
        btnRow.addWidget(testBtn)
        self.listModelsBtn = QPushButton(QApplication.translate('Button', 'List Models ↓'))
        self.listModelsBtn.clicked.connect(self._list_gemini_models)
        self.listModelsBtn.setVisible(False)
        btnRow.addWidget(self.listModelsBtn)
        simulateBtn = QPushButton(QApplication.translate('Button', '模擬烘豆查詢'))
        simulateBtn.clicked.connect(self._simulate_roast_query)
        btnRow.addWidget(simulateBtn)
        layout.addLayout(btnRow)

        simWindowBtn = QPushButton('🧪 開啟模擬烘豆器')
        simWindowBtn.clicked.connect(self._open_sim_window)
        layout.addWidget(simWindowBtn)
        self.testResultLabel = QTextEdit()
        self.testResultLabel.setReadOnly(True)
        self.testResultLabel.setMinimumHeight(120)
        layout.addWidget(self.testResultLabel)

        # Buttons
        buttonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttonBox.accepted.connect(self._save_and_close)
        buttonBox.rejected.connect(self.reject)
        layout.addWidget(buttonBox)

    def _load_from_advisor(self) -> None:
        advisor = self.aw.ai_advisor
        self.enabledCheck.setChecked(advisor.enabled)
        idx = self.providerCombo.findData(advisor.provider)
        if idx >= 0:
            self.providerCombo.setCurrentIndex(idx)
        self._update_model_list(advisor.provider)
        self.modelCombo.setCurrentText(advisor.model)
        self.apiKeyEdit.setText(advisor.api_key)
        self.ollamaUrlEdit.setText(advisor.ollama_url)
        self.intervalSpin.setValue(advisor.interval)
        self.ttsCheck.setChecked(advisor.tts_enabled)
        self._ttsModeFull.setChecked(advisor.tts_mode == 'full')
        self._ttsModeAction.setChecked(advisor.tts_mode == 'action')
        self.ttsRateSlider.setValue(advisor.tts_rate)
        self.ttsVolSlider.setValue(advisor.tts_volume)
        self.ttsPrefixCheck.setChecked(advisor.tts_prefix)
        self._refresh_voices()
        # Select saved voice
        idx = self.ttsVoiceCombo.findData(advisor.tts_voice)
        if idx >= 0:
            self.ttsVoiceCombo.setCurrentIndex(idx)
        self._on_enabled_toggled(advisor.enabled)

    def _update_model_list(self, provider: AIProvider) -> None:
        self.modelCombo.clear()
        for m in DEFAULT_MODELS.get(provider, []):
            self.modelCombo.addItem(m)
        is_ollama = provider == AIProvider.OLLAMA
        needs_key = provider in (AIProvider.CLAUDE, AIProvider.OPENAI, AIProvider.GEMINI)
        self.ollamaUrlLabel.setVisible(is_ollama)
        self.ollamaUrlEdit.setVisible(is_ollama)
        self.apiKeyLabel.setVisible(needs_key)
        self.apiKeyEdit.setVisible(needs_key)
        self.getApiKeyBtn.setVisible(needs_key and provider in API_KEY_URLS)
        self.listModelsBtn.setVisible(provider == AIProvider.GEMINI)

    @Slot()
    def _open_api_key_page(self) -> None:
        provider = self.providerCombo.currentData()
        url = API_KEY_URLS.get(provider)
        if url:
            QDesktopServices.openUrl(QUrl(url))

    @Slot(int)
    def _on_provider_changed(self, _: int) -> None:
        provider = self.providerCombo.currentData()
        if provider:
            self._update_model_list(provider)

    @Slot(bool)
    def _on_enabled_toggled(self, enabled: bool) -> None:
        self.aw.aiAdvisorWindow.setVisible(enabled)

    @Slot(str, str)
    def _set_result(self, text: str, color: str) -> None:
        self.testResultLabel.setPlainText(text)
        self.testResultLabel.setStyleSheet(f'QTextEdit {{ color: {color}; }}')

    @Slot()
    def _refresh_voices(self) -> None:
        import platform as _plt3
        if _plt3.system() != 'Windows':
            return
        from artisanlib.ai_advisor import AIAdvisor
        voices = AIAdvisor.list_available_voices()
        current_data = self.ttsVoiceCombo.currentData()
        self.ttsVoiceCombo.clear()
        self.ttsVoiceCombo.addItem('自動選擇（優先中文）', '')
        for v in voices:
            self.ttsVoiceCombo.addItem(v, v)
        # Restore selection
        idx = self.ttsVoiceCombo.findData(current_data)
        if idx >= 0:
            self.ttsVoiceCombo.setCurrentIndex(idx)

    def _apply_advisor_settings(self) -> None:
        advisor = self.aw.ai_advisor
        advisor.provider = self.providerCombo.currentData()
        advisor.api_key = self.apiKeyEdit.text().strip()
        advisor.model = self.modelCombo.currentText().strip()
        advisor.ollama_url = self.ollamaUrlEdit.text().strip()
        advisor.tts_enabled = self.ttsCheck.isChecked()
        advisor.tts_mode = 'action' if self._ttsModeAction.isChecked() else 'full'
        advisor.tts_rate = self.ttsRateSlider.value()
        advisor.tts_volume = self.ttsVolSlider.value()
        advisor.tts_prefix = self.ttsPrefixCheck.isChecked()
        advisor.tts_voice = self.ttsVoiceCombo.currentData() or ''

    @Slot()
    def _test_connection(self) -> None:
        import threading
        self._apply_advisor_settings()
        self._resultSignal.emit(QApplication.translate('Message', 'Testing...'), 'gray')
        advisor = self.aw.ai_advisor

        def _run() -> None:
            try:
                reply = advisor._call_provider('Say "OK" in one word.', 'You are a test assistant.', 50)  # pylint: disable=protected-access
                self._resultSignal.emit(f'✅ {reply[:80]}', 'green')
            except Exception as e:  # pylint: disable=broad-except
                self._resultSignal.emit(f'❌ {e}', 'red')

        threading.Thread(target=_run, daemon=True).start()

    @Slot()
    def _simulate_roast_query(self) -> None:
        import threading
        from artisanlib.ai_advisor import _build_user_message
        self._apply_advisor_settings()
        self._resultSignal.emit('模擬查詢中...', 'gray')
        advisor = self.aw.ai_advisor

        from artisanlib.ai_advisor import ROAST_SYSTEM_PROMPT, _MAX_TOKENS
        user_msg = _build_user_message(
            bt=185.0, et=220.0, ror_bt=8.5, ror_et=5.2,
            stage='development (FC started)', time_since_charge=510.0,
            mode='C',
            ror_values=[10.2, 9.8, 9.5, 9.1, 8.8, 8.5],
            trigger='模擬測試',
        )

        def _run() -> None:
            try:
                reply = advisor._call_provider(user_msg, ROAST_SYSTEM_PROMPT, _MAX_TOKENS)  # pylint: disable=protected-access
                self._resultSignal.emit(f'🤖 {reply}', '#006600')
            except Exception as e:  # pylint: disable=broad-except
                self._resultSignal.emit(f'❌ {e}', 'red')

        threading.Thread(target=_run, daemon=True).start()

    @Slot(list)
    def _on_gemini_models(self, names: list) -> None:
        for n in names:
            if self.modelCombo.findText(n) < 0:
                self.modelCombo.addItem(n)
        if names:
            self.modelCombo.setCurrentText(names[0])

    @Slot()
    def _list_gemini_models(self) -> None:
        import threading
        import urllib.request
        import json as _json
        api_key = self.apiKeyEdit.text().strip()
        if not api_key:
            self._resultSignal.emit('❌ 請先填入 API Key', 'red')
            return
        self._resultSignal.emit('查詢中...', 'gray')

        def _run() -> None:
            try:
                url = f'https://generativelanguage.googleapis.com/v1beta/models?key={api_key}'
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = _json.loads(resp.read().decode('utf-8'))
                names = [
                    m['name'].replace('models/', '')
                    for m in data.get('models', [])
                    if 'generateContent' in m.get('supportedGenerationMethods', [])
                ]
                self._resultSignal.emit(f'✅ 找到 {len(names)} 個可用 model', 'green')
                self._modelsSignal.emit(names)  # update combo safely on main thread
            except Exception as e:  # pylint: disable=broad-except
                self._resultSignal.emit(f'❌ {e}', 'red')

        threading.Thread(target=_run, daemon=True).start()

    @Slot()
    def _open_sim_window(self) -> None:
        from artisanlib.ai_advisor_sim import AIAdvisorSimDialog
        self._apply_advisor_settings()
        _was_enabled = self.aw.ai_advisor.enabled
        self.aw.ai_advisor.enabled = True  # force-enable for sim session
        dlg = AIAdvisorSimDialog(self, self.aw)
        dlg.finished.connect(lambda: setattr(self.aw.ai_advisor, 'enabled', _was_enabled))
        dlg.show()

    @Slot()
    def _test_tts(self) -> None:
        from artisanlib.ai_advisor import _compute_rule_advice
        self._apply_advisor_settings()
        advisor = self.aw.ai_advisor
        advisor._tts_busy = False  # pylint: disable=protected-access
        sample = _compute_rule_advice(148.0, 14.0, 300.0, 'BT 150', None, 160.0, 200.0)
        advisor._speak(advisor._prepare_tts(sample))  # pylint: disable=protected-access

    def reject(self) -> None:
        self.aw.aiAdvisorWindow.setVisible(self.aw.ai_advisor.enabled)
        super().reject()

    def _save_and_close(self) -> None:
        advisor = self.aw.ai_advisor
        advisor.enabled = self.enabledCheck.isChecked()
        advisor.provider = self.providerCombo.currentData()
        advisor.api_key = self.apiKeyEdit.text().strip()
        advisor.model = self.modelCombo.currentText().strip()
        advisor.ollama_url = self.ollamaUrlEdit.text().strip()
        advisor.interval = self.intervalSpin.value()
        advisor.tts_enabled = self.ttsCheck.isChecked()
        advisor.tts_mode = 'action' if self._ttsModeAction.isChecked() else 'full'
        advisor.tts_rate = self.ttsRateSlider.value()
        advisor.tts_volume = self.ttsVolSlider.value()
        advisor.tts_prefix = self.ttsPrefixCheck.isChecked()
        advisor.tts_voice = self.ttsVoiceCombo.currentData() or ''
        # aiAdvisorWindow visibility is controlled by OnMonitor/OffMonitor
        self.aw.saveSettings()
        self.accept()
