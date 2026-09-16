"""
全局配置。集中管理服务地址、版本号等常量。
支持通过项目根目录 .env 文件或系统环境变量覆盖默认值。
"""
import os
import re
import platform
from pathlib import Path


def _load_dotenv():
    # 从本文件所在目录逐级向上查找最近的 .env
    # (源码 src 布局下项目根在 parents[2]；向上搜索可兼容不同安装/打包布局)
    env_file = None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.exists():
            env_file = candidate
            break
    if env_file is None:
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
        "SMTPLAY_SERVER_HOST", "https://smtplay.cabyss.cn"
    )
    ws_url: str = os.environ.get(
        "SMTPLAY_WS_URL", "wss://smtplay.cabyss.cn/ws"
    )

    device_name: str = (
        os.environ.get("SMTPLAY_DEVICE_NAME")
        or re.sub(r"[^A-Za-z0-9_.\-]", "-", platform.node()).strip("-")
        or "client"
    )

    user: dict = {}
    _ui: bool = False

