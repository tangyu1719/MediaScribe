"""知识库 / RAG 服务 —— 导入 src/agent/kb_manager_fast.py，并与老项目 file_records 对齐。"""
from __future__ import annotations
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

_HERE = Path(__file__).resolve()
_AGENT_DIR = None
# 须为含 kb_manager_fast.py 的权威 agent 目录，避免误命中 web_rebuild_v2/src/agent 空桩
for _p in _HERE.parents:
    _candidate = _p / "src" / "agent"
    if _candidate.is_dir() and (_candidate / "kb_manager_fast.py").is_file():
        _AGENT_DIR = _candidate.resolve()
        break
if _AGENT_DIR and str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from kb_manager_fast import FastKnowledgeBaseManager, get_fast_knowledge_base, get_knowledge_base
from rag_tools import DocumentMetadata, get_metadata_manager
from .chonkie_chunker import chunk_text_with_meta
from .milvus_rag_query import (
    fetch_milvus_rag_snapshot,
    milvus_chunk_count_for_path,
    milvus_query_file_chunks,
    _norm_path,
)

_log = logging.getLogger("sba.kb_rag")
_kb: Optional[FastKnowledgeBaseManager] = None


def agent_kb_dir() -> Path:
    """原项目知识库目录：src/agent/knowledge_base（可用环境变量 SBA_KB_DIR 固定）。"""
    env_kb = (os.environ.get("SBA_KB_DIR") or "").strip()
    if env_kb:
        p = Path(env_kb).resolve()
        if p.is_dir():
            return p
    if _AGENT_DIR:
        return (_AGENT_DIR / "knowledge_base").resolve()
    return (Path(__file__).resolve().parents[4] / "src" / "agent" / "knowledge_base").resolve()


def load_merged_file_records() -> List[Dict[str, Any]]:
    """与老项目 rag_manager_gui._load_file_records 一致：合并 file_records + file_cache。"""
    kb_dir = agent_kb_dir()
    records_file = kb_dir / "file_records.json"
    cache_file = kb_dir / "file_cache_fast.json"
    file_records: List[Dict[str, Any]] = []
    cache_records: List[Dict[str, Any]] = []

    if records_file.is_file():
        try:
            file_records = json.loads(records_file.read_text(encoding="utf-8")) or []
        except Exception as ex:
            _log.warning("[RAG-知识库|kb_rag.load_merged_file_records|file_records.json|硬编执行|读取] 失败; error=%s", ex)

    if cache_file.is_file():
        try:
            cache_map = json.loads(cache_file.read_text(encoding="utf-8")) or {}
            for file_path in cache_map.keys():
                ext = os.path.splitext(file_path)[1].lower() or "未知"
                size_kb = 0.0
                try:
                    if os.path.exists(file_path):
                        size_kb = os.path.getsize(file_path) / 1024
                except OSError:
                    pass
                cache_records.append(
                    {
                        "file_path": file_path,
                        "file_name": os.path.basename(file_path),
                        "file_type": ext,
                        "file_size": size_kb,
                        "chunk_count": 0,
                        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "vector_bound": True,
                    }
                )
        except Exception as ex:
            _log.warning("[RAG-知识库|kb_rag.load_merged_file_records|file_cache_fast.json|硬编执行|读取] 失败; error=%s", ex)

    merged: Dict[str, Dict[str, Any]] = {
        str(r.get("file_path")): r for r in file_records if r.get("file_path")
    }
    for cr in cache_records:
        fp = cr.get("file_path")
        if not fp:
            continue
        if fp not in merged:
            merged[str(fp)] = cr
        elif not merged[str(fp)].get("vector_bound") and cr.get("vector_bound"):
            merged[str(fp)]["vector_bound"] = True

    if not merged and _ensure_file_records_from_persisted():
        if records_file.is_file():
            try:
                file_records = json.loads(records_file.read_text(encoding="utf-8")) or []
                merged = {str(r.get("file_path")): r for r in file_records if r.get("file_path")}
            except Exception as ex:
                _log.warning("[RAG-知识库|kb_rag.load_merged_file_records|file_records.json|硬编执行|二次读取] 失败; error=%s", ex)

    return list(merged.values())


def _kb_persisted_paths() -> Dict[str, Path]:
    kb_dir = agent_kb_dir()
    return {
        "kb_dir": kb_dir,
        "file_records": kb_dir / "file_records.json",
        "file_cache": kb_dir / "file_cache_fast.json",
        "vector_index": kb_dir / "vector_index_fast.json",
    }


