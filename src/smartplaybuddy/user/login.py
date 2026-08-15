from .. import i18n
from .. import log
from ..config import SERVER_HOST, LOCAL_PORT

import webbrowser
import http.server
import urllib.parse
import urllib.request
import json
import keyring
from dataclasses import dataclass, asdict


logger = log.logger.getChild("User").getChild("Login")

SERVICE_NAME = "SmartPlayBuddy"
ACCOUNT_NAME = "UserTokens"

@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_in: int


def save_tokens(tokens: Tokens):
    credential = json.dumps(asdict(tokens))
    keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, credential)
    logger.debug(i18n.translate("user.login.tokens_saved"))


def _load_tokens() -> Tokens | None:
    try:
        credential = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
        if credential is None:
            return None
        data = json.loads(credential)
        return Tokens(**data)
    except Exception as e:
        logger.warning(i18n.translate("user.login.load_tokens_failed", error=str(e)))
        return None


def refresh_login() -> Tokens | None:
    tokens = _load_tokens()
    if tokens is None or not tokens.refresh_token:
        return None
    try:
        req = urllib.request.Request(
            f"{SERVER_HOST}/api/user/auth/refresh",
            data=json.dumps({"refreshToken": tokens.refresh_token}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        new_tokens = Tokens(
            access_token=data["accessToken"],
            refresh_token=data.get("refreshToken", tokens.refresh_token),
            expires_in=data.get("expiresIn", 0),
        )
        logger.info(i18n.translate("user.login.auto_login_success", expires_in=new_tokens.expires_in))
        return new_tokens
    except Exception as e:
        logger.warning(i18n.translate("user.login.auto_login_failed", error=str(e)))
        return None


def login() -> Tokens:
    result: Tokens | None = None

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal result
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            access_token = params.get("accessToken", [None])[0]
            refresh_token = params.get("refreshToken", [None])[0]
            expires_in = int(params.get("expiresIn", ["0"])[0])

            if access_token:
                result = Tokens(access_token, refresh_token, expires_in)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h1>Login successful! You can close this tab.</h1>")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing token")

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", LOCAL_PORT), CallbackHandler)

    frontend_url = urllib.parse.quote(f"http://localhost:{LOCAL_PORT}", safe="")
    resp = urllib.request.urlopen(
        f"{SERVER_HOST}/api/user/auth/authorize?redirectUrl={frontend_url}"
    )
    iam_url = json.loads(resp.read())["url"]

    logger.info(i18n.translate("user.login.opening_browser"))
    logger.info(i18n.translate("user.login.manual_login_hint", url=iam_url))
    webbrowser.open(iam_url)

    server.handle_request()
    server.server_close()

    if result is None:
        raise RuntimeError("Login failed")

    logger.info(i18n.translate("user.login.login_success", expires_in=result.expires_in))
    return result
