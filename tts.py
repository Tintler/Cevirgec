"""Voice engines for read-aloud: discovery, installation and the worker processes.

An engine (EMA Lightning, Piper, …) is a separate program speaking the JSON-lines protocol in
engines/protocol.py. The user builds engine packages (BUILD_TTS.cmd) and installs them from the
settings page ("Dosyadan kur"); they live next to Çevirgeç in `ses-motorlari/<id>/` (worker.exe,
its runtime, motor.json and a data/ folder for the models and voices it fetches). When Çevirgeç runs
from source, an engine whose Python packages are installed is started from engines/<name>/ instead,
so development needs no packaging. Which engine and voice read a book is chosen per language.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import itertools
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import threading
import time
import wave
import zipfile

ENGINES_DIR = 'ses-motorlari'
PROTOCOL = 1
AUDIO_RATE = 48000     # requested from engines (those that cannot choose return their own rate)
CACHE_DIR = 'ses-onbellegi'   # rendered sentences per book: ses-onbellegi/<book id>/<key>.wav
SENTENCE_GAP = 0.35    # seconds of silence between sentences in an exported file
DEFAULT_ENGINE = {'tr': 'ema-lightning'}
MAX_PATH = 259
DATA_PATH_BUDGET = 70   # room for downloaded models/voices under <engine>/data
FROZEN = bool(getattr(sys, 'frozen', False))


def language_key(language):
    return str(language or 'tr').split('-')[0].split('_')[0].lower() or 'tr'


def _gap(gaps, index):
    """Seconds of silence after sentence `index`: the given one (clamped to 0–5 s) or SENTENCE_GAP."""
    try:
        return min(5.0, max(0.0, float(gaps[index])))
    except (IndexError, TypeError, ValueError):
        return SENTENCE_GAP


class Worker:
    """One engine process: request/response over stdin/stdout, events forwarded to `on_event`."""

    def __init__(self, engine, data, log_path, on_event):
        self.engine, self.data, self.log_path, self.on_event = engine, Path(data), Path(log_path), on_event
        self._ids = itertools.count(1)
        self._pending = {}
        self._lock = threading.Lock()
        self._write = threading.Lock()
        self._ready = threading.Event()
        self.process = None

    def start(self, timeout=60):
        self.data.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, 'w', encoding='utf-8', errors='replace')
        if self.engine['mode'] == 'package':
            command, cwd = [str(Path(self.engine['dir']) / self.engine.get('entry', 'worker.exe'))], self.engine['dir']
        else:
            command, cwd = [sys.executable, '-u', '-m', self.engine['module']], self.engine['root']
        env = dict(os.environ, CEVIRGEC_ENGINE_DATA=str(self.data), PYTHONIOENCODING='utf-8')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=log, creationflags=flags)
        finally:
            log.close()
        threading.Thread(target=self._read, name=f'engine-{self.engine["id"]}', daemon=True).start()
        if not self._ready.wait(timeout):
            self.stop()
            raise RuntimeError('Ses motoru başlatılamadı. ' + self.log_tail())

    def _read(self):
        for raw in self.process.stdout:
            try:
                message = json.loads(raw.decode('utf-8'))
            except ValueError:
                continue
            if message.get('event') == 'ready':
                if message.get('protocol') == PROTOCOL:
                    self._ready.set()
            elif 'event' in message:
                self.on_event(message)
            elif 'id' in message:
                with self._lock:
                    slot = self._pending.pop(message['id'], None)
                if slot:
                    slot['message'] = message
                    slot['done'].set()
        # The process ended: fail everything still waiting.
        with self._lock:
            pending, self._pending = self._pending, {}
        for slot in pending.values():
            slot['message'] = {'ok': False, 'error': 'Ses motoru beklenmedik biçimde kapandı. ' + self.log_tail()}
            slot['done'].set()

    def request(self, command, timeout=None, **payload):
        if not self.alive:
            raise RuntimeError('Ses motoru çalışmıyor. ' + self.log_tail())
        number = next(self._ids)
        slot = {'done': threading.Event(), 'message': None}
        with self._lock:
            self._pending[number] = slot
        line = json.dumps({'id': number, 'cmd': command, **payload}, ensure_ascii=False) + '\n'
        try:
            with self._write:
                self.process.stdin.write(line.encode('utf-8'))
                self.process.stdin.flush()
        except OSError as error:
            raise RuntimeError('Ses motoruna ulaşılamadı. ' + self.log_tail()) from error
        if not slot['done'].wait(timeout):
            with self._lock:
                self._pending.pop(number, None)
            raise RuntimeError('Ses motoru yanıt vermedi.')
        message = slot['message']
        if not message.get('ok'):
            raise RuntimeError(message.get('error') or 'Ses motoru hatası.')
        return message.get('result') or {}

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    def log_tail(self, lines=6):
        try:
            text = self.log_path.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            return ''
        tail = [line.strip() for line in text if line.strip()][-lines:]
        return ('Ayrıntı: ' + ' | '.join(tail))[:900] if tail else ''

    def stop(self):
        if not self.process:
            return
        if self.alive:
            try:
                with self._write:
                    self.process.stdin.write(b'{"id": 0, "cmd": "shutdown"}\n')
                    self.process.stdin.flush()
                self.process.wait(3)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if self.alive:
            self.process.kill()
            try:
                self.process.wait(3)
            except subprocess.TimeoutExpired:
                pass
        for pipe in (self.process.stdin, self.process.stdout):
            try:
                pipe.close()
            except OSError:
                pass


class Engines:
    """Lists, installs and runs voice engines; keeps at most one worker process running."""

    def __init__(self, app_dir, source_dir, emit=None, prefs=None):
        self.app_dir, self.source_dir = Path(app_dir), Path(source_dir)
        self._emit = emit or (lambda _event, _data: None)
        self._prefs = prefs or (lambda: {})
        self._lock = threading.RLock()
        self._worker = None
        self._loading = None
        self.state, self.message = 'idle', ''
        self.current = {'engine': None, 'voice': None, 'device': None, 'language': None}
        self._exporting = False
        self._busy = {}               # engine id -> installation progress text

    # ------------------------------------------------------------- discovery
    @property
    def install_root(self):
        return self.app_dir / ENGINES_DIR

    def _manifests(self):
        """Known engines: bundled descriptions (engines/*/motor.json) merged with installed packages."""
        known = {}
        for path in sorted(self.source_dir.glob('*/motor.json')):
            try:
                manifest = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            manifest['root'] = str(self.source_dir.parent)
            known[manifest['id']] = manifest
        if self.install_root.exists():
            for path in sorted(self.install_root.glob('*/motor.json')):
                try:
                    manifest = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                if manifest.get('protocol') == PROTOCOL and (path.parent / manifest.get('entry', 'worker.exe')).exists():
                    merged = dict(known.get(manifest['id'], {}), **manifest)
                    merged['dir'] = str(path.parent)
                    merged['installed_version'] = manifest.get('version')
                    known[manifest['id']] = merged
        return known

    def _mode(self, manifest):
        if (not FROZEN and manifest.get('module') and manifest.get('root')
                and all(importlib.util.find_spec(name) for name in manifest.get('python_modules', []))):
            return 'source'
        return 'package' if manifest.get('dir') else None

    def _data_dir(self, engine_id):
        return self.install_root / engine_id / 'data'

    def engines(self):
        result = []
        for engine_id, manifest in self._manifests().items():
            data = self._data_dir(engine_id)
            voices = [dict(voice, downloaded=all((data / name).exists() for name in voice.get('files', [])))
                      for voice in manifest.get('voices', [])]
            result.append({
                'id': engine_id, 'name': manifest.get('name', engine_id), 'version': manifest.get('version', ''),
                'installed_version': manifest.get('installed_version'), 'description': manifest.get('description', ''),
                'license': manifest.get('license', ''), 'homepage': manifest.get('homepage', ''),
                'mode': self._mode(manifest), 'removable': bool(manifest.get('dir')), 'speed': bool(manifest.get('speed')),
                'devices': manifest.get('devices', ['cpu']), 'voices': voices,
                'languages': sorted({voice['language'] for voice in voices}),
                'busy': self._busy.get(engine_id),
            })
        return result

    def choice(self, language):
        """(engine, voice) for a book language: the user's choice when usable, otherwise a default."""
        key = language_key(language)
        usable = {item['id']: item for item in self.engines() if item['mode']}
        engine_id, _, voice_id = ((self._prefs().get('voices') or {}).get(key) or '').partition('/')
        if engine_id in usable and any(v['id'] == voice_id and v['language'] == key for v in usable[engine_id]['voices']):
            return engine_id, voice_id
        for candidate in [DEFAULT_ENGINE.get(key)] + sorted(usable):
            voice = next((v for v in usable.get(candidate, {}).get('voices', []) if v['language'] == key), None)
            if voice:
                return candidate, voice['id']
        return None, None

    # ------------------------------------------------------------- status
    def status(self):
        return {'state': self.state, 'message': self.message, 'exporting': self._exporting, **self.current}

    def _set(self, state, message=''):
        self.state, self.message = state, message
        self._emit('tts', self.status())

    def _on_event(self, message):
        if message.get('event') == 'stage' and self.state == 'loading':
            self._set('loading', message.get('message', ''))

    # ------------------------------------------------------------- loading and speech
    def load(self, language, device='auto'):
        """Starts (or reuses) the worker for a book language in the background; returns the status."""
        engine_id, voice_id = self.choice(language)
        with self._lock:
            key = language_key(language)
            if engine_id is None:
                self.current = {'engine': None, 'voice': None, 'device': None, 'language': key}
                self._set('missing', 'Bu dil için kullanılabilir bir ses motoru yok. Ayarlar → Sesli okuma bölümünden kurun.')
                return self.status()
            target = {'engine': engine_id, 'voice': voice_id, 'device': device or 'auto', 'language': key}
            options = ((self._prefs().get('options') or {}).get(f'{engine_id}/{voice_id}') or {})
            if self._loading and self._loading.is_alive():
                return self.status()
            same = (self._worker and self._worker.alive and self.state == 'ready'
                    and (self.current['engine'], self.current['voice']) == (engine_id, voice_id)
                    and self._requested_device == target['device'] and self._options == options)
            if same:
                self.current['language'] = key
                return self.status()
            self.current = target
            self._requested_device = target['device']
            self._set('loading', 'Ses motoru başlatılıyor…')
            self._options = options
            self._loading = threading.Thread(target=self._load, args=(target, options), name='tts-load', daemon=True)
            self._loading.start()
        return self.status()

    _requested_device = None
    _options = None

    def _load(self, target, options=None):
        try:
            manifest = self._manifests()[target['engine']]
            mode = self._mode(manifest)
            if not mode:
                raise RuntimeError('Ses motoru kurulu değil.')
            worker = self._worker
            if not (worker and worker.alive and worker.engine['id'] == target['engine']
                    and self._requested_device == target['device']):
                self.stop_worker()
                worker = Worker(dict(manifest, mode=mode), self._data_dir(target['engine']),
                                self.install_root / target['engine'] / 'worker.log', self._on_event)
                worker.start()
                self._worker = worker
            result = worker.request('load', voice=target['voice'], device=target['device'], options=options or {})
            self.current = dict(target, device=result.get('device', target['device']))
            self._set('ready', 'Hazır')
        except Exception as error:
            self._set('error', f'Ses motoru yüklenemedi: {error}')

    def _ready_worker(self):
        if self.state != 'ready' or not self._worker or not self._worker.alive:
            raise RuntimeError('Ses motoru hazır değil.')
        return self._worker

    # ------------------------------------------------------------- sentence cache
    def _cache_path(self, book, text, speed, language):
        """Where a sentence rendered with the voice chosen for `language` is kept (None: no caching)."""
        if not book or not re.fullmatch(r'[0-9a-f]{8,40}', str(book)):
            return None
        engine_id, voice_id = self.choice(language)
        if engine_id is None:
            return None
        options = (self._prefs().get('options') or {}).get(f'{engine_id}/{voice_id}') or {}
        key = json.dumps([engine_id, voice_id, options, round(float(speed), 2), text], sort_keys=True, ensure_ascii=False)
        return self.app_dir / CACHE_DIR / book / (hashlib.sha1(key.encode('utf-8')).hexdigest() + '.wav')

    @staticmethod
    def _clip(data):
        with wave.open(io.BytesIO(data)) as clip:
            duration = clip.getnframes() / clip.getframerate()
        return {'audio': base64.b64encode(data).decode('ascii'), 'duration': duration}

    def say(self, text, speed=1.0, book=None, language='tr'):
        """One sentence as base64 WAV: from the book's cache when it was rendered before, else from the engine."""
        text = ' '.join(str(text).split())
        if not text:
            return {'audio': '', 'duration': 0.0}
        path = self._cache_path(book, text, speed, language)
        if path is not None and path.exists():
            return dict(self._clip(path.read_bytes()), cached=True)
        worker = self._ready_worker()
        engine_id, voice_id = self.choice(language)
        if (self.current.get('engine'), self.current.get('voice')) != (engine_id, voice_id):
            raise RuntimeError('Ses motoru hazır değil.')
        result = worker.request('say', text=text, speed=float(speed), rate=AUDIO_RATE, timeout=600)
        if path is not None and result.get('audio'):
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix('.part')
            partial.write_bytes(base64.b64decode(result['audio']))
            os.replace(partial, path)
        return {'audio': result.get('audio', ''), 'duration': result.get('duration', 0.0), 'cached': False}

    def cached(self, book, sentences, speed, language='tr'):
        """How many of the sentences are already rendered for this book and voice."""
        count = 0
        for sentence in sentences:
            text = ' '.join(str(sentence).split())
            path = self._cache_path(book, text, speed, language) if text else None
            count += bool(path is not None and path.exists())
        return count

    def prepare(self, book, sentences, speed, language='tr', progress=None, cancel=None):
        """Renders sentences into the book's cache (blocking); already rendered ones are skipped."""
        texts = [' '.join(str(s).split()) for s in sentences if str(s).strip()]
        for index, text in enumerate(texts):
            if cancel is not None and cancel.is_set():
                return index
            self.say(text, speed, book, language)
            if progress:
                progress(index + 1, len(texts))
        return len(texts)

    def cache_info(self):
        root = self.app_dir / CACHE_DIR
        files = list(root.rglob('*.wav')) if root.exists() else []
        return {'files': len(files), 'bytes': sum(f.stat().st_size for f in files)}

    def clear_cache(self, book=None):
        root = self.app_dir / CACHE_DIR
        target = root / book if book and re.fullmatch(r'[0-9a-f]{8,40}', str(book)) else root
        shutil.rmtree(target, ignore_errors=True)
        return self.cache_info()

    def export(self, sentences, speed, path, progress=None, book=None, language='tr', gaps=None):
        """Writes all sentences into one WAV file, reusing (and filling) the book's cache; blocking.
        `gaps` gives the seconds of silence after each sentence (default SENTENCE_GAP)."""
        gaps = list(gaps or ())
        pairs = [(' '.join(str(s).split()), _gap(gaps, i)) for i, s in enumerate(sentences) if str(s).strip()]
        texts = [text for text, _ in pairs]
        if not texts:
            raise ValueError('Kaydedilecek metin yok.')
        self._exporting = True
        stream = None
        try:
            for index, (text, gap) in enumerate(pairs):
                result = self.say(text, speed, book, language)
                with wave.open(io.BytesIO(base64.b64decode(result['audio']))) as clip:
                    if stream is None:
                        rate = clip.getframerate()
                        stream = wave.open(str(path), 'wb')
                        stream.setnchannels(1)
                        stream.setsampwidth(2)
                        stream.setframerate(rate)
                    stream.writeframes(clip.readframes(clip.getnframes()))
                    stream.writeframes(b'\x00\x00' * int(rate * gap))
                if progress:
                    progress(index + 1, len(texts))
        finally:
            if stream is not None:
                stream.close()
            self._exporting = False
        return len(texts)

    def stop_worker(self):
        worker, self._worker = self._worker, None
        if worker:
            worker.stop()

    def shutdown(self):
        self.stop_worker()

    # ------------------------------------------------------------- install / remove
    def _writable_root(self):
        try:
            self.install_root.mkdir(parents=True, exist_ok=True)
            probe = self.install_root / '.yazma-denemesi'
            probe.write_text('', encoding='utf-8')
            probe.unlink()
        except OSError as error:
            raise RuntimeError(f"Program klasörüne yazılamıyor ({self.install_root}). Çevirgeç'i yazılabilir bir "
                               'klasöre (örneğin Belgeler) taşıyın ya da yönetici olarak çalıştırın.') from error

    def _progress(self, engine_id, message):
        if message:
            self._busy[engine_id] = message
        else:
            self._busy.pop(engine_id, None)
        self._emit('tts_engines', {'engines': self.engines()})

    def install_zip(self, path):
        """Installs an engine package: a zip with motor.json at its root or inside one top folder."""
        self._writable_root()
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if not name.endswith('/')]
            manifest_name = min((name for name in names if PurePosixPath(name).name == 'motor.json'
                                 and len(PurePosixPath(name).parts) <= 2), key=len, default=None)
            if not manifest_name:
                raise ValueError('Bu dosya bir Çevirgeç ses motoru paketi değil (motor.json yok).')
            manifest = json.loads(archive.read(manifest_name).decode('utf-8'))
            engine_id = str(manifest.get('id', ''))
            if not engine_id or not all(c.isalnum() or c in '-_' for c in engine_id):
                raise ValueError('Paketteki motor kimliği geçersiz.')
            if manifest.get('protocol') != PROTOCOL:
                raise ValueError('Bu ses motoru paketi bu Çevirgeç sürümüyle uyumlu değil.')
            prefix = str(PurePosixPath(manifest_name).parent)
            prefix = '' if prefix == '.' else prefix + '/'
            if prefix + manifest.get('entry', 'worker.exe') not in names:
                raise ValueError('Pakette motor programı eksik.')
            members = [name for name in names if name.startswith(prefix)]
            final = self.install_root / engine_id
            longest = max(len(str(final / name[len(prefix):])) for name in members)
            longest = max(longest, len(str(final / 'data')) + DATA_PATH_BUDGET)
            if longest > MAX_PATH:
                raise RuntimeError(f'Program klasörünün yolu bu motor için çok uzun ({len(str(self.app_dir))} karakter). '
                                   "Windows'un dosya yolu sınırı nedeniyle kurulamaz; Çevirgeç'i daha kısa yollu bir "
                                   'klasöre (örneğin C:\\Cevirgec) taşıyın.')
            staging = self.install_root / f'.{engine_id}.kur'
            shutil.rmtree(staging, ignore_errors=True)
            try:
                staging.mkdir(parents=True)
                base = staging.resolve()
                for index, name in enumerate(members):
                    target = (staging / name[len(prefix):]).resolve()
                    if base not in target.parents:
                        raise ValueError('Paket güvenli olmayan bir dosya yolu içeriyor.')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(name) as source, open(target, 'wb') as out:
                        shutil.copyfileobj(source, out)
                    if index % 200 == 0:
                        self._progress(engine_id, f'Açılıyor… %{index * 100 // max(1, len(members))}')
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                self._progress(engine_id, None)
                raise
        with self._lock:
            if self._worker and self._worker.engine['id'] == engine_id:
                self.stop_worker()
                self._set('idle', '')
            if final.exists():
                if (final / 'data').exists():            # keep downloaded models and voices
                    shutil.move(str(final / 'data'), str(staging / 'data'))
                shutil.rmtree(final)
            staging.rename(final)
        self._progress(engine_id, None)
        return engine_id

    def remove(self, engine_id):
        folder = self.install_root / engine_id
        if not (folder / 'motor.json').exists():
            raise ValueError('Bu ses motoru paket olarak kurulu değil.')
        with self._lock:
            if self._worker and self._worker.engine['id'] == engine_id:
                self.stop_worker()
                self._set('idle', '')
            shutil.rmtree(folder)
        return self.engines()
