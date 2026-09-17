"""PySide6 desktop interface for the EPUB translation engine."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import sys
import threading
import traceback

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QBrush, QCloseEvent, QColor, QCursor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QDoubleSpinBox, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app_core import (APP_DIR, ROOT, DEFAULT_CONFIG, Client, GracefulStop, PauseController, TranslationEngine,
                      _dedupe_glossary, analysis_from_checkpoints, analysis_terms, chapters_containing,
                      first_heading, load_analysis_selection, load_config, read, render_analysis, rows,
                      save_json, validate_rerun)
from epub_import import prepare


STYLE = """
QWidget { background:#0d1117; color:#c9d1de; font:10pt 'Segoe UI'; }
QMainWindow { background:#0a0e14; }
QLabel { background:transparent; }
QLineEdit,QPlainTextEdit,QTreeWidget,QTableWidget,QTabWidget::pane,QSpinBox,QDoubleSpinBox,QComboBox {
  background:#151c29; border:1px solid #2a3450; border-radius:6px; padding:5px;
  selection-background-color:#8a6a1d; selection-color:#ffffff;
}
QPlainTextEdit { padding:8px; }
QLineEdit:focus,QPlainTextEdit:focus,QSpinBox:focus,QDoubleSpinBox:focus,QComboBox:focus { border:1px solid #7a5b17; }
QComboBox QAbstractItemView { background:#151c29; border:1px solid #39445c; selection-background-color:#8a6a1d; }
QPushButton { background:#1c2434; border:1px solid #333f5c; padding:7px 14px; border-radius:7px; color:#dbe4f3; }
QPushButton:hover { background:#273149; border-color:#4a5a80; }
QPushButton:pressed { background:#111622; }
QPushButton:disabled { color:#64708a; background:#181f2d; border-color:#272f42; }
QPushButton#primary {
  background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #f0b83c, stop:1 #d99e26);
  color:#14181f; font-weight:700; border:none; border-radius:7px; padding:9px 22px;
}
QPushButton#primary:hover { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #f6c24b, stop:1 #e2a92e); }
QPushButton#primary:disabled { background:#5c4c1f; color:#a89c66; }
QPushButton#ghost { background:transparent; border:1px solid #39445c; }
QPushButton#ghost:hover { background:#1c2434; }
QFrame#card { background:#111824; border:1px solid #232e44; border-radius:12px; }
QFrame#statusBar { background:#111824; border:1px solid #232e44; border-radius:9px; }
QFrame#sep { background:#2a3450; border:none; }
QLabel#appTitle { font-size:19pt; font-weight:700; color:#f2bd45; }
QLabel#appSubtitle { font-size:9pt; color:#7d899f; }
QLabel#sectionLabel { color:#e2b64c; font-weight:700; }
QLabel#modelStatus { background:#151c29; border:1px solid #2a3450; border-radius:6px; padding:6px 9px; }
QLabel#phasePill { background:#2a2116; color:#e8c170; border:1px solid #6b5420; border-radius:9px; padding:4px 12px; font-weight:600; }
QLabel#currentPill { background:#151c29; color:#9fb3d8; border:1px solid #2a3450; border-radius:9px; padding:4px 12px; }
QLabel#progressValue { background:#0d1420; color:#f2f5fb; border:1px solid #33405c; border-radius:6px; padding:5px 10px; font-weight:700; }
QLabel#lastSaved { color:#74809a; }
QLabel#settingsWarning { background:#262013; color:#e8c170; border:1px solid #6b5420; border-radius:6px; padding:8px; }
QLabel#settingsSection { color:#e2b64c; font-weight:600; padding-top:9px; padding-bottom:4px; border-bottom:1px solid #2a3450; }
QTreeWidget { alternate-background-color:#10161f; }
QTreeWidget::item { padding:4px; border-radius:4px; }
QTreeWidget::item:hover { background:#1a2434; }
QTreeWidget::item:selected { background:#2c3a55; color:#ffffff; }
QHeaderView::section { background:#151c29; color:#9db1d0; border:none; border-bottom:1px solid #2a3450; padding:7px; font-weight:600; }
QTabWidget::pane { border:1px solid #232e44; border-radius:8px; top:-1px; }
QTabBar::tab { background:#111824; color:#8fa0bd; padding:8px 18px; border:1px solid #232e44; border-bottom:none; border-top-left-radius:7px; border-top-right-radius:7px; margin-right:3px; }
QTabBar::tab:selected { color:#f2bd45; background:#1a2334; border-bottom:2px solid #f2bd45; }
QTabBar::tab:hover:!selected { background:#162031; }
QProgressBar { border:1px solid #2a3450; background:#141b28; border-radius:7px; text-align:center; }
QProgressBar::chunk { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #e0aa3a, stop:1 #f2bd45); border-radius:6px; }
QScrollBar:vertical { background:#121925; width:12px; border-radius:6px; }
QScrollBar::handle:vertical { background:#2b3850; border-radius:6px; min-height:30px; }
QScrollBar::handle:vertical:hover { background:#3a4a6a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar:horizontal { background:#121925; height:12px; border-radius:6px; }
QScrollBar::handle:horizontal { background:#2b3850; border-radius:6px; min-width:30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }
QCheckBox { spacing:7px; }
QCheckBox::indicator { width:16px; height:16px; border:1px solid #3b4a6b; border-radius:4px; background:#141b28; }
QCheckBox::indicator:hover { border-color:#d99e26; }
QCheckBox::indicator:checked { background:#d99e26; border-color:#d99e26; }
QToolTip { background:#1b2434; color:#e6ebf5; border:1px solid #3b4a6b; padding:6px; border-radius:4px; }
QMenu { background:#141b28; color:#d3dcec; border:1px solid #2a3450; border-radius:7px; padding:5px; }
QMenu::item { padding:6px 22px; border-radius:4px; }
QMenu::item:selected { background:#2c3a55; }
QTableWidget { gridline-color:#232e44; }
QTableWidget::item:selected { background:#2c3a55; }
"""


class ModelSignals(QObject):
    result = Signal(dict)
    failed = Signal(str)


class GlossaryReviewDialog(QDialog):
    """G16/G26: On analiz terim onerilerini kullanici onayina sunar.

    Mevcut sozluk satirlari ustte ve her zaman korunur; sozlukte zaten bulunan
    kaynak terimler oneri olarak tekrar gosterilmez (kullanici karari ezilmez).
    Kitap geneli oneriler isaretli, bolum ozel oneriler isaretsiz gelir.
    """

    def __init__(self, parent=None, proposed=None, current=None):
        super().__init__(parent)
        self.setWindowTitle('Terim Önerileri — Onay')
        self.resize(720, 460)
        current = _dedupe_glossary(current or [])
        existing = {item['source'].casefold() for item in current}
        proposed = [item for item in (proposed or [])
                    if str(item.get('suggested_target', '')).strip()
                    and str(item.get('source', '')).strip().casefold() not in existing]
        self.current_count = len(current)
        columns = ['Ekle', 'Kaynak terim', 'Karşılık', 'Not']
        self.table = QTableWidget(len(current) + len(proposed), len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch); header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        layout = QVBoxLayout(self)
        note = QLabel('Mevcut sözlük satırları korunur. Kitap geneli öneriler işaretli, yalnızca bir bölüme özgü öneriler '
                      'işaretsiz gelir. İşaretli öneriler sözlüğe eklenir; iptal ederseniz sözlük değişmez.')
        note.setWordWrap(True); layout.addWidget(note)
        layout.addWidget(self.table)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText('Onayla')
        box.button(QDialogButtonBox.Cancel).setText('İptal')
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        layout.addWidget(box)
        row = 0
        for item in current:
            marker = QTableWidgetItem('mevcut'); marker.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 0, marker)
            source = QTableWidgetItem(item['source']); source.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 1, source)
            self.table.setItem(row, 2, QTableWidgetItem(item['target']))
            self.table.setItem(row, 3, QTableWidgetItem('sözlükte var'))
            row += 1
        for item in proposed:
            check = QTableWidgetItem(); check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked if item.get('scope', 'book') == 'book' else Qt.Unchecked)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(str(item.get('source', ''))))
            self.table.setItem(row, 2, QTableWidgetItem(str(item.get('suggested_target', ''))))
            scope = 'kitap geneli' if item.get('scope', 'book') == 'book' else 'bölüm özel'
            reason = str(item.get('reason', '') or '')
            self.table.setItem(row, 3, QTableWidgetItem(scope + (' — ' + reason if reason else '')))
            row += 1

    def values(self):
        result = []
        for row in range(self.table.rowCount()):
            if row >= self.current_count:
                check = self.table.item(row, 0)
                if not check or check.checkState() != Qt.Checked:
                    continue
            source_item = self.table.item(row, 1); target_item = self.table.item(row, 2)
            source = source_item.text().strip() if source_item else ''
            target = target_item.text().strip() if target_item else ''
            if source and target:
                result.append({'source': source, 'target': target})
        return _dedupe_glossary(result)

    def exec_and_values(self):
        """Onaylanirsa (values, True), iptal edilirse (None, False) dondurur."""
        if self.exec() != QDialog.Accepted:
            return None, False
        return self.values(), True


class GlossaryDialog(QDialog):
    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle('Özel Terim Sözlüğü')
        self.resize(620, 400)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('İngilizce terim ile zorunlu Türkçe karşılığını girin. Boş bırakılabilir.'))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(['İngilizce terim', 'Türkçe karşılık'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        add = QPushButton('Satır ekle'); remove = QPushButton('Seçileni sil')
        add.clicked.connect(self.add_row); remove.clicked.connect(self.remove_rows)
        buttons.addWidget(add); buttons.addWidget(remove); buttons.addStretch()
        layout.addLayout(buttons)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        layout.addWidget(box)
        for item in existing or []:
            self.add_row(item.get('source', ''), item.get('target', ''))
        if not existing:
            self.add_row()

    def add_row(self, source='', target=''):
        row = self.table.rowCount(); self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(source))
        self.table.setItem(row, 1, QTableWidgetItem(target))

    def remove_rows(self):
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def values(self):
        result = []
        for row in range(self.table.rowCount()):
            source = (self.table.item(row, 0).text() if self.table.item(row, 0) else '').strip()
            target = (self.table.item(row, 1).text() if self.table.item(row, 1) else '').strip()
            if bool(source) != bool(target):
                raise ValueError('Her glossary satırında iki alan da dolu olmalı.')
            if source:
                result.append({'source': source, 'target': target})
        if len({item['source'].casefold() for item in result}) != len(result):
            raise ValueError('Aynı İngilizce terim birden fazla kez girilmiş.')
        return result

    def accept(self):
        try:
            self._values = self.values()
        except ValueError as error:
            QMessageBox.warning(self, 'Glossary', str(error)); return
        super().accept()


class AnalysisSelectionDialog(QDialog):
    EXCLUDED = re.compile(
        r'\b(cover|title page|copyright|contents|table of contents|dedication|acknowledg(?:e)?ments?|'
        r'about the author|newsletter|sign[ -]?up|bibliography|index|also by|other books|imprint)\b', re.I)

    def __init__(self, book, existing=None, parent=None):
        super().__init__(parent); self.book = Path(book); self.setWindowTitle('Ön Analize Dahil Edilecek Bölümler'); self.resize(720, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Yalnızca hikâye bölümlerini işaretleyin. Seçilmeyen bölümler çevrilir fakat ön analize girmez.'))
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(['Bölüm', 'Dosya'])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch); self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        layout.addWidget(self.tree)
        available = []
        for filename, _status in rows(read(self.book / '00-CONTEXT.md')):
            title = first_heading(read(self.book / 'source' / filename), Path(filename).stem)
            available.append((filename, title))
        selected = set(existing or [filename for filename, title in available if not self.EXCLUDED.search(title)])
        if not selected and available:
            selected = {filename for filename, _title in available}
        for filename, title in available:
            item = QTreeWidgetItem([title, filename]); item.setData(0, Qt.UserRole, filename)
            item.setCheckState(0, Qt.Checked if filename in selected else Qt.Unchecked); self.tree.addTopLevelItem(item)
        actions = QHBoxLayout(); all_button = QPushButton('Tümünü seç'); none_button = QPushButton('Tümünü kaldır'); story_button = QPushButton('Hikâyeyi tahmin et')
        all_button.clicked.connect(lambda: self.set_all(True)); none_button.clicked.connect(lambda: self.set_all(False)); story_button.clicked.connect(self.select_recommended)
        actions.addWidget(all_button); actions.addWidget(none_button); actions.addWidget(story_button); actions.addStretch(); layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def set_all(self, checked):
        for index in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(index).setCheckState(0, Qt.Checked if checked else Qt.Unchecked)

    def select_recommended(self):
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            item.setCheckState(0, Qt.Unchecked if self.EXCLUDED.search(item.text(0)) else Qt.Checked)

    def values(self):
        return [self.tree.topLevelItem(index).data(0, Qt.UserRole) for index in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(index).checkState(0) == Qt.Checked]

    def accept(self):
        if not self.values():
            QMessageBox.warning(self, 'Ön analiz', 'En az bir bölüm seçin veya ön analizi kapatın.'); return
        super().accept()


class SettingsDialog(QDialog):
    def __init__(self, config, loaded_models=None, parent=None):
        super().__init__(parent); self.setWindowTitle('Ayarlar'); self.resize(520, 0)
        outer = QVBoxLayout(self)
        warning = QLabel('Bu ayarlar deneyseldir ve çeviri kararlılığını etkiler. Ne yaptığınızdan emin değilseniz değiştirmeyin.')
        warning.setObjectName('settingsWarning'); warning.setWordWrap(True); outer.addWidget(warning)
        layout = QFormLayout(); outer.addLayout(layout); self.fields = {}

        book_section = QLabel('Kitap ve EPUB'); book_section.setObjectName('settingsSection'); layout.addRow(book_section)
        epubcheck = QCheckBox('Üretilen EPUB’u harici EPUBCheck ile doğrula')
        epubcheck.setChecked(bool(config.get('epubcheck_enabled', False)))
        epubcheck.setToolTip('Etkinse EPUB oluşturulduktan sonra ayarlanan EPUBCheck JAR/EXE çalıştırılır. JAR için Java gerekir.')
        self.fields['epubcheck_enabled'] = epubcheck; layout.addRow('EPUBCheck', epubcheck)
        epubcheck_path = QLineEdit(str(config.get('epubcheck_path', '')))
        epubcheck_path.setPlaceholderText('Boşsa PATH içindeki epubcheck; ayrıca .jar veya .exe seçilebilir')
        self.fields['epubcheck_path'] = epubcheck_path; layout.addRow('EPUBCheck yolu', epubcheck_path)
        strict = QCheckBox('Uygulanamayan terimde çeviriyi durdur'); strict.setChecked(bool(config.get('glossary_strict', False)))
        strict.setToolTip('Kapalıyken uyarı glossary_fixes.log dosyasına yazılır ve çeviri devam eder.')
        self.fields['glossary_strict'] = strict; layout.addRow('Katı sözlük', strict)
        marker_enabled = QCheckBox('Uzun çeviri yanıtlarında eksik sonuç korumasını kullan')
        marker_enabled.setChecked(bool(config.get('completion_marker_enabled', True)))
        marker_enabled.setToolTip(
            'Açıksa sınırı aşan çeviri parçalarında modelin yapay bitiş işaretini üretmesi gerekir. '
            'Kapalıysa hiçbir çeviri veya glossary düzeltme yanıtında bitiş işareti aranmaz; '
            'boş/bozuk çıktı, token sınırı ve EPUB yapı kontrolleri çalışmaya devam eder.'
        )
        self.fields['completion_marker_enabled'] = marker_enabled
        layout.addRow('Bitiş işareti denetimi', marker_enabled)
        marker_limit = QSpinBox(); marker_limit.setRange(0, 1000000)
        marker_limit.setValue(int(config.get('completion_marker_exempt_chars', 400)))
        marker_limit.setToolTip(
            'Denetim açıkken bu sayı kadar veya daha kısa ilk çeviri parçalarında bitiş işareti aranmaz. '
            'Örneğin 500 seçilirse 500 karakter ve altı işaretsiz kabul edilir; 501 ve üstünde işaret zorunludur.'
        )
        marker_limit.setEnabled(marker_enabled.isChecked())
        marker_enabled.toggled.connect(marker_limit.setEnabled)
        self.fields['completion_marker_exempt_chars'] = marker_limit
        layout.addRow('İşaretsiz sınır (karakter)', marker_limit)

        model_section = QLabel('LM Studio ve model'); model_section.setObjectName('settingsSection'); layout.addRow(model_section)
        base_url = QLineEdit(str(config['base_url'])); self.fields['base_url'] = base_url; layout.addRow('Base URL', base_url)
        self.loaded_models = list(dict.fromkeys(str(model).strip() for model in (loaded_models or []) if str(model).strip()))
        choices = self.loaded_models or list(dict.fromkeys((str(config['model']).strip(), str(DEFAULT_CONFIG['model']))))
        model = QComboBox(); model.setEditable(False); model.addItems(choices)
        configured_index = model.findText(str(config['model']))
        model.setCurrentIndex(configured_index if configured_index >= 0 else 0)
        model.setToolTip('LM Studio tarafından yüklü bildirilen modeller arasından seçim yapılır.')
        self.fields['model'] = model; layout.addRow('Model', model)
        model_note = QLabel('LM Studio yüklü model listesi kullanılıyor.' if self.loaded_models else
                            'Yüklü model listesi alınamadı; mevcut ve varsayılan model gösteriliyor.')
        model_note.setWordWrap(True); layout.addRow('', model_note)
        temperature = QDoubleSpinBox(); temperature.setRange(0, 2); temperature.setSingleStep(.05); temperature.setValue(float(config['temperature']))
        self.fields['temperature'] = temperature; layout.addRow('Temperature', temperature)
        for key, title, maximum in (
            ('timeout_seconds', 'Timeout (s)', 86400), ('chunk_chars', 'Parça boyutu (karakter)', 1000000),
            ('max_tokens', 'Çıktı token payı', 262144), ('notes_max_tokens', 'Not çıktı tokenı', 32768),
            ('fallback_context_length', 'Fallback context', 1048576), ('context_safety_tokens', 'Güvenlik payı', 65536),
        ):
            field = QSpinBox(); field.setRange(1, maximum); field.setValue(int(config[key])); self.fields[key] = field; layout.addRow(title, field)
        estimate = QCheckBox(); estimate.setChecked(bool(config.get('token_estimation_enabled', True)))
        self.fields['token_estimation_enabled'] = estimate; layout.addRow('Token tahmini', estimate)
        restore = QPushButton('Varsayılan ayarlara dön'); restore.clicked.connect(self.reset_defaults)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout(); bottom.addWidget(restore); bottom.addStretch(); bottom.addWidget(buttons); outer.addLayout(bottom)

    def reset_defaults(self):
        for key, field in self.fields.items():
            value = DEFAULT_CONFIG[key]
            if isinstance(field, QLineEdit): field.setText(str(value))
            elif isinstance(field, QComboBox):
                index = field.findText(str(value))
                field.setCurrentIndex(index if index >= 0 else 0)
            elif isinstance(field, QCheckBox): field.setChecked(bool(value))
            else: field.setValue(value)

    def value(self):
        result = {}
        for key, field in self.fields.items():
            if isinstance(field, QLineEdit): result[key] = field.text().strip()
            elif isinstance(field, QComboBox): result[key] = field.currentText().strip()
            elif isinstance(field, QCheckBox): result[key] = field.isChecked()
            else: result[key] = field.value()
        return result


class Worker(QObject):
    log = Signal(str); progress = Signal(dict); state = Signal(str); finished = Signal(object, str)
    review_requested = Signal(list)   # worker -> ana thread: onerilerle onay iste

    def __init__(self, book, controller, analysis_files=None, rerun_files=None):
        super().__init__(); self.book = Path(book); self.controller = controller
        self.analysis_files = analysis_files; self.rerun_files = rerun_files
        self._review_event = threading.Event()
        self._review_values = None

    @Slot()
    def run(self):
        try:
            engine = TranslationEngine(load_config, self.controller, self.log.emit, lambda **values: self.progress.emit(values))
            if self.analysis_files is not None:
                engine.run_analysis(self.book, self.analysis_files)
                pending = analysis_terms(self.book)
                if pending:
                    # Dialogun ana GUI thread'inde acilmasi icin sinyal gonderilir;
                    # Worker thread burada kullanici kararini bekler.
                    self.review_requested.emit(pending)
                    self._review_event.wait()
                    if self._review_values is not None:
                        save_json(self.book / 'glossary.json', self._review_values)
                        self.log.emit('Terim onerileri kabul edildi; glossary guncellendi.')
                    else:
                        self.log.emit('Terim onerileri kabul edilmedi; mevcut glossary korunuyor.')
            if self.rerun_files is not None:
                engine.rerun_chapters(self.book, self.rerun_files)
                self.finished.emit(True, 'Seçilen bölümler yeniden çevrildi.')
                return
            engine.run_book(self.book)
            self.finished.emit(True, 'Çeviri tamamlandı.')
        except GracefulStop as error:
            self.finished.emit('stopped', str(error))
        except Exception as error:
            self.log.emit(traceback.format_exc())
            self.finished.emit('failed', str(error))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle('Çevirgeç'); self.resize(1240, 780)
        icon_path = ROOT / 'assets' / 'app.ico'
        if icon_path.exists(): self.setWindowIcon(QIcon(str(icon_path)))
        self.book = None; self.epub = None; self.work_parent = None
        self.thread = None; self.worker = None; self.controller = None
        self.pending_close = False; self.runtime_state = 'idle'; self.active_filename = None; self.active_phase = None; self.tree_items = {}; self.loaded_models = []
        self.model_signals = ModelSignals(); self.model_signals.result.connect(self.model_probe_finished); self.model_signals.failed.connect(self.model_probe_failed)
        self._build_ui(); QTimer.singleShot(0, self.refresh_model_info)

    def _build_ui(self):
        """G41: arayuz yeniden tasarimi — baslik cubugu + proje karti + kalici durum seridi."""
        central = QWidget(); root = QVBoxLayout(central)
        root.setContentsMargins(14, 10, 14, 12); root.setSpacing(10)
        self.setCentralWidget(central)
        # ---- Baslik cubugu ----
        header = QHBoxLayout(); header.setSpacing(10)
        logo = QLabel(); logo.setFixedSize(42, 42)
        logo_path = ROOT / 'assets' / 'app.png'
        if logo_path.exists():
            logo.setPixmap(QPixmap(str(logo_path)).scaled(42, 42, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand = QVBoxLayout(); brand.setSpacing(0)
        title = QLabel('Çevirgeç'); title.setObjectName('appTitle')
        subtitle = QLabel('EPUB → Türkçe çeviri aracı'); subtitle.setObjectName('appSubtitle')
        brand.addWidget(title); brand.addWidget(subtitle)
        header.addWidget(logo); header.addLayout(brand); header.addStretch()
        self.open_project_button = QPushButton('Projeyi sürdür')
        self.open_project_button.setObjectName('ghost')
        self.open_project_button.setToolTip(
            'Daha önce oluşturulmuş {KitapAdı}-ceviri klasörünü açar ve kayıtlı checkpointlerden devam eder.'
        )
        self.open_project_button.clicked.connect(self.choose_existing)
        glossary_button = QPushButton('Terim düzenle'); glossary_button.clicked.connect(self.edit_glossary)
        settings = QPushButton('Ayarlar'); settings.clicked.connect(self.show_settings)
        header.addWidget(self.open_project_button); header.addWidget(glossary_button); header.addWidget(settings)
        root.addLayout(header)
        # ---- Proje karti ----
        card = QFrame(); card.setObjectName('card')
        form = QGridLayout(card); form.setContentsMargins(14, 12, 14, 12)
        form.setHorizontalSpacing(12); form.setVerticalSpacing(9); form.setColumnStretch(1, 1)
        self.epub_edit = QLineEdit(); self.epub_edit.setReadOnly(True)
        self.epub_edit.setPlaceholderText('Kaynak .epub dosyası seçilmedi')
        choose_epub = QPushButton('Dosya seç'); choose_epub.clicked.connect(self.choose_epub)
        self.folder_edit = QLineEdit(); self.folder_edit.setReadOnly(True)
        self.folder_edit.setPlaceholderText('Çeviri projesinin oluşturulacağı klasör')
        choose_folder = QPushButton('Klasör seç'); choose_folder.clicked.connect(self.choose_folder)
        form.addWidget(QLabel('Kaynak EPUB'), 0, 0)
        form.addWidget(self.epub_edit, 0, 1); form.addWidget(choose_epub, 0, 2)
        form.addWidget(QLabel('Çalışma klasörü'), 1, 0)
        form.addWidget(self.folder_edit, 1, 1); form.addWidget(choose_folder, 1, 2)
        sep = QFrame(); sep.setFixedHeight(1); sep.setObjectName('sep'); form.addWidget(sep, 2, 0, 1, 3)
        model_caption = QLabel('Seçili model')
        self.model_label = QLabel(load_config()['model']); self.model_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        status_caption = QLabel('LM Studio')
        self.model_status = QLabel('LM Studio sorgulanıyor…'); self.model_status.setObjectName('modelStatus')
        self.model_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        refresh = QPushButton('Yenile'); refresh.clicked.connect(self.refresh_model_info)
        form.addWidget(model_caption, 3, 0); form.addWidget(self.model_label, 3, 1)
        form.addWidget(status_caption, 4, 0)
        model_row = QHBoxLayout(); model_row.addWidget(self.model_status, 1); model_row.addWidget(refresh)
        form.addLayout(model_row, 4, 1, 1, 2)
        root.addWidget(card)
        # ---- Ana calisma alani ----
        splitter = QSplitter(Qt.Horizontal); root.addWidget(splitter, 1)
        tree_pane = QFrame(); tree_pane.setObjectName('card')
        tree_layout = QVBoxLayout(tree_pane); tree_layout.setContentsMargins(10, 10, 10, 10); tree_layout.setSpacing(6)
        tree_caption = QLabel('BÖLÜMLER'); tree_caption.setObjectName('sectionLabel')
        tree_layout.addWidget(tree_caption)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(['Bölümler', 'Durum'])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.setMinimumWidth(380); self.tree.setAlternatingRowColors(True)
        self.tree.itemSelectionChanged.connect(self.preview_selected)
        # G28: sinyal yalnizca bir kez baglanir (populate_tree her cagrida baglarsa menu cogalir).
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_context_menu)
        tree_layout.addWidget(self.tree)
        splitter.addWidget(tree_pane)
        self.tabs = QTabWidget(); splitter.addWidget(self.tabs); splitter.setSizes([420, 780])
        self.console = QPlainTextEdit(); self.console.setReadOnly(True)
        self.console.document().setMaximumBlockCount(10000); self.tabs.addTab(self.console, 'Konsol')
        self.analysis_preview = QPlainTextEdit(); self.analysis_preview.setReadOnly(True)
        self.analysis_preview.setPlainText('Ön analiz seçilirse sonuç burada gösterilir.')
        self.tabs.addTab(self.analysis_preview, 'Ön Analiz')
        preview = QSplitter(Qt.Horizontal); self.source_preview = QPlainTextEdit(); self.translation_preview = QPlainTextEdit()
        self.source_preview.setReadOnly(True); self.translation_preview.setReadOnly(True)
        preview.addWidget(self.source_preview); preview.addWidget(self.translation_preview)
        self.tabs.addTab(preview, 'Kaynak / Türkçe')
        # ---- Kalici durum seridi (her zaman gorunur) ----
        strip = QFrame(); strip.setObjectName('statusBar')
        strip_layout = QHBoxLayout(strip); strip_layout.setContentsMargins(10, 6, 10, 6); strip_layout.setSpacing(10)
        self.phase = QLabel('Başlatılmadı'); self.phase.setObjectName('phasePill')
        self.current = QLabel('Bölüm seçilmedi'); self.current.setObjectName('currentPill')
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setTextVisible(False)
        self.progress_value = QLabel('0%'); self.progress_value.setObjectName('progressValue')
        self.progress_value.setAlignment(Qt.AlignCenter); self.progress_value.setMinimumWidth(58)
        strip_layout.addWidget(self.phase); strip_layout.addWidget(self.current)
        strip_layout.addWidget(self.progress, 1); strip_layout.addWidget(self.progress_value)
        root.addWidget(strip)
        # ---- Alt kontrol cubugu ----
        bottom = QHBoxLayout(); self.last_saved = QLabel('Son kayıt: —')
        self.last_saved.setObjectName('lastSaved'); bottom.addWidget(self.last_saved); bottom.addStretch()
        self.pre_analysis = QCheckBox('Ön analiz yap (isteğe bağlı)')
        self.pre_analysis.setChecked(False)
        self.pre_analysis.setToolTip('İsteğe bağlıdır. Kitap çeviriden önce analiz edilir ve toplam işlem süresi iki kata kadar uzayabilir.')
        bottom.addWidget(self.pre_analysis)
        self.control = QPushButton('Çeviriyi Başlat'); self.control.setObjectName('primary')
        self.control.clicked.connect(self.control_clicked); bottom.addWidget(self.control)
        root.addLayout(bottom)

    def _detach_project(self):
        """G28: yeni EPUB/klasor secilince onceki proje birakilir; aksi halde
        "Ceviriyi Baslat" eski projeyi surdurur."""
        if self.book is None: return
        self.book = None; self.tree.clear(); self.tree_items = {}
        self.source_preview.clear(); self.translation_preview.clear()
        self.refresh_analysis_preview(); self.phase.setText('Başlatılmadı'); self.current.setText('Bölüm seçilmedi')
        self.set_progress_value(0)

    def _idle_or_warn(self):
        if self.runtime_state != 'idle':
            QMessageBox.warning(self, 'Proje çalışıyor', 'Çalışan çeviri tamamlanmadan veya durmadan seçim değiştirilemez.')
            return False
        return True

    def choose_folder(self):
        if not self._idle_or_warn(): return
        value = QFileDialog.getExistingDirectory(self, 'Çalışma klasörünü seç')
        if value: self._detach_project(); self.work_parent = Path(value); self.folder_edit.setText(value)

    def choose_epub(self):
        if not self._idle_or_warn(): return
        value, _ = QFileDialog.getOpenFileName(self, 'Kaynak EPUB seç', '', 'EPUB (*.epub)')
        if value: self._detach_project(); self.epub = Path(value); self.epub_edit.setText(value)

    def choose_existing(self):
        if self.runtime_state != 'idle':
            QMessageBox.warning(self, 'Proje çalışıyor', 'Çalışan çeviri tamamlanmadan veya durmadan başka proje açılamaz.'); return
        value = QFileDialog.getExistingDirectory(self, 'Mevcut çeviri projesini seç')
        if not value: return
        book = Path(value)
        if not (book / '00-CONTEXT.md').is_file() or not (book / 'source').is_dir():
            QMessageBox.warning(self, 'Geçersiz proje', '00-CONTEXT.md ve source klasörü bulunamadı.'); return
        self.book = book; self.folder_edit.setText(str(book.parent))
        marker = book / 'epub-import.json'
        if marker.exists():
            self.epub_edit.setText(json.loads(read(marker)).get('epub', ''))
        self.populate_tree(); self.refresh_analysis_preview(); self.phase.setText('Mevcut proje hazır')

    def show_settings(self):
        dialog = SettingsDialog(load_config(), self.loaded_models, self)
        if dialog.exec() != QDialog.Accepted: return
        try:
            from app_core import validate_config
            value = dialog.value(); validate_config(value); save_json(APP_DIR / 'config.json', value)
            self.model_label.setText(value['model'])
            self.append_log(
                'Ayarlar kaydedildi. Bitiş işareti ayarları yarım bölüm yeniden sürdürüldüğünde; '
                'diğer çalışma ayarları sonraki bölümde uygulanır.'
            )
            self.refresh_model_info()
        except Exception as error: QMessageBox.critical(self, 'Ayarlar', str(error))

    @Slot()
    def refresh_model_info(self):
        self.model_status.setText('LM Studio sorgulanıyor…')
        cfg = dict(load_config()); cfg['timeout_seconds'] = min(8, int(cfg['timeout_seconds']))

        def probe():
            try:
                _info, models = Client(cfg, lambda _message: None).model_info()
                loaded = []
                for model in models:
                    instances = model.get('loaded_instances') or []
                    for instance in instances:
                        context = instance.get('config', {}).get('context_length')
                        loaded.append({'name': instance.get('id') or model.get('key') or model.get('id'), 'context': context})
                    if not instances and model.get('state') == 'loaded':
                        loaded.append({'name': model.get('id') or model.get('key'), 'context': model.get('max_context_length')})
                self.model_signals.result.emit({'configured': cfg['model'], 'loaded': loaded})
            except Exception as error:
                self.model_signals.failed.emit(str(error))

        threading.Thread(target=probe, name='lmstudio-model-probe', daemon=True).start()

    @Slot(dict)
    def model_probe_finished(self, data):
        loaded = data.get('loaded', [])
        self.loaded_models = list(dict.fromkeys(item['name'] for item in loaded if item.get('name')))
        if not loaded:
            self.model_status.setText('Bağlantı var • Yüklü model yok')
            return
        descriptions = []
        for item in loaded:
            context = f' • {int(item["context"]):,} context' if item.get('context') else ''
            descriptions.append(f'{item["name"]}{context}')
        self.model_status.setText('Yüklü: ' + ' | '.join(descriptions))
        cfg = load_config()
        if len(self.loaded_models) == 1 and cfg['model'] != self.loaded_models[0]:
            cfg['model'] = self.loaded_models[0]
            try:
                save_json(APP_DIR / 'config.json', cfg)
            except OSError:
                self.append_log('UYARI: Tek model otomatik seçildi fakat config.json yazılamadı (korumalı klasör?). Ayarlar penceresinden modeli elle seçin.')
                return
            self.model_label.setText(cfg['model'])
            self.append_log('LM Studio’da tek model yüklü olduğu için otomatik seçildi: ' + cfg['model'])

    @Slot(str)
    def model_probe_failed(self, message):
        self.model_status.setText('Bağlantı kurulamadı • ' + message)

    def prepare_project(self):
        if self.book: return True
        if not self.work_parent or not self.epub:
            QMessageBox.warning(self, 'Eksik seçim', 'Önce çalışma klasörünü ve kaynak EPUB dosyasını seçin.'); return False
        try:
            self.book = prepare(self.epub, self.work_parent)
            glossary_path = self.book / 'glossary.json'
            existing = json.loads(read(glossary_path)) if glossary_path.exists() else []
            dialog = GlossaryDialog(self, existing)
            if dialog.exec() != QDialog.Accepted:
                self.book = None; return False
            save_json(glossary_path, dialog._values)
            self.populate_tree(); self.refresh_analysis_preview(); return True
        except Exception as error:
            QMessageBox.critical(self, 'Proje hazırlanamadı', str(error)); self.book = None; return False

    def populate_tree(self):
        self.tree.clear(); self.tree_items = {}
        if not self.book: return
        context_rows = rows(read(self.book / '00-CONTEXT.md'))
        parents = {}
        for filename, status in context_rows:
            source = self.book / 'source' / filename
            source_title = first_heading(read(source), Path(filename).stem)
            hierarchy = [part.strip() for part in source_title.split(' — ') if part.strip()]
            title = hierarchy[-1] if hierarchy else Path(filename).stem
            translated = self.book / 'translation' / filename
            if translated.exists():
                translated_title = first_heading(read(translated), title)
                title = translated_title.split(' — ')[-1].strip()
            state_path = self.book / '_python_translation' / filename / 'state.json'
            state = json.loads(read(state_path)) if state_path.exists() else {}
            if status == 'done' or state.get('complete'):
                label = 'Tamamlandı'
            elif state.get('parts') or translated.exists():
                label = 'Devam edecek'
            else:
                label = 'Sıradaki' if status == 'next' else 'Bekliyor'
            if filename == self.active_filename:
                label = {'analysis': 'Ön analiz', 'analysis_saved': 'Ön analiz', 'translation': 'Çevriliyor', 'notes': 'Notlar hazırlanıyor'}.get(self.active_phase, label)
            item = QTreeWidgetItem([title, label])
            item.setData(0, Qt.UserRole, filename)
            item.setForeground(1, self._status_brush(label))
            self.tree_items[filename] = item
            parent = None; key = ()
            for label in hierarchy[:-1]:
                key += (label,)
                if key not in parents:
                    parents[key] = QTreeWidgetItem([label, ''])
                    (parent.addChild(parents[key]) if parent else self.tree.addTopLevelItem(parents[key]))
                parent = parents[key]
            parent.addChild(item) if parent else self.tree.addTopLevelItem(item)
        self.tree.expandAll()

    @staticmethod
    def _status_brush(label):
        """G41: bolum agacindaki durum etiketlerini renklendirir."""
        colors = {
            'Tamamlandı': '#6fdb87',
            'Devam edecek': '#e8c170',
            'Sıradaki': '#8ab2f0',
            'Bekliyor': '#6b7488',
            'Çevriliyor': '#f0b83c',
            'Ön analiz': '#c58ff0',
            'Ön analiz hazır': '#c58ff0',
            'Notlar hazırlanıyor': '#e8c170',
        }
        return QBrush(QColor(colors.get(label, '#c9d1de')))

    def preview_selected(self):
        selected = self.tree.selectedItems()
        if not selected or not self.book: return
        filename = selected[0].data(0, Qt.UserRole)
        if not filename: return
        self.source_preview.setPlainText(read(self.book / 'source' / filename))
        target = self.book / 'translation' / filename
        self.translation_preview.setPlainText(read(target) if target.exists() else 'Henüz çevrilmedi.')
        self.tabs.setCurrentIndex(2)

    def refresh_analysis_preview(self):
        if not self.book:
            self.analysis_preview.setPlainText('Ön analiz seçilirse sonuç burada gösterilir.'); return
        merged = analysis_from_checkpoints(self.book)
        path = self.book / 'BOOK-ANALYSIS.md'
        if merged:
            self.analysis_preview.setPlainText(render_analysis(merged))
        else:
            self.analysis_preview.setPlainText(read(path) if path.exists() else 'Ön analiz seçilirse sonuç burada gösterilir.')

    def tree_context_menu(self, position):
        item = self.tree.itemAt(position)
        if not item: return
        filename = item.data(0, Qt.UserRole)
        if not filename: return
        menu = QMenu(self)
        action = menu.addAction('Bu bölümü yeniden çevir')
        action.triggered.connect(lambda: self.rerun_chapter(filename))
        menu.popup(QCursor.pos())

    def rerun_chapter(self, filename):
        if self.runtime_state != 'idle':
            QMessageBox.warning(self, 'Proje çalışıyor', 'Çalışan çeviri tamamlanmadan bölüm yeniden çevrilemez.'); return
        try:
            validate_rerun(self.book, [filename])
        except Exception as error:
            QMessageBox.warning(self, 'Bölüm yeniden çevrilemez', str(error)); return
        answer = QMessageBox.question(self, 'Bölümü yeniden çevir',
                                      f'"{filename}" bölümü yeniden çevrilecek. Mevcut çeviri `_python_translation/{filename}/recheck-prev-<tarih>.md` olarak yedeklenir; sonraki bölümlerin bağlamı (özet/notlar) bu çeviriye verilmez. Bitince TAM-CEVIRI.md (ve kitap tamamsa EPUB) yeniden üretilir. Devam edilsin mi?',
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes: return
        # Sifirlama worker icinde, proje kilidi altinda yapilir (G21: tek sifirlama).
        self.prepare_rerun([filename])

    def edit_glossary(self):
        if self.runtime_state != 'idle':
            QMessageBox.warning(self, 'Proje çalışıyor', 'Çalışan çeviri tamamlanmadan glossary düzenlenemez.'); return
        if not self.book:
            QMessageBox.warning(self, 'Proje yok', 'Önce bir proje seçin.'); return
        glossary_path = self.book / 'glossary.json'
        existing = json.loads(read(glossary_path)) if glossary_path.exists() else []
        dialog = GlossaryDialog(self, existing)
        if dialog.exec() != QDialog.Accepted: return
        new_glossary = dialog._values
        old_map = {item.get('source', '').casefold(): item.get('target', '') for item in existing}
        new_map = {item.get('source', '').casefold(): item.get('target', '') for item in new_glossary}
        changed = sorted({key for key in set(old_map) | set(new_map) if old_map.get(key) != new_map.get(key)})
        if not changed:
            QMessageBox.information(self, 'Glossary', 'Değişiklik yok; yeniden çeviri gerekmiyor.'); return
        save_json(glossary_path, new_glossary)
        # G21: yalnizca cevrilmis (done) bolumler; cevrilmemisler zaten yeni sozlukle cevrilecek.
        affected = chapters_containing(self.book, changed)
        if not affected:
            QMessageBox.information(self, 'Glossary', 'Sözlük kaydedildi. Değişen terimler çevrilmiş hiçbir bölümde geçmiyor; kalan bölümler yeni sözlükle çevrilecek.'); return
        answer = QMessageBox.question(self, 'Glossary güncellendi',
                                      f'Sözlük kaydedildi. Değişen terimlerin geçtiği {len(affected)} çevrilmiş bölüm yeniden çevrilsin mi?\n' + '\n'.join(f'- {name}' for name in affected) +
                                      '\n\nBu bölümler sonraki bölümlerin bağlamı olmadan yeniden çevrilir; yarım kalmış bölümün kaydı korunur.',
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.prepare_rerun(affected)

    def prepare_rerun(self, filenames):
        if self.runtime_state != 'idle': return
        self.controller = PauseController()
        self.thread = QThread(self); self.worker = Worker(self.book, self.controller, rerun_files=filenames)
        self.worker.moveToThread(self.thread)
        self.controller.set_state_callback(self.worker.state.emit)
        self.thread.started.connect(self.worker.run); self.worker.log.connect(self.append_log)
        self.worker.progress.connect(self.update_progress); self.worker.state.connect(self.apply_state)
        self.worker.finished.connect(self.worker_finished); self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater); self.thread.finished.connect(self.thread_stopped); self.thread.finished.connect(self.thread.deleteLater)
        self.runtime_state = 'running'; self.control.setText('Duraklat'); self.phase.setText('Bölüm yeniden çevriliyor')
        self.pre_analysis.setEnabled(False); self.open_project_button.setEnabled(False); self.thread.start()

    def control_clicked(self):
        if self.runtime_state == 'idle':
            if self.prepare_project(): self.start_worker()
        elif self.runtime_state == 'running':
            self.controller.request_pause(); self.runtime_state = 'pausing'; self.control.setText('Duraklatılıyor…'); self.control.setEnabled(False)
        elif self.runtime_state == 'paused':
            self.controller.resume(); self.runtime_state = 'running'; self.control.setText('Duraklat')

    def start_worker(self):
        configured_model = load_config()['model']
        if self.loaded_models and configured_model not in self.loaded_models:
            QMessageBox.warning(self, 'Model seçilmedi', 'Ayarlar bölümünden LM Studio’da yüklü modellerden birini seçin.'); return
        if not self.loaded_models:
            QMessageBox.warning(self, 'LM Studio bağlantısı doğrulanamadı',
                                'LM Studio’da yüklü model listesi sorgulanamadı. Sunucunun açık olduğunu ve '
                                'modelin yüklü olduğunu doğrulayın; aksi hâlde çeviri istekleri başarısız olabilir.')
        log_path = self.book / 'translation.log'
        self.log_path = log_path; self.controller = PauseController()
        analysis_files = None
        if self.pre_analysis.isChecked():
            dialog = AnalysisSelectionDialog(self.book, load_analysis_selection(self.book), self)
            if dialog.exec() != QDialog.Accepted:
                return
            analysis_files = dialog.values()
        use_analysis = analysis_files is not None; self.pre_analysis.setEnabled(False)
        self.open_project_button.setEnabled(False)
        self.thread = QThread(self); self.worker = Worker(self.book, self.controller, analysis_files); self.worker.moveToThread(self.thread)
        self.controller.set_state_callback(self.worker.state.emit)
        self.worker.review_requested.connect(self.handle_review)
        self.thread.started.connect(self.worker.run); self.worker.log.connect(self.append_log); self.worker.progress.connect(self.update_progress); self.worker.state.connect(self.apply_state); self.worker.finished.connect(self.worker_finished)
        self.worker.finished.connect(self.thread.quit); self.thread.finished.connect(self.worker.deleteLater); self.thread.finished.connect(self.thread_stopped); self.thread.finished.connect(self.thread.deleteLater)
        self.runtime_state = 'running'; self.control.setText('Duraklat'); self.phase.setText('Ön analiz hazırlanıyor' if use_analysis else 'Çeviri çalışıyor'); self.tabs.setCurrentIndex(1 if use_analysis else 0); self.thread.start()

    @Slot(list)
    def handle_review(self, pending):
        """G16: Worker thread analiz onerilerini bildirir; dialog ana GUI
        thread'inde acilir. Kullanici karari worker'in bekleyen Event'ine
        yazilir; boylece ceviri yalnizca onay sonrasi surer."""
        worker = self.worker
        if worker is None:
            return
        glossary_path = self.book / 'glossary.json'
        current = json.loads(read(glossary_path)) if glossary_path.exists() else []
        dialog = GlossaryReviewDialog(self, proposed=pending, current=current)
        values, accepted = dialog.exec_and_values()
        worker._review_values = values if accepted else None
        worker._review_event.set()

    @Slot(str)
    def apply_state(self, state):
        self.runtime_state = state
        if state == 'paused': self.control.setText('Devam Et'); self.control.setEnabled(True); self.phase.setText('Duraklatıldı')
        elif state == 'running': self.control.setText('Duraklat'); self.control.setEnabled(True); self.phase.setText('Çeviri çalışıyor')
        elif state == 'closing': self.control.setText('İstek bitince kapanacak'); self.control.setEnabled(False)

    @Slot(str)
    def append_log(self, message):
        stamp = datetime.now().strftime('%H:%M:%S'); line = f'[{stamp}] {message}'
        self.console.appendPlainText(line)
        if self.book:
            with (self.book / 'translation.log').open('a', encoding='utf-8', newline='\n') as stream:
                stream.write(line + '\n'); stream.flush()
        if 'kaydedildi' in message.lower(): self.last_saved.setText('Son kayıt: ' + stamp)

    @Slot(dict)
    def update_progress(self, data):
        phase = data.get('phase')
        if phase == 'complete': self.set_progress_value(100); self.populate_tree(); return
        if phase == 'chapter_complete':
            self.active_filename = None; self.active_phase = None; self.populate_tree(); return
        filename = data.get('filename', ''); part = int(data.get('part', 0)); parts = max(1, int(data.get('parts', 1)))
        previous_phase = self.active_phase; self.active_filename = filename; self.active_phase = phase
        labels = {'analysis': 'ön analiz', 'analysis_saved': 'ön analiz', 'translation': 'çeviri', 'notes': 'notlar'}
        if phase in ('analysis', 'analysis_saved'): self.phase.setText('Ön analiz yapılıyor')
        elif phase in ('translation', 'notes'): self.phase.setText('Çeviri çalışıyor')
        if phase == 'translation' and previous_phase in ('analysis', 'analysis_saved'):
            self.populate_tree()
        item = self.tree_items.get(filename)
        if item:
            status_label = {'analysis': 'Ön analiz', 'analysis_saved': 'Ön analiz hazır' if part == parts else 'Ön analiz',
                            'translation': 'Çevriliyor', 'notes': 'Notlar hazırlanıyor'}.get(phase)
            if status_label: item.setText(1, status_label)
        if phase == 'analysis_saved':
            self.refresh_analysis_preview()
        self.current.setText(f'{filename} — {labels.get(phase, phase)} {part}/{parts}')
        self.set_progress_value(int(part * 100 / parts))

    def set_progress_value(self, value):
        """Yuzdeyi dolgu renginden bagimsiz, sabit kontrastli kutuda gosterir."""
        value = max(0, min(100, int(value)))
        self.progress.setValue(value)
        self.progress_value.setText(f'{value}%')

    @Slot(bool, str)
    def worker_finished(self, outcome, message):
        self.append_log(message); self.populate_tree(); self.refresh_analysis_preview(); self.runtime_state = 'idle'; self.control.setEnabled(True); self.pre_analysis.setEnabled(True); self.open_project_button.setEnabled(True); self.control.setText('Çeviriyi Başlat')
        if outcome == 'stopped':
            self.phase.setText('İstek tamamlandı, kapatılıyor.')
        else:
            self.phase.setText('Tamamlandı' if outcome is True else 'Hata')
        if outcome == 'failed':
            self.tabs.setCurrentIndex(0); QMessageBox.critical(self, 'Çeviri durdu', message)

    @Slot()
    def thread_stopped(self):
        self.thread = None; self.worker = None
        if self.pending_close: QTimer.singleShot(0, self.close)

    def closeEvent(self, event: QCloseEvent):
        if self.thread and self.thread.isRunning():
            self.pending_close = True; self.controller.request_close(); event.ignore(); return
        event.accept()


def main():
    app = QApplication(sys.argv); app.setStyleSheet(STYLE)
    window = MainWindow(); window.show(); sys.exit(app.exec())


if __name__ == '__main__': main()
