from .. import i18n
from .. import log
from ..config import SERVER_HOST

import socket
import base64
import hashlib
import secrets
import webbrowser
import http.server
import urllib.parse
import urllib.request
import json
import keyring
import time
from dataclasses import dataclass, asdict


logger = log.logger.getChild("User").getChild("Login")

SERVICE_NAME = "SmartPlayBuddy"
ACCOUNT_NAME = "UserTokens"

#: access token 剩余有效期低于该值(秒)就提前刷新，避免握手中途过期
TOKEN_REFRESH_MARGIN = 60
#: 等待浏览器回调的上限(秒)，防止重连线程被无限期挂住
LOGIN_CALLBACK_TIMEOUT = 300

#: 服务端 HttpOnly cookie 名，必须与 common/authtoken.go 的 CookieName / RefreshCookieName 保持一致
ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_in: int


def save_tokens(tokens: Tokens):
    credential = json.dumps(asdict(tokens))
    keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, credential)
    logger.debug(i18n.translate("user.login.tokens_saved"))


def clear_tokens():
    try:
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
        logger.debug(i18n.translate("user.login.tokens_cleared"))
    except Exception as e:
        logger.warning(i18n.translate("user.login.load_tokens_failed", error=str(e)))


def access_token_ttl(access_token: str) -> float | None:
    """解析 JWT 的 exp 声明，返回 access token 剩余有效期(秒)；解析失败返回 None。"""
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except Exception:
        return None
    if not exp:
        return None
    return float(exp) - time.time()


def _extract_set_cookies(resp) -> dict[str, str]:
    """从响应的 Set-Cookie 头解析出 {cookie名: 值}。

    服务端全站改用 HttpOnly cookie 承载令牌、body 不再返回 token，
    原生客户端只能自行从 Set-Cookie 里取出 access/refresh token 的值。
    """
    cookies: dict[str, str] = {}
    for header in resp.headers.get_all("Set-Cookie") or []:
        name, _, value = header.split(";", 1)[0].partition("=")
        name = name.strip()
        if name:
            cookies[name] = value.strip()
    return cookies


def _generate_handoff_pair() -> tuple[str, str]:
    """生成 PKCE 风格的 handoff (verifier, challenge)。

    challenge = BASE64URL(SHA256(verifier)) 且无填充，必须与服务端 auth/pkce.go 的
    s256Challenge 逐字节一致：/authorize 传 challenge、/token 传 verifier，
    服务端据此确保一次性 code 只能被当初发起登录的这个客户端兑换。
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _exchange_handoff_code(code: str, verifier: str) -> Tokens:
    """用登录回调拿到的一次性 handoff code 到 /token 换取令牌(令牌仅经 Set-Cookie 下发)。

    必须同时回传发起 /authorize 时生成的 verifier：服务端校验 S256(verifier)==challenge，
    不匹配或 code 已被消费(GetDel)都会返回 401。
    """
    req = urllib.request.Request(
        f"{SERVER_HOST}/api/user/auth/token",
        data=json.dumps({"code": code, "verifier": verifier}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req)
    body = json.loads(resp.read())
    cookies = _extract_set_cookies(resp)
    access_token = cookies.get(ACCESS_COOKIE_NAME)
    if not access_token:
        raise RuntimeError("token exchange returned no access_token cookie")
    return Tokens(
        access_token=access_token,
        refresh_token=cookies.get(REFRESH_COOKIE_NAME, ""),
        expires_in=int(body.get("expiresIn", 0)),
    )


def refresh_login(tokens: Tokens | None = None) -> Tokens | None:
    tokens = tokens or _load_tokens()
    if tokens is None or not tokens.refresh_token:
        return None
    try:
        # refresh token 只经 HttpOnly cookie 传递，请求体不再携带；
        # 新令牌同样只从 Set-Cookie 取，body 仅返回 expiresIn。
        req = urllib.request.Request(
            f"{SERVER_HOST}/api/user/auth/refresh",
            data=b"",
            headers={"Cookie": f"{REFRESH_COOKIE_NAME}={tokens.refresh_token}"},
            method="POST",
        )
        resp = urllib.request.urlopen(req)
        body = json.loads(resp.read())
        cookies = _extract_set_cookies(resp)
        new_tokens = Tokens(
            access_token=cookies.get(ACCESS_COOKIE_NAME, tokens.access_token),
            refresh_token=cookies.get(REFRESH_COOKIE_NAME, tokens.refresh_token),
            expires_in=int(body.get("expiresIn", 0)),
        )
        # refresh token 是轮转的：新的必须立刻落盘，否则下次刷新会拿旧的去换而吃 400
        save_tokens(new_tokens)
        logger.info(i18n.translate("user.login.auto_login_success", expires_in=new_tokens.expires_in))
        return new_tokens
    except Exception as e:
        logger.warning(i18n.translate("user.login.auto_login_failed", error=str(e)))
        return None


def _browser_login() -> Tokens:
    tokens = login()
    save_tokens(tokens)
    return tokens


def ensure_tokens(tokens: Tokens | None = None, force_login: bool = False) -> Tokens:
    """返回可用的令牌：仍然有效则复用，过期则刷新，刷新失败或 force_login 则浏览器登录。

    force_login 用于 access token 被服务端吊销(WebSocket 关闭码 4001)的场景：
    此时 refresh token 通常已一并吊销，再刷新只会白吃一次 400。
    """
    if force_login:
        clear_tokens()
        return _browser_login()

    if tokens is None:
        tokens = _load_tokens()

    if tokens is not None and tokens.access_token:
        ttl = access_token_ttl(tokens.access_token)
        if ttl is None or ttl > TOKEN_REFRESH_MARGIN:
            return tokens

    refreshed = refresh_login(tokens)
    if refreshed is not None:
        return refreshed

    clear_tokens()
    return _browser_login()


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


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        port = s.getsockname()[1]
    return port


def _is_port_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def login() -> Tokens:
    handoff_code: str | None = None

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal handoff_code
            # 服务端回调只挂一次性 handoff code，真 token 不再出现在 URL 上
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            handoff_code = params.get("code", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if handoff_code:
                self.wfile.write(b"<h1>Login successful! You can close this tab.</h1>")
            else:
                self.wfile.write(b"<h1>Login failed: missing code.</h1>")

        def log_message(self, format, *args):
            pass

    port = _find_free_port()
    if not _is_port_available(port):
        raise RuntimeError(i18n.translate("user.login.no_free_port"))

    server = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
    # 轮询式等待：浏览器可能先请求 /favicon.ico 等噪音，需忽略后继续等真正的回调
    server.timeout = 5

    frontend_url = urllib.parse.quote(f"http://localhost:{port}", safe="")
    verifier, challenge = _generate_handoff_pair()
    resp = urllib.request.urlopen(
        f"{SERVER_HOST}/api/user/auth/authorize?redirectUrl={frontend_url}&handoffChallenge={challenge}"
    )
    iam_url = json.loads(resp.read())["url"]

    logger.info(i18n.translate("user.login.opening_browser"))
    logger.info(i18n.translate("user.login.manual_login_hint", url=iam_url))
    webbrowser.open(iam_url)

    deadline = time.monotonic() + LOGIN_CALLBACK_TIMEOUT
    while handoff_code is None and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()

    if not handoff_code:
        raise RuntimeError("Login failed")

    # 拿一次性 code 走反向通道换取令牌(服务端经 Set-Cookie 下发)
    result = _exchange_handoff_code(handoff_code, verifier)

    logger.info(i18n.translate("user.login.login_success", expires_in=result.expires_in))
    return result
