"""Milvus 轻量查询：不加载 BGE，仅用于 RAG 管理页统计与切片预览。"""
from __future__ import annotations

import logging
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("sba.milvus_rag_query")

_ALIAS = "sba_rag_light_query"
_CACHE_TTL_SEC = 45.0
_FETCH_TIMEOUT_SEC = 12.0
_FETCH_TIMEOUT_FORCE_SEC = 90.0
_cache: Dict[str, Any] = {"ts": 0.0, "data": None}


def _norm_path(p: str) -> str:
    return os.path.normcase(os.path.normpath(str(p or "").strip()))


def _milvus_cfg() -> Tuple[str, str, str]:
    host = os.environ.get("MILVUS_HOST", "127.0.0.1")
    port = str(os.environ.get("MILVUS_PORT", "19530"))
    coll = os.environ.get("MILVUS_COLLECTION", "kb_chunks_fast")
    return host, port, coll


def _disconnect(alias: str) -> None:
    try:
        from pymilvus import connections

        if connections.has_connection(alias):
            connections.disconnect(alias)
    except Exception:
        pass


def fetch_milvus_rag_snapshot(*, force: bool = False) -> Optional[Dict[str, Any]]:
    """
    返回 Milvus 侧真实切片统计（与老 GUI 一致：file_records.chunk_count 常不准）。
    结构: {ok, total_chunks, per_file_norm: {normpath: count}, sample_meta: {normpath: {domain,...}}}
    """
    global _cache
    now = time.time()
    if not force and _cache.get("data") and (now - float(_cache.get("ts") or 0)) < _CACHE_TTL_SEC:
        return _cache["data"]

    timeout = _FETCH_TIMEOUT_FORCE_SEC if force else _FETCH_TIMEOUT_SEC
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_fetch_milvus_rag_snapshot_impl)
            data = fut.result(timeout=timeout)
    except FuturesTimeout:
        _log.warning(
            "[RAG-知识库|milvus_rag_query.fetch_milvus_rag_snapshot|milvus|硬编执行|快照] 超时; timeout_sec=%s",
            _FETCH_TIMEOUT_SEC,
        )
        return None
    if data:
        _cache = {"ts": now, "data": data}
    return data


def _fetch_milvus_rag_snapshot_impl() -> Optional[Dict[str, Any]]:
    host, port, coll_name = _milvus_cfg()
    t0 = time.perf_counter()
    try:
        from pymilvus import Collection, connections, utility
    except ImportError:
        return None

    _disconnect(_ALIAS)
    try:
        connections.connect(alias=_ALIAS, host=host, port=port, timeout=8)
        if not utility.has_collection(coll_name, using=_ALIAS):
            return None
        coll = Collection(coll_name, using=_ALIAS)
        try:
            coll.load(timeout=8)
        except TypeError:
            coll.load()
        except Exception as load_ex:
            _log.warning(
                "[RAG-知识库|milvus_rag_query._fetch_milvus_rag_snapshot_impl|collection.load|硬编执行|加载] 失败; error=%s",
                load_ex,
            )
        total = 0
        try:
            cnt_rows = coll.query(expr="pk >= 0", output_fields=["count(*)"])
            if cnt_rows:
                v = cnt_rows[0].get("count(*)")
                if isinstance(v, int):
                    total = v
        except Exception:
            pass

        per_file: Counter[str] = Counter()
        sample_meta: Dict[str, Dict[str, str]] = {}
        fields = ["source_file", "domain", "module", "doc_type", "keyword1", "keyword2"]
        offset = 0
        page = 2000
        while True:
            try:
                rows = coll.query(
                    expr="pk >= 0",
                    output_fields=fields,
                    limit=page,
                    offset=offset,
                )
            except TypeError:
                rows = coll.query(
                    expr="pk >= 0",
                    output_fields=fields,
                    limit=page,
                )
                offset = page  # 不支持 offset 时只拉一页
            except Exception as ex:
                _log.warning(
                    "[RAG-知识库|milvus_rag_query.fetch_milvus_rag_snapshot|collection.query|硬编执行|分页] 失败; error=%s",
                    ex,
                )
                break
            batch = list(rows or [])
            if not batch:
                break
            for r in batch:
                src = str(r.get("source_file") or "").strip()
                if not src:
                    continue
                nk = _norm_path(src)
                per_file[nk] += 1
                if nk not in sample_meta and any(r.get(k) for k in ("domain", "module", "doc_type")):
                    sample_meta[nk] = {
                        "domain": str(r.get("domain") or ""),
                        "module": str(r.get("module") or ""),
                        "doc_type": str(r.get("doc_type") or ""),
                        "keyword1": str(r.get("keyword1") or ""),
                        "keyword2": str(r.get("keyword2") or ""),
                    }
            if len(batch) < page or offset == page:
                break
            offset += len(batch)
            if offset > 50000:
                break

        if total <= 0:
            total = sum(per_file.values())
        return {
            "ok": True,
            "total_chunks": int(total),
            "total_files": len(per_file),
            "per_file_norm": dict(per_file),
            "sample_meta": sample_meta,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "collection": coll_name,
        }
    except Exception as ex:
        _log.warning(
            "[RAG-知识库|milvus_rag_query._fetch_milvus_rag_snapshot_impl|milvus|硬编执行|快照] 失败; error=%s",
            ex,
        )
        return None
    finally:
        _disconnect(_ALIAS)


