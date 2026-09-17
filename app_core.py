"""Shared translation engine used by both the CLI and the PySide6 GUI."""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter

try:
    import msvcrt  # Windows tabanli dosya bolgesi kilidi (FileLock)
except ImportError:  # Windows disindaki ortamlar (ornegin CI) icin
    msvcrt = None
try:
    import fcntl  # POSIX: gelistirme/test ortamlari icin esdeger OS kilidi (G30)
except ImportError:
    fcntl = None
from datetime import datetime
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
    'auto_retry_count': 2,
    'token_estimation_enabled': True,
    'fallback_context_length': 32768,
    'context_safety_tokens': 2048,
    'glossary_strict': False,
    'completion_marker_enabled': True,
    'completion_marker_exempt_chars': 400,
    'epubcheck_enabled': False,
    'epubcheck_path': '',
}

ANALYSIS_VERSION = 2
LIVE_RESUMABLE_CONFIG_KEYS = (
    'completion_marker_enabled', 'completion_marker_exempt_chars', 'auto_retry_count',
)
# G22: Referans (kitap baglami) context'in bu oranini asinca, yuzde esigi
# beklenmeden notlar sikistirilir.
REFERENCE_BUDGET_RATIO = 0.4
DECISIONS_PROMPT = ('Extract only durable translation decisions from the notes: glossary pairs, proper noun renderings, '
                    'address forms (sen/siz) between named characters, recurring phrase renderings and fixed style decisions. '
                    'Merge them with EXISTING DECISIONS; keep existing decisions unless the new notes explicitly replace them. '
                    'Return the complete updated list as concise Markdown bullets in Turkish. No plot summary, no commentary. Do not invent facts.')
COMPRESS_PROMPT = ('Compress only plot/continuity summaries into concise Turkish Markdown. '
                   'Do not output or alter glossary, proper nouns, address forms, or style decisions.')
ANALYSIS_RESPONSE_ERROR_PREFIXES = (
    'On analiz ', 'Yanit ', 'Beklenmeyen API cikti', 'Cikti token sinirina ulasti',
    'Bos veya kod blogu', 'Gorunur newline',
)
TRANSLATION_RESPONSE_ERROR_PREFIXES = (
    'Yanit tamamlanma isareti', 'Beklenmeyen API cikti', 'Bos veya kod blogu',
    'Yanit dusunce/tool isaretleri', 'Gorunur newline kacislari', 'Yapisal EPUB isaretleri',
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


def translation_requires_completion_marker(source, cfg=None):
    """Kullanici ayarina gore ceviri parcasinin yapay bitis isareti gereksinimi."""
    cfg = cfg or DEFAULT_CONFIG
    if not bool(cfg.get('completion_marker_enabled', True)):
        return False
    limit = int(cfg.get('completion_marker_exempt_chars', 400))
    return len(str(source).strip()) > limit


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


class FileLock:
    """Windows OS file-region lock (msvcrt).

    A plain 'lock file' checks whether a file exists, so a crashed run leaves
    a stale file that blocks every later run. Here the lock is owned by the
    operating system and is released automatically when the owning process
    exits. The lock file itself may remain on disk -- that is harmless, only
    the region lock has meaning.
    """

    def __init__(self, path):
        self.path = Path(path)
        self._handle = None

    def acquire(self):
        if msvcrt is None and fcntl is None:  # pragma: no cover
            raise RuntimeError('Proje kilidi bu isletim sisteminde desteklenmiyor.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_BINARY', 0))
        try:
            if msvcrt is not None:
                if os.fstat(handle).st_size < 1:
                    os.write(handle, b'\x00')  # kilit bolgesi icin en az 1 bayt
                os.lseek(handle, 0, os.SEEK_SET)
                msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(handle)
            raise RuntimeError(
                'Bu proje baska bir Cevirgec tarafindan kullaniliyor ya da '
                'kilidi alinamiyor. Baska Cevirgec orneklerini kapatip yeniden deneyin.'
            ) from None
        self._handle = handle
        return self

    def release(self):
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if msvcrt is not None:
                os.lseek(handle, 0, os.SEEK_SET)
                msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback_):
        self.release()
        return False


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
    if not 1 <= int(cfg['auto_retry_count']) <= 100:
        raise ValueError('auto_retry_count 1 ile 100 arasinda olmali.')
    if not isinstance(cfg['completion_marker_enabled'], bool):
        raise ValueError('completion_marker_enabled true veya false olmali.')
    if not 0 <= int(cfg['completion_marker_exempt_chars']) <= 1000000:
        raise ValueError('completion_marker_exempt_chars 0 ile 1000000 arasinda olmali.')
    if not 0 <= float(cfg['temperature']) <= 2:
        raise ValueError('temperature 0 ile 2 arasinda olmali.')


def markdown_spans(text, image_only=False):
    """Dengeli parantezli Markdown baglanti/gorsellerini dondurur.

    Basit regex ``cover(1).jpg`` hedefini ilk kapanan parantezde keser. Bu
    tarayici kacis karakterlerini ve ic ice parantezleri korur. Sonuc
    ``(baslangic, bitis, gorunen_metin, hedef)`` dortluleridir.
    """
    prefix = '![' if image_only else '['
    index = 0
    while True:
        start = text.find(prefix, index)
        if start < 0:
            return
        if not image_only and text[start:start + 2] == '[[':
            index = start + 2
            continue
        if not image_only and start > 0 and text[start - 1] == '!':
            index = start + 1
            continue
        label_start = start + len(prefix)
        label_end = label_start
        while label_end < len(text):
            if text[label_end] == '\\':
                label_end += 2
                continue
            if text[label_end:label_end + 2] == '](':
                break
            label_end += 1
        if text[label_end:label_end + 2] != '](':
            index = start + len(prefix)
            continue
        target_start = label_end + 2
        cursor, depth = target_start, 1
        while cursor < len(text):
            char = text[cursor]
            if char == '\\':
                cursor += 2
                continue
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
                if depth == 0:
                    yield start, cursor + 1, text[label_start:label_end], text[target_start:cursor]
                    index = cursor + 1
                    break
            cursor += 1
        else:
            index = start + len(prefix)


