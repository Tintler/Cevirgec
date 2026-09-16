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
from PySide6.QtGui import QCloseEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QDoubleSpinBox, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app_core import (APP_DIR, ROOT, DEFAULT_CONFIG, Client, GracefulStop, PauseController, TranslationEngine,
                      analysis_from_checkpoints, first_heading, load_analysis_selection,
                      load_config, read, render_analysis, rows, save_json)
from epub_import import prepare


STYLE = """
QWidget { background:#10141b; color:#d9dde5; font:10pt 'Segoe UI'; }
QMainWindow { background:#0c1016; }
QLineEdit,QPlainTextEdit,QTreeWidget,QTableWidget,QTabWidget::pane,QSpinBox,QDoubleSpinBox,QComboBox {
  background:#151b24; border:1px solid #293241; padding:5px; selection-background-color:#805f19;
}
QComboBox QAbstractItemView { background:#151b24; border:1px solid #354154; selection-background-color:#805f19; }
QPushButton { background:#202936; border:1px solid #354154; padding:7px 12px; }
QPushButton:hover { background:#293548; }
QPushButton#primary { background:#d0a438; color:#11151b; font-weight:600; border:0; }
QPushButton#primary:hover { background:#e0b64c; }
QTabBar::tab { background:#151b24; padding:8px 15px; border:1px solid #293241; }
QTabBar::tab:selected { color:#e1b347; border-bottom:2px solid #e1b347; }
QProgressBar { border:1px solid #293241; background:#151b24; text-align:center; }
QProgressBar::chunk { background:#d0a438; }
QHeaderView::section { background:#1a222e; border:0; padding:6px; }
QLabel#appTitle { font-size:18pt; font-weight:600; color:#f0b82e; }
QLabel#modelStatus { background:#151b24; border:1px solid #293241; padding:7px; }
QLabel#settingsWarning { background:#2a2112; color:#e7c66b; border:1px solid #705a25; padding:8px; }
"""


