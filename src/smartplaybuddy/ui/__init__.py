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
from .capsule import FloatingBall

if sys.platform == "win32":
    appid = f"cabyss.smtplaybuddy.{Config.version}"
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(appid)


window: MainWindow = None
floating_ball: FloatingBall = None
client = None


def get_app():
    global window, floating_ball
    app = QApplication(sys.argv)
    window = MainWindow(Config)
    icon_path = Path(__file__).parent / "resources" / "icons" / "logo.ico"
    app.setWindowIcon(QIcon(str(icon_path)))
    floating_ball = FloatingBall(log_window=window)
    floating_ball.show()
    return app
