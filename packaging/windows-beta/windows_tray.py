"""Two-item native Windows notification-area UI; no business resources here."""
import ctypes
from ctypes import wintypes as W
import logging
from pathlib import Path
import threading


def alert(message):
    function = ctypes.WinDLL('user32').MessageBoxW
    function.argtypes = [W.HWND, W.LPCWSTR, W.LPCWSTR, W.UINT]
    function.restype = ctypes.c_int
    function(None, str(message), 'Remember Me Beta', 0x10)


def load_brand_icon(root):
    user = ctypes.WinDLL('user32', use_last_error=True)
    load = user.LoadImageW
    load.argtypes = [W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int, W.UINT]
    load.restype = W.HANDLE
    handle = load(None, str(Path(root) / 'web/static/img/brand/favicon-neutral.ico'),
                  1, 0, 0, 0x10 | 0x40)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


class Tray:
    def __init__(self, controller):
        self.controller = controller
        self.lock = threading.Lock()
        self.notice = None
        self.last_notice = None
        self.finished = False

    def run(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        result_type = ctypes.c_ssize_t
        callback = ctypes.WINFUNCTYPE(result_type, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

        class WindowClass(ctypes.Structure):
            _fields_ = [('style', W.UINT), ('procedure', callback), ('class_extra', ctypes.c_int),
                        ('window_extra', ctypes.c_int), ('instance', W.HINSTANCE), ('icon', W.HICON),
                        ('cursor', W.HANDLE), ('background', W.HANDLE), ('menu', W.LPCWSTR),
                        ('name', W.LPCWSTR)]

        class Icon(ctypes.Structure):
            _fields_ = [('size', W.DWORD), ('window', W.HWND), ('id', W.UINT), ('flags', W.UINT),
                        ('callback', W.UINT), ('icon', W.HICON), ('tip', W.WCHAR * 128),
                        ('state', W.DWORD), ('state_mask', W.DWORD), ('info', W.WCHAR * 256),
                        ('version', W.UINT), ('title', W.WCHAR * 64), ('info_flags', W.DWORD),
                        ('guid', ctypes.c_byte * 16), ('balloon_icon', W.HICON)]

        specs = {
            'RegisterClassW': ([ctypes.POINTER(WindowClass)], W.ATOM),
            'CreateWindowExW': ([W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU,
                                 W.HINSTANCE, ctypes.c_void_p], W.HWND),
            'DefWindowProcW': ([W.HWND, W.UINT, W.WPARAM, W.LPARAM], result_type),
            'DestroyIcon': ([W.HICON], W.BOOL),
            'CreatePopupMenu': ([], W.HMENU),
            'AppendMenuW': ([W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR], W.BOOL),
            'TrackPopupMenu': ([W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                W.HWND, ctypes.c_void_p], W.UINT),
            'DestroyMenu': ([W.HMENU], W.BOOL),
            'SetForegroundWindow': ([W.HWND], W.BOOL),
            'GetCursorPos': ([ctypes.POINTER(W.POINT)], W.BOOL),
            'SetTimer': ([W.HWND, ctypes.c_size_t, W.UINT, ctypes.c_void_p], ctypes.c_size_t),
            'DestroyWindow': ([W.HWND], W.BOOL),
            'GetMessageW': ([ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT], W.BOOL),
            'TranslateMessage': ([ctypes.POINTER(W.MSG)], W.BOOL),
            'DispatchMessageW': ([ctypes.POINTER(W.MSG)], result_type),
            'RegisterWindowMessageW': ([W.LPCWSTR], W.UINT),
            'PostQuitMessage': ([ctypes.c_int], None),
        }
        for name, (args, result) in specs.items():
            function = getattr(user, name)
            function.argtypes, function.restype = args, result
        kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = [W.LPCWSTR], W.HMODULE
        shell.Shell_NotifyIconW.argtypes = [W.DWORD, ctypes.POINTER(Icon)]
        shell.Shell_NotifyIconW.restype = W.BOOL
        instance = kernel.GetModuleHandleW(None)
        taskbar_created = user.RegisterWindowMessageW('TaskbarCreated')
        icon = Icon()
        icon.size, icon.id, icon.flags, icon.callback = ctypes.sizeof(Icon), 1, 7, 0x8001

        def publish(add=False):
            icon.tip = ('Remember Me Beta - ' + self.controller.state)[:127]
            if not shell.Shell_NotifyIconW(0 if add else 1, ctypes.byref(icon)):
                logging.error('Cannot publish notification icon: %s', ctypes.get_last_error())
                if add:
                    raise ctypes.WinError(ctypes.get_last_error())
            if add:
                # Version 0 retains simple WM_* mouse callbacks used below.
                icon.version = 0
                shell.Shell_NotifyIconW(4, ctypes.byref(icon))

        def update():
            if not self.lock.acquire(False):
                return
            try:
                state = self.controller.poll()
                if self.controller.error and self.controller.error != self.last_notice:
                    self.notice = self.controller.error
                    self.last_notice = self.controller.error
                    logging.error('%s', self.controller.error)
                if state in ('Stopped', 'Error'):
                    self.finished = True
            except Exception as exc:
                logging.warning('Service status/control failed: %s', exc)
            finally:
                self.lock.release()

        @callback
        def procedure(window, message, wparam, lparam):
            try:
                if message == taskbar_created:
                    publish(True)
                    return 0
                if message == 0x8001:
                    if lparam == 0x203:  # double click
                        self.controller.open()
                    elif lparam == 0x205:  # right button up
                        menu = user.CreatePopupMenu()
                        try:
                            active = self.controller.state in ('Running', 'Maintenance')
                            user.AppendMenuW(menu, 0 if active else 1, 1, 'Open Remember Me')
                            user.AppendMenuW(menu, 1 if self.controller.stopping else 0, 2, 'Exit')
                            point = W.POINT()
                            user.GetCursorPos(ctypes.byref(point))
                            user.SetForegroundWindow(window)
                            selected = user.TrackPopupMenu(menu, 0x100 | 2, point.x, point.y, 0, window, None)
                            if selected == 1:
                                self.controller.open()
                            elif selected == 2:
                                self.controller.stop()
                        finally:
                            user.DestroyMenu(menu)
                    return 0
                if message == 0x113:  # WM_TIMER
                    if self.notice:
                        notice, self.notice = self.notice, None
                        alert(notice)
                    if self.finished:
                        user.DestroyWindow(window)
                    else:
                        publish()
                        threading.Thread(target=update, daemon=True).start()
                    return 0
                if message == 0x10:  # WM_CLOSE
                    self.controller.stop()
                    return 0
                if message == 2:  # WM_DESTROY
                    user.PostQuitMessage(0)
                    return 0
            except Exception:
                logging.exception('Tray event failed')
            return user.DefWindowProcW(window, message, wparam, lparam)

        wc = WindowClass()
        wc.procedure, wc.instance, wc.name = procedure, instance, 'RememberMeBetaTray'
        if not user.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        window = user.CreateWindowExW(0, wc.name, 'Remember Me Beta', 0, 0, 0, 0, 0,
                                      None, None, instance, None)
        if not window:
            raise ctypes.WinError(ctypes.get_last_error())
        icon.window = window
        try:
            icon.icon = load_brand_icon(self.controller.root)
            publish(True)
            from app.startup_timing import mark
            mark('tray_icon_ready')
            if not user.SetTimer(window, 1, 1000, None):
                raise ctypes.WinError(ctypes.get_last_error())
            self.controller.start()
            message = W.MSG()
            while True:
                result = user.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if not result:
                    break
                user.TranslateMessage(ctypes.byref(message))
                user.DispatchMessageW(ctypes.byref(message))
        finally:
            shell.Shell_NotifyIconW(2, ctypes.byref(icon))
            if icon.icon:
                user.DestroyIcon(icon.icon)
