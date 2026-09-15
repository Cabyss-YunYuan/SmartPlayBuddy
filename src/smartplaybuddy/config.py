"""
全局配置。集中管理服务地址、版本号等常量。
支持通过项目根目录 .env 文件或系统环境变量覆盖默认值。
"""
import os
import re
import platform
from pathlib import Path


def _load_dotenv():
    env_file = Path(__file__).resolve().parents[3] / ".env"
    if not env_file.exists():
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            os.environ.setdefault(key, value)


class Config:
    """应用全局配置。"""

    _load_dotenv()

    version: str = "v0.0.1"

    server_host: str = os.environ.get(
        "SMTPLAY_SERVER_HOST", "http://smtplay.cabyss.cn:8000"
    )
    ws_url: str = os.environ.get(
        "SMTPLAY_WS_URL", "ws://smtplay.cabyss.cn:2508/ws"
    )

    device_name: str = (
        os.environ.get("SMTPLAY_DEVICE_NAME")
        or re.sub(r"[^A-Za-z0-9_.\-]", "-", platform.node()).strip("-")
        or "client"
    )

    user: dict = {}
    _ui: bool = False

