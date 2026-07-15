"""共享 Redis 连接池 —— 全进程复用，避免每个 Store 各自创建独立连接池。

所有 redis.Redis() 客户端应通过此模块获取连接池，而非直接实例化。
"""

from __future__ import annotations

import threading

import redis

from config.settings import get_settings

_pool: redis.ConnectionPool | None = None
_pool_lock = threading.Lock()

# 连接池上限：覆盖每个请求 2-3 个客户端 + PubSub + worker 消费
_MAX_CONNECTIONS = 80


def get_pool() -> redis.ConnectionPool:
    """返回进程级共享 Redis 连接池（线程安全）。"""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                cfg = get_settings()
                _pool = redis.ConnectionPool(
                    host=cfg.redis_host,
                    port=cfg.redis_port,
                    db=cfg.redis_db,
                    password=cfg.redis_password or None,
                    decode_responses=True,
                    max_connections=_MAX_CONNECTIONS,
                    socket_connect_timeout=5,
                    socket_timeout=10,
                    health_check_interval=30,
                )
    return _pool


def make_client(**overrides) -> redis.Redis:
    """用共享连接池创建一个 Redis 客户端。

    overrides 可覆盖 decode_responses 等默认参数（例如 lightrag 需要 decode_responses=False）。
    """
    kwargs = {"connection_pool": get_pool()}
    kwargs.update(overrides)
    return redis.Redis(**kwargs)


def reset_pool() -> None:
    """测试用：重置连接池。"""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.disconnect()
            except Exception:
                pass
            _pool = None
