"""wall_api.php 只读客户端：异步、带 TTL 缓存、失败静默降级（返回 None）。

生产环境访客侧由虚拟主机 wall_api.php 直接承担（DB-003）；本模块供本地网关演示与内网 Agent 使用。
"""
import time
import httpx
from src.config import settings

_cache: dict[str, tuple[float, list]] = {}


async def wall_query(qtype: str, thickness: int = 0) -> list[dict] | None:
    """返回 data 列表；接口失败/超时/未启用 → None（调用方回退 RAG，绝不阻断访客）。"""
    if not settings.wall_api_enabled:
        return None

    key = f"{qtype}:{thickness}"
    if key in _cache and time.time() - _cache[key][0] < settings.wall_api_cache_ttl:
        return _cache[key][1]

    params = {"type": qtype}
    if thickness:
        params["thickness"] = str(thickness)

    try:
        async with httpx.AsyncClient(timeout=settings.wall_api_timeout) as c:
            r = await c.get(settings.wall_api_base, params=params)
            j = r.json()
        if not j.get("ok"):
            return None
        _cache[key] = (time.time(), j["data"])
        return j["data"]
    except Exception:
        return None   # WARN 由调用方记日志