def _resolve_kb_source_path(source_file: str) -> str:
    """相对路径按 src/agent 解析；绝对路径原样保留。"""
    s = str(source_file or "").strip()
    if not s:
        return ""
    if os.path.isabs(s):
        return os.path.normpath(s)
    if _AGENT_DIR:
        cand = (_AGENT_DIR / s).resolve()
        if cand.is_file():
            return str(cand)
    return os.path.normpath(s)


def _load_vector_index_chunk_groups() -> Dict[str, List[Dict[str, Any]]]:
    path = _kb_persisted_paths()["vector_index"]
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
        chunks = data.get("chunks") or []
    except Exception as ex:
        _log.warning("[RAG-知识库|kb_rag._load_vector_index_chunk_groups|vector_index_fast.json|硬编执行|读取] 失败; error=%s", ex)
        return {}
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for ch in chunks:
        src = str(ch.get("source_file") or "").strip()
        if not src:
            continue
        groups.setdefault(src, []).append(ch)
    return groups


def _ensure_file_records_from_persisted() -> bool:
    """file_records 缺失时，从 file_cache + vector_index 重建登记册。"""
    paths = _kb_persisted_paths()
    if paths["file_records"].is_file():
        return False
    if not paths["file_cache"].is_file() and not paths["vector_index"].is_file():
        return False
    kb_rebuild_catalog_from_persisted(reindex_milvus=False)
    return True


def kb_persisted_inventory() -> Dict[str, Any]:
    """读取磁盘持久化层（不连 Milvus、不加载 BGE）。"""
    paths = _kb_persisted_paths()
    kb_dir = paths["kb_dir"]
    items: List[Dict[str, Any]] = []

    if paths["file_records"].is_file():
        try:
            recs = json.loads(paths["file_records"].read_text(encoding="utf-8")) or []
            items.append(
                {
                    "layer": "file_records.json",
                    "path": str(paths["file_records"]),
                    "exists": True,
                    "count": len(recs),
                    "note": "WEB 列表主登记册（路径、切片数、metadata）",
                }
            )
        except Exception as ex:
            items.append({"layer": "file_records.json", "path": str(paths["file_records"]), "exists": True, "error": str(ex)})

    cache_keys: List[str] = []
    if paths["file_cache"].is_file():
        try:
            cache = json.loads(paths["file_cache"].read_text(encoding="utf-8")) or {}
            cache_keys = list(cache.keys()) if isinstance(cache, dict) else []
            items.append(
                {
                    "layer": "file_cache_fast.json",
                    "path": str(paths["file_cache"]),
                    "exists": True,
                    "count": len(cache_keys),
                    "note": "源文件路径 → 内容哈希（入库缓存）",
                }
            )
        except Exception as ex:
            items.append({"layer": "file_cache_fast.json", "path": str(paths["file_cache"]), "exists": True, "error": str(ex)})

    vec_groups = _load_vector_index_chunk_groups()
    if paths["vector_index"].is_file():
        total_chunks = sum(len(v) for v in vec_groups.values())
        items.append(
            {
                "layer": "vector_index_fast.json",
                "path": str(paths["vector_index"]),
                "exists": True,
                "count": total_chunks,
                "files": len(vec_groups),
                "note": "本地 JSON 向量快照（含切片正文 content，Milvus 清空后可作文本兜底）",
            }
        )

    catalog: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for src, chs in vec_groups.items():
        fp = _resolve_kb_source_path(src)
        if fp in seen:
            continue
        seen.add(fp)
        catalog.append(
            {
                "path": fp,
                "source_key": src,
                "source_exists": os.path.isfile(fp),
                "local_json_chunks": len(chs),
                "text_preview_len": sum(len(str(c.get("content") or "")) for c in chs),
            }
        )
    for fp in cache_keys:
        if fp in seen:
            continue
        seen.add(fp)
        catalog.append(
            {
                "path": fp,
                "source_key": fp,
                "source_exists": os.path.isfile(fp),
                "local_json_chunks": len(vec_groups.get(os.path.basename(fp), [])),
                "text_preview_len": 0,
            }
        )

    return {
        "ok": True,
        "kb_dir": str(kb_dir),
        "layers": items,
        "catalog": catalog,
        "milvus_note": "向量嵌入在 Milvus；清空 Docker 卷后需对仍存在的源文件重新「入库」",
        "source_note": "原始全文在源文件路径；若源文件已删，可从 vector_index_fast.json 的 content 字段读切片文本",
    }


