"""
UI 层配置。导入后为全局 Config 追加 UI 专用字段。
"""
import os
from ..config import Config

# Web 应用地址：默认由 server_host 推导(:8000 → :8080)，可用 SMTPLAY_WEB_URL 覆盖。
# 这是内嵌窗口实际加载地址的唯一来源，也是本地桥 Origin 白名单的默认值。
Config.web_url = os.environ.get(
    "SMTPLAY_WEB_URL", "http://smtplay.cabyss.cn:8080"
)

# 本地桥 Origin 白名单：web_url(内嵌页面来源)始终放行，再用 SMTPLAY_BRIDGE_ORIGINS
# 追加额外来源(逗号分隔，支持 * 通配，如 http://localhost:*)。去重且保持顺序。
_extra_origins = [
    o.strip()
    for o in os.environ.get("SMTPLAY_BRIDGE_ORIGINS", "").split(",")
    if o.strip()
]
Config.bridge_origins = list(dict.fromkeys([Config.web_url, *_extra_origins]))