def milvus_chunk_count_for_path(file_path: str, snapshot: Optional[Dict[str, Any]] = None) -> int:
    snap = snapshot or fetch_milvus_rag_snapshot()
    if not snap or not snap.get("ok"):
        return 0
    per = snap.get("per_file_norm") or {}
    nk = _norm_path(file_path)
    if nk in per:
        return int(per[nk])
    # 路径分隔符不一致时再试
    alt = _norm_path(file_path.replace("/", "\\")) if "/" in file_path else _norm_path(file_path.replace("\\", "/"))
    return int(per.get(alt) or 0)


def milvus_query_file_chunks(file_path: str, limit: int = 30) -> List[Dict[str, Any]]:
    """按 source_file 查询切片预览，不初始化 kb_manager。"""
    fp = str(file_path or "").strip()
    if not fp:
        return []
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_milvus_query_file_chunks_impl, fp, limit).result(timeout=_FETCH_TIMEOUT_SEC)
    except FuturesTimeout:
        _log.warning(
            "[RAG-知识库|milvus_rag_query.milvus_query_file_chunks|milvus|硬编执行|按文件] 超时; path=%s",
            fp[:120],
        )
        return []


def _milvus_query_file_chunks_impl(file_path: str, limit: int) -> List[Dict[str, Any]]:
    host, port, coll_name = _milvus_cfg()
    chunks: List[Dict[str, Any]] = []
    try:
        from pymilvus import Collection, connections, utility
    except ImportError:
        return []

    def _esc(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace('"', '\\"')

    _disconnect(_ALIAS)
    try:
        connections.connect(alias=_ALIAS, host=host, port=port, timeout=8)
        if not utility.has_collection(coll_name, using=_ALIAS):
            return []
        coll = Collection(coll_name, using=_ALIAS)
        try:
            coll.load(timeout=8)
        except TypeError:
            coll.load()
        safe = _esc(file_path)
        lim = max(1, min(int(limit or 30), 100))
        rows = coll.query(
            expr=f'source_file == "{safe}"',
            output_fields=[
                "pk", "chunk_id", "start_pos", "end_pos", "content",
                "domain", "module", "doc_type", "keyword1", "keyword2",
            ],
            limit=lim,
        )
        for r in rows or []:
            chunks.append(
                {
                    "pk": r.get("pk"),
                    "chunk_id": r.get("chunk_id"),
                    "start_pos": r.get("start_pos"),
                    "end_pos": r.get("end_pos"),
                    "preview": ((r.get("content") or "")[:280]),
                    "domain": r.get("domain") or "",
                    "module": r.get("module") or "",
                    "doc_type": r.get("doc_type") or "",
                }
            )
    except Exception as ex:
        _log.warning(
            "[RAG-知识库|milvus_rag_query._milvus_query_file_chunks_impl|collection.query|硬编执行|按文件] 失败; error=%s",
            ex,
        )
    finally:
        _disconnect(_ALIAS)
    return chunks