def kb_rebuild_catalog_from_persisted(reindex_milvus: bool = False) -> Dict[str, Any]:
    """从 file_cache + vector_index 重建 file_records.json。"""
    paths = _kb_persisted_paths()
    records_by_path: Dict[str, Dict[str, Any]] = {}

    if paths["file_cache"].is_file():
        try:
            cache = json.loads(paths["file_cache"].read_text(encoding="utf-8")) or {}
            if isinstance(cache, dict):
                for fp in cache.keys():
                    records_by_path[str(fp)] = {
                        "file_path": str(fp),
                        "file_name": os.path.basename(str(fp)),
                        "file_type": os.path.splitext(str(fp))[1].lower() or "未知",
                        "file_size": 0.0,
                        "chunk_count": 0,
                        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "vector_bound": False,
                        "source_exists": os.path.isfile(str(fp)),
                        "catalog_source": "file_cache",
                    }
        except Exception as ex:
            return {"ok": False, "error": f"读取 file_cache 失败: {ex}"}

    for src, chs in _load_vector_index_chunk_groups().items():
        fp = _resolve_kb_source_path(src)
        if not fp:
            continue
        row = records_by_path.get(fp) or {
            "file_path": fp,
            "file_name": os.path.basename(fp),
            "file_type": os.path.splitext(fp)[1].lower() or "未知",
            "file_size": 0.0,
            "chunk_count": 0,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "vector_bound": False,
            "source_exists": os.path.isfile(fp),
            "catalog_source": "vector_index",
        }
        row["chunk_count"] = max(int(row.get("chunk_count") or 0), len(chs))
        row["vector_bound"] = row["chunk_count"] > 0
        row["local_json_chunks"] = len(chs)
        records_by_path[fp] = row

    for fp, row in list(records_by_path.items()):
        try:
            if os.path.isfile(fp):
                row["file_size"] = os.path.getsize(fp) / 1024
                row["source_exists"] = True
        except OSError:
            row["source_exists"] = False

    records = list(records_by_path.values())
    _save_records_file(records)
    _log.info(
        "[RAG-知识库|kb_rag.kb_rebuild_catalog_from_persisted|file_records.json|硬编执行|重建] 完成; records=%s",
        len(records),
    )

    reindexed = 0
    reindex_errors: List[str] = []
    if reindex_milvus:
        for r in records:
            fp = str(r.get("file_path") or "")
            if not fp or not os.path.isfile(fp):
                continue
            try:
                out = kb_add_file(fp, "auto", metadata=None)
                if out.get("ok"):
                    reindexed += 1
                else:
                    reindex_errors.append(f"{r.get('file_name')}: {out.get('error') or out.get('message')}")
            except Exception as ex:
                reindex_errors.append(f"{r.get('file_name')}: {ex}")

    missing = [r for r in records if not r.get("source_exists")]
    return {
        "ok": True,
        "records": len(records),
        "missing_sources": len(missing),
        "missing": [{"path": r.get("file_path"), "name": r.get("file_name")} for r in missing[:20]],
        "reindexed": reindexed,
        "reindex_errors": reindex_errors[:10],
        "file_records_path": str(paths["file_records"]),
    }


def kb_read_persisted_text(file_path: str, limit: int = 50000) -> Dict[str, Any]:
    """读知识库文本：优先源文件；否则拼接 vector_index 中切片 content。"""
    fp = str(file_path or "").strip()
    if not fp:
        return {"ok": False, "error": "缺少 path"}
    lim = max(1000, min(int(limit or 50000), 200000))

    if os.path.isfile(fp):
        text = _read_text_preview(fp, lim)
        if text:
            return {"ok": True, "source": "disk", "path": fp, "text": text, "truncated": len(text) >= lim}

    base = os.path.basename(fp)
    groups = _load_vector_index_chunk_groups()
    matched: List[Dict[str, Any]] = []
    for src, chs in groups.items():
        resolved = _resolve_kb_source_path(src)
        if resolved == fp or src == fp or os.path.basename(src) == base:
            matched = sorted(chs, key=lambda c: int(c.get("chunk_id") or 0))
            break
    if matched:
        text = "\n\n".join(str(c.get("content") or "") for c in matched).strip()[:lim]
        return {
            "ok": True,
            "source": "vector_index_fast",
            "path": fp,
            "text": text,
            "chunks": len(matched),
            "truncated": len(text) >= lim,
        }

    return {
        "ok": False,
        "error": "源文件不存在，且 vector_index_fast.json 中无该文件切片文本",
        "path": fp,
    }


def _latest_added_at(records: List[Dict[str, Any]]) -> str:
    latest = None
    for r in records:
        t = (r.get("added_at") or "").strip()
        if not t:
            continue
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                dt_obj = datetime.strptime(t, fmt)
                if latest is None or dt_obj > latest:
                    latest = dt_obj
                break
            except ValueError:
                continue
    return latest.strftime("%Y-%m-%d %H:%M:%S") if latest else ""