def protected_epub_tokens(text):
    """Modelin degistirmemesi gereken EPUB hedeflerini sayimli olarak toplar."""
    images = Counter(target for _start, _end, _alt, target in markdown_spans(text, image_only=True)
                     if target.startswith('epub-resource:'))
    links = Counter(target for _start, _end, _label, target in markdown_spans(text)
                    if target.startswith('epub-link:'))
    anchors = Counter(re.findall(r'\[\[EPUB_ANCHOR:([^\]]+)\]\]', text))
    def cells(line):
        value = line.strip().strip('|')
        return re.split(r'(?<!\\)\|', value)

    tables = Counter()
    lines = text.splitlines()
    index = 0
    while index + 1 < len(lines):
        first, separator = cells(lines[index]), cells(lines[index + 1])
        if (len(first) > 1 and len(first) == len(separator)
                and all(re.fullmatch(r'\s*:?-{3,}:?\s*', item) for item in separator)):
            row_count = 1
            cursor = index + 2
            while cursor < len(lines) and len(cells(lines[cursor])) == len(first) and '|' in lines[cursor]:
                row_count += 1; cursor += 1
            tables[f'{len(first)}x{row_count}'] += 1
            index = cursor
            continue
        index += 1
    return {'gorsel': images, 'ic baglanti': links, 'capa': anchors, 'tablo yapisi': tables}


def validate_translation_structure(source, translated):
    """Kaynak EPUB'un yapisal belirteclerinin ceviride aynen kaldigini dogrular."""
    source_tokens = protected_epub_tokens(source)
    translated_tokens = protected_epub_tokens(translated)
    problems = []
    for label in source_tokens:
        if source_tokens[label] != translated_tokens[label]:
            missing = source_tokens[label] - translated_tokens[label]
            extra = translated_tokens[label] - source_tokens[label]
            detail = []
            if missing:
                detail.append('eksik=' + ', '.join(missing.elements()))
            if extra:
                detail.append('fazla/degismis=' + ', '.join(extra.elements()))
            problems.append(label + ' (' + '; '.join(detail) + ')')
    if problems:
        raise ValueError('Yapisal EPUB isaretleri korunmadi: ' + ' | '.join(problems))


def restore_missing_epub_anchors(source, translated):
    """Modelin dusurdugu bagimsiz EPUB capa bloklarini kaynak konumuna koyar.

    EPUB importer, blok duzeyindeki ``id``/``name`` hedeflerini kendi Markdown
    paragrafinda tasir. Capa gorunur metin olmadigi icin ceviri modeline
    birakilmasi gereksiz ve guvenilmezdir. Yalnizca kaynak ve cevirinin gorunur
    blok sayilari esitken eksik capa bloklari geri eklenir. Degismis/fazladan
    capa ya da metin icine gomulu capa otomatik duzeltilmez; normal yapisal
    dogrulama bunlari reddeder.
    """
    anchor_pattern = re.compile(r'\[\[EPUB_ANCHOR:([^\]]+)\]\]')
    source_anchors = Counter(anchor_pattern.findall(source))
    translated_anchors = Counter(anchor_pattern.findall(translated))
    missing = source_anchors - translated_anchors
    if not missing or translated_anchors - source_anchors:
        return translated, 0

    separator = re.compile(r'\n[ \t]*\n+')

    def blocks(text):
        result = []
        start = 0
        for match in separator.finditer(text):
            if text[start:match.start()].strip():
                result.append((start, match.start(), text[start:match.start()]))
            start = match.end()
        if text[start:].strip():
            result.append((start, len(text), text[start:]))
        return result

    def anchor_only(block):
        without = anchor_pattern.sub('', block)
        return not without.strip() and bool(anchor_pattern.search(block))

    source_blocks = blocks(source)
    translated_blocks = blocks(translated)
    source_visible = [item for item in source_blocks if not anchor_only(item[2])]
    translated_visible = [item for item in translated_blocks if not anchor_only(item[2])]
    if len(source_visible) != len(translated_visible):
        return translated, 0

    insertions = []
    visible_before = 0
    remaining = missing.copy()
    for _start, _end, block in source_blocks:
        if not anchor_only(block):
            visible_before += 1
            continue
        tokens = []
        for value in anchor_pattern.findall(block):
            if remaining[value] > 0:
                tokens.append(f'[[EPUB_ANCHOR:{value}]]')
                remaining[value] -= 1
        if not tokens:
            continue
        if visible_before < len(translated_visible):
            position = translated_visible[visible_before][0]
            payload = '\n'.join(tokens) + '\n\n'
        else:
            position = len(translated.rstrip())
            payload = ('\n\n' if position else '') + '\n'.join(tokens)
        insertions.append((position, payload, len(tokens)))

    # Kaynakta yalniz blok halinde bulunmayan bir eksik capa varsa konumu
    # belirsizdir; hicbir seyi kismen degistirmeden kati dogrulamaya birak.
    if any(remaining.values()):
        return translated, 0
    for position, payload, _count in reversed(insertions):
        translated = translated[:position] + payload + translated[position:]
    return translated, sum(count for _position, _payload, count in insertions)


def image_only_source(text):
    """Teknik TOC basligi disinda yalnizca gorsel/isaret tasiyan bolum mu?"""
    images = list(markdown_spans(text, image_only=True))
    if not images:
        return False
    body = re.sub(r'\A\s*#{1,6}[^\n]*(?:\n[ \t]*)?\n?', '', text, count=1)
    for start, end, _alt, _target in reversed(list(markdown_spans(body, image_only=True))):
        body = body[:start] + body[end:]
    body = re.sub(r'\[\[EPUB_ANCHOR:[^\]]+\]\]', '', body)
    body = re.sub(r'(?m)^\s*(?:\*\s*){3,}\s*$', '', body)
    return not bool(re.search(r'[^\W\d_]', body, re.UNICODE))


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
        # A single pathological paragraph must not defeat context protection;
        # EPUB hedef belirteci de iki istek arasinda ortadan kesilmemeli.
        protected = [(start, end) for start, end, _label, _target in markdown_spans(paragraph, image_only=True)]
        protected += [(start, end) for start, end, _label, _target in markdown_spans(paragraph)]
        protected += [(match.start(), match.end()) for match in re.finditer(r'\[\[EPUB_ANCHOR:[^\]]+\]\]', paragraph)]
        start = 0
        while start < len(paragraph):
            end = min(start + limit, len(paragraph))
            crossing = next(((left, right) for left, right in protected if left < end < right), None)
            if crossing:
                end = crossing[1] if crossing[0] <= start else crossing[0]
            if end <= start:
                end = min(start + limit, len(paragraph))
            result.append(paragraph[start:end])
            start = end
    if current:
        result.append(current)
    assert ''.join(result) == text
    return result


