"""Shared translation engine used by both the CLI and the PySide6 GUI."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import urllib.error
import urllib.request

ROOT = Path(getattr(__import__('sys'), '_MEIPASS', Path(__file__).resolve().parent))
APP_DIR = Path(__import__('sys').executable).resolve().parent if getattr(__import__('sys'), 'frozen', False) else Path(__file__).resolve().parent
DEFAULT_CONFIG = {
    'base_url': 'http://127.0.0.1:1234/v1',
    'model': 'gemma-4-26b-a4b-it-qat@q4_k_xl',
    'chunk_chars': 6000,
    'max_tokens': 8192,
    'notes_max_tokens': 4096,
    'temperature': 0.2,
    'timeout_seconds': 1800,
    'token_estimation_enabled': True,
    'fallback_context_length': 32768,
    'context_safety_tokens': 2048,
}

ANALYSIS_VERSION = 2
ANALYSIS_AUTO_RETRIES = 2
TRANSLATION_AUTO_RETRIES = 2
ANALYSIS_RESPONSE_ERROR_PREFIXES = (
    'On analiz ', 'Yanit ', 'Beklenmeyen API cikti', 'Cikti token sinirina ulasti',
    'Bos veya kod blogu', 'Gorunur newline',
)
TRANSLATION_RESPONSE_ERROR_PREFIXES = (
    'Yanit tamamlanma isareti', 'Beklenmeyen API cikti', 'Bos veya kod blogu',
    'Yanit dusunce/tool isaretleri', 'Gorunur newline kacislari', 'Glossary zorunlulugu',
)
ANALYSIS_PROMPT = '''Analyze only the supplied fragment before translation. Return only valid JSON, without Markdown fences or commentary, using this exact shape:
{"summary":"short Turkish summary","characters":[{"name":"name","facts":["source-grounded fact"],"voice":["speech or inner-voice observation"],"scope":"book|chapter"}],"terms":[{"source":"English term","suggested_target":"Turkish suggestion","reason":"short reason","scope":"book|chapter"}],"style":[{"observation":"source-grounded style observation","scope":"book|chapter"}],"changes":["change in voice, relationship, role, mood or narrative style within this fragment"],"ambiguities":["translation risk or ambiguity"]}
Scope rules are strict. Use "book" only for a fact or convention that is explicitly stable and independent of time, plot, mood, relationship, location, knowledge, role or character development. Otherwise use "chapter". When uncertain, use "chapter". Do not translate the fragment. Do not invent facts. User glossary always has priority.'''


class GracefulStop(Exception):
    """Raised only after the current response has been checkpointed."""


def retryable_translation_error(error):
    message = str(error)
    if isinstance(error, ValueError):
        return message.startswith(TRANSLATION_RESPONSE_ERROR_PREFIXES)
    if isinstance(error, RuntimeError):
        return (message.startswith(('LM Studio baglantisi kurulamadi', 'LM Studio istegi zaman asimina ugradi')) or
                bool(re.match(r'HTTP (?:408|429|5\d\d):', message)))
    return False


class PauseController:
    def __init__(self, state_callback=None):
        self._condition = threading.Condition()
        self._pause_requested = False
        self._close_requested = False
        self._state_callback = state_callback or (lambda _state: None)

    def set_state_callback(self, callback):
        self._state_callback = callback

    @property
    def pause_requested(self):
        with self._condition:
            return self._pause_requested

    def request_pause(self):
        with self._condition:
            self._pause_requested = True
        self._state_callback('pausing')

    def resume(self):
        with self._condition:
            self._pause_requested = False
            self._condition.notify_all()
        self._state_callback('running')

    def request_close(self):
        with self._condition:
            self._close_requested = True
            self._pause_requested = False
            self._condition.notify_all()
        self._state_callback('closing')

    def checkpoint(self):
        with self._condition:
            if self._close_requested:
                raise GracefulStop('Mevcut istek kaydedildi; program kapatiliyor.')
            if not self._pause_requested:
                return
            self._state_callback('paused')
            while self._pause_requested and not self._close_requested:
                self._condition.wait()
            if self._close_requested:
                raise GracefulStop('Mevcut istek kaydedildi; program kapatiliyor.')
            self._state_callback('running')


def read(path):
    return Path(path).read_text(encoding='utf-8-sig')


def atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def save_json(path, value):
    atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def load_config(path=None):
    path = Path(path or APP_DIR / 'config.json')
    value = dict(DEFAULT_CONFIG)
    if path.exists():
        value.update(json.loads(read(path)))
    validate_config(value)
    return value


def validate_config(cfg):
    from urllib.parse import urlparse
    parsed = urlparse(str(cfg['base_url']))
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('base_url yalnizca yerel HTTP adresi olabilir.')
    if not str(cfg['model']).strip():
        raise ValueError('Model adi bos olamaz.')
    for key in ('chunk_chars', 'max_tokens', 'notes_max_tokens', 'timeout_seconds', 'fallback_context_length'):
        if int(cfg[key]) <= 0:
            raise ValueError(f'{key} sifirdan buyuk olmali.')
    if not 0 <= float(cfg['temperature']) <= 2:
        raise ValueError('temperature 0 ile 2 arasinda olmali.')


def prompt_path():
    external = APP_DIR / 'translation_prompt.txt'
    return external if external.exists() else ROOT / 'translation_prompt.txt'


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def rows(context):
    found = []
    for line in context.splitlines():
        match = re.match(r'^\|\s*`?([^|`]+\.md)`?\s*\|\s*(.*?)\s*\|\s*$', line)
        if match:
            found.append((match[1].strip(), match[2].strip()))
    if len({item[0] for item in found}) != len(found):
        raise ValueError('Files tablosunda tekrar eden dosya adi var.')
    return found


def split_source(text, limit):
    pieces = re.split(r'(\n\s*\n)', text)
    paragraphs = [''.join(pieces[index:index + 2]) for index in range(0, len(pieces), 2)]
    result, current = [], ''
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) > limit:
            result.append(current)
            current = ''
        if len(paragraph) <= limit:
            current += paragraph
            continue
        if current:
            result.append(current)
            current = ''
        # A single pathological paragraph must not defeat context protection.
        result.extend(paragraph[start:start + limit] for start in range(0, len(paragraph), limit))
    if current:
        result.append(current)
    assert ''.join(result) == text
    return result


def first_heading(text, fallback):
    match = re.search(r'^#\s+(.+?)\s*$', text, re.M)
    return match.group(1).strip() if match else fallback


def load_glossary(book):
    path = Path(book) / 'glossary.json'
    if not path.exists():
        return []
    data = json.loads(read(path))
    result = []
    for item in data:
        source = str(item.get('source', '')).strip()
        target = str(item.get('target', '')).strip()
        if source and target:
            result.append({'source': source, 'target': target})
    return result


def glossary_text(items):
    if not items:
        return '(Kullanici tarafindan tanimlanmis ozel terim yok.)'
    return '\n'.join(f'- {item["source"]} -> {item["target"]}' for item in items)


def fixed_context(context):
    pattern = r'^## (Metadata|Tone and Style|Address Form[^\n]*|Proper Nouns|Glossary|Recurring Phrases|Open Questions)\s*\n(.*?)(?=^## |\Z)'
    return '\n\n'.join(match[0].strip() for match in re.finditer(pattern, context, re.M | re.S))


def note_sections(context):
    pattern = r'^## Translation notes — ([^\n]+)\s*\n(.*?)(?=^## |\Z)'
    return [(match.group(1).strip(), match.group(2).strip()) for match in re.finditer(pattern, context, re.M | re.S)]


def book_data(book, context, filename=None):
    state_path = Path(book) / '_python_translation' / 'book-state.json'
    state = json.loads(read(state_path)) if state_path.exists() else {}
    cutoff = int(state.get('summary_note_count', 0))
    parts = [fixed_context(context)]
    selected_analysis = load_analysis_selection(book)
    analysis_applies = bool(filename and filename in selected_analysis)
    analysis = Path(book) / '_python_translation' / 'pre-analysis-reference.md'
    if analysis_applies and analysis.exists():
        parts.extend(['## Verified Book-wide Pre-analysis Reference', read(analysis).strip()])
    if analysis_applies:
        chapter_analysis = Path(book) / '_python_analysis' / (filename + '.reference.md')
        if chapter_analysis.exists():
            parts.extend([f'## Current Chapter Pre-analysis Reference — {filename}', read(chapter_analysis).strip()])
    summary = Path(book) / '_python_translation' / 'continuity-summary.md'
    if summary.exists():
        parts.extend(['## Compressed Section Summaries', read(summary).strip()])
    remaining = note_sections(context)[cutoff:]
    parts.extend(f'## Translation notes — {name}\n{notes}' for name, notes in remaining)
    return '\n\n'.join(part for part in parts if part)


def load_analysis_selection(book):
    path = Path(book) / '_python_analysis' / 'selection.json'
    if not path.exists():
        return []
    value = json.loads(read(path))
    return [str(item) for item in value.get('filenames', []) if str(item).strip()]


def _clean_strings(value):
    if not isinstance(value, list):
        raise ValueError('On analiz JSON listesinin bicimi gecersiz.')
    return [str(item).strip() for item in value if str(item).strip()]


def _scope(value):
    # An omitted or malformed scope must never leak chapter-local information.
    return 'book' if value == 'book' else 'chapter'


def parse_analysis(text):
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError('On analiz yaniti gecerli JSON degil; checkpoint ilerletilmedi.') from error
    if not isinstance(value, dict):
        raise ValueError('On analiz JSON semasi gecersiz.')
    required = {'summary', 'characters', 'terms', 'style', 'changes', 'ambiguities'}
    missing = sorted(required.difference(value))
    if missing:
        raise ValueError('On analiz JSON yaniti eksik alanlar iceriyor: ' + ', '.join(missing))
    if not isinstance(value['summary'], str):
        raise ValueError('On analiz JSON semasi gecersiz.')
    for key in ('characters', 'terms', 'style', 'changes', 'ambiguities'):
        if not isinstance(value[key], list):
            raise ValueError(f'On analiz JSON alani liste olmali: {key}')
    characters = []
    for item in value['characters']:
        if not isinstance(item, dict) or not str(item.get('name', '')).strip():
            raise ValueError('On analiz karakter kaydi gecersiz.')
        characters.append({'name': str(item['name']).strip(), 'facts': _clean_strings(item.get('facts', [])),
                           'voice': _clean_strings(item.get('voice', [])), 'scope': _scope(item.get('scope'))})
    terms = []
    for item in value['terms']:
        if not isinstance(item, dict) or not str(item.get('source', '')).strip():
            raise ValueError('On analiz terim kaydi gecersiz.')
        terms.append({'source': str(item['source']).strip(), 'suggested_target': str(item.get('suggested_target', '')).strip(),
                      'reason': str(item.get('reason', '')).strip(), 'scope': _scope(item.get('scope'))})
    style = []
    for item in value['style']:
        if isinstance(item, str):
            item = {'observation': item, 'scope': 'chapter'}
        if not isinstance(item, dict) or not str(item.get('observation', '')).strip():
            raise ValueError('On analiz uslup kaydi gecersiz.')
        style.append({'observation': str(item['observation']).strip(), 'scope': _scope(item.get('scope'))})
    return {'summary': value['summary'].strip(), 'characters': characters, 'terms': terms, 'style': style,
            'changes': _clean_strings(value['changes']), 'ambiguities': _clean_strings(value['ambiguities'])}


def _unique(values):
    result, seen = [], set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            result.append(value); seen.add(key)
    return result


def merge_analysis(chapters):
    """Keep chapter observations local; promote repeated, conflict-free book candidates."""
    merged = {'analysis_version': ANALYSIS_VERSION, 'global': {'characters': [], 'terms': [], 'style': []}, 'chapters': []}
    character_candidates, term_candidates, style_candidates = {}, {}, {}
    for chapter in chapters:
        local = {'filename': chapter['filename'], 'summary': '', 'characters': [], 'terms': [], 'style': [], 'changes': [], 'ambiguities': []}
        character_map, term_map = {}, {}
        for part in chapter['parts']:
            if part['summary']:
                local['summary'] += (' ' if local['summary'] else '') + part['summary']
            local['changes'].extend(part['changes']); local['ambiguities'].extend(part['ambiguities'])
            for item in part['style']:
                local['style'].append(item['observation'])
                if item['scope'] == 'book':
                    key = item['observation'].casefold()
                    style_candidates.setdefault(key, {'observation': item['observation'], 'chapters': set()})['chapters'].add(chapter['filename'])
            for item in part['characters']:
                key = item['name'].casefold()
                entry = character_map.setdefault(key, {'name': item['name'], 'facts': [], 'voice': []})
                entry['facts'].extend(item['facts']); entry['voice'].extend(item['voice'])
                if item['scope'] == 'book':
                    for fact in item['facts']:
                        candidate_key = (key, fact.casefold())
                        candidate = character_candidates.setdefault(candidate_key, {'name': item['name'], 'fact': fact, 'chapters': set()})
                        candidate['chapters'].add(chapter['filename'])
            for item in part['terms']:
                term_map.setdefault((item['source'].casefold(), item['suggested_target'].casefold()), item)
                if item['scope'] == 'book' and item['suggested_target']:
                    source_key = item['source'].casefold()
                    targets = term_candidates.setdefault(source_key, {})
                    candidate = targets.setdefault(item['suggested_target'].casefold(), {'source': item['source'], 'suggested_target': item['suggested_target'], 'reason': item['reason'], 'chapters': set()})
                    candidate['chapters'].add(chapter['filename'])
        for item in character_map.values():
            item['facts'] = _unique(item['facts']); item['voice'] = _unique(item['voice'])
        local['characters'] = list(character_map.values()); local['terms'] = list(term_map.values())
        local['style'] = _unique(local['style']); local['changes'] = _unique(local['changes']); local['ambiguities'] = _unique(local['ambiguities'])
        merged['chapters'].append(local)
    global_characters = {}
    for candidate in character_candidates.values():
        if len(candidate['chapters']) >= 2:
            entry = global_characters.setdefault(candidate['name'].casefold(), {'name': candidate['name'], 'facts': []})
            entry['facts'].append(candidate['fact'])
    merged['global']['characters'] = list(global_characters.values())
    for targets in term_candidates.values():
        if len(targets) == 1:
            candidate = next(iter(targets.values()))
            if len(candidate['chapters']) >= 2:
                merged['global']['terms'].append({key: candidate[key] for key in ('source', 'suggested_target', 'reason')})
    merged['global']['style'] = [candidate['observation'] for candidate in style_candidates.values() if len(candidate['chapters']) >= 2]
    return merged


def render_analysis(merged):
    lines = ['# Kitap Ön Analizi', '', '> Kullanıcı sözlüğü her zaman önceliklidir. Kitap geneline yalnızca en az iki bölümde tekrarlanan ve çelişmeyen adaylar yükseltilir.', '', '## Doğrulanmış Kitap Geneli', '', '### Karakterler']
    for item in merged['global']['characters']:
        lines.append(f'- **{item["name"]}:** ' + '; '.join(item['facts']))
    lines.extend(['', '### Terimler'])
    for item in merged['global']['terms']:
        lines.append(f'- **{item["source"]}** → {item["suggested_target"]}')
    lines.extend(['', '### Üslup']); lines.extend(f'- {item}' for item in merged['global']['style'])
    lines.extend(['', '## Bölüm Özelindeki Analiz', ''])
    for item in merged['chapters']:
        lines.extend([f'### {item["filename"]}', '', item['summary'] or '(Özet yok.)', '', '**Karakter durumu ve sesi**'])
        for character in item['characters']:
            details = _unique(character['facts'] + character['voice'])
            lines.append(f'- **{character["name"]}:** ' + ('; '.join(details) or 'Ek gözlem yok.'))
        lines.append(''); lines.append('**Terim önerileri**')
        for term in item['terms']:
            lines.append(f'- {term["source"]} → {term["suggested_target"] or "(öneri yok)"}')
        lines.append(''); lines.append('**Üslup**'); lines.extend(f'- {value}' for value in item['style'])
        lines.append(''); lines.append('**Değişimler**'); lines.extend(f'- {value}' for value in item['changes'])
        lines.append(''); lines.append('**Belirsizlikler**'); lines.extend(f'- {value}' for value in item['ambiguities']); lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def render_analysis_reference(merged):
    lines = ['Yalnızca birden fazla bölümde doğrulanmış, çelişmeyen kitap geneli adaylarıdır. Kullanıcı sözlüğü üstündür.', '', '### Sabit karakter bilgileri']
    for item in merged['global']['characters']:
        lines.append(f'- {item["name"]}: ' + '; '.join(item['facts']))
    lines.extend(['', '### Terim önerileri'])
    for item in merged['global']['terms']:
        lines.append(f'- {item["source"]} -> {item["suggested_target"]} (öneri)')
    lines.extend(['', '### Kitap geneli üslup']); lines.extend(f'- {item}' for item in merged['global']['style'])
    return '\n'.join(lines).rstrip() + '\n'


def render_chapter_reference(chapter):
    lines = ['Bu bilgi yalnızca mevcut bölüm için geçerlidir. Başka bölümlere genellenmemelidir.', '', '### Karakterlerin bu bölümdeki durumu ve sesi']
    for item in chapter['characters']:
        lines.append(f'- {item["name"]}: ' + ('; '.join(_unique(item['facts'] + item['voice'])) or 'Ek gözlem yok.'))
    lines.extend(['', '### Bu bölümdeki terim önerileri'])
    for item in chapter['terms']:
        if item['suggested_target']:
            lines.append(f'- {item["source"]} -> {item["suggested_target"]} (öneri)')
    lines.extend(['', '### Bu bölümün üslubu']); lines.extend(f'- {item}' for item in chapter['style'])
    lines.extend(['', '### Bu bölümdeki değişimler']); lines.extend(f'- {item}' for item in chapter['changes'])
    lines.extend(['', '### Çeviride dikkat edilecek belirsizlikler']); lines.extend(f'- {item}' for item in chapter['ambiguities'])
    return '\n'.join(lines).rstrip() + '\n'


def analysis_from_checkpoints(book, filenames=None):
    book = Path(book); analysis_dir = book / '_python_analysis'
    selected = list(filenames if filenames is not None else load_analysis_selection(book))
    if not selected:
        selected = [path.name[:-len('.v2.state.json')] for path in sorted(analysis_dir.glob('*.v2.state.json'))]
    chapters = []
    for filename in selected:
        state_path = analysis_dir / (filename + '.v2.state.json')
        if not state_path.exists():
            continue
        state = json.loads(read(state_path))
        if state.get('analysis_version') == ANALYSIS_VERSION and state.get('parts'):
            chapters.append({'filename': filename, 'parts': state['parts']})
    return merge_analysis(chapters) if chapters else None


def write_analysis_outputs(book, filenames):
    book = Path(book); analysis_dir = book / '_python_analysis'
    merged = analysis_from_checkpoints(book, filenames)
    if merged is None:
        names = '\n'.join(f'- {name}' for name in filenames)
        atomic(book / 'BOOK-ANALYSIS.md', '# Kitap Ön Analizi\n\nÖn analiz henüz sonuç üretmedi.\n\n## Seçilen bölümler\n\n' + names + '\n')
        return
    save_json(analysis_dir / 'book-analysis.json', merged)
    atomic(book / 'BOOK-ANALYSIS.md', render_analysis(merged))
    atomic(book / '_python_translation' / 'pre-analysis-reference.md', render_analysis_reference(merged))
    for chapter in merged['chapters']:
        atomic(analysis_dir / (chapter['filename'] + '.reference.md'), render_chapter_reference(chapter))


def update_context(context, filename, notes):
    table = rows(context)
    statuses = dict(table)
    if filename not in statuses:
        raise ValueError('Kaynak dosya Files tablosunda yok.')
    statuses[filename] = 'done'
    for name, status in table:
        if status == 'next' and name != filename:
            statuses[name] = ''
    next_name = next((name for name, _ in table if statuses[name] not in ('done', 'skip')), None)
    if next_name:
        statuses[next_name] = 'next'
    output = []
    for line in context.splitlines():
        match = re.match(r'^\|\s*`?([^|`]+\.md)`?\s*\|\s*(.*?)\s*\|\s*$', line)
        if match:
            name = match[1].strip()
            line = f'| `{name}` | {statuses[name]} |'
        if re.match(r'^- \*\*Last completed file:\*\*', line):
            line = f'- **Last completed file:** {filename}'
        output.append(line)
    output += ['', f'## Translation notes — {filename}', '', notes.strip(), '']
    return '\n'.join(output), next_name


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('HTTP yonlendirmesi kabul edilmiyor.')


class Client:
    def __init__(self, cfg, log=print):
        validate_config(cfg)
        self.cfg = cfg
        self.log = log
        self._context_cache = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _url(self, route):
        from urllib.parse import urlsplit
        base = self.cfg['base_url'].rstrip('/')
        parsed = urlsplit(base)
        if route.startswith('/api/'):
            return f'{parsed.scheme}://{parsed.netloc}{route}'
        return base + route

    def request(self, route, payload=None):
        headers = {'Content-Type': 'application/json'}
        token = os.environ.get('LM_STUDIO_API_KEY')
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request(
            self._url(route),
            data=None if payload is None else json.dumps(payload).encode('utf-8'),
            headers=headers,
        )
        self.log(f'LM Studio istegi: {"POST" if payload is not None else "GET"} {route}')
        try:
            with self.opener.open(request, timeout=int(self.cfg['timeout_seconds'])) as response:
                result = json.load(response)
                self.log(f'LM Studio yaniti: HTTP {getattr(response, "status", 200)} {route}')
                return result
        except urllib.error.HTTPError as error:
            detail = error.read().decode('utf-8', errors='replace')[:1500]
            raise RuntimeError(f'HTTP {error.code}: {detail}') from error
        except urllib.error.URLError as error:
            raise RuntimeError(f'LM Studio baglantisi kurulamadi: {error.reason}') from error
        except TimeoutError as error:
            raise RuntimeError('LM Studio istegi zaman asimina ugradi.') from error

    def model_info(self):
        try:
            response = self.request('/api/v1/models')
            models = response.get('models', response.get('data', []))
        except Exception as v1_error:
            self.log(f'UYARI: /api/v1/models kullanilamadi, v0 deneniyor: {v1_error}')
            response = self.request('/api/v0/models')
            models = response.get('data', [])
        selected = next((model for model in models if self.cfg['model'] in (
            model.get('key'), model.get('id'), model.get('selected_variant')) or
            any(instance.get('id') == self.cfg['model'] for instance in model.get('loaded_instances', []))), None)
        if selected is None:
            return None, models
        loaded = selected.get('loaded_instances') or []
        actual = loaded[0].get('config', {}).get('context_length') if loaded else None
        return {
            'context_length': actual,
            'max_context_length': selected.get('max_context_length'),
            'loaded': bool(loaded),
        }, models

    def context_length(self):
        if self._context_cache is not None:
            return self._context_cache
        try:
            info, _ = self.model_info()
            if info and info['context_length']:
                self._context_cache = (int(info['context_length']), 'LM Studio yuklu model')
                return self._context_cache
            if info and info['max_context_length']:
                self.log('UYARI: Model yuklu degil; teorik azami context yerine config fallback kullaniliyor.')
        except Exception as error:
            self.log(f'UYARI: Model context bilgisi alinamadi: {error}')
        self._context_cache = (int(self.cfg['fallback_context_length']), 'config fallback')
        return self._context_cache

    def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
        end_marker = '[[END_' + secrets.token_hex(12) + ']]'
        marker_instruction = '\nAfter completing ALL requested text, append this exact completion marker on its own final line: ' + end_marker
        response = self.request('/api/v1/chat', {
            'model': self.cfg['model'],
            'system_prompt': system + (marker_instruction if require_marker else ''),
            'input': user,
            'integrations': [],
            'store': False,
            'reasoning': 'off',
            'temperature': float(self.cfg['temperature']),
            'top_p': 0.9,
            'max_output_tokens': int(max_tokens),
            'stream': False,
        })
        stats = response.get('stats', {})
        output_count = stats.get('total_output_tokens')
        self.log(f'Token: giris={stats.get("input_tokens", "?")}, cikti={output_count}, dusunme={stats.get("reasoning_output_tokens", "?")}')
        outputs = response.get('output', [])
        if any(item.get('type') not in ('message', 'reasoning') for item in outputs):
            raise ValueError('Beklenmeyen API cikti tipi; sonuc kaydedilmedi.')
        text = '\n'.join(str(item.get('content', '')) for item in outputs if item.get('type') == 'message').strip()
        if isinstance(output_count, (int, float)) and output_count >= int(max_tokens):
            raise ValueError('Cikti token sinirina ulasti; eksik sonuc kaydedilmedi.')
        if text.endswith(end_marker) and text.count(end_marker) == 1:
            text = text[:-len(end_marker)].rstrip()
        elif require_marker:
            raise ValueError('Yanit tamamlanma isareti tasimiyor; sonuc kaydedilmedi.')
        if allow_json_fence and text.startswith('```'):
            fenced = re.fullmatch(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.S | re.I)
            if fenced:
                text = fenced.group(1).strip()
        if not text or text.startswith('```'):
            raise ValueError('Bos veya kod blogu biciminde yanit; sonuc kaydedilmedi.')
        if any(marker in text for marker in ('<|channel>', '<tool_call', '<|tool_call', '<think>', '</think>')):
            raise ValueError('Yanit dusunce/tool isaretleri iceriyor; sonuc kaydedilmedi.')
        if text.count('\\n') > 3 and text.count('\n') < 2:
            raise ValueError('Gorunur newline kacislari tespit edildi; sonuc kaydedilmedi.')
        return text


class TranslationEngine:
    def __init__(self, config_provider, controller=None, log=None, progress=None):
        self.config_provider = config_provider
        self.controller = controller or PauseController()
        self.log = log or print
        self.progress = progress or (lambda **_values: None)

    def _budget_check(self, cfg, client, system, user, max_tokens):
        if not cfg.get('token_estimation_enabled', True):
            return
        context, source = client.context_length()
        estimate = (len(system) + len(user) + 2) // 3
        required = estimate + int(max_tokens) + int(cfg.get('context_safety_tokens', 2048))
        self.log(f'Token tahmini: giris≈{estimate}, gerekli≈{required}, context={context} ({source})')
        if required > context:
            raise ValueError(f'Tahmini istek context sinirini asiyor: {required}/{context}. Parca boyutunu veya cikti payini azaltin.')

    def _generate(self, client, system, user, max_tokens, require_marker=True, allow_json_fence=False):
        self._budget_check(client.cfg, client, system, user, max_tokens)
        return client.generate(system, user, max_tokens, require_marker=require_marker, allow_json_fence=allow_json_fence)

    def _enforce_glossary(self, client, source, translated, glossary, max_tokens):
        missing = [item for item in glossary if re.search(re.escape(item['source']), source, re.I) and not re.search(re.escape(item['target']), translated, re.I)]
        if not missing:
            return translated
        self.log('Glossary denetimi: eksik terimler duzeltiliyor: ' + ', '.join(item['source'] for item in missing))
        system = 'Return only the corrected Turkish translation. Preserve every sentence and Markdown. Change only terminology needed to obey the mandatory glossary.'
        user = f'MANDATORY GLOSSARY:\n{glossary_text(missing)}\n\nSOURCE:\n{source}\n\nTRANSLATION TO CORRECT:\n{translated}'
        fixed = self._generate(client, system, user, max_tokens)
        still_missing = [item['target'] for item in missing if not re.search(re.escape(item['target']), fixed, re.I)]
        if still_missing:
            raise ValueError('Glossary zorunlulugu saglanamadi: ' + ', '.join(still_missing))
        return fixed

    def run_analysis(self, book, filenames=None):
        """Optionally analyze every source chunk, checkpointing each API response."""
        book = Path(book)
        lock = book / '_python_analysis.lock'
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.close(descriptor)
        try:
            cfg = dict(self.config_provider()); validate_config(cfg)
            client = Client(cfg, self.log)
            analysis_dir = book / '_python_analysis'; analysis_dir.mkdir(parents=True, exist_ok=True)
            available = [name for name, _status in rows(read(book / '00-CONTEXT.md'))]
            requested = available if filenames is None else list(dict.fromkeys(filenames))
            selected = [name for name in available if name in requested]
            unknown = [name for name in requested if name not in available]
            if unknown:
                raise ValueError('Ön analiz için bilinmeyen bölüm: ' + ', '.join(unknown))
            if not selected:
                raise ValueError('Ön analiz için en az bir bölüm seçilmeli.')
            save_json(analysis_dir / 'selection.json', {'analysis_version': ANALYSIS_VERSION, 'filenames': selected})
            write_analysis_outputs(book, selected)
            if not any(analysis_dir.glob('*.v2.state.json')):
                for state_path in (book / '_python_translation').glob('*/state.json'):
                    translation_state = json.loads(read(state_path))
                    if translation_state.get('parts') and not translation_state.get('complete'):
                        raise ValueError('Yeni ön analiz, başlamış bir bölümün bağlamını değiştiremez. Önce mevcut bölümü eski bağlamla tamamlayın.')
            chapters = []
            for filename in selected:
                source = read(book / 'source' / filename); chunks = split_source(source, int(cfg['chunk_chars']))
                state_path = analysis_dir / (filename + '.v2.state.json')
                existing = json.loads(read(state_path)) if state_path.exists() else None
                signature = digest(source + '\0' + ANALYSIS_PROMPT + '\0' + json.dumps(cfg, sort_keys=True, ensure_ascii=False))
                state = existing or {'analysis_version': ANALYSIS_VERSION, 'signature': signature, 'effective_config': cfg, 'parts': [], 'complete': False}
                if state.get('signature') != signature:
                    raise ValueError(f'{filename} için kaynak veya analiz ayarları değişmiş; ön analiz checkpointi korundu.')
                save_json(state_path, state)
                for index in range(len(state['parts']), len(chunks)):
                    self.controller.checkpoint()
                    self.progress(filename=filename, part=index + 1, parts=len(chunks), phase='analysis')
                    self.log(f'On analiz {filename} {index + 1}/{len(chunks)} — yanit bekleniyor...')
                    user = f'FILE: {filename}\nPART: {index + 1}/{len(chunks)}\n\nSOURCE CHUNK:\n{chunks[index]}'
                    # A complete, schema-valid JSON object is the completion signal here.
                    # Some models omit artificial trailing markers even after returning
                    # valid JSON; normal translation output keeps the stricter marker.
                    for attempt in range(ANALYSIS_AUTO_RETRIES + 1):
                        retry_notice = ('' if attempt == 0 else
                            '\n\nRETRY NOTICE: The previous response failed validation. Return one complete JSON object with every required field and no commentary.')
                        try:
                            result = parse_analysis(self._generate(
                                client, ANALYSIS_PROMPT, user + retry_notice, int(cfg['notes_max_tokens']),
                                require_marker=False, allow_json_fence=True,
                            ))
                            break
                        except ValueError as error:
                            retryable = str(error).startswith(ANALYSIS_RESPONSE_ERROR_PREFIXES)
                            if not retryable or attempt >= ANALYSIS_AUTO_RETRIES:
                                raise
                            self.log(
                                f'On analiz yaniti dogrulanamadi: {error} '
                                f'Otomatik yeniden deneme {attempt + 1}/{ANALYSIS_AUTO_RETRIES}...'
                            )
                            self.controller.checkpoint()
                    state['parts'].append(result); save_json(state_path, state)
                    write_analysis_outputs(book, selected)
                    self.log(f'On analiz parcasi kaydedildi: {filename} {index + 1}/{len(chunks)}')
                    self.progress(filename=filename, part=index + 1, parts=len(chunks), phase='analysis_saved')
                    self.controller.checkpoint()
                state['complete'] = True; save_json(state_path, state)
                chapter = {'filename': filename, 'parts': state['parts']}; chapters.append(chapter)
                save_json(analysis_dir / (filename + '.v2.analysis.json'), chapter)
            write_analysis_outputs(book, selected)
            self.log('On analiz tamamlandi ve BOOK-ANALYSIS.md kaydedildi.')
        finally:
            lock.unlink(missing_ok=True)

    def run_file(self, book, filename=None):
        book = Path(book)
        context_path = book / '00-CONTEXT.md'
        context = read(context_path)
        selected = [name for name, status in rows(context) if status == 'next'] if filename is None else [filename]
        if len(selected) != 1:
            raise ValueError('Files tablosunda tam bir next dosyasi bulunmali.')
        filename = selected[0]
        if Path(filename).name != filename or any(char in filename for char in ('/', '\\', ':')):
            raise ValueError('Dosya adi calisma klasoru disina cikamaz.')
        source = read(book / 'source' / filename)
        if not source.strip():
            raise ValueError('Kaynak bos.')
        work = book / '_python_translation' / filename
        work.mkdir(parents=True, exist_ok=True)
        state_path = work / 'state.json'
        existing = json.loads(read(state_path)) if state_path.exists() else None
        cfg = dict(existing.get('effective_config', {})) if existing and existing.get('effective_config') else dict(self.config_provider())
        validate_config(cfg)
        client = Client(cfg, self.log)
        glossary = existing.get('glossary', []) if existing and 'glossary' in existing else load_glossary(book)
        chunks = split_source(source, int(cfg['chunk_chars']))
        signature = digest(source + '\0' + read(prompt_path()) + '\0' + json.dumps(glossary, sort_keys=True, ensure_ascii=False))
        if existing and 'effective_config' not in existing:
            legacy_keys = ('base_url', 'model', 'chunk_chars', 'max_tokens', 'notes_max_tokens', 'temperature', 'timeout_seconds')
            legacy_cfg = {key: cfg[key] for key in legacy_keys}
            legacy_signature = digest(source + '\0' + json.dumps(legacy_cfg, sort_keys=True) + '\0' + read(prompt_path()))
            if existing.get('signature') != legacy_signature or glossary:
                raise ValueError('Eski surum checkpointi otomatik dogrulanamadi; klasoru koruyup eski surumle bolumu tamamlayin.')
            existing.update(signature=signature, effective_config=cfg, glossary=[])
            save_json(state_path, existing)
            self.log('Eski checkpoint yeni motora kayipsiz aktarildi.')
        state = existing or {'signature': signature, 'effective_config': cfg, 'glossary': glossary, 'context_before': context,
                             'reference': book_data(book, context, filename), 'parts': [], 'notes': None}
        if state['signature'] != signature:
            raise ValueError('Kaynak/prompt/glossary degismis. Tamamlanmamis bolumun is klasorunu arsivleyin.')
        if state.get('complete'):
            return
        if context not in (state['context_before'], state.get('context_after')):
            raise ValueError('Baglam dosyasi is basladiktan sonra degismis; checkpoint korundu.')
        if 'reference' not in state:
            state['reference'] = book_data(book, state['context_before'], filename)
        save_json(state_path, state)
        reference = state['reference']
        system = read(prompt_path())
        for index in range(len(state['parts']), len(chunks)):
            self.controller.checkpoint()
            self.progress(filename=filename, part=index + 1, parts=len(chunks), phase='translation')
            self.log(f'Ceviri {index + 1}/{len(chunks)} — yanit bekleniyor...')
            previous = state['parts'][-1][-1800:] if state['parts'] else '(Yok)'
            user = f'BOOK REFERENCE DATA:\n{reference}\n\nMANDATORY USER GLOSSARY:\n{glossary_text(glossary)}\n\nPREVIOUS TRANSLATION END:\n{previous}\n\nSOURCE TO TRANSLATE — PART {index + 1}/{len(chunks)}:\n{chunks[index]}'
            for attempt in range(TRANSLATION_AUTO_RETRIES + 1):
                retry_system = (system if attempt == 0 else system +
                    '\nA previous response for this same source fragment failed validation. '
                    'Translate the complete fragment again from the beginning and obey every output and glossary rule.')
                try:
                    translated = self._generate(client, retry_system, user, int(cfg['max_tokens']))
                    translated = self._enforce_glossary(
                        client, chunks[index], translated, glossary, int(cfg['max_tokens']))
                    break
                except (ValueError, RuntimeError) as error:
                    if not retryable_translation_error(error) or attempt >= TRANSLATION_AUTO_RETRIES:
                        raise
                    self.log(
                        f'Ceviri yaniti dogrulanamadi: {error} '
                        f'Otomatik yeniden deneme {attempt + 1}/{TRANSLATION_AUTO_RETRIES}...'
                    )
                    self.controller.checkpoint()
            state['parts'].append(translated)
            save_json(state_path, state)
            atomic(work / f'part-{index + 1:03}.md', translated + '\n')
            self.log(f'Parca {index + 1} kaydedildi.')
            self.controller.checkpoint()
        translation = '\n\n'.join(state['parts']) + '\n'
        atomic(work / 'translation-draft.md', translation)
        if state['notes'] is None:
            notes_parts = state.setdefault('notes_parts', [])
            for index in range(len(notes_parts), len(chunks)):
                self.controller.checkpoint()
                self.progress(filename=filename, part=index + 1, parts=len(chunks), phase='notes')
                note = self._generate(
                    client,
                    'Return only concise Turkish Markdown continuity notes: new glossary pairs, address-form decisions, names, and a two-sentence plot summary. Preserve existing decisions. Do not invent facts.',
                    f'EXISTING REFERENCE:\n{reference}\n\nSOURCE:\n{chunks[index]}\n\nTRANSLATION:\n{state["parts"][index]}',
                    int(cfg['notes_max_tokens']),
                    require_marker=False,
                )
                notes_parts.append(note)
                save_json(state_path, state)
                self.controller.checkpoint()
            state['notes'] = '\n\n'.join(notes_parts)
            save_json(state_path, state)
        if 'context_after' not in state:
            after, next_name = update_context(state['context_before'], filename, state['notes'])
            state.update(context_after=after, next_file=next_name)
            save_json(state_path, state)
        destination = book / 'translation' / filename
        if destination.exists() and read(destination) != translation:
            raise ValueError('Hedef dosya farkli; uzerine yazilmadi.')
        if not destination.exists():
            atomic(destination, translation)
        if read(context_path) == state['context_before']:
            atomic(work / '00-CONTEXT-before.md', state['context_before'])
            atomic(context_path, state['context_after'])
        elif read(context_path) != state['context_after']:
            raise ValueError('Baglam eszamanli degisti; ceviri kayitli, baglam guncellenmedi.')
        state['complete'] = True
        save_json(state_path, state)
        self.log(f'Kaydedildi: {destination}')
        self.progress(filename=filename, part=1, parts=1, phase='chapter_complete')

    def _compress_if_needed(self, book, client):
        context = read(Path(book) / '00-CONTEXT.md')
        table = rows(context)
        completed = sum(status == 'done' for _, status in table)
        if not table:
            return
        percentage = completed * 100 / len(table)
        book_state_path = Path(book) / '_python_translation' / 'book-state.json'
        state = json.loads(read(book_state_path)) if book_state_path.exists() else {'compressed_at': []}
        pending = [value for value in (25, 50, 75) if percentage >= value and value not in state['compressed_at']]
        sections = note_sections(context)
        prior = Path(book) / '_python_translation' / 'continuity-summary.md'
        for threshold in pending:
            prior_text = read(prior) if prior.exists() else '(Yok)'
            new_notes = '\n\n'.join(notes for _, notes in sections[int(state.get('summary_note_count', 0)):]) or '(Yeni not yok.)'
            user = f'PREVIOUS COMPRESSED SUMMARY:\n{prior_text}\n\nNEW NOTES:\n{new_notes}'
            summary = self._generate(client, 'Compress only plot/continuity summaries into concise Turkish Markdown. Do not output or alter glossary, proper nouns, address forms, or style decisions.', user, int(client.cfg['notes_max_tokens']), require_marker=False)
            atomic(prior, summary + '\n')
            state['compressed_at'].append(threshold)
            state['summary_note_count'] = len(sections)
            save_json(book_state_path, state)
            self.log(f'Baglam ozeti %{threshold} asamasinda sikistirildi.')

    def run_book(self, book, single_file=None, build_epub=True):
        book = Path(book)
        lock = book / '_python_translation.lock'
        lock.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
        try:
            while True:
                context = read(book / '00-CONTEXT.md')
                pending = [name for name, status in rows(context) if status == 'next']
                if single_file:
                    pending = [single_file]
                if not pending:
                    break
                self.run_file(book, pending[0])
                self.controller.checkpoint()
                cfg = dict(self.config_provider())
                self._compress_if_needed(book, Client(cfg, self.log))
                if single_file:
                    break
            completed = [read(book / 'translation' / name) for name, status in rows(read(book / '00-CONTEXT.md')) if status == 'done']
            atomic(book / 'TAM-CEVIRI.md', '\n\n'.join(completed))
            if build_epub and (book / 'epub-import.json').exists() and not single_file:
                from epub_output import build_translated_epub
                output = build_translated_epub(book, self.log)
                self.log('EPUB olusturuldu: ' + str(output))
            self.progress(phase='complete')
        finally:
            lock.unlink(missing_ok=True)
