"""
全局配置。集中管理服务地址、版本号等常量。
支持通过项目根目录 .env 文件或系统环境变量覆盖默认值。
"""
import os
import re
import platform
from pathlib import Path

VERSION = "v0.0.1"

SERVER_HOST = "http://smtplay.cabyss.cn:8000"
WS_URL = "ws://smtplay.cabyss.cn:2508/ws"


def _load_dotenv():
    env_file = Path(__file__).resolve().parents[3] / ".env"
    if not env_file.exists():
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            os.environ.setdefault(key, value)


_load_dotenv()

SERVER_HOST = os.environ.get("SMTPLAY_SERVER_HOST", SERVER_HOST)
WS_URL = os.environ.get("SMTPLAY_WS_URL", WS_URL)

# 设备名是服务端路由地址 {type}:{uid}:{deviceName} 与 Redis 在线键的组成部分。
# 留空会被 claimlogic.go 替换成随机 UUID：每次重启换身份，且 claim 无回执，
# 客户端自己也无从得知地址。因此这里给出一个稳定且可读的默认值。
DEVICE_NAME = (
    os.environ.get("SMTPLAY_DEVICE_NAME")
    or re.sub(r"[^A-Za-z0-9_.\-]", "-", platform.node()).strip("-")
    or "client"
)
