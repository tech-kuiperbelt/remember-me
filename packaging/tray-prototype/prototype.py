"""Bounded S2-A tray menu probe; does not start or control Remember Me."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path,
                        help='Isolated output directory; defaults to a new /tmp directory')
    parser.add_argument('--duration-seconds', type=int, default=0,
                        help='Automatically exit after 1–3600 seconds; 0 waits for Exit')
    args = parser.parse_args()
    if not 0 <= args.duration_seconds <= 3600:
        parser.error('--duration-seconds must be between 0 and 3600')
    evidence = (args.evidence_dir or Path(tempfile.mkdtemp(prefix='remember-me-tray-s2a-'))).resolve()
    root = Path(__file__).resolve().parent
    if evidence == root or root in evidence.parents:
        parser.error('evidence directory must be outside the prototype directory')
    evidence.mkdir(parents=True, exist_ok=True)
    state = {'clicks': 0, 'checked': False, 'revision': 0}
    with (evidence / 'events.jsonl').open('x', encoding='utf-8') as journal:
        def record(event, **details):
            row = {'at': datetime.now(timezone.utc).isoformat(), 'event': event,
                   **state, **details}
            journal.write(json.dumps(row, ensure_ascii=False) + '\n')
            journal.flush()
            print(json.dumps(row, ensure_ascii=False), flush=True)

        record('start', pid=os.getpid(), executable=sys.executable, prefix=sys.prefix,
               root=str(root), evidence_dir=str(evidence),
               desktop=os.environ.get('XDG_CURRENT_DESKTOP'),
               session_type=os.environ.get('XDG_SESSION_TYPE'))
        icon = None
        try:
            sys.path.insert(0, str(root / 'vendor'))
            os.environ['PYSTRAY_BACKEND'] = 'appindicator'
            import gi
            gi.require_version('Gtk', '3.0')
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio, GLib, Gtk
            initialized = Gtk.init_check()
            if not (initialized[0] if isinstance(initialized, tuple) else initialized):
                raise RuntimeError('GTK cannot connect to this graphical session')
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            owner = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                                  'org.freedesktop.DBus', 'NameHasOwner',
                                  GLib.Variant('(s)', ('org.kde.StatusNotifierWatcher',)),
                                  GLib.VariantType.new('(b)'),
                                  Gio.DBusCallFlags.NO_AUTO_START, 3000, None)
            if not owner.unpack()[0]:
                raise RuntimeError('No StatusNotifierWatcher; enable a compatible desktop tray host')
            host = bus.call_sync('org.kde.StatusNotifierWatcher', '/StatusNotifierWatcher',
                                 'org.freedesktop.DBus.Properties', 'Get',
                                 GLib.Variant('(ss)', ('org.kde.StatusNotifierWatcher',
                                                      'IsStatusNotifierHostRegistered')),
                                 GLib.VariantType.new('(v)'),
                                 Gio.DBusCallFlags.NO_AUTO_START, 3000, None)
            if not host.unpack()[0]:
                raise RuntimeError('StatusNotifierWatcher has no registered desktop host')
            import pystray
            from PIL import Image, ImageDraw
            if not pystray.Icon.HAS_MENU:
                raise RuntimeError('Selected backend has no menu support')
            record('dependencies_loaded', gi=gi.__file__, pystray=pystray.__file__,
                   pillow=Image.__file__, backend=pystray.Icon.__module__,
                   gtk_version='.'.join(map(str, (Gtk.get_major_version(),
                                                  Gtk.get_minor_version(),
                                                  Gtk.get_micro_version()))),
                   host_registered=True)
            image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((3, 3, 61, 61), radius=12, fill='#168a78',
                                   outline='white', width=3)
            draw.text((12, 23), 'S2-A', fill='white')

            def clicked(icon, item):
                state['clicks'] += 1
                icon.update_menu()
                record('menu_click')

            def toggled(icon, item):
                state['checked'] = not state['checked']
                icon.update_menu()
                record('checkbox_toggled')

            def updated(icon, item):
                state['revision'] += 1
                icon.update_menu()
                record('menu_updated')

            def stop(reason):
                record('stop_requested', reason=reason)
                icon.stop()
                return False

            icon = pystray.Icon('remember-me-s2a-probe', image,
                                'Remember Me S2-A GUI probe',
                                menu=pystray.Menu(
                                    pystray.MenuItem('Remember Me S2-A — GUI only', None,
                                                     enabled=False),
                                    pystray.MenuItem(lambda item: f'Clicks: {state["clicks"]}',
                                                     None, enabled=False),
                                    pystray.MenuItem('Increment click counter', clicked),
                                    pystray.MenuItem('Test checkbox', toggled,
                                                     checked=lambda item: state['checked']),
                                    pystray.MenuItem(lambda item: f'Update label (revision {state["revision"]})',
                                                     updated),
                                    pystray.Menu.SEPARATOR,
                                    pystray.MenuItem('Exit prototype',
                                                     lambda icon, item: stop('menu_exit'))))

            def ready():
                # GtkIcon initializes SIGINT itself: install ours after initialization.
                for sig in (signal.SIGINT, signal.SIGTERM):
                    signal.signal(sig, lambda signum, frame: GLib.idle_add(stop, signal.Signals(signum).name))
                record('ready', note='Client ready; visibility and menu rendering require desktop observation')
                if args.duration_seconds:
                    GLib.timeout_add_seconds(args.duration_seconds, stop, 'duration_elapsed')
                return False

            def setup(icon):
                icon.visible = True
                GLib.idle_add(ready)

            icon.run(setup=setup)
            record('closed')
            return 0
        except Exception as exc:
            record('fatal', error=f'{type(exc).__name__}: {exc}')
            print(f'S2-A prototype failed: {exc}', file=sys.stderr)
            return 1
        finally:
            if icon is not None:
                icon.stop()


if __name__ == '__main__':
    raise SystemExit(main())
