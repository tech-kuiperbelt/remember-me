"""Remember Me's Linux tray launcher. Business resources live in its child."""
import argparse
import logging
import os
from pathlib import Path
import signal
import sys
import threading
import webbrowser


ROOT = Path(__file__).resolve().parents[1]


class LauncherUI:
    def __init__(self, controller, autostart, gui):
        self.controller = controller
        self.autostart = autostart
        self.Gtk, self.GLib, self.Gio, self.pystray, self.Image = gui
        self.snapshot = {'state': 'Starting', 'url': '', 'error': None}
        self.icon = None
        self.window = None
        self.status_label = None
        self.open_button = None
        self.stop_polling = threading.Event()
        self.quitting = False
        self.cleaned = False
        self.autostart_enabled = self.autostart.enabled()
        self._previous_exception_hook = sys.excepthook

    def _signals(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda signum, frame: self.GLib.idle_add(self.quit))

    def _tray_available(self):
        try:
            bus = self.Gio.bus_get_sync(self.Gio.BusType.SESSION, None)
            result = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                                  'org.freedesktop.DBus', 'NameHasOwner',
                                  self.GLib.Variant('(s)', ('org.kde.StatusNotifierWatcher',)),
                                  self.GLib.VariantType.new('(b)'),
                                  self.Gio.DBusCallFlags.NO_AUTO_START, 500, None)
            if not result.unpack()[0]:
                return False
            result = bus.call_sync('org.kde.StatusNotifierWatcher', '/StatusNotifierWatcher',
                                  'org.freedesktop.DBus.Properties', 'Get',
                                  self.GLib.Variant('(ss)', ('org.kde.StatusNotifierWatcher',
                                                           'IsStatusNotifierHostRegistered')),
                                  self.GLib.VariantType.new('(v)'),
                                  self.Gio.DBusCallFlags.NO_AUTO_START, 500, None)
            return bool(result.unpack()[0])
        except Exception:
            return False

    def _status_text(self):
        state = self.snapshot['state']
        error = self.snapshot.get('error')
        return f'{state}: {error}' if error else state

    def _can_open(self):
        return not self.quitting and self.snapshot['state'] in ('Running', 'Maintenance')

    def _make_tray(self):
        if self.icon is None:
            Item, Menu = self.pystray.MenuItem, self.pystray.Menu
            menu = Menu(
                Item(lambda item: f'Remember Me — {self.snapshot["state"]}', None, enabled=False),
                Menu.SEPARATOR,
                Item('Open Remember Me', lambda icon, item: self.open_page(), enabled=lambda item: self._can_open()),
                Item('Memories', lambda icon, item: self.open_page('#memories'), enabled=lambda item: self._can_open()),
                Item('Stats', lambda icon, item: self.open_page('#stats'), enabled=lambda item: self._can_open()),
                Item('Settings', lambda icon, item: self.open_page('#ops'), enabled=lambda item: self._can_open()),
                Item('Help', lambda icon, item: self.open_page('#help'), enabled=lambda item: self._can_open()),
                Menu.SEPARATOR,
                Item('Run automatically at login', lambda icon, item: self.toggle_autostart(),
                     checked=lambda item: self.autostart_enabled,
                     enabled=lambda item: not self.quitting),
                Item('Quit Remember Me', lambda icon, item: self.quit(), enabled=lambda item: not self.quitting))
            image = self.Image.open(ROOT / 'web/static/img/brand/logo-neutral-64.png').convert('RGBA')
            self.icon = self.pystray.Icon('remember-me-launcher', image, 'Remember Me', menu)
            self.icon.run_detached(setup=lambda icon: self.GLib.idle_add(self._show_icon))
            # GtkIcon's initialization resets SIGINT to SIG_DFL.
            self._signals()
        elif not self.icon.visible:
            self.icon.visible = True

    def _show_icon(self):
        if not self.quitting:
            self.icon.visible = True
        return False

    def _fallback(self):
        if self.window is None:
            self.window = self.Gtk.Window(title='Remember Me')
            self.window.set_default_size(390, 160)
            self.window.set_border_width(18)
            self.window.connect('delete-event', lambda *args: self._window_closed())
            box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=12)
            self.status_label = self.Gtk.Label()
            self.status_label.set_line_wrap(True)
            self.status_label.set_selectable(True)
            box.pack_start(self.Gtk.Label(label='System tray unavailable'), False, False, 0)
            box.pack_start(self.status_label, True, True, 0)
            buttons = self.Gtk.Box(spacing=8)
            self.open_button = self.Gtk.Button(label='Open Remember Me')
            self.open_button.connect('clicked', lambda button: self.open_page())
            buttons.pack_start(self.open_button, True, True, 0)
            quit_button = self.Gtk.Button(label='Quit Remember Me')
            quit_button.connect('clicked', lambda button: self.quit())
            buttons.pack_start(quit_button, True, True, 0)
            box.pack_start(buttons, False, False, 0)
            self.window.add(box)
        self.status_label.set_text(self._status_text())
        self.open_button.set_sensitive(self._can_open())
        self.window.show_all()

    def _window_closed(self):
        self.quit()
        return True

    def error(self, message):
        logging.error('%s', message)
        dialog = self.Gtk.MessageDialog(transient_for=self.window,
                                        message_type=self.Gtk.MessageType.ERROR,
                                        buttons=self.Gtk.ButtonsType.OK,
                                        text='Remember Me Launcher')
        dialog.format_secondary_text(str(message))
        dialog.connect('response', lambda widget, response: widget.destroy())
        dialog.show_all()
        return False

    def open_page(self, fragment=''):
        if not self._can_open():
            return
        url = self.snapshot['url'].rstrip('/') + '/admin/console' + fragment

        def open_browser():
            try:
                if not webbrowser.open(url, new=2):
                    raise RuntimeError(f'Could not open the default browser. Open this URL: {url}')
            except Exception as exc:
                self.GLib.idle_add(self.error, str(exc))
        threading.Thread(target=open_browser, name='launcher-browser', daemon=True).start()

    def toggle_autostart(self):
        try:
            self.autostart.set_enabled(not self.autostart_enabled)
            self.autostart_enabled = self.autostart.enabled()
            if self.icon is not None:
                self.icon.update_menu()
        except Exception as exc:
            self.error(str(exc))

    def _apply(self, snapshot, host):
        if self.quitting:
            return False
        previous_error = self.snapshot.get('error')
        changed = self.snapshot != snapshot
        self.snapshot = snapshot
        if host:
            self._make_tray()
            if self.window is not None:
                self.window.hide()
        else:
            if self.icon is not None and self.icon.visible:
                self.icon.visible = False
            self._fallback()
        if self.icon is not None and changed:
            self.icon.title = f'Remember Me — {snapshot["state"]}'
            self.icon.update_menu()
        if snapshot.get('error') and snapshot.get('error') != previous_error:
            self.error(snapshot['error'])
        return False

    def _poll(self):
        while not self.stop_polling.wait(0.5):
            try:
                snapshot = self.controller.poll()
                host = self._tray_available()
                self.GLib.idle_add(self._apply, snapshot, host)
            except Exception as exc:
                self.GLib.idle_add(self._fatal, str(exc))
                return

    def _fatal(self, message):
        self.error(message)
        self.quit()
        return False

    def quit(self):
        if self.quitting:
            return False
        self.quitting = True
        self.stop_polling.set()
        self.snapshot = {**self.snapshot, 'state': 'Stopping'}
        if self.icon is not None:
            self.icon.title = 'Remember Me — Stopping'
            self.icon.update_menu()
        if self.window is not None and self.window.get_visible():
            self._fallback()

        def stop_service():
            try:
                self.controller.close()
            except Exception as exc:
                logging.exception('Failed while stopping Remember Me')
                self.GLib.idle_add(self._stop_failed, str(exc))
            else:
                self.GLib.idle_add(self._finish)
        threading.Thread(target=stop_service, name='launcher-stop').start()
        return False

    def _stop_failed(self, message):
        self.quitting = False
        self.snapshot = {**self.snapshot, 'state': 'Error', 'error': message}
        if self.icon is not None:
            self.icon.update_menu()
        self.error(message)
        if self.window is not None and self.window.get_visible():
            self._fallback()
        return False

    def _finish(self):
        self.cleaned = True
        try:
            if self.icon is not None:
                self.icon.visible = False
                self.icon.stop()
            if self.window is not None:
                self.window.destroy()
        finally:
            self.Gtk.main_quit()
        return False

    def run(self):
        try:
            self._signals()
            if self._tray_available():
                self._make_tray()
            else:
                self._fallback()
            self.snapshot = self.controller.start()  # Must remain on this long-lived main thread.
            logging.info('Remember Me data directory: %s', self.controller.data_dir)
            self._apply(self.snapshot, self._tray_available())
            sys.excepthook = lambda kind, value, trace: self.GLib.idle_add(self._fatal, str(value))
            threading.Thread(target=self._poll, name='launcher-status', daemon=True).start()
            self.Gtk.main()
        finally:
            sys.excepthook = self._previous_exception_hook
            self.quitting = True
            self.stop_polling.set()
            if not self.cleaned:
                self.controller.close()
            if self.icon is not None:
                self.icon.visible = False
                self.icon.stop()
            if self.window is not None:
                self.window.destroy()


