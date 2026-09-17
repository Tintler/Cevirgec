"""Command-line entry point. The GUI and CLI share app_core.TranslationEngine."""
import argparse
import sys
from pathlib import Path

from app_core import Client, GracefulStop, TranslationEngine, analysis_terms, load_config

def make_logger(book):
    """CLI icin hem stdout'a yazdiran hem de proje icindeki translation.log'a ekleyen log fonksiyonu."""
    log_path = Path(book) / 'translation.log'

    def log(message):
        print(message)
        with log_path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(str(message) + '\n')
            stream.flush()

    return log


def resolve_input(value, explicit_book=False, choose=input):
    value = value.strip()
    if value.startswith('& '):
        value = value[2:].strip()
    value = value.strip('"').strip("'")
    if not value:
        raise ValueError('Dosya veya klasor yolu bos.')
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise ValueError('Yol bulunamadi: ' + str(path))
    if path.is_file():
        if path.suffix.lower() != '.epub':
            raise ValueError('Secilen dosya EPUB degil: ' + str(path))
        return path, True
    if (path / '00-CONTEXT.md').is_file() and (path / 'source').is_dir():
        return path, False
    if explicit_book:
        raise ValueError('--book klasorunde 00-CONTEXT.md ve source klasoru bulunmali.')
    epubs = sorted((item for item in path.iterdir() if item.is_file() and item.suffix.lower() == '.epub'), key=lambda item: item.name.lower())
    if not epubs:
        raise ValueError('Klasorun dogrudan icinde EPUB yok: ' + str(path))
    if len(epubs) == 1:
        return epubs[0], True
    for index, epub in enumerate(epubs, 1):
        print(f'{index}. {epub.name}')
    selection = choose('Cevrilecek kitabin numarasi: ').strip()
    if not selection.isdigit() or not 1 <= int(selection) <= len(epubs):
        raise ValueError('Gecersiz kitap numarasi.')
    return epubs[int(selection) - 1], True


def review_analysis_terms(engine, book, confirm=input):
    """G16: On analiz onerilerini CLI'da kullaniciya gosterir ve onaya sunar.

    Evet denirse oneriler glossary.json'a islenir (duplicate onlenir);
    hayir/atlanirsa mevcut glossary aynen kalir. Onay islemi testlerde
    `confirm` parametresiyle soketlenir.
    """
    terms = analysis_terms(book)
    if not terms:
        return False
    print('\nOn analiz su terim onerilerini buldu:')
    for item in terms:
        scope = '' if item.get('scope', 'book') == 'book' else '  (bolum ozel; otomatik eklenmez)'
        print(f"  - {item.get('source', '')} -> {item.get('suggested_target', '')}{scope}")
    answer = confirm('Kitap geneli oneriler glossary.json ile birlestirilsin mi? Mevcut kayitlar korunur. [e/H]: ').strip().lower()
    if answer in ('e', 'evet', 'y', 'yes'):
        engine._merge_analysis_terms_into_glossary(book)
        print('Oneriler glossary.json ile birlestirildi.')
        return True
    print('Oneriler reddedildi; mevcut glossary korunuyor.')
    return False


def main(confirm=input):
    parser = argparse.ArgumentParser(description='LM Studio ile EPUB/Markdown cevirisi.')
    parser.add_argument('--epub', help='EPUB dosyasini bol, cevir ve EPUB olustur')
    parser.add_argument('--book', help='Hazir calisma klasoru')
    parser.add_argument('--file', help='Yalnizca bir Markdown kaynak dosyasi')
    parser.add_argument('--check', action='store_true', help='API, model ve context bilgisini goster')
    parser.add_argument('--analyze', action='store_true', help='Ceviriden once istege bagli kitap on analizi yap')
    parser.add_argument('--analyze-file', action='append', help='On analize dahil edilecek Markdown dosyasi; birden fazla kez kullanilabilir')
    args = parser.parse_args()
    cfg = load_config()
    if args.check:
        client = Client(cfg)
        info, models = client.model_info()
        print('API erisilebilir. Modeller:')
        for model in models:
            print(model.get('key') or model.get('id'))
        print('Secili model:', cfg['model'])
        print('Model bilgisi:', info or 'Bulunamadi')
        return
    chosen = args.epub or args.book or input('EPUB dosyasinin veya EPUB iceren klasorun tam yolu: ')
    selected, is_epub = resolve_input(chosen, explicit_book=bool(args.book))
    if is_epub:
        from epub_import import prepare
        book = prepare(selected)
    else:
        book = selected
    engine = TranslationEngine(load_config, log=make_logger(book))
    if args.analyze or args.analyze_file:
        engine.run_analysis(book, args.analyze_file)
        review_analysis_terms(engine, book, confirm=confirm)
    engine.run_book(book, single_file=args.file, build_epub=not bool(args.file))
    print('Tamamlandi:', book)


if __name__ == '__main__':
    try:
        main()
    except GracefulStop as error:
        print(str(error))
    except (Exception, KeyboardInterrupt) as error:
        print(f'\nDURDURULDU: {error or "Kullanici durdurdu"}\nKaydedilmis checkpointler korundu.', file=sys.stderr)
        sys.exit(1)
