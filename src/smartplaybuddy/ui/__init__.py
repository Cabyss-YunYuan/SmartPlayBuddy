"""
UI 模块。提供 PyQt6 桌面界面。
"""
import sys
import ctypes
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
import PyQt6.QtWebEngineWidgets  # noqa: F401 — 必须在 QApplication 之前导入
from ..config import Config
from .main import MainWindow


if sys.platform == "win32":
    appid = f"cabyss.smtplaybuddy.{Config.version}"
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(appid)


window: MainWindow = None

def get_app():
    global window
    app = QApplication(sys.argv)
    window = MainWindow(Config)
    icon_path = Path(__file__).parent / "resources" / "icons" / "logo.ico"
    app.setWindowIcon(QIcon(str(icon_path)))
    return app