def _load_gui():
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib, Gio
    initialized = Gtk.init_check()
    if not (initialized[0] if isinstance(initialized, tuple) else initialized):
        raise RuntimeError('GTK cannot connect to the graphical desktop session')
    os.environ['PYSTRAY_BACKEND'] = 'appindicator'
    import pystray
    from PIL import Image
    if not pystray.Icon.HAS_MENU:
        raise RuntimeError('The AppIndicator backend has no usable menu support')
    return Gtk, GLib, Gio, pystray, Image


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', help='Persistent data root (default: XDG_DATA_HOME/remember-me-launcher)')
    parser.add_argument('--port', type=int, help='Override the configured port for this launch')
    args = parser.parse_args(argv)
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'launcher/vendor'))
    from launcher.controller import ServiceController, default_data_dir
    from launcher.desktop import Autostart
    data = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    if not data.is_absolute():
        data = ROOT / data
    data = data.resolve()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    try:
        gui = _load_gui()
    except Exception as exc:
        print(f'Remember Me Launcher cannot start: {exc}. No service was started.', file=sys.stderr)
        return 1
    controller = ServiceController(ROOT, data, args.port)
    autostart = Autostart(ROOT / 'launch.sh', data, args.port)
    try:
        LauncherUI(controller, autostart, gui).run()
        return 0
    except Exception as exc:
        logging.error('Remember Me Launcher failed: %s', exc)
        dialog = gui[0].MessageDialog(message_type=gui[0].MessageType.ERROR,
                                     buttons=gui[0].ButtonsType.OK,
                                     text='Remember Me Launcher could not start')
        dialog.format_secondary_text(str(exc))
        dialog.run()
        dialog.destroy()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
