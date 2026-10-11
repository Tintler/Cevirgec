"""The window's own title bar on Windows: no system frame, the interface draws minimise, maximise/restore
and close; its empty top strip moves the window and its edges resize it.

Moving is handed to Windows itself (the message a title bar click sends), so it is as smooth as a normal
window's. Windows does not resize a window without a sizing border that way, so resizing follows the
pointer here until the button is released. Elsewhere the system frame stays and the interface hides its
buttons.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import threading
import time

WM_SYSCOMMAND = 0x0112
WM_NCLBUTTONDOWN = 0x00A1
SC_MINIMIZE, SC_MAXIMIZE, SC_RESTORE, SC_CLOSE = 0xF020, 0xF030, 0xF120, 0xF060
HTCAPTION = 2
# Which sides of the window each resize zone moves: (left, top, right, bottom).
EDGES = {'left': (1, 0, 0, 0), 'right': (0, 0, 1, 0), 'top': (0, 1, 0, 0), 'bottom': (0, 0, 0, 1),
         'topleft': (1, 1, 0, 0), 'topright': (0, 1, 1, 0), 'bottomleft': (1, 0, 0, 1), 'bottomright': (0, 0, 1, 1)}
VK_LBUTTON, VK_RBUTTON, SM_SWAPBUTTON = 0x01, 0x02, 23
SWP_NOACTIVATE = 0x0010
RESIZE_INTERVAL = 1 / 120
GWL_STYLE = -16
WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00080000, 0x00020000, 0x00010000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x0001, 0x0002, 0x0004, 0x0020
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2

SUPPORTED = sys.platform == 'win32'


def frameless():
    """True when the window should be created without the system frame."""
    return SUPPORTED


class WindowFrame:
    def __init__(self, window, emit):
        self.window = window
        self._emit = emit
        self._hwnd = None
        self._maximized = None
        if SUPPORTED:
            self._user32 = ctypes.WinDLL('user32', use_last_error=True)
            self._user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            self._user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            self._user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            self._user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            self._user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
            self._user32.IsZoomed.argtypes = [wintypes.HWND]
            self._user32.IsWindowVisible.argtypes = [wintypes.HWND]
            self._user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            self._user32.GetAsyncKeyState.restype = ctypes.c_short
            window.events.shown += self._on_shown
            window.events.moved += lambda *_: self._sync()
            window.events.resized += lambda *_: self._sync()

    # ------------------------------------------------------------------ setup
    def _ui(self, fn):
        """Runs fn on the window's UI thread and returns its result."""
        from System import Func, Object
        box = []
        self.window.native.Invoke(Func[Object](lambda: box.append(fn())))
        return box[0] if box else None

    def _on_shown(self):
        try:
            self._hwnd = self._ui(lambda: int(self.window.native.Handle.ToInt64()))
        except Exception:
            self._hwnd = None
            return
        hwnd = self._hwnd
        # Without these styles a frameless window cannot be minimised from the taskbar or with Win+Down.
        style = self._user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        self._user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        self._user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
        try:  # Windows 11: rounded corners like other windows (ignored on Windows 10).
            corner = ctypes.c_int(DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
                                                       ctypes.byref(corner), ctypes.sizeof(corner))
        except (AttributeError, OSError):
            pass
        self._sync()

    def _fit_maximized(self):
        """A frameless window would maximise over the taskbar; keep it to the screen's working area."""
        def apply():
            from System.Drawing import Rectangle
            from System.Windows.Forms import Screen
            form = self.window.native
            screen = Screen.FromHandle(form.Handle)
            area, bounds = screen.WorkingArea, screen.Bounds
            form.MaximizedBounds = Rectangle(area.X - bounds.X, area.Y - bounds.Y, area.Width, area.Height)
        self._ui(apply)

    def _sync(self):
        """Keeps the maximised size right for the current screen and tells the interface the state."""
        # Closing hides the window first; the page is gone by then and cannot be told anything.
        if not self._hwnd or not self._user32.IsWindowVisible(self._hwnd):
            return
        try:
            self._fit_maximized()
        except Exception:
            pass
        maximized = bool(self._user32.IsZoomed(self._hwnd))
        if maximized != self._maximized:
            self._maximized = maximized
            self._emit('window', self.state())

    # ------------------------------------------------------------------ commands from the interface
    def state(self):
        return {'custom': True, 'maximized': bool(self._maximized)}

    def _command(self, code):
        self._user32.PostMessageW(self._hwnd, WM_SYSCOMMAND, code, 0)

    def minimize(self):
        if self._hwnd:
            self._command(SC_MINIMIZE)
        else:
            self.window.minimize()

    def toggle_maximize(self):
        if not self._hwnd:
            self.window.maximize()
            return
        if self._user32.IsZoomed(self._hwnd):
            self._command(SC_RESTORE)
        else:
            self._fit_maximized()
            self._command(SC_MAXIMIZE)

    def close(self):
        """Same path as Alt+F4, so the running-translation check in desktop.py still applies."""
        # Shortly after returning, so pywebview can still hand this call's result to the page.
        if self._hwnd:
            threading.Timer(0.1, self._command, (SC_CLOSE,)).start()
        else:
            threading.Timer(0.1, self.window.destroy).start()

    def _cursor(self):
        point = wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(point))
        return point

    def drag(self):
        if not self._hwnd:
            return
        if self._user32.IsZoomed(self._hwnd):
            self._unmaximize_under_cursor()
        point = self._cursor()
        lparam = (point.x & 0xFFFF) | ((point.y & 0xFFFF) << 16)

        def start():
            # The page holds the mouse; Windows takes it over and runs its own move loop.
            self._user32.ReleaseCapture()
            self._user32.PostMessageW(self._hwnd, WM_NCLBUTTONDOWN, HTCAPTION, lparam)
        self._ui(start)

    def _unmaximize_under_cursor(self):
        """Dragging a maximised window restores it with the same spot of the strip under the pointer."""
        point = self._cursor()
        rect = wintypes.RECT()
        self._user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        ratio = (point.x - rect.left) / max(1, rect.right - rect.left)
        offset_y = point.y - rect.top

        def restore():
            from System.Windows.Forms import FormWindowState
            self.window.native.WindowState = FormWindowState.Normal
        self._ui(restore)
        self._user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        self._user32.SetWindowPos(self._hwnd, None, int(point.x - ratio * width), point.y - offset_y, 0, 0,
                                  SWP_NOSIZE | SWP_NOZORDER)

    def resize(self, edge):
        """Follows the pointer from a resize zone until the mouse button is released."""
        if not self._hwnd or edge not in EDGES or self._user32.IsZoomed(self._hwnd):
            return
        left, top, right, bottom = EDGES[edge]
        button = VK_RBUTTON if self._user32.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON
        minimum = self._ui(lambda: (self.window.native.MinimumSize.Width, self.window.native.MinimumSize.Height))
        min_width, min_height = minimum or (1, 1)
        start = self._cursor()
        rect = wintypes.RECT()
        self._user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        x0, y0, x1, y1 = rect.left, rect.top, rect.right, rect.bottom
        last = None
        while self._user32.GetAsyncKeyState(button) & 0x8000:
            point = self._cursor()
            dx, dy = point.x - start.x, point.y - start.y
            # The moving side stops where the window reaches its minimum size; the other side stays put.
            new_left = min(x0 + dx, x1 - min_width) if left else x0
            new_right = max(x1 + dx, x0 + min_width) if right else x1
            new_top = min(y0 + dy, y1 - min_height) if top else y0
            new_bottom = max(y1 + dy, y0 + min_height) if bottom else y1
            bounds = (new_left, new_top, new_right - new_left, new_bottom - new_top)
            if bounds != last:
                last = bounds
                self._user32.SetWindowPos(self._hwnd, None, *bounds, SWP_NOZORDER | SWP_NOACTIVATE)
            time.sleep(RESIZE_INTERVAL)
