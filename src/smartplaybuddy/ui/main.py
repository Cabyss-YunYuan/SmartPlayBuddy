"""
主窗口模块。
内嵌 Web 应用作为主界面，通过 cookie 拦截同步令牌到 keyring。
keyring 为唯一令牌源，cookie 仅作为传输层。
认证状态变化通过 auth_changed 信号通知外部。
"""
import time
from PyQt6.QtWidgets import QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineScript
from PyQt6.QtCore import QUrl, QTimer, QDateTime, pyqtSignal
from PyQt6.QtNetwork import QNetworkCookie
from .config import Config
from ..user.login import (
    Tokens, save_tokens, clear_tokens, _load_tokens,
    access_token_ttl, decode_jwt_payload,
    ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME, TOKEN_REFRESH_MARGIN,
)


class MainWindow(QMainWindow):
    auth_changed = pyqtSignal(bool)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setWindowTitle(config.version)

        self._profile = QWebEngineProfile(self)
        self._profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self._profile.cookieStore().cookieAdded.connect(self._on_cookie)
        self._profile.cookieStore().cookieRemoved.connect(self._on_cookie_removed)

        self._web_view = QWebEngineView(self)
        self._web_view.setPage(QWebEnginePage(self._profile, self._web_view))
        self.setCentralWidget(self._web_view)
        self.resize(1280, 800)

        self._access_token = ""
        self._refresh_token = ""
        self._last_saved_token = ""
        self._last_cookie_activity = 0.0
        self._authenticated = False

        self._web_view.loadFinished.connect(self._on_load_finished)
        QTimer.singleShot(0, self._init_auth)

        self._logout_timer = QTimer(self)
        self._logout_timer.setInterval(2000)
        self._logout_timer.timeout.connect(self._check_logout)
        self._logout_timer.start()

    def _init_auth(self):
        """从 keyring 加载令牌：有效则注入 cookie，无效则加载 Web 应用并弹出登录对话框。"""
        tokens = _load_tokens()
        web_url = self.config.server_host.replace(":8000", ":8080")
        if tokens and tokens.access_token:
            ttl = access_token_ttl(tokens.access_token)
            if ttl is not None and ttl > TOKEN_REFRESH_MARGIN:
                Config.user = decode_jwt_payload(tokens.access_token)
                self._inject_cookies(tokens)
                self._set_authenticated(True)
                return
        self._web_view.load(QUrl(web_url))
        self.show()
        QTimer.singleShot(300, self._show_login_dialog)

    def _show_login_dialog(self):
        """弹出登录对话框，成功后刷新主窗口；取消则保留当前状态。"""
        from .login import gui_login
        try:
            tokens = gui_login(self.config.server_host)
        except RuntimeError:
            return
        Config.user = decode_jwt_payload(tokens.access_token)
        self._inject_cookies(tokens)
        self._set_authenticated(True)

    def _inject_cookies(self, tokens: Tokens):
        """从 keyring 读取令牌，注入 cookie 和 localStorage 到 Web 视图。"""
        target_url = QUrl(self.config.server_host.replace(":8000", ":8080"))
        store = self._profile.cookieStore()

        self._last_saved_token = tokens.access_token
        self._last_cookie_activity = time.monotonic()

        for name, value, path in [
            (ACCESS_COOKIE_NAME, tokens.access_token, "/"),
            (REFRESH_COOKIE_NAME, tokens.refresh_token, "/api/user/auth"),
        ]:
            if not value:
                continue
            cookie = QNetworkCookie(name.encode(), value.encode())
            cookie.setPath(path)
            cookie.setHttpOnly(True)
            cookie.setExpirationDate(QDateTime.currentDateTime().addSecs(86400))
            store.setCookie(cookie, target_url)

        js = QWebEngineScript()
        js.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        js.setWorldId(0)
        js.setName("__smtplay_auth")
        js.setSourceCode(
            'localStorage.setItem("smtplay_authenticated", "1");'
            'localStorage.setItem("smtplay_token_expires_at", String(Date.now() + 86400000));'
        )
        self._profile.scripts().insert(js)

        self._web_view.load(target_url)

    def _on_load_finished(self, ok):
        """页面加载完成后清理注入脚本。"""
        if not ok:
            return
        if not self.isVisible():
            self.show()
        for s in self._profile.scripts().find("__smtplay_auth"):
            self._profile.scripts().remove(s)

    def _on_cookie_removed(self, cookie: QNetworkCookie):
        """cookie 被删除时，若距上次 cookie 活动不足 2 秒则视为启动注入，跳过。"""
        name = bytes(cookie.name()).decode("utf-8", errors="ignore")
        if name == ACCESS_COOKIE_NAME:
            if time.monotonic() - self._last_cookie_activity < 2.0:
                return
            clear_tokens()
            self._set_authenticated(False)

    def _on_cookie(self, cookie: QNetworkCookie):
        """拦截服务端 cookie，同步写入 keyring。"""
        name = bytes(cookie.name()).decode("utf-8", errors="ignore")
        value = bytes(cookie.value()).decode("utf-8")

        if name == ACCESS_COOKIE_NAME and not value:
            clear_tokens()
            self._set_authenticated(False)
            return

        if name == ACCESS_COOKIE_NAME:
            self._access_token = value
        elif name == REFRESH_COOKIE_NAME:
            self._refresh_token = value
        else:
            return

        self._last_cookie_activity = time.monotonic()
        QTimer.singleShot(200, self._flush_tokens)

    def _flush_tokens(self):
        """合并写入 keyring（去重：令牌未变化时跳过）。"""
        if not self._access_token:
            return
        if self._access_token == self._last_saved_token:
            return
        tokens = Tokens(
            access_token=self._access_token,
            refresh_token=self._refresh_token,
            expires_in=0,
        )
        save_tokens(tokens)
        self._last_saved_token = self._access_token
        Config.user = decode_jwt_payload(tokens.access_token)
        self._set_authenticated(True)

    def _check_logout(self):
        """轮询 localStorage 检测退出登录（后备机制）。"""
        if not self._authenticated:
            return
        self._web_view.page().runJavaScript(
            "localStorage.getItem('smtplay_authenticated')",
            self._on_logout_check
        )

    def _on_logout_check(self, result):
        if result is None and self._authenticated:
            clear_tokens()
            self._set_authenticated(False)

    def _set_authenticated(self, value: bool):
        if self._authenticated == value:
            return
        self._authenticated = value
        self.auth_changed.emit(value)

    def on_close(self):
        self._logout_timer.stop()
        page = self._web_view.page()
        self._web_view.setPage(None)
        if page:
            page.deleteLater()
        self._profile.deleteLater()
