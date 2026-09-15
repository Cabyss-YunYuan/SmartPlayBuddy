"""
登录对话框：内嵌 QWebEngineView 完成 OAuth 登录，
通过拦截 QWebEngineCookieStore 的 cookie 写入来判断登录成功。
"""
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtNetwork import QNetworkCookie

from .. import i18n, logger
from ..user.login import (
    Tokens, save_tokens,
    ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME,
)

import urllib.request
import urllib.parse
import json
import base64
import time

logger = logger.logger.getChild("User").getChild("LoginDialog")

LOGIN_WINDOW_SIZE = (480, 640)
COOKIE_TIMEOUT = 300


class LoginDialog(QDialog):
    def __init__(self, server_host: str, profile: QWebEngineProfile, parent=None):
        super().__init__(parent)
        self._server_host = server_host
        self.tokens: Tokens | None = None
        self._found_access = ""
        self._found_refresh = ""

        self.setWindowTitle(i18n.translate("ui.login.title"))
        self.resize(*LOGIN_WINDOW_SIZE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        profile.cookieStore().cookieAdded.connect(self._on_cookie_added)

        self._web_view = QWebEngineView(self)
        self._web_view.setPage(QWebEnginePage(profile, self._web_view))
        layout.addWidget(self._web_view)

        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)

        self._start_login()

    def _start_login(self):
        try:
            redirect_url = urllib.parse.quote("http://localhost/done", safe="")
            resp = urllib.request.urlopen(
                f"{self._server_host}/api/user/auth/authorize?redirectUrl={redirect_url}"
            )
            iam_url = json.loads(resp.read())["url"]
            logger.info(i18n.translate("user.login.opening_login"))
            self._web_view.load(QUrl(iam_url))
        except Exception as e:
            logger.error(i18n.translate("user.login.start_failed", error=e))
            return

        self._timeout_timer.start(COOKIE_TIMEOUT * 1000)

    def _on_cookie_added(self, cookie: QNetworkCookie):
        name = bytes(cookie.name()).decode("utf-8", errors="ignore")
        if name == ACCESS_COOKIE_NAME:
            self._found_access = bytes(cookie.value()).decode("utf-8")
        elif name == REFRESH_COOKIE_NAME:
            self._found_refresh = bytes(cookie.value()).decode("utf-8")

        if self._found_access:
            self._timeout_timer.stop()
            expires_in = self._parse_expires(self._found_access)
            self.tokens = Tokens(
                access_token=self._found_access,
                refresh_token=self._found_refresh,
                expires_in=expires_in,
            )
            save_tokens(self.tokens)
            logger.info(i18n.translate("user.login.login_success", expires_in=expires_in))
            self.accept()

    @staticmethod
    def _parse_expires(access_token: str) -> int:
        try:
            payload = access_token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
            if exp:
                return max(0, int(float(exp) - time.time()))
        except Exception:
            pass
        return 0

    def _on_timeout(self):
        logger.warning(i18n.translate("user.login.dialog_timeout"))
        self.reject()


def gui_login(server_host: str) -> Tokens:
    profile = QWebEngineProfile()
    dialog = LoginDialog(server_host, profile)
    try:
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted and dialog.tokens:
            return dialog.tokens
        raise RuntimeError(i18n.translate("user.login.cancelled_or_failed"))
    finally:
        dialog._web_view.setPage(None)
        profile.deleteLater()
