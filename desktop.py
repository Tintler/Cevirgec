"""Desktop entry point: opens the Svelte interface in a native WebView2 window via pywebview.

    py -3 desktop.py          built interface from web/dist
    py -3 desktop.py --dev    Vite dev server at http://localhost:5173 (run `npm run dev` in web/)
"""
from __future__ import annotations

import json
import sys

import webview
from webview.dom import DOMEventHandler

from app_core import ROOT
from bridge import Bridge

DEV_URL = 'http://localhost:5173'


class Dialogs:
    def __init__(self):
        self.window = None

    def open_file(self, title, file_types):
        result = self.window.create_file_dialog(webview.FileDialog.OPEN, file_types=file_types)
        return result[0] if result else None

    def open_files(self, title, file_types):
        result = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True, file_types=file_types)
        return list(result) if result else None

    def save_file(self, title, filename, file_types):
        result = self.window.create_file_dialog(webview.FileDialog.SAVE, save_filename=filename, file_types=file_types)
        if isinstance(result, (list, tuple)):
            result = result[0] if result else None
        return result or None

    def open_folder(self, title):
        result = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        return result[0] if result else None


def main():
    dev = '--dev' in sys.argv
    index = ROOT / 'web' / 'dist' / 'index.html'
    if not dev and not index.exists():
        sys.exit('Arayüz derlenmemiş: web klasöründe "npm install" ve "npm run build" çalıştırın '
                 '(ya da GUI_BASLAT.cmd kullanın).')
    dialogs = Dialogs()
    state = {'window': None, 'closing_ok': False}

    def emit(event, data):
        window = state['window']
        if window is not None:
            payload = json.dumps(data, ensure_ascii=False)
            window.run_js(f'window.__cevirgecEmit && window.__cevirgecEmit({json.dumps(event)}, {payload})')

    bridge = Bridge(emit=emit, dialogs=dialogs)
    width, height = 1320, 860
    if webview.screens:
        # Leave room for the taskbar on small or highly scaled screens.
        screen = webview.screens[0]
        width, height = min(width, int(screen.width * 0.92)), min(height, int(screen.height * 0.88))
    window = webview.create_window('Çevirgeç', DEV_URL if dev else str(index), js_api=bridge,
                                   width=width, height=height, min_size=(980, 640), background_color='#09090b')
    state['window'] = dialogs.window = window

    def close_later():
        state['closing_ok'] = True
        window.destroy()

    def on_closing():
        if state['closing_ok']:
            return True
        return bridge.request_close(close_later)

    window.events.closing += on_closing

    def on_drop(event):
        files = event.get('dataTransfer', {}).get('files', [])
        paths = [item['pywebviewFullPath'] for item in files if item.get('pywebviewFullPath')]
        if paths:
            bridge.library_add(paths)

    def on_loaded():
        # EPUB files dropped anywhere on the window are added to the reading library.
        window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True)

    window.events.loaded += on_loaded
    icon = ROOT / 'assets' / 'app.ico'
    webview.start(http_server=not dev, debug='--debug' in sys.argv, icon=str(icon) if icon.exists() else None)


if __name__ == '__main__':
    main()