def get_kb_manager() -> Optional[FastKnowledgeBaseManager]:
    global _kb
    if _kb is None:
        try:
            _kb = get_fast_knowledge_base()
        except Exception:
            try:
                _kb = get_knowledge_base()
            except Exception:
                return None
    return _kb


def reset_kb_manager() -> None:
    """Milvus 恢复或配置变更后，强制下次重新初始化 kb_manager。"""
    global _kb
    _kb = None


def _apply_milvus_slice_aggregate(rows: List[Dict[str, Any]], snap: Optional[Dict[str, Any]]) -> None:
    """刷新时：直连 Milvus 切片，按 source_file 聚合后写回各行 chunk_count（不依赖 file_records 缓存值）。"""
    if not snap or not snap.get("ok"):
        return
    meta_samples = snap.get("sample_meta") or {}
    for row in rows:
        fp = str(row.get("path") or "")
        mc = milvus_chunk_count_for_path(fp, snap)
        if mc is not None:
            row["chunk_count"] = mc
            row["chunk_count_source"] = "milvus_slice_agg"
            if mc > 0:
                row["vector_bound"] = True
        bn = _norm_path(os.path.basename(fp))
        sm = meta_samples.get(bn) or meta_samples.get(_norm_path(fp)) or {}
        if sm.get("domain") and not row.get("domain"):
            row["domain"] = sm["domain"]
        if sm.get("module") and not row.get("module"):
            row["module"] = sm["module"]
        if sm.get("doc_type") and not row.get("doc_type"):
            row["doc_type"] = sm["doc_type"]


def _enrich_rows_from_milvus(rows: List[Dict[str, Any]], snap: Optional[Dict[str, Any]]) -> None:
    """兼容旧名：统一走切片聚合。"""
    _apply_milvus_slice_aggregate(rows, snap)


def kb_stats(*, refresh: bool = False) -> Dict[str, Any]:
    records = load_merged_file_records()
    rec_files = len(records)
    rec_chunks = sum(int(r.get("chunk_count") or 0) for r in records)
    embedding_dim = 1024
    storage_backend = "file_records"
    milvus_ok = False
    model_loaded = False
    last_update = _latest_added_at(records)
    chunk_count_source = "file_records"
    chunk_agg_ms: Optional[int] = None

    milvus_snap = None
    milvus_degraded = False
    try:
        from .milvus_health import check_milvus

        milvus_ok = bool(check_milvus().get("milvus_ok"))
    except Exception:
        milvus_ok = False

    if milvus_ok:
        milvus_snap = fetch_milvus_rag_snapshot(force=refresh)
        if milvus_snap and milvus_snap.get("ok"):
            mv_total = int(milvus_snap.get("total_chunks") or 0)
            chunk_agg_ms = int(milvus_snap.get("latency_ms") or 0)
            if mv_total > 0:
                storage_backend = "milvus"
                rec_chunks = mv_total
                rec_files = max(rec_files, int(milvus_snap.get("total_files") or 0))
                chunk_count_source = "milvus_slice_agg"
            elif rec_chunks > 0:
                milvus_degraded = True
                chunk_count_source = "file_records"
        else:
            milvus_degraded = True

    # 仅当 kb 已初始化时才合并引擎统计（不为此加载 BGE）
    if _kb is not None:
        try:
            stats = _kb.get_stats() or {}
            embedding_dim = int(stats.get("embedding_dim") or embedding_dim)
            storage_backend = str(stats.get("storage_backend") or storage_backend)
            milvus_ok = milvus_ok or (storage_backend == "milvus" and bool(getattr(_kb, "_milvus", None)))
            model_loaded = bool(getattr(_kb, "_model_loaded", False))
            rec_files = max(rec_files, int(stats.get("total_files") or 0))
            rec_chunks = max(rec_chunks, int(stats.get("total_chunks") or 0))
        except Exception as ex:
            _log.warning("[RAG-知识库|kb_rag.kb_stats|kb.get_stats|硬编执行|统计] 失败; error=%s", ex)

    return {
        "ok": True,
        "data": {
            "total_files": rec_files,
            "total_chunks": rec_chunks,
            "embedding_dim": embedding_dim,
            "model_loaded": model_loaded,
            "storage_backend": storage_backend,
            "milvus_ok": milvus_ok,
            "last_update": last_update,
            "chunk_count_source": chunk_count_source,
            "chunk_agg_ms": chunk_agg_ms,
            "milvus_degraded": milvus_degraded,
            "file_records_path": str(agent_kb_dir() / "file_records.json"),
            "agent_dir": str(_AGENT_DIR or ""),
            "records_merged_count": rec_files,
        },
    }


