"""Create a translated EPUB while retaining source metadata and binary assets."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit
import uuid
import xml.etree.ElementTree as ET
import zipfile

from app_core import first_heading, markdown_spans, read

OPF = 'http://www.idpf.org/2007/opf'
DC = 'http://purl.org/dc/elements/1.1/'
XHTML = 'http://www.w3.org/1999/xhtml'
ET.register_namespace('', OPF)
ET.register_namespace('dc', DC)


ORDERED_ITEM = re.compile(r'^(\d+)[.)]\s+')


def local(tag):
    return tag.rsplit('}', 1)[-1]


def safe_member(name):
    value = posixpath.normpath(name).lstrip('/')
    if value == '..' or value.startswith('../'):
        raise ValueError('EPUB icinde gecersiz yol: ' + name)
    return value


def inline_markup(text, asset_map, link_map=None, current_href=''):
    """Markdown satir ici isaretlerini XHTML'e cevirir.

    Gorsel/baglanti hedefleri dengeli parantez tarayicisi ile ayrilir; bu sayede
    dosya adindaki ``(1)`` gibi parantezler hedefi kesmez.
    """
    link_map = link_map or {}
    placeholders = {}
    replacements = []

    def placeholder(value):
        key = f'\x00epub{len(placeholders)}\x00'
        placeholders[key] = value
        return key

    for start, end, alt, source in markdown_spans(text, image_only=True):
        if not source.startswith('epub-resource:'):
            continue
        original = safe_member(unquote(urlsplit(source[len('epub-resource:'):]).path))
        href = asset_map.get(original)
        if href:
            value = f'<img src="../{html.escape(href, quote=True)}" alt="{html.escape(alt, quote=True)}" />'
        else:
            value = f'<span class="missing-image">[{html.escape(alt or "Resim", quote=True)}]</span>'
        replacements.append((start, end, placeholder(value)))

    for start, end, label, target in markdown_spans(text):
        if target.startswith('epub-link:'):
            key = unquote(target[len('epub-link:'):])
            mapped = link_map.get(key)
            if mapped:
                target_href = mapped['href']
                fragment = mapped.get('id') or ''
                if target_href == current_href and fragment:
                    href = '#' + fragment
                else:
                    href = posixpath.relpath(target_href, posixpath.dirname(current_href) or '.')
                    if fragment:
                        href += '#' + fragment
                value = f'<a href="{html.escape(href, quote=True)}">{html.escape(label, quote=False)}</a>'
            else:
                value = html.escape(label, quote=False)
        elif re.match(r'^(?:https?:|mailto:)', target, re.I):
            value = f'<a href="{html.escape(html.unescape(target), quote=True)}">{html.escape(label, quote=False)}</a>'
        else:
            value = html.escape(label, quote=False)
        replacements.append((start, end, placeholder(value)))

    for match in re.finditer(r'\[\[EPUB_ANCHOR:([^\]]+)\]\]', text):
        mapped = link_map.get(unquote(match.group(1)))
        value = f'<span id="{html.escape(mapped["id"], quote=True)}"></span>' if mapped and mapped.get('id') else ''
        replacements.append((match.start(), match.end(), placeholder(value)))

    for start, end, value in sorted(replacements, reverse=True):
        text = text[:start] + value + text[end:]
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r'\^([^\^\s][^\^\n]{0,20}?)\^', r'<sup>\1</sup>', escaped)
    escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
    escaped = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<em>\1</em>', escaped)
    for key, replacement in placeholders.items():
        escaped = escaped.replace(key, replacement)
    return escaped


def without_first_heading(markdown):
    """Remove the importer-only TOC heading while preserving native headings."""
    return re.sub(r'\A\s*#{1,6}[^\n]*(?:\n[ \t]*)?\n?', '', markdown, count=1)


def _table_cells(line):
    line = line.strip()
    if line.startswith('|'):
        line = line[1:]
    if line.endswith('|') and not line.endswith(r'\|'):
        line = line[:-1]
    cells, current, escaped = [], [], False
    for char in line:
        if escaped:
            current.append(char); escaped = False
        elif char == '\\':
            escaped = True
        elif char == '|':
            cells.append(''.join(current).strip()); current = []
        else:
            current.append(char)
    cells.append(''.join(current).strip())
    return cells


def _markdown_table(lines):
    if len(lines) < 2 or '|' not in lines[0]:
        return None
    separator = _table_cells(lines[1])
    if not separator or not all(re.fullmatch(r':?-{3,}:?', cell) for cell in separator):
        return None
    rows = [_table_cells(lines[0])] + [_table_cells(line) for line in lines[2:]]
    width = len(separator)
    if any(len(row) != width for row in rows):
        return None
    return rows


def markdown_xhtml(markdown, title, asset_map, hide_first_heading=False, link_map=None, current_href=''):
    if hide_first_heading:
        markdown = without_first_heading(markdown)
    blocks = re.split(r'\n\s*\n', markdown.strip())
    body = []
    for block in blocks:
        stripped = block.strip()
        if re.fullmatch(r'(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,}', stripped):
            body.append('<p class="scene-break">* * *</p>')  # G29: sahne gecisi
            continue
        block_lines = stripped.splitlines()
        table = _markdown_table(block_lines)
        if table:
            head = ''.join(f'<th>{inline_markup(cell, asset_map, link_map, current_href)}</th>' for cell in table[0])
            rows = ''.join('<tr>' + ''.join(
                f'<td>{inline_markup(cell, asset_map, link_map, current_href)}</td>' for cell in row) + '</tr>'
                           for row in table[1:])
            body.append('<table><thead><tr>' + head + '</tr></thead><tbody>' + rows + '</tbody></table>')
            continue
        if block_lines and all(line.startswith('>') for line in block_lines):
            inner = [re.sub(r'^>\s?', '', line) for line in block_lines]
            body.append('<blockquote><p>' + '<br/>'.join(inline_markup(line, asset_map, link_map, current_href) for line in inner) + '</p></blockquote>')
            continue
        if block_lines and all(ORDERED_ITEM.match(line) for line in block_lines):
            start = int(ORDERED_ITEM.match(block_lines[0]).group(1))
            items = ''.join(f'<li>{inline_markup(ORDERED_ITEM.sub("", line, count=1), asset_map, link_map, current_href)}</li>' for line in block_lines)
            body.append((f'<ol start="{start}">' if start != 1 else '<ol>') + items + '</ol>')
            continue
        heading = re.match(r'^(#{1,6})\s+(.+)$', block.strip(), re.S)
        if heading and '\n' not in block.strip():
            level = len(heading.group(1))
            body.append(f'<h{level}>{inline_markup(heading.group(2), asset_map, link_map, current_href)}</h{level}>')
            continue
        image_spans = list(markdown_spans(block.strip(), image_only=True))
        if len(image_spans) == 1 and image_spans[0][0] == 0 and image_spans[0][1] == len(block.strip()):
            body.append('<div class="image">' + inline_markup(block.strip(), asset_map, link_map, current_href) + '</div>')
            continue
        lines = block.splitlines()
        if lines and all(line.startswith('- ') for line in lines):
            body.append('<ul>' + ''.join(f'<li>{inline_markup(line[2:], asset_map, link_map, current_href)}</li>' for line in lines) + '</ul>')
            continue
        body.append('<p>' + '<br/>'.join(inline_markup(line, asset_map, link_map, current_href) for line in lines) + '</p>')
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            f'<html xmlns="{XHTML}" lang="tr" xml:lang="tr"><head><title>{html.escape(title)}</title>'
            '<link rel="stylesheet" type="text/css" href="../style.css"/></head><body>'
            + ''.join(body) + '</body></html>')


def output_path(book, original):
    base = book / f'{original.stem} TR.epub'
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = book / f'{original.stem} TR-{index}.epub'
        if not candidate.exists():
            return candidate
        index += 1


def validate_epub_archive(path):
    """Uretilen paketin temel EPUB/ZIP/XML baglantilarini yerel olarak denetler."""
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if not infos or infos[0].filename != 'mimetype' or infos[0].compress_type != zipfile.ZIP_STORED:
            raise ValueError('EPUB dogrulamasi: mimetype ilk ve sikistirilmamis oge degil.')
        if archive.read('mimetype') != b'application/epub+zip':
            raise ValueError('EPUB dogrulamasi: mimetype icerigi gecersiz.')
        names = set(archive.namelist())
        container = ET.fromstring(archive.read('META-INF/container.xml'))
        opf_path = next((node.attrib.get('full-path') for node in container.iter()
                         if local(node.tag) == 'rootfile'), None)
        if not opf_path or opf_path not in names:
            raise ValueError('EPUB dogrulamasi: package belgesi bulunamadi.')
        package = ET.fromstring(archive.read(opf_path))
        opf_dir = posixpath.dirname(opf_path)
        manifest = {node.attrib.get('id'): node for node in package.iter() if local(node.tag) == 'item'}
        parsed_documents = {}
        for item in manifest.values():
            href = item.attrib.get('href', '')
            member = safe_member(posixpath.join(opf_dir, unquote(urlsplit(href).path)))
            if member not in names:
                raise ValueError('EPUB dogrulamasi: manifest ogesi pakette yok: ' + member)
            media = item.attrib.get('media-type', '')
            if media in ('application/xhtml+xml', 'application/x-dtbncx+xml'):
                parsed_documents[member] = ET.fromstring(archive.read(member))
        for itemref in (node for node in package.iter() if local(node.tag) == 'itemref'):
            if itemref.attrib.get('idref') not in manifest:
                raise ValueError('EPUB dogrulamasi: spine idref manifestte yok.')
        for member, document in parsed_documents.items():
            for node in document.iter():
                for attribute in ('href', 'src'):
                    value = node.attrib.get(attribute)
                    if not value:
                        continue
                    url = urlsplit(value)
                    if url.scheme or url.netloc or value.startswith('data:'):
                        continue
                    target_member = member if not url.path else safe_member(
                        posixpath.join(posixpath.dirname(member), unquote(url.path)))
                    if target_member not in names:
                        raise ValueError('EPUB dogrulamasi: XHTML hedefi pakette yok: ' + target_member)
                    if url.fragment and target_member in parsed_documents:
                        ids = {item.attrib.get('id') for item in parsed_documents[target_member].iter()}
                        if unquote(url.fragment) not in ids:
                            raise ValueError('EPUB dogrulamasi: baglanti capasi bulunamadi: ' + value)


def run_epubcheck(path, configured_path='', log=print):
    """Harici EPUBCheck'i istege bagli calistirir (jar veya exe)."""
    configured_path = str(configured_path or '').strip()
    candidate = Path(configured_path).expanduser() if configured_path else None
    if candidate and not candidate.is_file():
        raise ValueError('EPUBCheck yolu bulunamadi: ' + str(candidate))
    if candidate and candidate.suffix.lower() == '.jar':
        java = shutil.which('java')
        if not java:
            raise ValueError('EPUBCheck JAR secildi fakat Java PATH icinde bulunamadi.')
        command = [java, '-jar', str(candidate), str(path)]
    elif candidate:
        if candidate.suffix.lower() in ('.cmd', '.bat') and os.name == 'nt':
            command = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(candidate), str(path)]
        else:
            command = [str(candidate), str(path)]
    else:
        executable = shutil.which('epubcheck')
        if not executable:
            raise ValueError('EPUBCheck etkin fakat yol ayarlanmadi ve epubcheck PATH icinde bulunamadi.')
        command = [executable, str(path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=300, check=False)
    except subprocess.TimeoutExpired as error:
        raise ValueError('EPUBCheck 300 saniyede tamamlanmadi.') from error
    report = '\n'.join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if report:
        for line in report.splitlines():
            log('EPUBCheck: ' + line)
    if result.returncode != 0:
        raise ValueError(f'EPUBCheck dogrulamasi basarisiz (cikis kodu {result.returncode}); EPUB dosyasi korundu.')
    log('EPUBCheck dogrulamasi basarili.')


def build_translated_epub(book, log=print, validation_config=None):
    book = Path(book)
    marker = json.loads(read(book / 'epub-import.json'))
    source_epub = Path(marker['epub'])
    if not source_epub.is_file():
        raise ValueError('Kaynak EPUB bulunamadi: ' + str(source_epub))
    legacy_hidden_headings = marker.get('import_version') in (3, 4) and marker.get('toc_mode') in ('EPUB3 nav', 'EPUB2 NCX')
    translated = []
    for index, filename in enumerate(marker['files'], 1):
        path = book / 'translation' / filename
        if not path.exists():
            raise ValueError('EPUB olusturulamadi; ceviri eksik: ' + filename)
        text = read(path)
        translated.append({
            'filename': filename,
            'href': f'text/chapter-{index:04}.xhtml',
            'title': first_heading(text, Path(filename).stem),
            'text': text,
            'hide_heading': legacy_hidden_headings,
        })

    with zipfile.ZipFile(source_epub) as source:
        container = ET.fromstring(source.read('META-INF/container.xml'))
        opf_path = next(node.attrib['full-path'] for node in container.iter() if local(node.tag) == 'rootfile')
        package = ET.fromstring(source.read(opf_path))
        opf_dir = posixpath.dirname(opf_path)
        source_manifest = [node for node in package.iter() if local(node.tag) == 'item']
        document_types = {'application/xhtml+xml', 'text/html', 'application/x-dtbncx+xml'}
        asset_entries = []
        asset_map = {}
        for number, item in enumerate(source_manifest, 1):
            media = item.attrib.get('media-type', 'application/octet-stream')
            if media in document_types or 'nav' in item.attrib.get('properties', '').split():
                continue
            original = safe_member(posixpath.join(opf_dir, unquote(urlsplit(item.attrib['href']).path)))
            if original not in source.namelist():
                continue
            target = 'assets/' + original
            properties = item.attrib.get('properties', '')
            asset_entries.append((f'asset-{number}', target, media, properties, original))
            asset_map[original] = target

        metadata_source = next(node for node in package if local(node.tag) == 'metadata')
        metadata = ET.Element(f'{{{OPF}}}metadata')
        has_language = False
        has_identifier = False
        source_titles = [node for node in metadata_source if node.tag == f'{{{DC}}}title']
        subtitle_ids = {
            node.attrib.get('refines', '').lstrip('#') for node in metadata_source
            if local(node.tag) == 'meta' and node.attrib.get('property') == 'title-type'
            and (node.text or '').strip().lower() == 'subtitle'
        }
        title_target_id = next((node.attrib.get('id') for node in reversed(source_titles)
                                if node.attrib.get('id') in subtitle_ids),
                               source_titles[0].attrib.get('id') if source_titles else None)
        title_updated = False
        for child in metadata_source:
            cloned = copy.deepcopy(child)
            if local(cloned.tag) == 'language':
                cloned.text = 'tr'
                has_language = True
            if local(cloned.tag) == 'identifier':
                has_identifier = True
            is_target_title = cloned.tag == f'{{{DC}}}title' and (
                (title_target_id and cloned.attrib.get('id') == title_target_id) or
                (not title_target_id and not title_updated)
            )
            if is_target_title:
                original_title = (cloned.text or '').strip()
                cloned.text = original_title + ('' if original_title.endswith(' TR') else ' TR')
                title_updated = True
            if local(cloned.tag) == 'meta' and cloned.attrib.get('property') == 'dcterms:modified':
                cloned.text = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            metadata.append(cloned)
        if not has_language:
            ET.SubElement(metadata, f'{{{DC}}}language').text = 'tr'
        # EPUB 3 cover-image meta: manifest'te cover-image property'li varliga
        # refines eden <meta property="cover-image"> olmazsa kutuphane/gorsel
        # okuyucular kapaği bulamaz. Kaynak metadata'daki eski cover meta'lari
        # (EPUB2 <meta name="cover" content="..."> dahil) eskimiş id'leri
        # gosterir; temizleyip yeni asset id'leriyle yeniden uretiyoruz.
        for node in list(metadata):
            if local(node.tag) == 'meta' and (
                    node.attrib.get('property') == 'cover-image'
                    or node.attrib.get('name') == 'cover'):
                metadata.remove(node)
        for item_id, _href, _media, properties, _original in asset_entries:
            if 'cover-image' in properties.split():
                ET.SubElement(metadata, f'{{{OPF}}}meta',
                              {'property': 'cover-image', 'refines': '#' + item_id})
        unique_id = package.attrib.get('unique-identifier', 'book-id')
        identifiers = [node for node in metadata if local(node.tag) == 'identifier']
        if not has_identifier:
            identifier = ET.SubElement(metadata, f'{{{DC}}}identifier', {'id': 'book-id'})
            identifier.text = 'urn:uuid:' + str(uuid.uuid4())
            unique_id = 'book-id'
        elif not any(node.attrib.get('id') == unique_id for node in identifiers):
            identifiers[0].set('id', 'book-id')
            unique_id = 'book-id'

        new_package = ET.Element(f'{{{OPF}}}package', {'version': '3.0', 'unique-identifier': unique_id})
        new_package.append(metadata)
        manifest = ET.SubElement(new_package, f'{{{OPF}}}manifest')
        ET.SubElement(manifest, f'{{{OPF}}}item', {'id': 'nav', 'href': 'nav.xhtml', 'media-type': 'application/xhtml+xml', 'properties': 'nav'})
        ET.SubElement(manifest, f'{{{OPF}}}item', {'id': 'ncx', 'href': 'toc.ncx', 'media-type': 'application/x-dtbncx+xml'})
        ET.SubElement(manifest, f'{{{OPF}}}item', {'id': 'style', 'href': 'style.css', 'media-type': 'text/css'})
        spine = ET.SubElement(new_package, f'{{{OPF}}}spine', {'toc': 'ncx'})
        for index, chapter in enumerate(translated, 1):
            ET.SubElement(manifest, f'{{{OPF}}}item', {'id': f'chapter-{index}', 'href': chapter['href'], 'media-type': 'application/xhtml+xml'})
            ET.SubElement(spine, f'{{{OPF}}}itemref', {'idref': f'chapter-{index}'})
        for item_id, href, media, properties, _original in asset_entries:
            attrs = {'id': item_id, 'href': href, 'media-type': media}
            if properties:
                attrs['properties'] = properties
            ET.SubElement(manifest, f'{{{OPF}}}item', attrs)

        toc_chapters = translated
        href_by_filename = {chapter['filename']: chapter['href'] for chapter in translated}
        link_map = {}
        for key, mapped in marker.get('link_map', {}).items():
            href = href_by_filename.get(mapped.get('filename'))
            if href:
                link_map[key] = {'href': href, 'id': mapped.get('id', '')}
        navigation = '<?xml version="1.0" encoding="utf-8"?><html xmlns="%s" xmlns:epub="http://www.idpf.org/2007/ops" lang="tr"><head><title>İçindekiler</title></head><body><nav epub:type="toc" id="toc"><h1>İçindekiler</h1><ol>%s</ol></nav></body></html>' % (XHTML, ''.join(f'<li><a href="{chapter["href"]}">{html.escape(chapter["title"])}</a></li>' for chapter in toc_chapters))
        identifier_text = next((node.text for node in metadata if local(node.tag) == 'identifier' and node.text), 'urn:uuid:' + str(uuid.uuid4()))
        ncx = '<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="%s"/></head><docTitle><text>İçindekiler</text></docTitle><navMap>%s</navMap></ncx>' % (html.escape(identifier_text, quote=True), ''.join(f'<navPoint id="nav-{index}" playOrder="{index}"><navLabel><text>{html.escape(chapter["title"])}</text></navLabel><content src="{chapter["href"]}"/></navPoint>' for index, chapter in enumerate(toc_chapters, 1)))
        container_xml = '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
        style = 'body{font-family:serif;line-height:1.45;margin:5%;}h1,h2,h3{text-align:left;}img{max-width:100%;height:auto;}.image{text-align:center;}p{text-indent:1.2em;margin:.35em 0;}h1+p,h2+p,h3+p{ text-indent:0;}.scene-break{text-align:center;text-indent:0;margin:1em 0;}blockquote{margin:1em 1.5em;}blockquote p{text-indent:0;}table{border-collapse:collapse;width:100%;margin:1em 0;}th,td{border:1px solid #888;padding:.35em;text-align:left;vertical-align:top;}'
        destination = output_path(book, source_epub)
        temporary = destination.with_suffix('.epub.tmp')
        with zipfile.ZipFile(temporary, 'w') as output:
            output.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            output.writestr('META-INF/container.xml', container_xml)
            output.writestr('OEBPS/content.opf', ET.tostring(new_package, encoding='utf-8', xml_declaration=True))
            output.writestr('OEBPS/nav.xhtml', navigation)
            output.writestr('OEBPS/toc.ncx', ncx)
            output.writestr('OEBPS/style.css', style)
            for chapter in translated:
                output.writestr('OEBPS/' + chapter['href'], markdown_xhtml(
                    chapter['text'], chapter['title'], asset_map, chapter['hide_heading'], link_map, chapter['href']))
            for _item_id, target, _media, _properties, original in asset_entries:
                output.writestr('OEBPS/' + target, source.read(original))
        validate_epub_archive(temporary)
        temporary.replace(destination)
    log(f'EPUB metadata dili tr olarak ayarlandi; {len(asset_entries)} kaynak varlik kopyalandi.')
    cfg = validation_config or {}
    if cfg.get('epubcheck_enabled', False):
        run_epubcheck(destination, cfg.get('epubcheck_path', ''), log)
    return destination
