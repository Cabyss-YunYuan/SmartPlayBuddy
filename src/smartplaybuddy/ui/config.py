"""
UI 层配置。导入后为全局 Config 追加 UI 专用字段。
"""
import os
from urllib.parse import urlsplit
from ..config import Config

# Web 应用地址：可用 SMTPLAY_WEB_URL 覆盖。
# 这是内嵌窗口实际加载地址的唯一来源，也是本地桥 Origin 白名单的默认值。
Config.web_url = os.environ.get(
    "SMTPLAY_WEB_URL", "https://smtplay.cabyss.cn"
)


def _origin_of(url: str) -> str:
    """从完整 URL 提取 Origin(scheme://host:port)，去掉路径/查询，
    以便与浏览器发送的 Origin 头精确匹配。"""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url.rstrip("/")
    return f"{parts.scheme}://{parts.netloc}"


# 本地桥 Origin 白名单：web_url 的 origin(内嵌页面来源)始终放行，再用
# SMTPLAY_BRIDGE_ORIGINS 追加额外来源(逗号分隔，支持 * 通配，如 http://localhost:*)。
# 去重且保持顺序。
_extra_origins = [
    o.strip()
    for o in os.environ.get("SMTPLAY_BRIDGE_ORIGINS", "").split(",")
    if o.strip()
]
Config.bridge_origins = list(dict.fromkeys([_origin_of(Config.web_url), *_extra_origins]))