class ModelSignals(QObject):
    result = Signal(dict)
    failed = Signal(str)


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
    log = Signal(str); progress = Signal(dict); state = Signal(str); finished = Signal(bool, str)

    def __init__(self, book, controller, analysis_files=None):
        super().__init__(); self.book = Path(book); self.controller = controller; self.analysis_files = analysis_files

    @Slot()
    def run(self):
        try:
            engine = TranslationEngine(load_config, self.controller, self.log.emit, lambda **values: self.progress.emit(values))
            if self.analysis_files is not None:
                engine.run_analysis(self.book, self.analysis_files)
            engine.run_book(self.book)
            self.finished.emit(True, 'Çeviri tamamlandı.')
        except GracefulStop as error:
            self.finished.emit(True, str(error))
        except Exception as error:
            self.log.emit(traceback.format_exc())
            self.finished.emit(False, str(error))


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
        central = QWidget(); root = QVBoxLayout(central); self.setCentralWidget(central)
        header = QHBoxLayout(); logo = QLabel()
        logo_path = ROOT / 'assets' / 'app.png'
        if logo_path.exists(): logo.setPixmap(QPixmap(str(logo_path)).scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        title = QLabel('Çevirgeç'); title.setObjectName('appTitle')
        self.open_project_button = QPushButton('Projeyi sürdür')
        self.open_project_button.setObjectName('openProjectButton')
        self.open_project_button.setToolTip(
            'Daha önce oluşturulmuş {KitapAdı}-ceviri klasörünü açar ve kayıtlı checkpointlerden devam eder.'
        )
        self.open_project_button.clicked.connect(self.choose_existing)
        settings = QPushButton('Ayarlar'); settings.setObjectName('settingsButton'); settings.clicked.connect(self.show_settings)
        header.addWidget(logo); header.addWidget(title); header.addStretch()
        header.addWidget(self.open_project_button); header.addWidget(settings); root.addLayout(header)
        top = QFormLayout()
        self.model_label = QLabel(load_config()['model']); self.model_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addRow('Seçili model', self.model_label)
        self.model_status = QLabel('LM Studio sorgulanıyor…'); self.model_status.setObjectName('modelStatus'); self.model_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        refresh = QPushButton('Yenile'); refresh.clicked.connect(self.refresh_model_info)
        model_row = QHBoxLayout(); model_row.addWidget(self.model_status, 1); model_row.addWidget(refresh)
        top.addRow('LM Studio', model_row)
        self.epub_edit = QLineEdit(); self.epub_edit.setReadOnly(True); choose_epub = QPushButton('Dosya seç'); choose_epub.clicked.connect(self.choose_epub)
        row = QHBoxLayout(); row.addWidget(self.epub_edit); row.addWidget(choose_epub); top.addRow('EPUB dosyası', row)
        self.folder_edit = QLineEdit(); self.folder_edit.setReadOnly(True); choose_folder = QPushButton('Klasör seç'); choose_folder.clicked.connect(self.choose_folder)
        row2 = QHBoxLayout(); row2.addWidget(self.folder_edit); row2.addWidget(choose_folder); top.addRow('Çalışma klasörü', row2)
        root.addLayout(top)
        splitter = QSplitter(Qt.Horizontal); root.addWidget(splitter, 1)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(['Bölümler', 'Durum'])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch); self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.setMinimumWidth(390); self.tree.itemSelectionChanged.connect(self.preview_selected); splitter.addWidget(self.tree)
        self.tabs = QTabWidget(); splitter.addWidget(self.tabs); splitter.setSizes([410, 790])
        status = QWidget(); status_layout = QVBoxLayout(status)
        self.phase = QLabel('Başlatılmadı'); self.current = QLabel('Bölüm seçilmedi')
        self.progress = QProgressBar(); self.progress.setRange(0, 100)
        status_layout.addWidget(self.phase); status_layout.addWidget(self.current); status_layout.addWidget(self.progress); status_layout.addStretch()
        self.tabs.addTab(status, 'Durum')
        self.console = QPlainTextEdit(); self.console.setReadOnly(True); self.console.document().setMaximumBlockCount(10000); self.tabs.addTab(self.console, 'Konsol')
        self.analysis_preview = QPlainTextEdit(); self.analysis_preview.setReadOnly(True)
        self.analysis_preview.setPlainText('Ön analiz seçilirse sonuç burada gösterilir.')
        self.tabs.addTab(self.analysis_preview, 'Ön Analiz')
        preview = QSplitter(Qt.Horizontal); self.source_preview = QPlainTextEdit(); self.translation_preview = QPlainTextEdit()
        self.source_preview.setReadOnly(True); self.translation_preview.setReadOnly(True)
        preview.addWidget(self.source_preview); preview.addWidget(self.translation_preview); self.tabs.addTab(preview, 'Kaynak / Türkçe')
        bottom = QHBoxLayout(); self.last_saved = QLabel('Son kayıt: —'); bottom.addWidget(self.last_saved); bottom.addStretch()
        self.pre_analysis = QCheckBox('Ön analiz yap (isteğe bağlı)')
        self.pre_analysis.setChecked(False)
        self.pre_analysis.setToolTip('İsteğe bağlıdır. Kitap çeviriden önce analiz edilir ve toplam işlem süresi iki kata kadar uzayabilir.')
        bottom.addWidget(self.pre_analysis)
        self.control = QPushButton('Çeviriyi Başlat'); self.control.setObjectName('primary'); self.control.clicked.connect(self.control_clicked); bottom.addWidget(self.control)
        root.addLayout(bottom)

    def choose_folder(self):
        value = QFileDialog.getExistingDirectory(self, 'Çalışma klasörünü seç')
        if value: self.work_parent = Path(value); self.folder_edit.setText(value)

    def choose_epub(self):
        value, _ = QFileDialog.getOpenFileName(self, 'Kaynak EPUB seç', '', 'EPUB (*.epub)')
        if value: self.epub = Path(value); self.epub_edit.setText(value)

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
            self.model_label.setText(value['model']); self.append_log('Ayarlar kaydedildi. Çalışan bölüm varsa değişiklik sonraki bölümde uygulanır.'); self.refresh_model_info()
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
            cfg['model'] = self.loaded_models[0]; save_json(APP_DIR / 'config.json', cfg)
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

    def preview_selected(self):
        selected = self.tree.selectedItems()
        if not selected or not self.book: return
        filename = selected[0].data(0, Qt.UserRole)
        if not filename: return
        self.source_preview.setPlainText(read(self.book / 'source' / filename))
        target = self.book / 'translation' / filename
        self.translation_preview.setPlainText(read(target) if target.exists() else 'Henüz çevrilmedi.')
        self.tabs.setCurrentIndex(3)

    def refresh_analysis_preview(self):
        if not self.book:
            self.analysis_preview.setPlainText('Ön analiz seçilirse sonuç burada gösterilir.'); return
        merged = analysis_from_checkpoints(self.book)
        path = self.book / 'BOOK-ANALYSIS.md'
        if merged:
            self.analysis_preview.setPlainText(render_analysis(merged))
        else:
            self.analysis_preview.setPlainText(read(path) if path.exists() else 'Ön analiz seçilirse sonuç burada gösterilir.')

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
        self.thread.started.connect(self.worker.run); self.worker.log.connect(self.append_log); self.worker.progress.connect(self.update_progress); self.worker.state.connect(self.apply_state); self.worker.finished.connect(self.worker_finished)
        self.worker.finished.connect(self.thread.quit); self.thread.finished.connect(self.worker.deleteLater); self.thread.finished.connect(self.thread_stopped); self.thread.finished.connect(self.thread.deleteLater)
        self.runtime_state = 'running'; self.control.setText('Duraklat'); self.phase.setText('Ön analiz hazırlanıyor' if use_analysis else 'Çeviri çalışıyor'); self.tabs.setCurrentIndex(2 if use_analysis else 0); self.thread.start()

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
        if phase == 'complete': self.progress.setValue(100); self.populate_tree(); return
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
        self.current.setText(f'{filename} — {labels.get(phase, phase)} {part}/{parts}'); self.progress.setValue(int(part * 100 / parts))

    @Slot(bool, str)
    def worker_finished(self, success, message):
        self.append_log(message); self.populate_tree(); self.refresh_analysis_preview(); self.runtime_state = 'idle'; self.control.setEnabled(True); self.pre_analysis.setEnabled(True); self.open_project_button.setEnabled(True); self.control.setText('Çeviriyi Başlat')
        self.phase.setText('Tamamlandı' if success else 'Hata');
        if not success: self.tabs.setCurrentIndex(1); QMessageBox.critical(self, 'Çeviri durdu', message)

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
