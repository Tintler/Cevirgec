"""EPUB spine extraction with stdlib. No scripts, network or ZIP extraction."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
from urllib.parse import quote, unquote
import xml.etree.ElementTree as ET
import zipfile


EXTERNAL_LINK = re.compile(r'^(?:https?:|mailto:)', re.I)
DECLARED_ENCODING = re.compile(rb"(?:encoding|charset)\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", re.I)


def decode_document(raw, path):
    """XHTML baytlarini metne cevirir (G29): UTF-8, UTF-16 BOM, bildirilen
    kodlama ve son olarak cp1252 denenir; olmazsa anlamli ValueError."""
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16')
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        pass
    declared = DECLARED_ENCODING.search(raw[:4096])
    for encoding in ([declared.group(1).decode('ascii', 'ignore')] if declared else []) + ['cp1252']:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    raise ValueError('EPUB belgesi okunamadi (desteklenmeyen karakter kodlamasi): ' + path)


def tidy_markdown(text):
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'^>\s*$', '', text, flags=re.M)
    return re.sub(r'\n{3,}', '\n\n', text)


class Markdown(HTMLParser):
    BLOCKS = ('p', 'div', 'section', 'article', 'table', 'tr', 'figure', 'aside')

    def __init__(self, document_path):
        super().__init__(convert_charrefs=True)
        self.out=[]; self.skip=0; self.pre=False; self.anchors={}
        self.document_path=document_path; self.links=[]; self.lists=[]; self.quote=0
        self.table=None; self.table_row=None; self.table_cell=None; self.table_header=False

    def _append(self, value):
        (self.table_cell if self.table_cell is not None else self.out).append(value)

    @staticmethod
    def _token(kind, value):
        return f'[[EPUB_{kind}:' + quote(value, safe='/-._~') + ']]'

    def _break(self):
        self._append('\n\n' + ('> ' if self.quote else ''))

    def _finish_table(self):
        rows = self.table or []
        self.table = None
        if not rows:
            return
        width = max(len(row) for row, _header in rows)
        normalized = []
        for row, header in rows:
            cells = row + [''] * (width - len(row))
            normalized.append(([cell.replace('|', r'\|').replace('\n', '<br/>').strip() for cell in cells], header))
        first, first_header = normalized[0]
        header = first if first_header else [''] * width
        body_rows = normalized[1:] if first_header else normalized
        self.out.append('\n\n| ' + ' | '.join(header) + ' |\n')
        self.out.append('| ' + ' | '.join('---' for _ in header) + ' |\n')
        for cells, _header in body_rows:
            self.out.append('| ' + ' | '.join(cells) + ' |\n')
        self.out.append('\n')

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag in ('script','style','head'):
            self.skip+=1; return
        if self.skip:return
        anchor_values=[]
        for key in ('id', 'name'):
            if attrs.get(key) and attrs[key] not in anchor_values:
                anchor_values.append(attrs[key])
                self.anchors[attrs[key]]=len(''.join(self.out))
        for anchor in anchor_values:
            self._append(self._token('ANCHOR', self.document_path + '#' + anchor))
        if tag == 'table':
            self.table=[]; self.table_row=None; self.table_cell=None; return
        if self.table is not None and tag == 'tr':
            self.table_row=[]; self.table_header=False; return
        if self.table is not None and tag in ('td', 'th'):
            self.table_cell=[]; self.table_header = self.table_header or tag == 'th'; return
        if tag == 'blockquote':
            self.quote += 1; self._break()
        elif tag in self.BLOCKS:
            self._break()
        elif tag in ('ul', 'ol'):
            self.lists.append(0 if tag == 'ol' else None); self._break()
        elif re.fullmatch('h[1-6]',tag):self._append('\n\n'+'#'*int(tag[1])+' ')
        elif tag=='br':self._append('\n' + ('> ' if self.quote else ''))
        elif tag=='hr':self._append('\n\n* * *\n\n')  # sahne gecisi (G29)
        elif tag in ('em','i'):self._append('*')
        elif tag in ('strong','b'):self._append('**')
        elif tag=='sup':self._append('^')
        elif tag=='li':
            if self.lists and self.lists[-1] is not None:
                self.lists[-1] += 1; self._append(f'\n{self.lists[-1]}. ')
            else:
                self._append('\n- ')
        elif tag=='img' and attrs.get('src'):
            resource,_=target(self.document_path,attrs['src'])
            self._append('\n\n!['+attrs.get('alt','')+'](epub-resource:'+resource+')\n\n')
        elif tag=='a':
            # Dis baglantilar aynen; EPUB ici dipnot/capraz referanslar kaynak
            # hedefiyle tasinir ve cikti olusturulurken yeni bolume eslenir.
            href = attrs.get('href') or ''
            if EXTERNAL_LINK.match(href):
                self._append('['); self.links.append(href)
            elif href:
                try:
                    path, fragment = target(self.document_path, href)
                except ValueError:
                    self.links.append(None)
                else:
                    key = path + (('#' + fragment) if fragment else '')
                    self._append('['); self.links.append('epub-link:' + quote(key, safe='/-._~'))
            else:
                self.links.append(None)
        elif tag=='pre':self.pre=True;self._append('\n\n')

    def handle_endtag(self,tag):
        if tag in ('script','style','head'):
            self.skip=max(0,self.skip-1);return
        if self.skip:return
        if self.table is not None and tag in ('td', 'th'):
            cell=''.join(self.table_cell or [])
            if self.table_row is None:self.table_row=[]
            self.table_row.append(' '.join(cell.split()))
            self.table_cell=None; return
        if self.table is not None and tag == 'tr':
            if self.table_row:self.table.append((self.table_row, self.table_header))
            self.table_row=None; self.table_header=False; return
        if tag == 'table' and self.table is not None:
            if self.table_row:self.table.append((self.table_row, self.table_header))
            self.table_cell=None; self.table_row=None; self._finish_table(); return
        if tag in ('em','i'):self._append('*')
        elif tag in ('strong','b'):self._append('**')
        elif tag=='sup':self._append('^')
        elif tag=='blockquote':
            self.quote=max(0,self.quote-1); self._append('\n\n')
        elif tag in ('ul','ol'):
            if self.lists: self.lists.pop()
            self._break()
        elif tag in self.BLOCKS or re.fullmatch('h[1-6]',tag):self._break()
        elif tag=='pre':self.pre=False;self._append('\n\n')
        elif tag=='a' and self.links:
            href=self.links.pop()
            if href: self._append(']('+href+')')

    def handle_data(self,data):
        if not self.skip:self._append(data if self.pre else re.sub(r'\s+',' ',data))

    def result(self):
        return tidy_markdown(''.join(self.out)).strip()+'\n'


def local(tag):
    return tag.rsplit('}',1)[-1]


def target(basefile, href):
    from urllib.parse import urlsplit
    url=urlsplit(href)
    if url.scheme or url.netloc:raise ValueError('EPUB TOC dis baglanti iceriyor: '+href)
    path=posixpath.normpath(posixpath.join(posixpath.dirname(basefile),unquote(url.path))) if url.path else basefile
    if path.startswith('../') or path.startswith('/'):raise ValueError('Gecersiz EPUB yolu.')
    return path,unquote(url.fragment)


def toc_entries(z, opf, manifest, package):
    nav=next((item for item in manifest.values() if 'nav' in item.get('properties','').split()),None)
    entries=[]
    if nav:
        navpath,_=target(opf,nav['href'])
        root=ET.fromstring(z.read(navpath))
        toc=next((n for n in root.iter() if local(n.tag)=='nav' and
            ('toc' in next((v for k,v in n.attrib.items() if local(k)=='type'),'').split() or n.attrib.get('role')=='doc-toc')),None)
        if toc is not None:
            def walk(node, parents):
                if local(node.tag)=='li':
                    link=next((c for c in node if local(c.tag) in ('a','span')),None)
                    label=' '.join(''.join(link.itertext()).split()) if link is not None else ''
                    labels=parents+([label] if label else [])
                    if link is not None and link.attrib.get('href'):
                        try:
                            path,anchor=target(navpath,link.attrib['href'])
                        except ValueError:
                            pass  # tek bozuk girdi tum TOC'yu dusurmesin
                        else:
                            entries.append((path,anchor,' — '.join(labels)))
                    for child in node:
                        if local(child.tag) not in ('a','span'):walk(child,labels)
                else:
                    for child in node:walk(child,parents)
            walk(toc,[])
    if entries:return entries,'EPUB3 nav'
    spine=next((n for n in package.iter() if local(n.tag)=='spine'),None)
    ncx=manifest.get(spine.attrib.get('toc','')) if spine is not None else None
    if not ncx:ncx=next((i for i in manifest.values() if i.get('media-type')=='application/x-dtbncx+xml'),None)
    if ncx:
        ncxpath,_=target(opf,ncx['href']);root=ET.fromstring(z.read(ncxpath))
        def walk_ncx(node,parents):
            if local(node.tag)=='navPoint':
                labelnode=next((c for c in node if local(c.tag)=='navLabel'),None)
                label=' '.join(''.join(labelnode.itertext()).split()) if labelnode is not None else ''
                labels=parents+([label] if label else [])
                content=next((c for c in node if local(c.tag)=='content'),None)
                if content is not None and content.attrib.get('src'):
                    path,anchor=target(ncxpath,content.attrib['src']);entries.append((path,anchor,' — '.join(labels)))
                for child in node:
                    if local(child.tag)=='navPoint':walk_ncx(child,labels)
            else:
                for child in node:walk_ncx(child,parents)
        walk_ncx(root,[])
    return entries,'EPUB2 NCX' if entries else 'Spine fallback (TOC yok)'


def chapter_files(z,opf,manifest,spine,package):
    entries,mode=toc_entries(z,opf,manifest,package)
    documents={};full='';starts=[]
    for key in spine:
        item=manifest[key]
        if item.get('media-type') not in ('application/xhtml+xml','text/html'):continue
        path,_=target(opf,item['href'])
        if path in documents:
            # Ayni HTML spine'de birden cok kez gecebilir (yayincilik gercegi);
            # metni cift katlamak yerine tekrar listesini gec.
            continue
        parser=Markdown(path);parser.feed(decode_document(z.read(path),path))
        raw=''.join(parser.out)
        documents[path]=(len(full),parser.anchors)
        starts.append((len(full),PurePosixPath(path).stem))
        full+=raw+'\n\n'
    boundaries={}
    for path,anchor,label in entries:
        if path not in documents:raise ValueError('TOC hedefi spine icinde yok: '+path)
        offset,anchors=documents[path]
        if anchor:
            if anchor not in anchors:raise ValueError('TOC bolum isareti bulunamadi: '+path+'#'+anchor)
            offset+=anchors[anchor]
        boundaries[offset]=label
    if not boundaries:
        boundaries=dict(starts)
    elif min(boundaries)>0:
        first=min(boundaries)
        for offset,label in starts:
            if offset<first:boundaries[offset]=label
    if full[:min(boundaries,default=0)].strip():boundaries[0]='Front matter'
    result=[]; segments=[]; ordered=sorted(boundaries.items())
    for index,(offset,label) in enumerate(ordered):
        end=ordered[index+1][0] if index+1<len(ordered) else len(full)
        text=full[offset:end]
        text=tidy_markdown(text).strip()
        text=re.sub(r'^#{1,6}\s*$', '', text, flags=re.M).strip()
        if not text:continue
        if entries:text='# '+label+'\n\n'+text
        import unicodedata
        slug=unicodedata.normalize('NFKD',label).encode('ascii','ignore').decode()
        slug=re.sub(r'[^A-Za-z0-9]+','-',slug).strip('-')[:120] or 'Section'
        name=f'{len(result)+1:03d}-{slug}.md'
        result.append((name,text+'\n'))
        segments.append((offset,end,name))
    link_map={}
    for path,(document_offset,anchors) in documents.items():
        targets=[(path,document_offset,'')]
        targets.extend((path+'#'+anchor,document_offset+position,anchor) for anchor,position in anchors.items())
        for key,position,anchor in targets:
            segment=next(((start,end,name) for start,end,name in segments if start <= position < end),None)
            if segment is None and segments and position == segments[-1][1]:
                segment=segments[-1]
            if segment is None:
                continue
            generated_id=('ref-'+hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]) if anchor else ''
            link_map[key]={'filename':segment[2],'id':generated_id}
    return result,mode,link_map


def prepare(epub, workspace_parent=None):
    epub=Path(epub).resolve()
    raw=epub.read_bytes(); fingerprint=hashlib.sha256(raw).hexdigest()
    parent=Path(workspace_parent).resolve() if workspace_parent else epub.parent
    parent.mkdir(parents=True,exist_ok=True)
    base_book=parent/(epub.stem+'-ceviri')
    book=base_book
    marker=book/'epub-import.json'
    if marker.exists() and json.loads(marker.read_text(encoding='utf-8')).get('import_version') not in (3,4):
        print('Eski parca duzeni korunuyor. Bolum duzeni icin ayri klasor olusturulacak.')
        book=parent/(epub.stem+'-ceviri-bolumler')
        marker=book/'epub-import.json'
    if marker.exists():
        state=json.loads(marker.read_text(encoding='utf-8'))
        if state.get('sha256')==fingerprint and state.get('import_version') in (3,4):return book
    if book.exists():
        index=2
        while (parent/(epub.stem+f'-ceviri-{index}')).exists():index+=1
        book=parent/(epub.stem+f'-ceviri-{index}')
        marker=book/'epub-import.json'
    with zipfile.ZipFile(epub) as z:
        if any(i.file_size>50_000_000 for i in z.infolist()):raise ValueError('EPUB icinde 50 MB ustu oge var.')
        if 'META-INF/encryption.xml' in z.namelist():
            enc=ET.fromstring(z.read('META-INF/encryption.xml'))
            methods=[n.attrib.get('Algorithm','') for n in enc.iter() if n.tag.endswith('EncryptionMethod')]
            if any(a not in ('http://www.idpf.org/2008/embedding',) for a in methods):
                raise ValueError('Sifreli/DRM EPUB desteklenmiyor.')
        root=ET.fromstring(z.read('META-INF/container.xml'))
        opf=next(n.attrib['full-path'] for n in root.iter() if n.tag.endswith('rootfile'))
        package=ET.fromstring(z.read(opf)); base=posixpath.dirname(opf)
        manifest={n.attrib['id']:n.attrib for n in package.iter() if n.tag.endswith('}item')}
        spine=[n.attrib['idref'] for n in package.iter() if n.tag.endswith('}itemref')]
        meta=lambda key:next((n.text or '' for n in package.iter() if n.tag.endswith('}'+key)), '')
        title=meta('title') or epub.stem; author=meta('creator')
        chapters,mode,link_map=chapter_files(z,opf,manifest,spine,package)
        print('Bolum kaynagi: '+mode,flush=True)
        if not chapters:raise ValueError('EPUB icinde okunabilir metin bulunamadi; taranmis kitap/OCR desteklenmiyor.')
    book.mkdir();(book/'source').mkdir();(book/'translation').mkdir()
    for name,text in chapters:(book/'source'/name).write_text(text,encoding='utf-8')
    context=f'''# 00-CONTEXT
## Metadata
- **Title:** {title}
- **Author:** {author}
- **Direction:** English → Turkish
## Workflow State
- **Style approved:** yes
- **Last completed file:** none
## Files
| File | Status |
|---|---|
'''
    context+='\n'.join(f'| `{n}` | {"next" if i==0 else ""} |' for i,(n,_) in enumerate(chapters))
    context+='''
## Tone and Style
- Follow the source narrator, tense, register and tone; use natural literary Turkish.
- Preserve profanity, ambiguity and deliberate repetition. No unit conversion.
- Address form: infer from the source relationship, then keep it consistent.
- Automatic initial defaults for unattended translation; not a recorded human style review.
## Proper Nouns
Preserve original names unless a conventional Turkish form is clearly established.
## Glossary
No initial glossary. Follow decisions recorded in Translation notes.
## Recurring Phrases
Preserve consistency.
## Section Summaries
'''
    (book/'00-CONTEXT.md').write_text(context,encoding='utf-8')
    marker.write_text(json.dumps({'import_version':4,'toc_mode':mode,'sha256':fingerprint,'epub':str(epub),
                                  'files':[n for n,t in chapters],'link_map':link_map},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'EPUB ayrildi: {len(chapters)} metin dosyasi. Klasor: {book}',flush=True)
    return book