def first_heading(text, fallback):
    match = re.search(r'^#\s+(.+?)\s*$', text, re.M)
    return match.group(1).strip() if match else fallback


def _word_regex(pattern, flags=0):
    return re.compile(r'\b(?:' + pattern + r')\b', flags)


def source_term_regex(term):
    """Ingilizce kaynak terimi tam kelime olarak arar (G17); yalnizca -s/-es ve
    iyelik 's eklerine izin verir: "Ash" -> "Ash's" evet, "ashamed" hayir."""
    return re.compile(r'(?<!\w)' + re.escape(term) + r"(?:s|es|'s|’s)?(?!\w)", re.I)


def tr_lower(text):
    """Turkce kucuk harf: I -> ı, İ -> i (str.lower/casefold bunu yanlis yapar)."""
    return str(text).replace('I', 'ı').replace('İ', 'i').lower()


def _harmony(*templates):
    result = set()
    for template in templates:
        if 'I' in template:
            result.update(template.replace('I', vowel) for vowel in 'ıiuü')
        elif 'A' in template:
            result.update(template.replace('A', vowel) for vowel in 'ae')
        else:
            result.add(template)
    return result


def _expand_consonants(values):
    result = set()
    for value in values:
        if 'D' in value:
            result.update(value.replace('D', letter) for letter in 'dt')
        elif 'C' in value:
            result.update(value.replace('C', letter) for letter in 'cç')
        else:
            result.add(value)
    return result


# Turkce isim cekim ekleri ve ek-fiiller (G24). Yapim ekleri (-lı, -cı, -lık)
# kasitli olarak YOK: "yapılı", "külot" glossary karsiligi sayilmaz.
_TURKISH_SUFFIX_TOKENS = frozenset(_expand_consonants(
    _harmony('lAr', 'I', 'sI', 'yI', 'nI', 'A', 'yA', 'nA', 'DA', 'nDA', 'DAn', 'nDAn',
             'In', 'nIn', 'lA', 'ylA', 'Im', 'm', 'n', 'ImIz', 'mIz', 'InIz', 'nIz',
             'CA', 'ki', 'ken', 'yken', 'DIr', 'DI', 'yDI', 'sA', 'ysA', 'mIş', 'ymIş')
    | {'nDAki', 'DAki'}
))


def _is_suffix_chain(rest, _cache={}):
    if rest in _cache:
        return _cache[rest]
    if not rest:
        return True
    if len(rest) > 18:
        return False
    found = any(rest.startswith(token) and _is_suffix_chain(rest[len(token):]) for token in _TURKISH_SUFFIX_TOKENS)
    _cache[rest] = found
    return found


_SOFTEN = {'p': 'b', 'ç': 'c', 't': 'd', 'k': 'ğ'}
_VOWELS = set('aeıioöuüâîû')


def _stem_variants(word):
    """Hedef kelimenin cekim sirasinda alabilecegi govde bicimleri:
    kitap -> kitab, ağaç -> ağac, renk -> reng, burun -> burn, Yapılar -> yapı."""
    variants = {word}
    last = word[-1:]
    if last in _SOFTEN and len(word) >= 3:
        variants.add(word[:-1] + _SOFTEN[last])
        if last == 'k' and word[-2:-1] == 'n':
            variants.add(word[:-1] + 'g')
    if (len(word) >= 4 and word[-1] not in _VOWELS and word[-2] in _VOWELS
            and word[-3] not in _VOWELS):
        variants.add(word[:-2] + word[-1])
    # Glossary karsiligi cogul yazilmissa ("Yapılar") tekil kullanimi da kabul.
    for plural in ('lar', 'ler'):
        index = word.find(plural)
        if index >= 2 and _is_suffix_chain(word[index:]):
            variants.add(word[:index])
    return variants


def _target_search(target, text):
    """Glossary karsiliginin ceviride Turkce cekimli olarak gecip gecmedigini denetler.

    Metindeki kelime, hedefin govde bicimlerinden biriyle baslamali ve geriye
    kalan kisim yalnizca bilinen cekim eklerinden olusmali. Cok kelimeli
    hedeflerde onceki kelimeler aynen ve ardisik gecmeli; ek yalnizca son kelimeye gelir.
    """
    target_words = re.findall(r'[^\W\d_]+', tr_lower(target))
    if not target_words:
        return False
    words = re.findall(r'[^\W\d_]+', tr_lower(text))
    head, last = target_words[:-1], target_words[-1]
    variants = sorted(_stem_variants(last), key=len, reverse=True)
    for index in range(len(head), len(words)):
        if words[index - len(head):index] != head:
            continue
        word = words[index]
        if any(word.startswith(variant) and _is_suffix_chain(word[len(variant):]) for variant in variants):
            return True
    return False


def _dedupe_glossary(items):
    """Ayni kaynak terimi (casefold) yalnizca ilk kaydiyla korur; bos kayitlari atlar.

    Duplicate onleme: hem kullanici elle duzenlenmis glossary.json hem de eski
    checkpoint state'lerindeki glossary listeleri bu filtreye tabi tutulur.
    """
    result, seen = [], set()
    for item in items or []:
        source = str(item.get('source', '')).strip()
        target = str(item.get('target', '')).strip()
        if not source or not target:
            continue
        key = source.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append({'source': source, 'target': target})
    return result


def load_glossary(book):
    path = Path(book) / 'glossary.json'
    if not path.exists():
        return []
    return _dedupe_glossary(json.loads(read(path)))


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


def load_book_state(book):
    path = Path(book) / '_python_translation' / 'book-state.json'
    return json.loads(read(path)) if path.exists() else {}


def compressed_note_names(state, sections):
    """Sikistirilmis not bolumlerinin DOSYA ADLARI (G21).

    Eski surum `summary_note_count` sira numarasi tutuyordu; yeniden ceviride
    notlar sona tasininca sira kayiyor ve sikistirilmamis notlar gizleniyordu.
    Eski state okunurken sira numarasi o anki bolum adlarina cevrilir."""
    if 'compressed_notes' in state:
        return set(state['compressed_notes'])
    count = int(state.get('summary_note_count', 0))
    return {name for name, _notes in sections[:count]}


