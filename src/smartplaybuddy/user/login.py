from .. import i18n
from .. import logger
from ..config import Config

import socket
import base64
import hashlib
import secrets
import webbrowser
import asyncio
import multiprocessing
import sys
import urllib.parse
import urllib.request
import json
import keyring
import time
from dataclasses import dataclass, asdict

logger = logger.logger.getChild("User").getChild("Login")

SERVICE_NAME = "SmartPlayBuddy"
ACCOUNT_NAME = "UserTokens"

#: access token 剩余有效期低于该值(秒)就提前刷新，避免握手中途过期
TOKEN_REFRESH_MARGIN = 60
#: 等待浏览器回调的上限(秒)，防止重连线程被无限期挂住
LOGIN_CALLBACK_TIMEOUT = 120

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


def decode_jwt_payload(access_token: str) -> dict:
    """解析 JWT 的 payload 部分，返回完整 claims dict；解析失败返回空 dict。"""
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


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
        f"{Config.server_host}/api/user/auth/token",
        data=json.dumps({"code": code, "verifier": verifier}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req)
    body = json.loads(resp.read())
    cookies = _extract_set_cookies(resp)
    access_token = cookies.get(ACCESS_COOKIE_NAME)
    if not access_token:
        raise RuntimeError(i18n.translate("user.login.token_exchange_failed"))

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
        req = urllib.request.Request(
            f"{Config.server_host}/api/user/auth/refresh",
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

_login_proc: multiprocessing.Process | None = None

async def _do_login() -> Tokens:
    """交互式登录：无 UI 时走系统浏览器子进程。有 UI 时由 Web 应用处理，不自动弹窗。"""
    if Config._ui:
        raise RuntimeError(i18n.translate("user.login.ui_mode_no_auto_login"))

    return await _do_login_subprocess()


async def _do_login_subprocess() -> Tokens:
    """系统浏览器登录：在子进程中运行，不阻塞主进程事件循环。"""
    global _login_proc
    parent_conn, child_conn = multiprocessing.Pipe()
    proc = multiprocessing.Process(
        target=_login_subprocess,
        args=(Config.server_host, child_conn),
        daemon=True,
    )
    _login_proc = proc
    proc.start()
    child_conn.close()

    try:
        while proc.is_alive():
            await asyncio.sleep(0.2)

        if parent_conn.poll():
            result = parent_conn.recv()
        else:
            raise RuntimeError(i18n.translate("user.login.process_crashed"))
    except asyncio.CancelledError:
        raise
    except EOFError:
        raise RuntimeError(i18n.translate("user.login.process_crashed"))
    finally:
        parent_conn.close()
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=3)
        _login_proc = None

    if isinstance(result, dict) and result.get("ok"):
        tokens = result["tokens"]
        save_tokens(tokens)
        return tokens
    error_msg = result.get("error", i18n.translate("user.login.unknown_error")) if isinstance(result, dict) else str(result)
    raise RuntimeError(error_msg)


def _login_subprocess(server_host, pipe):
    """子进程入口：系统浏览器登录，通过 pipe 返回 Tokens。"""
    try:
        tokens = login_in_subprocess(server_host)
        pipe.send({"ok": True, "tokens": tokens})
    except Exception as e:
        pipe.send({"ok": False, "error": str(e)})


def login_in_subprocess(server_host: str) -> Tokens:
    """系统浏览器登录，在子进程中同步执行。"""
    handoff_code_holder = [None]
    loop = None

    async def handle_callback(reader, writer):
        try:
            request_line = await reader.readline()
            path = request_line.decode("utf-8", errors="ignore").split(" ")[1] if request_line else "/"
            params = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
            code = params.get("code", [None])[0]

            if code and handoff_code_holder[0] is None:
                handoff_code_holder[0] = code
                body = f"<h1>{i18n.translate('user.login.browser_success')}</h1>".encode()
            else:
                body = f"<h1>{i18n.translate('user.login.browser_failed')}</h1>".encode()

            writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode())
            writer.write(body)
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _run():
        nonlocal loop
        loop = asyncio.get_running_loop()
        port = _find_free_port()
        if not _is_port_available(port):
            raise RuntimeError(i18n.translate("user.login.no_free_port"))

        server = await asyncio.start_server(handle_callback, "127.0.0.1", port)
        frontend_url = urllib.parse.quote(f"http://localhost:{port}", safe="")
        verifier, challenge = _generate_handoff_pair()
        resp = urllib.request.urlopen(
            f"{server_host}/api/user/auth/authorize?redirectUrl={frontend_url}&handoffChallenge={challenge}"
        )
        iam_url = json.loads(resp.read())["url"]
        logger.info(i18n.translate("user.login.login_url", url=iam_url))
        webbrowser.open(iam_url)

        def _wait_for_code():
            while handoff_code_holder[0] is None:
                time.sleep(0.5)
            return handoff_code_holder[0]

        try:
            handoff_code = await asyncio.wait_for(
                loop.run_in_executor(None, _wait_for_code),
                timeout=LOGIN_CALLBACK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            server.close()
            raise RuntimeError(i18n.translate("user.login.login_timeout"))
        finally:
            server.close()
        return _exchange_handoff_code(handoff_code, verifier)

    return asyncio.run(_run())


async def _browser_login() -> Tokens:
    return await _do_login()


async def ensure_tokens(tokens: Tokens | None = None, force_login: bool = False) -> Tokens:
    """返回可用的令牌：仍然有效则复用，过期则刷新，刷新失败或 force_login 则登录。"""
    if force_login:
        clear_tokens()
        tokens = await _browser_login()
        Config.user = decode_jwt_payload(tokens.access_token)
        return tokens

    if tokens is None:
        tokens = _load_tokens()

    if tokens is not None and tokens.access_token:
        ttl = access_token_ttl(tokens.access_token)
        if ttl is None or ttl > TOKEN_REFRESH_MARGIN:
            Config.user = decode_jwt_payload(tokens.access_token)
            return tokens

    refreshed = refresh_login(tokens)
    if refreshed is not None:
        Config.user = decode_jwt_payload(refreshed.access_token)
        return refreshed

    clear_tokens()
    tokens = await _browser_login()
    Config.user = decode_jwt_payload(tokens.access_token)
    return tokens


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
