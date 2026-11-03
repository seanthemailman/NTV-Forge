"""NTV Forge Windows tray launcher; keeps the web server running after browser closes."""
import os, sys, time, threading, webbrowser, traceback, logging
from pathlib import Path

# For packaged EXE, application assets live in the PyInstaller bundle.
if getattr(sys, 'frozen', False):
    bundle=Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    # The application uses its own directory for project.json; run in a writable folder.
    # In onedir mode, data and assets are beside the executable (see build script).
    os.chdir(Path(sys.executable).resolve().parent)
else:
    os.chdir(Path(__file__).resolve().parent)

import pystray
from PIL import Image, ImageDraw
import server

URL=f'http://localhost:{server.PORT}'
LOG=Path.cwd()/'ntv-forge.log'
# Both HTTP logging and unexpected errors must work without a console.
logging.basicConfig(filename=str(LOG), level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
                    encoding='utf-8')
class _LogStream:
    def __init__(self, level): self.level=level
    def write(self, msg):
        if msg and msg.strip(): logging.log(self.level, msg.rstrip())
    def flush(self): pass
if sys.stdout is None: sys.stdout=_LogStream(logging.INFO)
if sys.stderr is None: sys.stderr=_LogStream(logging.ERROR)

def icon_image():
    path=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'forge-logo-small.png'
    try:return Image.open(path).convert('RGBA').resize((64,64),Image.Resampling.LANCZOS)
    except Exception:return Image.new('RGBA',(64,64),(24,40,44,255))

def main():
    try:
        httpd=server.ThreadingHTTPServer(('0.0.0.0',server.PORT),server.Handler)
    except OSError as exc:
        # No console exists; log startup failures for troubleshooting.
        LOG.write_text(f'Could not start NTV Forge on port {server.PORT}: {exc}\n',encoding='utf-8')
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0,f'NTV Forge could not start on port {server.PORT}.\nClose any other Forge server.\n\n{exc}', 'NTV Forge',0x10)
        except Exception:pass
        return 1
    worker=threading.Thread(target=httpd.serve_forever,daemon=True,name='ntv-http')
    worker.start()
    def open_ui(icon=None,item=None):webbrowser.open(URL)
    def copy_playlist(icon=None,item=None):
        try:
            import tkinter as tk
            root=tk.Tk();root.withdraw();root.clipboard_clear()
            root.clipboard_append(f'http://{server.lan_ip()}:{server.PORT}/channels.m3u')
            root.update();root.destroy()
        except Exception as exc:LOG.write_text(f'Clipboard error: {exc}\n',encoding='utf-8')
    def exit_app(icon,item):
        icon.stop()
        httpd.shutdown()
        httpd.server_close()
    icon=pystray.Icon('ntv-forge',icon_image(),'NTV Forge 1.3.2',menu=pystray.Menu(
        pystray.MenuItem('Open NTV Forge',open_ui,default=True),
        pystray.MenuItem('Copy M3U Playlist URL',copy_playlist),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Exit NTV Forge',exit_app)))
    threading.Timer(0.7,open_ui).start()
    try:icon.run()
    finally:
        httpd.shutdown();httpd.server_close()
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except Exception:
        LOG.write_text(traceback.format_exc(),encoding='utf-8')
        raise