def _record_to_web_row(r: Dict[str, Any]) -> Dict[str, Any]:
    size_kb = float(r.get("file_size") or 0)
    return {
        "name": r.get("file_name") or os.path.basename(str(r.get("file_path") or "")),
        "path": r.get("file_path") or "",
        "size": int(size_kb * 1024),
        "suffix": r.get("file_type") or "",
        "chunk_count": int(r.get("chunk_count") or 0),
        "vector_bound": bool(r.get("vector_bound")),
        "added_at": r.get("added_at") or "",
        "domain": r.get("domain") or "",
        "module": r.get("module") or "",
        "doc_type": r.get("doc_type") or "",
        "keyword1": r.get("keyword1") or "",
        "keyword2": r.get("keyword2") or "",
        "tag_id": r.get("tag_id"),
        "source_exists": bool(r.get("source_exists", os.path.isfile(str(r.get("file_path") or "")))),
        "local_json_chunks": int(r.get("local_json_chunks") or 0),
    }


def kb_list_files(*, refresh: bool = False) -> Dict[str, Any]:
    """文件列表：登记册 + 刷新时 Milvus 切片按父文档聚合的 chunk_count。"""
    records = load_merged_file_records()
    milvus_snap = fetch_milvus_rag_snapshot(force=refresh) if records else None
    chunk_agg_ms = int((milvus_snap or {}).get("latency_ms") or 0) if milvus_snap else None
    if records:
        rows = [_record_to_web_row(r) for r in records]
        _apply_milvus_slice_aggregate(rows, milvus_snap)
        rows.sort(
            key=lambda x: (
                int(x.get("chunk_count") or 0),
                x.get("added_at") or "",
                x.get("name") or "",
            ),
            reverse=True,
        )
        return {
            "files": rows,
            "chunk_agg_ms": chunk_agg_ms,
            "chunk_count_source": "milvus_slice_agg" if milvus_snap and milvus_snap.get("ok") else "file_records",
        }
    kb = get_kb_manager()
    if kb is None:
        return {"files": [], "chunk_agg_ms": chunk_agg_ms, "chunk_count_source": "none"}
    chunks = kb.list_chunks(offset=0, limit=500)
    by_file: Dict[str, Dict[str, Any]] = {}
    for c in chunks or []:
        src = c.get("source_file") or ""
        if not src:
            continue
        if src not in by_file:
            by_file[src] = {
                "name": os.path.basename(src),
                "path": src,
                "size": 0,
                "suffix": Path(src).suffix.lower(),
                "chunk_count": 0,
            }
        by_file[src]["chunk_count"] = int(by_file[src].get("chunk_count") or 0) + 1
    return {
        "files": list(by_file.values()),
        "chunk_agg_ms": chunk_agg_ms,
        "chunk_count_source": "kb_manager",
    }


def _metadata_from_payload(raw: Optional[Dict[str, Any]]) -> Optional[DocumentMetadata]:
    if not raw or not isinstance(raw, dict):
        return None
    return DocumentMetadata.from_dict(
        {
            "domain": str(raw.get("domain") or "").strip(),
            "module": str(raw.get("module") or "").strip(),
            "doc_type": str(raw.get("doc_type") or "").strip(),
            "keyword1": str(raw.get("keyword1") or "").strip(),
            "keyword2": str(raw.get("keyword2") or "").strip(),
        }
    )


def _save_records_file(records: List[Dict[str, Any]]) -> None:
    path = agent_kb_dir() / "file_records.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def kb_metadata_options() -> Dict[str, Any]:
    mm = get_metadata_manager()
    return {
        "ok": True,
        "domains": list(mm.DOMAINS),
        "modules": list(mm.MODULES),
        "doc_types": list(mm.DOC_TYPES),
        "required": ["domain", "module", "doc_type"],
        "optional": ["keyword1", "keyword2"],
    }


def kb_find_record(file_path: str) -> Optional[Dict[str, Any]]:
    fp = str(file_path or "").strip()
    if not fp:
        return None
    norm = os.path.normcase(os.path.normpath(fp))
    for r in load_merged_file_records():
        p = str(r.get("file_path") or "")
        if os.path.normcase(os.path.normpath(p)) == norm:
            return r
    return None