def book_data(book, context, filename=None, recheck=False):
    state = load_book_state(book)
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
    decisions = Path(book) / '_python_translation' / 'continuity-decisions.md'
    if decisions.exists() and read(decisions).strip():
        # G23: Sikistirma olay ozetini kisaltirken terim/isim/hitap kararlari
        # burada kalici tutulur. Olay orgusu icermedigi icin yeniden ceviride de verilir.
        parts.extend(['## Continuity Decisions (terms, names, address forms)', read(decisions).strip()])
    summary = Path(book) / '_python_translation' / 'continuity-summary.md'
    if summary.exists() and not recheck:
        # Yeniden ceviride sikistirilmis olay ozeti verilmez: yapisal olarak
        # bolumlere ayrıstirilamaz ve sonraki bolumlere ait olay orgusu icerebilir.
        parts.extend(['## Compressed Section Summaries', read(summary).strip()])
    sections = note_sections(context)
    compressed = compressed_note_names(state, sections)
    remaining = [item for item in sections if item[0] not in compressed]
    if recheck and filename:
        order = [name for name, _status in rows(context)]
        if filename in order:
            allowed = set(order[:order.index(filename)])
            remaining = [item for item in remaining if item[0] in allowed]
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
                # Ayni kaynak terim yalnizca ILK karşiligiyla tutulur. Anahtar
                # source+target cifti olursa ayni source iki farkli karşilikla
                # iki ayri kayit olur (G16 duplicate kayit sorunu; bkz. Martha
                # Wells projesindeki Corporation Rim ornegi).
                term_map.setdefault(item['source'].casefold(), item)
                if item['scope'] == 'book' and item['suggested_target']:
                    # Kitap geneli adaylar: ayni source icin birden cok farkli
                    # karşilik toplanirsa guvenilir sayilmaz; asagidaki
                    # len(targets) == 1 kosulu bu celiskiyi global listeden eler.
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


def analysis_terms(book):
    """On analiz checkpoint'lerinden terim onerilerini tek listeye indirger (G16).

    Kitap geneli (global) terimler once gelir; ayni source.casefold() icin ILK
    karşılik kazanir (duplicate olusmaz). Cikti elemanlari
    {"source", "suggested_target", "reason"} bicimindedir.
    """
    merged = analysis_from_checkpoints(Path(book))
    if merged is None:
        return []
    result, seen = [], set()
    # G26: kapsam bilgisi korunur. Dogrulanmis global oneriler ve modelin kitap
    # geneli ('book') dedigi oneriler varsayilan olarak sozluge onerilir; yalnizca
    # bolume ozgu ('chapter') oneriler isaretsiz gelir ve otomatik birlestirilmez.
    items = [dict(item, scope='book') for item in merged['global']['terms']]
    for chapter in merged['chapters']:
        items.extend(dict(item, scope=item.get('scope') or 'chapter') for item in chapter['terms'])
    for item in items:
        source = str(item.get('source', '')).strip()
        target = str(item.get('suggested_target', '') or '').strip()
        if not source or not target:
            continue
        key = source.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


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


def remove_translation_notes(context, filename):
    """00-CONTEXT icinden verilen dosyaya ait eski not bolumunu kaldirir."""
    pattern = re.compile(r'^## Translation notes — ' + re.escape(filename) + r'\s*\n.*?(?=^## |\Z)', re.M | re.S)
    return pattern.sub('', context)


def rebase_partial_states(book, old_context, new_context, exclude=None):
    """G21: 00-CONTEXT.md bu surec tarafindan degistirildiginde yarim kalmis
    bolumlerin `context_before` anlik goruntusunu yeni metne tasir. Aksi halde
    yarim bolum "Baglam dosyasi is basladiktan sonra degismis" hatasiyla kilitlenir.
    Bolumun referansi (`reference`) ve parcalari degismez."""
    if old_context == new_context:
        return []
    moved = []
    for state_path in sorted((Path(book) / '_python_translation').glob('*/state.json')):
        filename = state_path.parent.name
        if filename == exclude:
            continue
        state = json.loads(read(state_path))
        if state.get('complete') or state.get('context_before') != old_context:
            continue
        state['context_before'] = new_context
        if 'context_after' in state and state.get('notes') is not None:
            after, next_name = update_context(new_context, filename, state['notes'])
            state.update(context_after=after, next_file=next_name)
        save_json(state_path, state)
        moved.append(filename)
    return moved


def write_context(book, old_context, new_context, exclude=None):
    """00-CONTEXT.md'yi atomik yazar ve yarim bolum checkpointlerini tasir."""
    atomic(Path(book) / '00-CONTEXT.md', new_context)
    return rebase_partial_states(book, old_context, new_context, exclude=exclude)


def _check_filename(filename):
    if Path(filename).name != filename or any(char in filename for char in ('/', '\\', ':')):
        raise ValueError('Dosya adi calisma klasoru disina cikamaz.')


def validate_rerun(book, filenames):
    """Yeniden ceviri oncesi tum secimi dogrular; hicbir dosyaya dokunmaz."""
    book = Path(book)
    if not filenames:
        raise ValueError('Yeniden cevrilecek bolum secilmedi.')
    statuses = dict(rows(read(book / '00-CONTEXT.md')))
    for filename in filenames:
        _check_filename(filename)
        if filename not in statuses:
            raise ValueError('Dosya Files tablosunda yok: ' + filename)
        if not (book / 'source' / filename).exists():
            raise ValueError('Kaynak dosya yok: ' + filename)
        if statuses[filename] not in ('done', 'next'):
            raise ValueError(f'"{filename}" henuz cevrilmedi; sirasi geldiginde normal akista cevrilecek.')


def _backup_path(work):
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    candidate = work / f'recheck-prev-{stamp}.md'
    index = 2
    while candidate.exists():
        candidate = work / f'recheck-prev-{stamp}-{index}.md'
        index += 1
    return candidate


