"""
路由标识工具。
路由标识格式: {type}:{userId}:{deviceName}，
例如 client:1001:desktop-a。
"""


def parse(route: str) -> tuple[str, str, str] | None:
    """解析路由标识，返回 (type, userId, deviceName)，非法格式返回 None。"""
    if not isinstance(route, str):
        return None
    parts = route.split(":")
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def same_account(route_a: str, route_b: str) -> bool:
    """判断两个路由标识是否属于同一账号（比较 userId 段）。"""
    parsed_a = parse(route_a)
    parsed_b = parse(route_b)
    if parsed_a is None or parsed_b is None:
        return False
    return parsed_a[1] == parsed_b[1]