def kb_file_detail(file_path: str) -> Dict[str, Any]:
    fp = str(file_path or "").strip()
    if not fp:
        return {"ok": False, "error": "缺少 path"}
    rec = kb_find_record(fp)
    row = _record_to_web_row(rec) if rec else {
        "name": os.path.basename(fp),
        "path": fp,
        "size": int(os.path.getsize(fp)) if os.path.isfile(fp) else 0,
        "suffix": Path(fp).suffix.lower(),
        "chunk_count": 0,
        "vector_bound": False,
        "added_at": "",
        "domain": "",
        "module": "",
        "doc_type": "",
        "keyword1": "",
        "keyword2": "",
    }
    snap = fetch_milvus_rag_snapshot()
    _apply_milvus_slice_aggregate([row], snap)
    return {"ok": True, "file": row, "record": rec or {}, "chunks": []}


def kb_sync_chunk_counts() -> Dict[str, Any]:
    """从 Milvus 回写 file_records.chunk_count（需集合可查询）。"""
    from .milvus_rag_query import fetch_milvus_rag_snapshot as _fetch

    snap = _fetch(force=True)
    per = (snap or {}).get("per_file_norm") or {}
    if not per:
        return {
            "ok": False,
            "error": "Milvus 集合无法加载或未返回切片统计，请检查 docker/milvus（MinIO SlowDown/恢复中）后重试",
        }
    path = agent_kb_dir() / "file_records.json"
    if not path.is_file():
        if _ensure_file_records_from_persisted():
            pass
    if not path.is_file():
        return {"ok": False, "error": "file_records.json 不存在，请先点「恢复本地目录」"}
    try:
        records = json.loads(path.read_text(encoding="utf-8")) or []
    except Exception as ex:
        return {"ok": False, "error": f"读取 file_records 失败: {ex}"}
    updated = 0
    total_milvus = int((snap or {}).get("total_chunks") or sum(per.values()))
    for r in records:
        fp = str(r.get("file_path") or "")
        mc = milvus_chunk_count_for_path(fp, snap)
        if mc is None:
            continue
        if int(r.get("chunk_count") or 0) != mc:
            r["chunk_count"] = mc
            if mc > 0:
                r["vector_bound"] = True
            updated += 1
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.info(
        "[RAG-知识库|kb_rag.kb_sync_chunk_counts|file_records.json|硬编执行|回写] 完成; updated=%s; milvus_total=%s",
        updated,
        total_milvus,
    )
    return {
        "ok": True,
        "updated": updated,
        "milvus_total_chunks": total_milvus,
        "milvus_files": len(per),
    }


def kb_file_chunks(file_path: str, limit: int = 30) -> Dict[str, Any]:
    """Milvus 切片预览：轻量查询，不加载 BGE。"""
    fp = str(file_path or "").strip()
    chunks = milvus_query_file_chunks(fp, limit=limit)
    hint = ""
    if not chunks:
        snap = fetch_milvus_rag_snapshot()
        mc = milvus_chunk_count_for_path(fp, snap)
        if mc is not None and mc > 0:
            hint = f"Milvus 中有 {mc} 条切片，但按路径查询为空（可能路径与入库时不一致）"
        elif mc == 0:
            hint = "该文件在 Milvus 中无切片（已匹配 source_file 但计数为 0）"
        else:
            hint = "该文件在 Milvus 中无切片或未连接向量库"
    return {"ok": True, "chunks": chunks, "hint": hint}