def reset_chapter(book, filename):
    """Bir bolumu yeniden ceviriye hazirlar.

    - `done` bolum: ceviri zaman damgali yedege tasinir, state silinir, notlari
      00-CONTEXT'ten cikarilir, bolum `next` yapilir ve `recheck.json` yazilir.
    - `next` bolum (yarim da olabilir): yalnizca checkpoint sifirlanir; baglam
      degismez ve normal (recheck olmayan) referansla bastan cevrilir.
    Yarim kalmis baska bolumlerin checkpointleri yeni baglama tasinir (G21)."""
    book = Path(book)
    validate_rerun(book, [filename])
    context_path = book / '00-CONTEXT.md'
    context = read(context_path)
    status = dict(rows(context))[filename]
    work = book / '_python_translation' / filename
    work.mkdir(parents=True, exist_ok=True)
    translation_path = book / 'translation' / filename
    if translation_path.exists():
        translation_path.replace(_backup_path(work))
    state_path = work / 'state.json'
    if state_path.exists():
        state_path.unlink()
    if status == 'next':
        (work / 'recheck.json').unlink(missing_ok=True)
        return
    (work / 'recheck.json').write_text('{}', encoding='utf-8')
    new_context = remove_translation_notes(context, filename)
    lines = []
    for line in new_context.splitlines():
        match = re.match(r'^\|\s*`?([^|`]+\.md)`?\s*\|\s*(.*?)\s*\|\s*$', line)
        if match:
            name = match[1].strip()
            if name == filename:
                line = f'| `{name}` | next |'
            elif match[2].strip() == 'next':
                line = f'| `{name}` |  |'
        if re.match(r'^- \*\*Last completed file:\*\*', line):
            line = f'- **Last completed file:** {filename}'
        lines.append(line)
    new_context = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip() + '\n'
    book_state_path = book / '_python_translation' / 'book-state.json'
    if book_state_path.exists():
        book_state = json.loads(read(book_state_path))
        names = compressed_note_names(book_state, note_sections(context))
        if filename in names or 'compressed_notes' not in book_state:
            names.discard(filename)
            book_state['compressed_notes'] = sorted(names)
            book_state.pop('summary_note_count', None)
            save_json(book_state_path, book_state)
    write_context(book, context, new_context, exclude=filename)


