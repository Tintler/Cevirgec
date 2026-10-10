"""Interface-independent application controller exposed to the web interface.

Every public method is callable from JavaScript through pywebview (`window.pywebview.api`).
Methods return JSON-serialisable values; user-facing failures are returned as
{'error': message} instead of raised. Backend-initiated updates (log lines, progress,
state changes, glossary review requests) are pushed with the injected `emit` callable.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import threading
import traceback
import webbrowser
import zipfile

from app_core import (APP_DIR, ROOT, DEFAULT_CONFIG, Client, GracefulStop, PauseController, TranslationEngine,
                      _dedupe_glossary, analysis_from_checkpoints, analysis_terms, chapters_containing,
                      first_heading, load_analysis_selection, load_config, read, render_analysis, rows,
                      save_json, validate_config, validate_rerun)
from epub_import import prepare
from epub_meta import book_info
from reader import Library
from tts import Engines

SETTINGS_KEYS = tuple(DEFAULT_CONFIG)
LOCKED_WHILE_RUNNING = ('base_url', 'model', 'temperature', 'timeout_seconds', 'chunk_chars', 'max_tokens',
                        'notes_max_tokens', 'fallback_context_length', 'context_safety_tokens',
                        'token_estimation_enabled')
EXCLUDED_FROM_ANALYSIS = re.compile(
    r'\b(cover|title page|copyright|contents|table of contents|dedication|acknowledg(?:e)?ments?|'
    r'about the author|newsletter|sign[ -]?up|bibliography|index|also by|other books|imprint)\b', re.I)
PHASE_LABELS = {'analysis': 'Ön analiz', 'analysis_saved': 'Ön analiz', 'translation': 'Çevriliyor',
                'notes': 'Notlar hazırlanıyor'}
UI_FILE = 'ui.json'


def validate_glossary_rows(items):
    """Same rules as the old glossary dialog: both fields or neither, no duplicate sources."""
    result = []
    for item in items or []:
        source = str(item.get('source', '')).strip()
        target = str(item.get('target', '')).strip()
        if bool(source) != bool(target):
            raise ValueError('Her sözlük satırında iki alan da dolu olmalı.')
        if source:
            result.append({'source': source, 'target': target})
    if len({item['source'].casefold() for item in result}) != len(result):
        raise ValueError('Aynı İngilizce terim birden fazla kez girilmiş.')
    return result


def _error(error):
    return {'error': str(error)}


class Bridge:
    def __init__(self, emit=None, dialogs=None):
        self._emit = emit or (lambda _event, _data: None)
        self._dialogs = dialogs          # object with open_file(), open_folder(); set by desktop.py
        self._lock = threading.RLock()
        self._book = None
        self._epub = None
        self._work_parent = None
        self._fresh_book = False         # prepared in this session but glossary not yet confirmed
        self._controller = None
        self._thread = None
        self._runtime = 'idle'
        self._phase = 'Başlatılmadı'
        self._current = ''
        self._active_filename = None
        self._active_phase = None
        self._active_fraction = 0.0
        self._last_saved = ''
        self._loaded_models = []
        self._review_event = None
        self._review_values = None
        self._pending_close = None       # callable invoked once the worker stops

    # ------------------------------------------------------------------ helpers
    def _log(self, message):
        stamp = datetime.now().strftime('%H:%M:%S')
        line = f'[{stamp}] {message}'
        book = self._book
        if book:
            try:
                with (book / 'translation.log').open('a', encoding='utf-8', newline='\n') as stream:
                    stream.write(line + '\n')
            except OSError:
                pass
        if 'kaydedildi' in message.lower():
            self._last_saved = stamp
        self._emit('log', {'line': line, 'last_saved': self._last_saved})

    def _status(self):
        return {'runtime': self._runtime, 'phase': self._phase, 'current': self._current,
                'last_saved': self._last_saved, 'progress': self._progress()}

    def _push_status(self):
        self._emit('status', self._status())

    def _idle_error(self, action):
        if self._runtime != 'idle':
            return {'error': f'Çalışan çeviri tamamlanmadan veya durmadan {action}.'}
        return None

    def _chapter_states(self):
        """Flat list of chapters with group headers, mirroring the old tree view."""
        book = self._book
        if not book:
            return []
        result, seen_groups = [], set()
        for filename, status in rows(read(book / '00-CONTEXT.md')):
            source_title = first_heading(read(book / 'source' / filename), Path(filename).stem)
            hierarchy = [part.strip() for part in source_title.split(' — ') if part.strip()]
            title = hierarchy[-1] if hierarchy else Path(filename).stem
            translated = book / 'translation' / filename
            if translated.exists():
                title = first_heading(read(translated), title).split(' — ')[-1].strip()
            state_path = book / '_python_translation' / filename / 'state.json'
            state = json.loads(read(state_path)) if state_path.exists() else {}
            if status == 'done' or state.get('complete'):
                label, kind = 'Tamamlandı', 'done'
            elif state.get('parts') or translated.exists():
                label, kind = 'Devam edecek', 'partial'
            else:
                label, kind = ('Sıradaki', 'next') if status == 'next' else ('Bekliyor', 'waiting')
            if filename == self._active_filename and self._active_phase in PHASE_LABELS:
                label, kind = PHASE_LABELS[self._active_phase], 'active'
            key = ()
            for depth, group in enumerate(hierarchy[:-1]):
                key += (group,)
                if key not in seen_groups:
                    seen_groups.add(key)
                    result.append({'type': 'group', 'label': group, 'depth': depth})
            result.append({'type': 'chapter', 'filename': filename, 'title': title, 'label': label,
                           'kind': kind, 'depth': max(0, len(hierarchy) - 1)})
        return result

    def _progress(self):
        book = self._book
        if not book:
            return {'done': 0, 'total': 0, 'percent': 0}
        try:
            states = rows(read(book / '00-CONTEXT.md'))
        except OSError:
            return {'done': 0, 'total': 0, 'percent': 0}
        total = len(states)
        done = sum(1 for _name, status in states if status == 'done')
        fraction = self._active_fraction if self._active_phase in ('translation', 'notes') else 0
        percent = int(min(100, (done + fraction) * 100 / total)) if total else 0
        return {'done': done, 'total': total, 'percent': percent}

    def _book_card(self):
        epub = self._epub
        card = {'epub': str(epub) if epub else '', 'folder': str(self._work_parent) if self._work_parent else '',
                'project': str(self._book) if self._book else '', 'title': '', 'author': '', 'cover': None,
                'chars': 0}
        if epub and Path(epub).exists():
            card.update(book_info(epub))
        elif self._book:
            context = read(self._book / '00-CONTEXT.md')
            title = re.search(r'\*\*Title:\*\*\s*(.+)', context)
            author = re.search(r'\*\*Author:\*\*\s*(.+)', context)
            card['title'] = title.group(1).strip() if title else self._book.name
            card['author'] = author.group(1).strip() if author else ''
        if self._book:
            card['chars'] = sum(len(read(path)) for path in (self._book / 'source').glob('*.md'))
        return card

    def _detach(self):
        """A new EPUB or folder releases the previous project (G28)."""
        self._book = None
        self._fresh_book = False
        self._phase = 'Başlatılmadı'
        self._current = ''
        self._active_filename = self._active_phase = None
        self._active_fraction = 0.0

    # ------------------------------------------------------------------ snapshot
    def snapshot(self):
        with self._lock:
            return {'book': self._book_card(), 'chapters': self._chapter_states(), 'status': self._status(),
                    'analysis': self._analysis_text(), 'model': load_config()['model'],
                    'loaded_models': self._loaded_models}

    def ui_prefs(self):
        path = APP_DIR / UI_FILE
        try:
            return json.loads(read(path)) if path.exists() else {}
        except (OSError, ValueError):
            return {}

    def save_ui_prefs(self, values):
        prefs = self.ui_prefs()
        prefs.update(values or {})
        try:
            save_json(APP_DIR / UI_FILE, prefs)
        except OSError as error:
            return _error(error)
        return prefs

    # ------------------------------------------------------------------ LM Studio
    def refresh_models(self):
        self._emit('models', {'state': 'probing'})
        cfg = dict(load_config())
        cfg['timeout_seconds'] = min(8, int(cfg['timeout_seconds']))

        def probe():
            try:
                _info, models = Client(cfg, lambda _message: None).model_info()
            except Exception as error:
                self._emit('models', {'state': 'failed', 'message': str(error), 'model': cfg['model']})
                return
            loaded = []
            for model in models:
                instances = model.get('loaded_instances') or []
                for instance in instances:
                    context = instance.get('config', {}).get('context_length')
                    loaded.append({'name': instance.get('id') or model.get('key') or model.get('id'), 'context': context})
                if not instances and model.get('state') == 'loaded':
                    loaded.append({'name': model.get('id') or model.get('key'), 'context': model.get('max_context_length')})
            self._loaded_models = list(dict.fromkeys(item['name'] for item in loaded if item.get('name')))
            current = load_config()
            if len(self._loaded_models) == 1 and current['model'] != self._loaded_models[0]:
                current['model'] = self._loaded_models[0]
                try:
                    save_json(APP_DIR / 'config.json', current)
                    self._log('LM Studio’da tek model yüklü olduğu için otomatik seçildi: ' + current['model'])
                except OSError:
                    self._log('UYARI: Tek model otomatik seçildi fakat config.json yazılamadı. Ayarlardan modeli elle seçin.')
            self._emit('models', {'state': 'ok', 'loaded': loaded, 'model': load_config()['model']})

        threading.Thread(target=probe, name='lmstudio-model-probe', daemon=True).start()
        return True

    # ------------------------------------------------------------------ selection
    def choose_epub(self):
        blocked = self._idle_error('seçim değiştirilemez')
        if blocked:
            return blocked
        value = self._dialogs.open_file('Kaynak EPUB seç', ('EPUB (*.epub)',)) if self._dialogs else None
        if not value:
            return None
        with self._lock:
            self._detach()
            self._epub = Path(value)
            if not self._work_parent:
                self._work_parent = self._epub.parent
        return self.snapshot()

    def choose_folder(self):
        blocked = self._idle_error('seçim değiştirilemez')
        if blocked:
            return blocked
        value = self._dialogs.open_folder('Çalışma klasörünü seç') if self._dialogs else None
        if not value:
            return None
        with self._lock:
            self._detach()
            self._work_parent = Path(value)
        return self.snapshot()

    def open_project(self, path=None):
        blocked = self._idle_error('başka proje açılamaz')
        if blocked:
            return blocked
        value = path or (self._dialogs.open_folder('Mevcut çeviri projesini seç') if self._dialogs else None)
        if not value:
            return None
        book = Path(value)
        if not (book / '00-CONTEXT.md').is_file() or not (book / 'source').is_dir():
            return {'error': '00-CONTEXT.md ve source klasörü bulunamadı.'}
        with self._lock:
            self._detach()
            self._book = book
            self._work_parent = book.parent
            marker = book / 'epub-import.json'
            epub = json.loads(read(marker)).get('epub', '') if marker.exists() else ''
            self._epub = Path(epub) if epub else None
            self._phase = 'Mevcut proje hazır'
        return self.snapshot()

    # ------------------------------------------------------------------ previews
    def preview(self, filename):
        book = self._book
        if not book or not filename:
            return None
        source = read(book / 'source' / filename)
        target = book / 'translation' / filename
        translated = read(target) if target.exists() else ''
        return {'source': source, 'translation': translated, 'translated': target.exists(),
                'source_chars': len(source), 'translation_chars': len(translated)}

    def _analysis_text(self):
        book = self._book
        if not book:
            return ''
        merged = analysis_from_checkpoints(book)
        if merged:
            return render_analysis(merged)
        path = book / 'BOOK-ANALYSIS.md'
        return read(path) if path.exists() else ''

    # ------------------------------------------------------------------ start flow
    def prepare_project(self):
        """Step 1 of start: split the EPUB. Returns the glossary to confirm for a new session."""
        blocked = self._idle_error('yeni çeviri başlatılamaz')
        if blocked:
            return blocked
        with self._lock:
            if self._book and not self._fresh_book:
                return {'ok': True, 'glossary_required': False}
            if not self._book:
                if not self._work_parent or not self._epub:
                    return {'error': 'Önce kaynak EPUB dosyasını ve çalışma klasörünü seçin.'}
                try:
                    self._book = prepare(self._epub, self._work_parent)
                except Exception as error:
                    self._book = None
                    return _error(error)
                self._fresh_book = True
            glossary_path = self._book / 'glossary.json'
            existing = json.loads(read(glossary_path)) if glossary_path.exists() else []
            return {'ok': True, 'glossary_required': True, 'glossary': existing}

    def confirm_glossary(self, items):
        """Step 1b: saves the glossary chosen before the first run."""
        with self._lock:
            if not self._book:
                return {'error': 'Proje hazırlanmadı.'}
            try:
                values = validate_glossary_rows(items)
                save_json(self._book / 'glossary.json', values)
            except (ValueError, OSError) as error:
                return _error(error)
            self._fresh_book = False
        return self.snapshot()

    def cancel_prepare(self):
        with self._lock:
            if self._fresh_book:
                self._book = None
                self._fresh_book = False
        return self.snapshot()

    def model_check(self):
        """Step 2: block when the configured model is not loaded, warn when LM Studio was unreachable."""
        configured = load_config()['model']
        if self._loaded_models and configured not in self._loaded_models:
            return {'block': 'Ayarlar bölümünden LM Studio’da yüklü modellerden birini seçin.'}
        if not self._loaded_models:
            return {'warn': 'LM Studio’da yüklü model listesi sorgulanamadı. Sunucunun açık ve modelin yüklü '
                            'olduğunu doğrulayın; aksi hâlde çeviri istekleri başarısız olabilir.'}
        return {}

    def analysis_candidates(self):
        """Step 3 (optional): chapters offered for pre-analysis with the default selection."""
        book = self._book
        if not book:
            return {'error': 'Proje hazırlanmadı.'}
        available = []
        for filename, _status in rows(read(book / '00-CONTEXT.md')):
            title = first_heading(read(book / 'source' / filename), Path(filename).stem)
            available.append({'filename': filename, 'title': title,
                              'story': not EXCLUDED_FROM_ANALYSIS.search(title)})
        existing = load_analysis_selection(book)
        selected = set(existing or [item['filename'] for item in available if item['story']])
        if not selected:
            selected = {item['filename'] for item in available}
        for item in available:
            item['checked'] = item['filename'] in selected
        return {'chapters': available}

    def start(self, analysis_files=None):
        """Step 4: runs the whole book (with optional pre-analysis) on a worker thread."""
        blocked = self._idle_error('yeni çeviri başlatılamaz')
        if blocked:
            return blocked
        if not self._book or self._fresh_book:
            return {'error': 'Proje hazırlanmadı.'}
        if analysis_files is not None and not analysis_files:
            return {'error': 'En az bir bölüm seçin veya ön analizi kapatın.'}
        self._launch(analysis_files=analysis_files)
        return self._status()

    def rerun(self, filenames):
        blocked = self._idle_error('bölüm yeniden çevrilemez')
        if blocked:
            return blocked
        try:
            validate_rerun(self._book, list(filenames))
        except Exception as error:
            return _error(error)
        self._launch(rerun_files=list(filenames))
        return self._status()

    def validate_rerun(self, filename):
        if not self._book:
            return {'error': 'Önce bir proje seçin.'}
        try:
            validate_rerun(self._book, [filename])
        except Exception as error:
            return _error(error)
        return {'ok': True}

    def control(self):
        """Pause / resume toggle for the running worker."""
        # The controller calls _apply_state while holding its own condition, so it must not be
        # invoked under self._lock (lock-order inversion with the worker thread).
        with self._lock:
            runtime, controller = self._runtime, self._controller
        if controller and runtime == 'running':
            controller.request_pause()
        elif controller and runtime == 'paused':
            controller.resume()
        return self._status()

    def _launch(self, analysis_files=None, rerun_files=None):
        with self._lock:
            self._controller = PauseController(self._apply_state)
            self._runtime = 'running'
            self._phase = ('Bölüm yeniden çevriliyor' if rerun_files is not None else
                           'Ön analiz hazırlanıyor' if analysis_files is not None else 'Çeviri çalışıyor')
            self._thread = threading.Thread(target=self._work, args=(self._book, analysis_files, rerun_files),
                                            name='translation-worker', daemon=True)
            self._thread.start()
        self._push_status()

    def _work(self, book, analysis_files, rerun_files):
        outcome, message = 'failed', ''
        try:
            engine = TranslationEngine(load_config, self._controller, self._log, self._on_progress)
            if analysis_files is not None:
                engine.run_analysis(book, analysis_files)
                pending = analysis_terms(book)
                if pending:
                    values = self._ask_review(book, pending)
                    if values is not None:
                        save_json(book / 'glossary.json', values)
                        self._log('Terim önerileri kabul edildi; sözlük güncellendi.')
                    else:
                        self._log('Terim önerileri kabul edilmedi; mevcut sözlük korunuyor.')
            if rerun_files is not None:
                engine.rerun_chapters(book, rerun_files)
                outcome, message = 'done', 'Seçilen bölümler yeniden çevrildi.'
            else:
                engine.run_book(book)
                outcome, message = 'done', 'Çeviri tamamlandı.'
        except GracefulStop as error:
            outcome, message = 'stopped', str(error)
        except Exception as error:
            self._log(traceback.format_exc())
            outcome, message = 'failed', str(error)
        finally:
            self._finish(outcome, message)

    def _finish(self, outcome, message):
        with self._lock:
            self._log(message)
            self._runtime = 'idle'
            self._active_filename = self._active_phase = None
            self._active_fraction = 0.0
            self._phase = {'done': 'Tamamlandı', 'stopped': 'Durduruldu', 'failed': 'Hata'}[outcome]
            self._controller = None
            self._thread = None
            close = self._pending_close
        self._emit('finished', {'outcome': outcome, 'message': message, 'snapshot': self.snapshot()})
        if close:
            close()

    def _apply_state(self, state):
        with self._lock:
            self._runtime = state
            if state == 'paused':
                self._phase = 'Duraklatıldı'
            elif state == 'running':
                self._phase = 'Çeviri çalışıyor'
            elif state == 'pausing':
                self._phase = 'Duraklatılıyor… (geçerli istek bitince)'
            elif state == 'closing':
                self._phase = 'İstek bitince kapanacak'
        self._push_status()

    def _on_progress(self, **data):
        phase = data.get('phase')
        refresh = False
        with self._lock:
            if phase == 'complete':
                self._active_filename = self._active_phase = None
                refresh = True
            elif phase == 'chapter_complete':
                self._active_filename = self._active_phase = None
                self._active_fraction = 0.0
                refresh = True
            else:
                filename = data.get('filename', '')
                part = int(data.get('part', 0))
                parts = max(1, int(data.get('parts', 1)))
                previous = self._active_phase
                changed = filename != self._active_filename or (phase != previous)
                self._active_filename, self._active_phase = filename, phase
                self._active_fraction = part / parts
                labels = {'analysis': 'ön analiz', 'analysis_saved': 'ön analiz', 'translation': 'çeviri', 'notes': 'notlar'}
                if phase in ('analysis', 'analysis_saved'):
                    self._phase = 'Ön analiz yapılıyor'
                elif phase in ('translation', 'notes'):
                    self._phase = 'Çeviri çalışıyor'
                self._current = f'{filename} — {labels.get(phase, phase)} {part}/{parts}'
                refresh = changed
                if phase == 'analysis_saved':
                    self._emit('analysis', {'text': self._analysis_text()})
        if refresh:
            self._emit('chapters', {'chapters': self._chapter_states()})
        self._push_status()

    # ------------------------------------------------------------------ review (worker waits)
    def _ask_review(self, book, pending):
        glossary_path = book / 'glossary.json'
        current = _dedupe_glossary(json.loads(read(glossary_path)) if glossary_path.exists() else [])
        existing = {item['source'].casefold() for item in current}
        proposed = [{'source': str(item.get('source', '')).strip(),
                     'target': str(item.get('suggested_target', '')).strip(),
                     'scope': item.get('scope', 'book'), 'reason': str(item.get('reason', '') or '')}
                    for item in pending
                    if str(item.get('suggested_target', '')).strip()
                    and str(item.get('source', '')).strip().casefold() not in existing]
        if not proposed:
            return None
        event = threading.Event()
        with self._lock:
            self._review_event, self._review_values = event, None
        self._emit('review', {'current': current, 'proposed': proposed})
        while not event.wait(0.5):
            controller = self._controller
            if controller and controller._close_requested:
                return None
        return self._review_values

    def submit_review(self, values=None):
        """values: list of {'source','target'} rows (current + accepted proposals), or None to reject."""
        with self._lock:
            event = self._review_event
            if event is None:
                return False
            self._review_values = _dedupe_glossary(
                [{'source': str(item.get('source', '')).strip(), 'target': str(item.get('target', '')).strip()}
                 for item in values if str(item.get('source', '')).strip() and str(item.get('target', '')).strip()]
            ) if values is not None else None
            self._review_event = None
        event.set()
        return True

    # ------------------------------------------------------------------ glossary editing
    def get_glossary(self):
        if not self._book:
            return {'error': 'Önce bir proje seçin.'}
        path = self._book / 'glossary.json'
        return {'glossary': json.loads(read(path)) if path.exists() else []}

    def save_glossary(self, items):
        """Saves an edited glossary; returns the translated chapters affected by the change."""
        blocked = self._idle_error('sözlük düzenlenemez')
        if blocked:
            return blocked
        if not self._book:
            return {'error': 'Önce bir proje seçin.'}
        try:
            new_glossary = validate_glossary_rows(items)
        except ValueError as error:
            return _error(error)
        path = self._book / 'glossary.json'
        existing = json.loads(read(path)) if path.exists() else []
        old_map = {item.get('source', '').casefold(): item.get('target', '') for item in existing}
        new_map = {item.get('source', '').casefold(): item.get('target', '') for item in new_glossary}
        changed = sorted({key for key in set(old_map) | set(new_map) if old_map.get(key) != new_map.get(key)})
        if not changed:
            return {'changed': False, 'affected': []}
        save_json(path, new_glossary)
        return {'changed': True, 'affected': chapters_containing(self._book, changed)}

    # ------------------------------------------------------------------ settings
    def get_settings(self):
        return {'config': load_config(), 'defaults': DEFAULT_CONFIG, 'loaded_models': self._loaded_models,
                'locked': list(LOCKED_WHILE_RUNNING) if self._runtime in ('running', 'pausing', 'paused', 'closing') else []}

    def save_settings(self, values):
        current = load_config()
        locked = self._runtime in ('running', 'pausing', 'paused', 'closing')
        merged = dict(current)
        for key in SETTINGS_KEYS:
            if key in (values or {}) and not (locked and key in LOCKED_WHILE_RUNNING):
                default = DEFAULT_CONFIG[key]
                value = values[key]
                try:
                    if isinstance(default, bool):
                        value = bool(value)
                    elif isinstance(default, int):
                        value = int(value)
                    elif isinstance(default, float):
                        value = float(value)
                    else:
                        value = str(value).strip()
                except (TypeError, ValueError):
                    return {'error': f'{key} için geçersiz değer.'}
                merged[key] = value
        try:
            validate_config(merged)
            save_json(APP_DIR / 'config.json', merged)
        except (ValueError, OSError) as error:
            return _error(error)
        self._log('Ayarlar kaydedildi. Bitiş işareti/otomatik tekrar ayarları yarım bölüm sürdürüldüğünde; '
                  'diğer çalışma ayarları sonraki bölümde uygulanır.')
        self.refresh_models()
        return {'config': merged}

    # ------------------------------------------------------------------ reading tab
    def _library(self):
        directory = Path(APP_DIR)
        if getattr(self, '_library_cache', None) is None or self._library_cache.directory != directory:
            self._library_cache = Library(directory)
        return self._library_cache

    def _reading(self, action, *args):
        try:
            return action(*args)
        except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
            return _error(error)

    def library_list(self):
        return self._reading(self._library().list)

    def library_add(self, paths=None):
        """Adds EPUB files (opens a multi-select dialog when no paths are given)."""
        if paths is None:
            paths = self._dialogs.open_files('Kitaplığa EPUB ekle', ('EPUB (*.epub)',)) if self._dialogs else None
            if not paths:
                return None
        result = self._reading(self._library().add, list(paths))
        if 'error' not in result:
            result['books'] = self._library().list()
            self._emit('library', result)
        return result

    def library_remove(self, book_id):
        return self._reading(lambda: (self._library().remove(book_id), self._library().list())[1])

    def library_cover(self, book_id):
        return self._reading(self._library().cover, book_id)

    def reader_open(self, book_id):
        return self._reading(self._library().open, book_id)

    def reader_section(self, book_id, number):
        return self._reading(self._library().section, book_id, number)

    def reader_save_position(self, book_id, section, offset):
        return self._reading(self._library().save_position, book_id, section, offset)

    def reader_add_bookmark(self, book_id, section, offset, label):
        return self._reading(self._library().add_bookmark, book_id, section, offset, label)

    def reader_remove_bookmark(self, book_id, bookmark_id):
        return self._reading(self._library().remove_bookmark, book_id, bookmark_id)

    def open_external(self, url):
        """Opens an http(s)/mailto link from a book in the system browser."""
        if not re.match(r'^(?:https?://|mailto:)', str(url), re.I):
            return {'error': 'Yalnızca web ve e-posta bağlantıları açılabilir.'}
        webbrowser.open(str(url))
        return True

    # ------------------------------------------------------------------ read aloud
    def _engines(self):
        directory = Path(APP_DIR)
        cached = getattr(self, '_engines_cache', None)
        if cached is None or cached.app_dir != directory:
            self._engines_cache = Engines(directory, Path(ROOT) / 'engines', emit=self._emit,
                                          prefs=lambda: self.ui_prefs().get('tts', {}))
        return self._engines_cache

    def tts_status(self):
        return self._engines().status()

    def tts_engines(self):
        return self._engines().engines()

    def tts_load(self, language=None):
        """Prepares the engine and voice chosen for a book language (in the background)."""
        device = self.ui_prefs().get('tts', {}).get('device', 'auto')
        return self._engines().load(language or 'tr', device)

    def tts_say(self, text, speed=1.0):
        try:
            return self._engines().say(text, speed)
        except Exception as error:
            return _error(error)

    def tts_install_file(self):
        """Installs an engine package chosen from disk (zip)."""
        path = self._dialogs.open_file('Ses motoru paketi seç', ('Ses motoru paketi (*.zip)',)) if self._dialogs else None
        if not path:
            return None
        return self._engine_job(lambda: self._engines().install_zip(path), 'kuruldu')

    def tts_download(self, engine_id):
        return self._engine_job(lambda: self._engines().download(engine_id), 'indirildi ve kuruldu')

    def _engine_job(self, job, verb):
        def work():
            try:
                engine_id = job()
                self._emit('tts_engines', {'engines': self._engines().engines(), 'done': f'Ses motoru {verb}: {engine_id}'})
            except Exception as error:
                self._emit('tts_engines', {'engines': self._engines().engines(), 'error': str(error)})

        threading.Thread(target=work, name='tts-install', daemon=True).start()
        return {'started': True}

    def tts_remove(self, engine_id):
        return self._reading(self._engines().remove, engine_id)

    def tts_export(self, title, sentences, speed=1.0):
        """Asks where to save, then writes the section as one WAV file on a background thread."""
        engines = self._engines()
        if engines.state != 'ready':
            return {'error': 'Ses motoru henüz hazır değil.'}
        if engines.status()['exporting']:
            return {'error': 'Bir kayıt zaten sürüyor.'}
        name = re.sub(r'[\\/:*?"<>|]+', ' ', str(title or 'bolum')).strip()[:120] or 'bolum'
        path = self._dialogs.save_file('Sesi kaydet', name + '.wav', ('WAV ses dosyası (*.wav)',)) if self._dialogs else None
        if not path:
            return None
        if not str(path).lower().endswith('.wav'):
            path = str(path) + '.wav'

        def work():
            try:
                count = engines.export(sentences, speed, path,
                                       lambda done, total: self._emit('tts_export', {'done': done, 'total': total}))
                self._emit('tts_export', {'finished': True, 'path': str(path), 'count': count})
            except Exception as error:
                self._emit('tts_export', {'finished': True, 'error': str(error)})

        threading.Thread(target=work, name='tts-export', daemon=True).start()
        return {'started': True, 'path': str(path)}

    def shutdown(self):
        """Stops helper processes when the window closes."""
        cached = getattr(self, '_engines_cache', None)
        if cached is not None:
            cached.shutdown()

    # ------------------------------------------------------------------ window lifecycle
    def request_close(self, close):
        """Called when the window is closing. Returns True when it may close now."""
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                return True
            self._pending_close = close
            if self._review_event is not None:
                self._review_values = None
                self._review_event.set()
            controller = self._controller
        if controller:
            controller.request_close()
        return False