def _read_text_preview(file_path: str, limit: int = 12000) -> str:
    p = Path(file_path)
    if not p.is_file():
        return ""
    suf = p.suffix.lower()
    if suf in (".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".py", ".java", ".js", ".ts"):
        for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
            try:
                text = p.read_text(encoding=enc, errors="strict")
                if text:
                    return text[:limit]
            except Exception:
                continue
        return p.read_text(encoding="utf-8", errors="ignore")[:limit]
    return ""


def kb_auto_metadata(file_path: str, mode: str = "rule") -> Dict[str, Any]:
    """mode: rule | llm"""
    fp = str(file_path or "").strip()
    if not fp or not os.path.isfile(fp):
        return {"ok": False, "error": "文件不存在或不可读"}
    content = _read_text_preview(fp)
    name = os.path.basename(fp)
    mm = get_metadata_manager()
    meta = mm.auto_extract_metadata(content, name)
    source = "rule"
    if (mode or "").strip().lower() == "llm":
        llm_meta = _llm_extract_metadata(content, name)
        if llm_meta:
            meta = llm_meta
            source = "llm"
    return {"ok": True, "metadata": meta.to_dict(), "source": source, "valid": meta.is_valid()}


def _llm_extract_metadata(content: str, filename: str) -> Optional[DocumentMetadata]:
    try:
        from .tools_detail_llm import _load_llm_cfg
        from provider_adapters import invoke_chat_completion_raw, _extract_openai_message_dict
    except ImportError:
        return None
    cfg = _load_llm_cfg()
    api_key = (cfg.get("volcengine_api_key") or cfg.get("openai_api_key") or "").strip()
    model = (cfg.get("ai_chat_model") or "").strip()
    if not api_key or not model:
        return None
    base_url = (cfg.get("volcengine_base_url") or cfg.get("openai_base_url") or "https://ark.cn-beijing.volces.com/api/v3").strip()
    provider = (cfg.get("gateway_provider") or "ark").strip().lower()
    system = (
        "你是文档元数据标注助手。根据文件名与正文摘要，输出 JSON 对象，字段仅包含："
        "domain, module, doc_type, keyword1, keyword2（均为字符串）。"
        "domain 取值参考：技术/产品/运营/市场/人事/财务/法务/其他；"
        "module 参考：前端/后端/算法/测试/运维/设计/文档/会议/其他；"
        "doc_type 参考：代码/文档/规范/报告/邮件/聊天记录/其他。"
        "只输出 JSON，不要 markdown。"
    )
    user = f"文件名: {filename}\n\n正文摘要:\n{(content or '')[:8000]}"
    try:
        data = invoke_chat_completion_raw(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.1,
            max_tokens=400,
            timeout=60.0,
            thinking_enabled=False,
            tools=None,
        )
        msg = _extract_openai_message_dict(data)
        raw = (msg.get("content") or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
        obj = json.loads(raw)
        meta = DocumentMetadata.from_dict(obj if isinstance(obj, dict) else {})
        mm = get_metadata_manager()
        if not meta.domain:
            meta.domain = "其他"
        if not meta.module:
            meta.module = "其他"
        if not meta.doc_type:
            meta.doc_type = "文档"
        ok, _ = mm.validate_metadata(meta)
        return meta if ok else None
    except Exception as ex:
        _log.warning("[RAG-知识库|kb_rag._llm_extract_metadata|llm|Agent执行|槽位] 失败; error=%s", ex)
        return None


def kb_update_file_metadata(file_path: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    fp = str(file_path or "").strip()
    meta = _metadata_from_payload(metadata)
    if meta is None:
        return {"ok": False, "error": "metadata 无效"}
    mm = get_metadata_manager()
    ok, msg = mm.validate_metadata(meta)
    if not ok:
        return {"ok": False, "error": msg}
    records = load_merged_file_records()
    norm = os.path.normcase(os.path.normpath(fp))
    found = False
    for r in records:
        p = str(r.get("file_path") or "")
        if os.path.normcase(os.path.normpath(p)) == norm:
            r["domain"] = meta.domain
            r["module"] = meta.module
            r["doc_type"] = meta.doc_type
            r["keyword1"] = meta.keyword1
            r["keyword2"] = meta.keyword2
            found = True
            break
    if not found:
        size_kb = 0.0
        try:
            if os.path.isfile(fp):
                size_kb = os.path.getsize(fp) / 1024
        except OSError:
            pass
        records.append(
            {
                "file_path": fp,
                "file_name": os.path.basename(fp),
                "file_type": Path(fp).suffix.lower(),
                "file_size": size_kb,
                "chunk_count": 0,
                "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "vector_bound": False,
                "domain": meta.domain,
                "module": meta.module,
                "doc_type": meta.doc_type,
                "keyword1": meta.keyword1,
                "keyword2": meta.keyword2,
            }
        )
    _save_records_file(records)
    return {"ok": True, "metadata": meta.to_dict(), "file": _record_to_web_row(kb_find_record(fp) or {})}


def kb_add_file(
    file_path: str,
    slice_method: str = "auto",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    kb = get_kb_manager()
    if kb is None:
        return {"ok": False, "error": "知识库未初始化"}
    meta_obj = _metadata_from_payload(metadata)
    try:
        text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        chunked = chunk_text_with_meta(text, mode=slice_method or "auto", source=str(file_path))
        ok, msg = kb.add_document(file_path, metadata=meta_obj)
        return {
            "ok": ok,
            "message": msg,
            "chunk_stats": chunked,
            "slice_method": slice_method or "auto",
            "metadata": meta_obj.to_dict() if meta_obj else None,
        }
    except Exception:
        ok, msg = kb.add_document(file_path, metadata=meta_obj)
        return {
            "ok": ok,
            "message": msg,
            "slice_method": slice_method or "auto",
            "metadata": meta_obj.to_dict() if meta_obj else None,
        }


def _kb_import_allowed_path(path: str) -> Path:
    """校验导入路径在白名单内（web output + demo_wendanghua/output）。"""
    from .fs_browse import allowed_roots

    p = Path(path).resolve()
    if not p.is_dir():
        raise ValueError(f"目录不存在: {path}")
    roots = list(allowed_roots())
    for parent in _HERE.parents:
        demo_out = parent / "demo_wendanghua" / "output"
        if demo_out.is_dir():
            roots.append(demo_out.resolve())
            break
    for root in roots:
        try:
            p.relative_to(root.resolve())
            return p
        except ValueError:
            continue
    raise ValueError("路径不在允许导入的白名单内")


def kb_import_local_folder(
    folder_path: str,
    *,
    extensions: str = ".md,.txt,.markdown",
    slice_method: str = "auto",
    granularity: bool = True,
) -> Dict[str, Any]:
    """服务端本地目录批量导入，并按大/中/小粒度自动标注元数据（父文档与子切片一致）。"""
    root = _kb_import_allowed_path(folder_path)
    return kb_add_folder(
        str(root),
        extensions=extensions,
        slice_method=slice_method,
        granularity=granularity,
        import_root=str(root),
    )


def kb_add_folder(
    folder_path: str,
    extensions: str = ".md,.txt",
    slice_method: str = "auto",
    *,
    granularity: bool = False,
    import_root: Optional[str] = None,
) -> Dict[str, Any]:
    kb = get_kb_manager()
    if kb is None:
        return {"ok": False, "error": "知识库未初始化"}

    exts = set()
    for e in extensions.lower().split(","):
        e = e.strip()
        if not e:
            continue
        exts.add(e if e.startswith(".") else "." + e)
    files: List[str] = []
    for root, _dirs, filenames in os.walk(folder_path):
        for fn in filenames:
            suf = Path(fn).suffix.lower()
            if suf in exts:
                files.append(str(Path(root) / fn))

    from .kb_granularity_metadata import infer_granularity_metadata

    results = []
    success = 0
    for fp in files:
        chunked = None
        meta_dict: Optional[Dict[str, Any]] = None
        try:
            text = Path(fp).read_text(encoding="utf-8", errors="ignore")
            chunked = chunk_text_with_meta(text, mode=slice_method or "auto", source=str(fp))
            if granularity:
                meta_dict = infer_granularity_metadata(
                    fp, text, import_root=import_root or folder_path
                )
        except Exception:
            chunked = None
        ok, msg = kb.add_document(fp, metadata=_metadata_from_payload(meta_dict) if meta_dict else None)
        results.append({
            "file": fp,
            "ok": ok,
            "message": msg,
            "slice_method": slice_method or "auto",
            "chunk_stats": chunked,
            "metadata": meta_dict,
        })
        if ok:
            success += 1

    return {"ok": True, "total": len(files), "success": success, "failed": len(files) - success, "results": results}


def kb_rebuild_index(folder_path: str = None) -> Dict[str, Any]:
    kb = get_kb_manager()
    if kb is None:
        return {"ok": False, "error": "知识库未初始化"}

    files = []
    if folder_path and Path(folder_path).is_dir():
        for root, _dirs, filenames in os.walk(folder_path):
            for fn in filenames:
                if fn.endswith((".md", ".txt", ".markdown")):
                    files.append(str(Path(root) / fn))
    else:
        files = [p for p in getattr(kb, "_file_cache", {}).keys() if Path(p).exists()]

    old_cache = dict(getattr(kb, "_file_cache", {}))
    kb._file_cache = {}
    rebuilt = 0
    for fp in files:
        ok, _ = kb.add_document(fp)
        if ok:
            rebuilt += 1

    if not getattr(kb, "_file_cache", None):
        kb._file_cache = old_cache

    return {"ok": True, "total": len(files), "rebuilt": rebuilt}


def kb_search(
    query: str,
    top_k: int = 5,
    *,
    span_ctx: Optional[Dict[str, Any]] = None,
    metadata_filter: Optional[Dict[str, str]] = None,
) -> List[Dict]:
    """语义检索；可选 span_ctx 写入 span_audit（step_type=retrieval）。"""
    err = ""
    hits: List[Dict] = []
    meta_obj = _metadata_from_payload(metadata_filter) if metadata_filter else None
    try:
        kb = get_kb_manager()
        if kb is None:
            hits = []
        else:
            hits = list(kb.search(query, top_k=top_k, metadata_filter=meta_obj) or [])
    except Exception as ex:
        err = str(ex)
        hits = []
    try:
        from .span_orchestration import persist_retrieval_step

        persist_retrieval_step(
            (query or "").strip(),
            hits,
            span_ctx=span_ctx,
            error=err,
            top_k=top_k,
            source="kb_search",
        )
    except Exception:
        pass
    return hits