def chapters_containing(book, terms, only_done=True):
    """Kaynakta terimlerin tam kelime gectigi bolumler. Varsayilan olarak yalnizca
    cevrilmis (`done`) bolumler doner; henuz cevrilmemis bolumler zaten guncel
    sozlukle cevrilecegi icin yeniden ceviri listesine girmez (G21)."""
    book = Path(book)
    terms = [str(term).strip() for term in terms if str(term).strip()]
    if not terms:
        return []
    patterns = [source_term_regex(term) for term in terms]
    matched = []
    for filename, status in rows(read(book / '00-CONTEXT.md')):
        if only_done and status != 'done':
            continue
        path = book / 'source' / filename
        if not path.exists():
            continue
        text = read(path)
        if any(pattern.search(text) for pattern in patterns):
            matched.append(filename)
    return matched


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
        if re.search(r'<\|channel>|<tool_call|<\|tool_call', text) or \
                re.search(r'(?m)^[ \t]*(?:thinking|response)\b', text):
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
        self.glossary_fixes = []  # G19/G25: uyulamayan glossary kayitlari
        self._glossary_fixes_path = None

    def _register_glossary_fix(self, source, translated_before, attempted_fix, missing_targets):
        """Uyulamayan glossary duzeltmesini bellege ve glossary_fixes.log'a yazar.
        Dosya her kayitta acilip kapanir; yarim kalan calismada tutamak sizmaz."""
        self.glossary_fixes.append({
            'source': source,
            'translated_before': translated_before,
            'attempted_fix': attempted_fix,
            'missing_targets': missing_targets,
        })
        if self._glossary_fixes_path is None:
            return
        try:
            with Path(self._glossary_fixes_path).open('a', encoding='utf-8', newline='\n') as stream:
                stream.write('\n' + '=' * 70 + '\n')
                stream.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S') + '\n')
                stream.write('SOURCE:\n' + source + '\n')
                stream.write('TRANSLATED BEFORE FIX:\n' + translated_before + '\n')
                stream.write('ATTEMPTED FIX:\n' + attempted_fix + '\n')
                stream.write('MISSING TARGETS: ' + ', '.join(missing_targets) + '\n')
        except OSError:
            pass

    def _open_glossary_fix_log(self, book):
        self._glossary_fixes_path = Path(book) / 'glossary_fixes.log'

    def _close_glossary_fix_log(self):
        self._glossary_fixes_path = None

    def _generate_retry(self, client, system, user, max_tokens, label, **kwargs):
        """G27: notlar ve sikistirma icin gecici hatalarda otomatik yeniden deneme."""
        retry_count = int(client.cfg['auto_retry_count'])
        for attempt in range(retry_count + 1):
            try:
                return self._generate(client, system, user, max_tokens, **kwargs)
            except (ValueError, RuntimeError) as error:
                if not retryable_translation_error(error) or attempt >= retry_count:
                    raise
                self.log(f'{label} yaniti alinamadi: {error} Otomatik yeniden deneme {attempt + 1}/{retry_count}...')
                self.controller.checkpoint()

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
        # Kaynak: tam kelime (G17). Hedef: Turkce cekim/ses olayi toleransli (G24).
        def missing_in(text, items):
            return [item for item in items if not _target_search(item['target'], text)]

        present = [item for item in glossary if source_term_regex(item['source']).search(source)]
        missing = missing_in(translated, present)
        if not missing:
            return translated
        self.log('Glossary denetimi: eksik terimler duzeltiliyor: ' + ', '.join(item['source'] for item in missing))
        system = 'Return only the corrected Turkish translation. Preserve every sentence and Markdown. Change only terminology needed to obey the mandatory glossary.'
        user = f'MANDATORY GLOSSARY:\n{glossary_text(missing)}\n\nSOURCE:\n{source}\n\nTRANSLATION TO CORRECT:\n{translated}'
        fixed = self._generate(
            client, system, user, max_tokens,
            # Denetim tamamen kapatildiysa glossary duzeltmesi de yapay marker
            # yuzunden takilmaz. Denetim acikken duzeltme yaniti katidir.
            require_marker=bool(client.cfg.get('completion_marker_enabled', True)),
        )
        still_missing_items = missing_in(fixed, missing)
        if not still_missing_items:
            return fixed
        still_missing = [item['target'] for item in still_missing_items]
        self._register_glossary_fix(source, translated, fixed, still_missing)
        if client.cfg.get('glossary_strict', False):
            raise ValueError('Glossary zorunlulugu saglanamadi: ' + ', '.join(still_missing) +
                             ' (detay icin proje klasorundeki glossary_fixes.log dosyasina bakin)')
        # G25: gozetimsiz calismada tek terim yuzunden kitap durmaz. Duzeltme
        # ciktisi glossary'ye daha fazla uyuyorsa o, degilse ilk ceviri kullanilir.
        chosen = fixed if len(still_missing_items) < len(missing) else translated
        self.log('UYARI: Glossary karsiligi uygulanamadi, ceviri devam ediyor: ' + ', '.join(still_missing) +
                 ' (glossary_fixes.log dosyasina yazildi)')
        return chosen

    def run_analysis(self, book, filenames=None):
        """Optionally analyze every source chunk, checkpointing each API response."""
        book = Path(book)
        with FileLock(book / '_python_analysis.lock'):
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
                # Imza yalnızca analiz sonucunu gerçekten değiştirebilecek değerleri kapsar.
                # temperature, model, max_tokens, timeout, base_url gibi çeviri kararlılığı
                # dışı ayarlar değişirse mevcut analiz checkpointleri geçersiz olmaz.
                signature = digest(source + '\0' + ANALYSIS_PROMPT + '\0' + json.dumps(
                    {key: cfg[key] for key in ('notes_max_tokens', 'chunk_chars') if key in cfg},
                    sort_keys=True, ensure_ascii=False))
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
                    retry_reason = None  # 'schema' | 'connection' - son retry'in sebebi
                    retry_count = int(cfg['auto_retry_count'])
                    for attempt in range(retry_count + 1):
                        if retry_reason == 'schema':
                            retry_notice = ('\n\nRETRY NOTICE: The previous response failed validation. '
                                            'Return one complete JSON object with every required field and no commentary.')
                        elif retry_reason == 'connection':
                            retry_notice = ('\n\nRETRY NOTICE: The previous request failed because of a temporary '
                                            'CONNECTION problem. Please retry and return one complete JSON object with every required field and no commentary.')
                        else:
                            retry_notice = ''
                        try:
                            result = parse_analysis(self._generate(
                                client, ANALYSIS_PROMPT, user + retry_notice, int(cfg['notes_max_tokens']),
                                require_marker=False, allow_json_fence=True,
                            ))
                            break
                        except ValueError as error:
                            retryable = str(error).startswith(ANALYSIS_RESPONSE_ERROR_PREFIXES)
                            if not retryable or attempt >= retry_count:
                                raise
                            retry_reason = 'schema'
                            self.log(
                                f'On analiz yaniti dogrulanamadi: {error} '
                                f'Otomatik yeniden deneme {attempt + 1}/{retry_count}...'
                            )
                            self.controller.checkpoint()
                        except RuntimeError as error:
                            if not retryable_translation_error(error) or attempt >= retry_count:
                                raise
                            retry_reason = 'connection'
                            self.log(
                                f'On analiz baglanti hatasi: {error} '
                                f'Otomatik yeniden deneme {attempt + 1}/{retry_count}...'
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

    def _merge_analysis_terms_into_glossary(self, book):
        """On analizden cikan terim onerilerini (kitap geneli + bolum) mevcut
        glossary.json ile birlestirir ve temizler.

        Kapsam: G16. Analiz onerileri kaynak terimi ilk karşiligiyla ekler;
        kullanici tarafindan onceden kararlanmis/anlasilmis kayitlar korunur.
        YalnizCA YENI kaynak terimler eklenir; ayni source yeni bir karşilik
        ile gelirse duplicate olusturmaz (ilk kayit kazanir).

        CLI akisinda (cevir.py review_analysis_terms) kullanici onayi sonrasi
        cagrilir; GUI akisinda kullanici onay dialogu dogrudan save_json ile
        yazar, bu metod GUI'de kullanilmaz (docstring'teki eski ``merge'' notu
        gecersizdir). run_analysis bu metodu cagirmaz; onay mekanizmasi onceden
        olmadan glossary'ye dokunulmaz.
        """
        book = Path(book)
        glossary_path = book / 'glossary.json'
        existing = load_glossary(book)
        merged, seen = [], set()
        for item in existing:
            merged.append(item)
            seen.add(item['source'].casefold())
        for item in analysis_terms(book):
            if item.get('scope', 'book') != 'book':
                continue  # G26: bolum ozel oneriler kitap geneli zorunlu sozluge otomatik girmez
            source = str(item.get('source', '')).strip()
            target = str(item.get('suggested_target', '') or '').strip()
            if not source or not target or source.casefold() in seen:
                continue
            seen.add(source.casefold())
            merged.append({'source': source, 'target': target})
        if merged != existing:
            save_json(glossary_path, merged)
            self.log('On analiz terim onerileri glossary.json ile birlestirildi (duplicate onlendi).')
        else:
            self.log('On analiz terim onerileri glossary.json ile ayni; birlestirme yapilmadi.')

    def run_file(self, book, filename=None):
        book = Path(book)
        context_path = book / '00-CONTEXT.md'
        context = read(context_path)
        self._open_glossary_fix_log(book)  # G19: başarısız düzeltmeler bu dosyaya yazılır
        selected = [name for name, status in rows(context) if status == 'next'] if filename is None else [filename]
        if len(selected) != 1:
            raise ValueError('Files tablosunda tam bir next dosyasi bulunmali.')
        filename = selected[0]
        _check_filename(filename)
        source = read(book / 'source' / filename)
        if not source.strip():
            raise ValueError('Kaynak bos.')
        work = book / '_python_translation' / filename
        work.mkdir(parents=True, exist_ok=True)
        state_path = work / 'state.json'
        existing = json.loads(read(state_path)) if state_path.exists() else None
        recheck = (work / 'recheck.json').exists()
        live_cfg = dict(self.config_provider())
        validate_config(live_cfg)
        cfg = None
        if existing and 'effective_config' in existing:
            try:
                cfg = dict(existing['effective_config'])
                # Eski checkpointlerde sonradan eklenen canli ayarlar yoktur.
                # Yalnizca bu alanlari geriye uyumlu doldur; diger eksik
                # checkpoint alanlari hata olmaya devam etsin.
                for key in LIVE_RESUMABLE_CONFIG_KEYS:
                    cfg.setdefault(key, DEFAULT_CONFIG[key])
                validate_config(cfg)  # eksik/yanlis tip alan -> KeyError/TypeError
            except (KeyError, TypeError):
                raise ValueError(
                    'Checkpoint ayarlari bozuk veya eksik; klasoru koruyup '
                    'state.json dosyasindaki effective_config alanini duzeltin.'
                ) from None
        if cfg is None:
            cfg = live_cfg
        elif any(cfg[key] != live_cfg[key] for key in LIVE_RESUMABLE_CONFIG_KEYS):
            for key in LIVE_RESUMABLE_CONFIG_KEYS:
                cfg[key] = live_cfg[key]
            validate_config(cfg)
            self.log('Bitis isareti/otomatik tekrar ayarlari mevcut yarim bolum icin guncellendi.')
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
                             'reference': book_data(book, context, filename, recheck=recheck), 'parts': [], 'notes': None}
        if existing:
            state.setdefault('effective_config', {}).update({key: cfg[key] for key in LIVE_RESUMABLE_CONFIG_KEYS})
        if recheck and not existing:
            (work / 'recheck.json').unlink(missing_ok=True)
        if state['signature'] != signature:
            raise ValueError('Kaynak/prompt/glossary degismis. Tamamlanmamis bolumun is klasorunu arsivleyin.')
        if state.get('complete'):
            return
        if context not in (state['context_before'], state.get('context_after')):
            raise ValueError('Baglam dosyasi is basladiktan sonra degismis; checkpoint korundu.')
        if 'reference' not in state:
            state['reference'] = book_data(book, state['context_before'], filename)
        if image_only_source(source) and not state['parts'] and state['notes'] is None:
            # Kapak, harita ve benzeri yalnizca gorsel tasiyan parcalari modele
            # gondermek hem token harcar hem de epub-resource hedefini bozabilir.
            chunks = [source]
            state['parts'] = [source.rstrip('\n')]
            state['notes'] = '- Bu bölüm yalnızca görsel içerik taşıdığı için modele gönderilmeden korundu.'
            self.log(f'Yalnizca gorsel bolum modele gonderilmeden korundu: {filename}')
        save_json(state_path, state)
        reference = state['reference']
        system = read(prompt_path())
        for index in range(len(state['parts']), len(chunks)):
            self.controller.checkpoint()
            self.progress(filename=filename, part=index + 1, parts=len(chunks), phase='translation')
            self.log(f'Ceviri {index + 1}/{len(chunks)} — yanit bekleniyor...')
            previous = state['parts'][-1][-1800:] if state['parts'] else '(Yok)'
            user = f'BOOK REFERENCE DATA:\n{reference}\n\nMANDATORY USER GLOSSARY:\n{glossary_text(glossary)}\n\nPREVIOUS TRANSLATION END:\n{previous}\n\nSOURCE TO TRANSLATE — PART {index + 1}/{len(chunks)}:\n{chunks[index]}'
            require_marker = translation_requires_completion_marker(chunks[index], cfg)
            if not require_marker:
                if cfg['completion_marker_enabled']:
                    reason = f'isaretsiz kabul siniri={cfg["completion_marker_exempt_chars"]}'
                else:
                    reason = 'bitis isareti denetimi kapali'
                self.log(f'Parca {len(chunks[index].strip())} karakter ({reason}): '
                         'cikti ve EPUB yapi denetimleri kullaniliyor.')
            retry_count = int(cfg['auto_retry_count'])
            for attempt in range(retry_count + 1):
                retry_system = (system if attempt == 0 else system +
                    '\nA previous response for this same source fragment failed validation. '
                    'Translate the complete fragment again from the beginning and obey every output and glossary rule.')
                try:
                    translated = self._generate(
                        client, retry_system, user, int(cfg['max_tokens']),
                        require_marker=require_marker,
                    )
                    translated = self._enforce_glossary(
                        client, chunks[index], translated, glossary, int(cfg['max_tokens']))
                    translated, restored_anchors = restore_missing_epub_anchors(chunks[index], translated)
                    if restored_anchors:
                        self.log(
                            f'EPUB capasi kaynak konumundan otomatik geri yerlestirildi: '
                            f'{restored_anchors}'
                        )
                    validate_translation_structure(chunks[index], translated)
                    break
                except (ValueError, RuntimeError) as error:
                    if not retryable_translation_error(error) or attempt >= retry_count:
                        raise
                    self.log(
                        f'Ceviri yaniti dogrulanamadi: {error} '
                        f'Otomatik yeniden deneme {attempt + 1}/{retry_count}...'
                    )
                    self.controller.checkpoint()
            state['parts'].append(translated)
            save_json(state_path, state)
            atomic(work / f'part-{index + 1:03}.md', translated + '\n')
            self.log(f'Parca {index + 1} kaydedildi.')
            self.controller.checkpoint()
        translation = '\n\n'.join(state['parts']) + '\n'
        validate_translation_structure(source, translation)
        atomic(work / 'translation-draft.md', translation)
        if state['notes'] is None:
            notes_parts = state.setdefault('notes_parts', [])
            for index in range(len(notes_parts), len(chunks)):
                self.controller.checkpoint()
                self.progress(filename=filename, part=index + 1, parts=len(chunks), phase='notes')
                note = self._generate_retry(
                    client,
                    'Return only concise Turkish Markdown continuity notes: new glossary pairs, address-form decisions, names, and a two-sentence plot summary. Preserve existing decisions. Do not invent facts.',
                    f'EXISTING REFERENCE:\n{reference}\n\nSOURCE:\n{chunks[index]}\n\nTRANSLATION:\n{state["parts"][index]}',
                    int(cfg['notes_max_tokens']),
                    'Bolum notu',
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
            write_context(book, state['context_before'], state['context_after'], exclude=filename)
        elif read(context_path) != state['context_after']:
            raise ValueError('Baglam eszamanli degisti; ceviri kayitli, baglam guncellenmedi.')
        state['complete'] = True
        save_json(state_path, state)
        self.log(f'Kaydedildi: {destination}')
        self.progress(filename=filename, part=1, parts=1, phase='chapter_complete')
        self._close_glossary_fix_log()

    def _reference_over_budget(self, book, client, context):
        """G22: bir sonraki bolumun referansi context butcesini zorluyor mu?"""
        pending = [name for name, status in rows(context) if status == 'next']
        try:
            context_tokens, _source = client.context_length()
        except Exception:
            context_tokens = int(client.cfg.get('fallback_context_length', 32768))
        estimate = len(book_data(book, context, pending[0] if pending else None)) // 3
        limit = int(context_tokens * REFERENCE_BUDGET_RATIO)
        if estimate > limit:
            self.log(f'Baglam referansi buyudu (≈{estimate} token > {limit}); notlar sikistiriliyor.')
            return True
        return False

    def _note_batches(self, client, sections, fixed_chars):
        """Sikistirma istegi de context'e sigsin diye notlari parti parti boler."""
        try:
            context_tokens, _source = client.context_length()
        except Exception:
            context_tokens = int(client.cfg.get('fallback_context_length', 32768))
        budget = (context_tokens - int(client.cfg['notes_max_tokens']) -
                  int(client.cfg.get('context_safety_tokens', 2048))) * 3 - fixed_chars - 2000
        budget = max(budget, 2000)
        batches, current, size = [], [], 0
        for name, notes in sections:
            if len(notes) > budget:
                self.log(f'UYARI: {name} notlari tek istege sigmiyor; sikistirma icin kirpildi.')
                notes = notes[:budget]
            if current and size + len(notes) > budget:
                batches.append(current); current, size = [], 0
            current.append((name, notes)); size += len(notes) + 50
        if current:
            batches.append(current)
        return batches

    def _compress_if_needed(self, book, client):
        book = Path(book)
        context = read(book / '00-CONTEXT.md')
        table = rows(context)
        if not table:
            return
        completed = sum(status == 'done' for _, status in table)
        percentage = completed * 100 / len(table)
        book_state_path = book / '_python_translation' / 'book-state.json'
        state = load_book_state(book)
        state.setdefault('compressed_at', [])
        sections = note_sections(context)
        compressed = compressed_note_names(state, sections)
        if 'compressed_notes' not in state:
            state['compressed_notes'] = sorted(compressed)
            state.pop('summary_note_count', None)
        pending_thresholds = [value for value in (25, 50, 75) if percentage >= value and value not in state['compressed_at']]
        pending_notes = [item for item in sections if item[0] not in compressed]
        if not pending_thresholds and not (pending_notes and self._reference_over_budget(book, client, context)):
            return
        summary_path = book / '_python_translation' / 'continuity-summary.md'
        decisions_path = book / '_python_translation' / 'continuity-decisions.md'
        prior_text = read(summary_path) if summary_path.exists() else '(Yok)'
        decisions_text = read(decisions_path) if decisions_path.exists() else '(Yok)'
        for batch in self._note_batches(client, pending_notes, max(len(prior_text), len(decisions_text))):
            names = [name for name, _notes in batch]
            new_notes = '\n\n'.join(f'### {name}\n{notes}' for name, notes in batch)
            if state.get('decisions_batch') != names:
                # G23: once kalici kararlar ayiklanir, sonra olay ozeti sikistirilir.
                decisions_text = self._generate_retry(
                    client, DECISIONS_PROMPT, f'EXISTING DECISIONS:\n{decisions_text}\n\nNEW NOTES:\n{new_notes}',
                    int(client.cfg['notes_max_tokens']), 'Karar listesi', require_marker=False)
                atomic(decisions_path, decisions_text.strip() + '\n')
                state['decisions_batch'] = names
                save_json(book_state_path, state)
            self.controller.checkpoint()
            prior_text = self._generate_retry(
                client, COMPRESS_PROMPT, f'PREVIOUS COMPRESSED SUMMARY:\n{prior_text}\n\nNEW NOTES:\n{new_notes}',
                int(client.cfg['notes_max_tokens']), 'Baglam ozeti', require_marker=False)
            atomic(summary_path, prior_text.strip() + '\n')
            state['compressed_notes'] = sorted(set(state['compressed_notes']) | set(names))
            state.pop('decisions_batch', None)
            save_json(book_state_path, state)
            self.log('Baglam notlari sikistirildi: ' + ', '.join(names))
            self.controller.checkpoint()
        if pending_thresholds:
            state['compressed_at'].extend(pending_thresholds)
            self.log('Baglam ozeti esikleri islendi: ' + ', '.join(f'%{value}' for value in pending_thresholds))
        save_json(book_state_path, state)

    def rerun_chapters(self, book, filenames):
        """Verilen bolumleri guvenli yeniden cevirir (G14/G15/G21). Once tum secim
        dogrulanir; sonra her biri sifirlanip tek dosya olarak islenir. Bitince
        TAM-CEVIRI.md ve (kitap tamamsa) EPUB yeniden uretilir."""
        book = Path(book)
        filenames = [str(name) for name in filenames]
        with FileLock(book / '_python_translation.lock'):
            validate_rerun(book, filenames)
            for filename in filenames:
                reset_chapter(book, filename)
                self.run_file(book, filename)
                self.controller.checkpoint()
            self._finalize_outputs(book, build_epub=True, require_complete=True)

    def _finalize_outputs(self, book, build_epub=True, require_complete=False):
        book = Path(book)
        table = rows(read(book / '00-CONTEXT.md'))
        completed = [read(book / 'translation' / name) for name, status in table if status == 'done']
        atomic(book / 'TAM-CEVIRI.md', '\n\n'.join(completed))
        if not build_epub or not (book / 'epub-import.json').exists():
            return
        if require_complete and any(status != 'done' for _name, status in table):
            self.log('TAM-CEVIRI.md guncellendi; EPUB kitap tamamlaninca uretilecek.')
            return
        from epub_output import build_translated_epub
        output = build_translated_epub(book, self.log, validation_config=dict(self.config_provider()))
        self.log('EPUB olusturuldu: ' + str(output))

    def run_book(self, book, single_file=None, build_epub=True):
        book = Path(book)
        with FileLock(book / '_python_translation.lock'):
            context = read(book / '00-CONTEXT.md')
            max_iterations = len(rows(context)) * 2 + 1
            previous_file = None
            iterations = 0
            while True:
                context = read(book / '00-CONTEXT.md')
                pending = [name for name, status in rows(context) if status == 'next']
                if single_file:
                    pending = [single_file]
                if not pending:
                    break
                if pending[0] == previous_file:
                    raise RuntimeError(
                        'Ilerleme yok: "' + pending[0] + '" tamamlanmasina ragmen hala siradaki dosya. '
                        '00-CONTEXT.md dosyasindaki durumlari ve checkpointleri kontrol edin; kayitlar korundu.'
                    )
                previous_file = pending[0]
                self.run_file(book, pending[0])
                self.controller.checkpoint()
                # Sikistirma, cevirisi devam eden bolumun kendi config anlik
                # goruntusuyle gider; arada config.json/model degisirse ozet
                # modeli ile ceviri modeli farklilasmaz.
                state_path = book / '_python_translation' / pending[0] / 'state.json'
                cfg = dict(json.loads(read(state_path)).get('effective_config') or self.config_provider())
                validate_config(cfg)
                self._compress_if_needed(book, Client(cfg, self.log))
                iterations += 1
                if iterations > max_iterations:
                    raise RuntimeError(
                        'Guvenlik siniri asildi: ceviri dongusu ilerlemiyor. '
                        '00-CONTEXT.md dosyasindaki durumlari ve checkpointleri kontrol edin; kayitlar korundu.'
                    )
                if single_file:
                    break
            self._finalize_outputs(book, build_epub=build_epub and not single_file)
            self.progress(phase='complete')
            self._close_glossary_fix_log()
