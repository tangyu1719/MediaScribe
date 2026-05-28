"""Milvus 连通性探测（懒加载 pymilvus，避免未安装时阻塞应用启动）。"""
from __future__ import annotations
import os
import time
from typing import Any, Dict


def check_milvus(host: str | None = None, port: str | None = None) -> Dict[str, Any]:
    host = host or os.environ.get("MILVUS_HOST", "127.0.0.1")
    port = port or os.environ.get("MILVUS_PORT", "19530")
    t0 = time.perf_counter()
    alias = "sba_health_probe"
    try:
        from pymilvus import connections, utility

        if connections.has_connection(alias):
            connections.disconnect(alias)
        connections.connect(alias=alias, host=host, port=str(port), timeout=5)
        ver = utility.get_server_version(using=alias)
        connections.disconnect(alias)
        return {
            "milvus_ok": True,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "version": ver,
            "host": host,
            "port": port,
            "error": None,
        }
    except Exception as e:
        try:
            from pymilvus import connections

            connections.disconnect(alias)
        except Exception:
            pass
        return {
            "milvus_ok": False,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "version": None,
            "host": host,
            "port": port,
            "error": str(e),
        }
