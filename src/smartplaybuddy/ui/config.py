"""
UI 层配置。导入后为全局 Config 追加 UI 专用字段。
"""
import os
from ..config import Config

Config.web_url = os.environ.get(
    "SMTPLAY_WEB_URL", "http://smtplay.cabyss.cn:8080"
)
