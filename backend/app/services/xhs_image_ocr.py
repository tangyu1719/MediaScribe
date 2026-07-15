"""
小红书笔记图片 OCR 补偿（扩展模块，不修改 MinerU / link_analyzer 源码）。

策略（对齐 MinerU.process_image 思路）：
  1. 下载 CDN 图片
  2. 百度 AipOcr：按官方错误码重试 / 配额类直接降级
  3. 失败或空结果 → MinerU._local_ocr_fallback（pytesseract）
  4. 合并百度 + 本地文本（MinerU._merge_ocr_texts）

百度 OCR 错误码参考：
  https://cloud.baidu.com/doc/OCR/s/Ak3h7y8q6
"""
from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

_log = logging.getLogger("sba.xhs_image_ocr")

# ── 百度 OCR 错误码（官方文档）──
BAIDU_OCR_ERROR_MSG: Dict[int, str] = {
    1: "未知错误",
    2: "服务暂不可用",
    4: "集群超限额",
    17: "每日调用量超限",
    18: "QPS超限额",
    19: "请求总量超限",
    100: "access_token无效",
    110: "access_token无效或已失效",
    111: "access_token过期",
    216201: "图片格式错误",
    216630: "识别错误",
    282000: "服务器内部错误",
}

# 可退避重试（瞬态 / QPS）
BAIDU_OCR_RETRY_CODES = frozenset({1, 2, 4, 18, 282000})
# 配额耗尽：不重试，直接走 MinerU 本地 OCR
BAIDU_OCR_QUOTA_CODES = frozenset({17, 19})

_MIN_IMAGE_BYTES = 800
_BAIDU_MAX_RETRY = 3
_BAIDU_QPS_BACKOFF_SEC = 1.5

_mineru_processor = None


def _ensure_agent_path() -> None:
    try:
        from app.services.config import resolve_agent_dir

        agent_dir = resolve_agent_dir()
        if agent_dir and str(agent_dir) not in sys.path:
            sys.path.insert(0, str(agent_dir))
    except Exception:
        pass


def _apply_baidu_env() -> None:
    try:
        from app.services.doc_normalize_settings import settings

        if settings.BAIDU_OCR_APP_ID:
            os.environ.setdefault("BAIDU_OCR_APP_ID", settings.BAIDU_OCR_APP_ID)
        if settings.BAIDU_OCR_API_KEY:
            os.environ.setdefault("BAIDU_OCR_API_KEY", settings.BAIDU_OCR_API_KEY)
        if settings.BAIDU_OCR_SECRET_KEY:
            os.environ.setdefault("BAIDU_OCR_SECRET_KEY", settings.BAIDU_OCR_SECRET_KEY)
    except Exception:
        pass


def _get_mineru():
    global _mineru_processor
    if _mineru_processor is not None:
        return _mineru_processor
    _ensure_agent_path()
    _apply_baidu_env()
    from mineru_processor import MinerUProcessor

    _mineru_processor = MinerUProcessor()
    return _mineru_processor


def baidu_error_label(code: int) -> str:
    return BAIDU_OCR_ERROR_MSG.get(int(code or 0), f"error_code={code}")


def _fetch_headers(note_url: str = "") -> dict:
    referer = (note_url or "").strip() or "https://www.xiaohongshu.com/"
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
        ),
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }


def build_xhs_image_url_candidates(url: str) -> List[str]:
    raw = (url or "").strip()
    if not raw:
        return []
    out: List[str] = []
    if raw.startswith("http://"):
        out.append("https://" + raw[len("http://") :])
    out.append(raw)
    if "!nd_dft_wlteh_" in raw:
        out.append(raw.replace("!nd_dft_wlteh_", "!nd_dft_wgth_"))
    return list(dict.fromkeys(u for u in out if u))


def download_xhs_image(url: str, *, note_url: str = "", timeout: float = 20.0) -> Tuple[bytes, str]:
    headers = _fetch_headers(note_url)
    last_status = 0
    for cand in build_xhs_image_url_candidates(url):
        try:
            resp = requests.get(cand, headers=headers, timeout=timeout, allow_redirects=True)
            last_status = resp.status_code
            body = resp.content or b""
            if resp.status_code == 200 and len(body) >= _MIN_IMAGE_BYTES:
                return body, ""
        except Exception as ex:
            _log.warning(
                "[链接沉淀文档-小红书OCR|xhs_image_ocr.download_xhs_image|image|工具执行|重试] "
                "下载异常; url=%s; error_message=%s",
                cand[:100],
                ex,
            )
    return b"", f"download_failed:status={last_status}"


def _extract_words_from_baidu_result(result: dict) -> str:
    if not result or "words_result" not in result:
        return ""
    parts = [(item.get("words") or "").strip() for item in result.get("words_result") or []]
    parts = [p for p in parts if p]
    return "\n".join(parts)


def _baidu_ocr_with_retry(img_bytes: bytes) -> Tuple[str, Dict[str, Any]]:
    """
    百度 AipOcr：按错误码重试；配额类错误不重试。
    返回 (text, meta)，meta 含 error_code / retries / degraded_reason。
    """
    meta: Dict[str, Any] = {"error_code": 0, "error_msg": "", "retries": 0, "degraded_reason": ""}
    _apply_baidu_env()
    app_id = os.environ.get("BAIDU_OCR_APP_ID", "").strip() or "122094788"
    api_key = os.environ.get("BAIDU_OCR_API_KEY", "").strip()
    secret_key = os.environ.get("BAIDU_OCR_SECRET_KEY", "").strip()
    if not api_key or not secret_key:
        meta["degraded_reason"] = "baidu_not_configured"
        return "", meta

    try:
        from aip import AipOcr
    except ImportError as ex:
        meta["degraded_reason"] = f"aip_import_failed:{ex}"
        return "", meta

    client = AipOcr(app_id, api_key, secret_key)
    client.setConnectionTimeoutInMillis(90000)
    client.setSocketTimeoutInMillis(90000)

    last_code = 0
    for attempt in range(_BAIDU_MAX_RETRY):
        try:
            result = client.basicAccurate(img_bytes)
            if not result:
                last_code = -1
                meta["degraded_reason"] = "baidu_empty_response"
                break

            code = int(result.get("error_code") or 0)
            if code:
                last_code = code
                msg = str(result.get("error_msg") or baidu_error_label(code))
                meta["error_code"] = code
                meta["error_msg"] = msg
                _log.warning(
                    "[链接沉淀文档-小红书OCR|xhs_image_ocr._baidu_ocr_with_retry|baidu|工具执行|降级标记] "
                    "百度OCR错误; error_code=%s; error_msg=%s; attempt=%s",
                    code,
                    msg,
                    attempt + 1,
                )
                if code in BAIDU_OCR_QUOTA_CODES:
                    meta["degraded_reason"] = f"baidu_quota_{code}"
                    break
                if code in BAIDU_OCR_RETRY_CODES and attempt < _BAIDU_MAX_RETRY - 1:
                    meta["retries"] = attempt + 1
                    time.sleep(_BAIDU_QPS_BACKOFF_SEC)
                    continue
                meta["degraded_reason"] = f"baidu_error_{code}"
                break

            text = _extract_words_from_baidu_result(result)
            if not text:
                try:
                    result2 = client.basicGeneral(img_bytes)
                    if result2 and not result2.get("error_code"):
                        text = _extract_words_from_baidu_result(result2)
                except Exception:
                    pass
            if text.strip():
                meta["degraded_reason"] = ""
                return text.strip(), meta
            meta["degraded_reason"] = "baidu_words_empty"

        except Exception as ex:
            err = str(ex).lower()
            is_timeout = "timeout" in err or "timed out" in err
            meta["error_msg"] = str(ex)[:200]
            if is_timeout and attempt < _BAIDU_MAX_RETRY - 1:
                meta["retries"] = attempt + 1
                _log.warning(
                    "[链接沉淀文档-小红书OCR|xhs_image_ocr._baidu_ocr_with_retry|baidu|工具执行|重试] "
                    "请求超时; attempt=%s",
                    attempt + 1,
                )
                time.sleep(_BAIDU_QPS_BACKOFF_SEC)
                continue
            meta["degraded_reason"] = "baidu_exception"
            break

    if last_code and not meta.get("degraded_reason"):
        meta["degraded_reason"] = f"baidu_error_{last_code}"
    return "", meta


def ocr_image_bytes(image_bytes: bytes) -> Tuple[str, str, Dict[str, Any]]:
    """
    百度 OCR（带错误码重试）→ MinerU 本地 OCR 降级 → 合并。
    返回 (text, method, meta)。
    """
    meta: Dict[str, Any] = {"baidu": {}, "local": False}
    if not image_bytes or len(image_bytes) < _MIN_IMAGE_BYTES:
        return "", "empty", {**meta, "reason": "image_too_small"}

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name

        baidu_text, baidu_meta = _baidu_ocr_with_retry(image_bytes)
        meta["baidu"] = baidu_meta

        mp = _get_mineru()
        local_text = (mp._local_ocr_fallback(tmp_path) or "").strip()
        meta["local"] = bool(local_text)

        merged = mp._merge_ocr_texts(baidu_text, local_text).strip()
        if not merged:
            reason = baidu_meta.get("degraded_reason") or "all_ocr_empty"
            return "", "empty", {**meta, "reason": reason}

        if baidu_text and local_text:
            method = "baidu+local_tesseract"
        elif baidu_text:
            method = "baidu"
        else:
            method = "local_tesseract"
            reason = baidu_meta.get("degraded_reason") or "baidu_degraded"
            meta["reason"] = reason
        return merged, method, meta
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def ocr_one_xhs_image(img_url: str, idx: int, *, note_url: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "url": img_url,
        "index": idx,
        "text": "",
        "ok": False,
        "reason": "",
        "method": "",
        "download_bytes": 0,
        "baidu_error_code": 0,
    }
    data, dl_reason = download_xhs_image(img_url, note_url=note_url)
    if not data:
        out["reason"] = dl_reason or "download_failed"
        return out
    out["download_bytes"] = len(data)

    text, method, meta = ocr_image_bytes(data)
    out["baidu_error_code"] = int((meta.get("baidu") or {}).get("error_code") or 0)
    if text:
        out["text"] = text
        out["ok"] = True
        out["reason"] = "ok"
        out["method"] = method
        if meta.get("reason"):
            out["degraded"] = meta["reason"]
        return out

    out["reason"] = meta.get("reason") or (meta.get("baidu") or {}).get("degraded_reason") or "ocr_empty"
    out["method"] = method or "empty"
    return out


def run_xhs_ocr_compensation(
    image_links: List[str],
    *,
    note_url: str = "",
    sleep_sec: float = 1.0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """批量 OCR；默认间隔 1s，避免百度免费 2QPS 触发 18。"""
    recovered: List[Dict[str, Any]] = []
    fail_stats: Dict[str, int] = {}
    methods: Dict[str, int] = {}
    baidu_errors: Dict[str, int] = {}

    for idx, img_url in enumerate(image_links, 1):
        row = ocr_one_xhs_image(img_url, idx, note_url=note_url)
        reason = str(row.get("reason") or "unknown")
        fail_stats[reason] = fail_stats.get(reason, 0) + 1
        code = int(row.get("baidu_error_code") or 0)
        if code:
            label = f"{code}:{baidu_error_label(code)}"
            baidu_errors[label] = baidu_errors.get(label, 0) + 1
        if row.get("ok") and row.get("text"):
            recovered.append({
                "url": img_url,
                "text": row["text"],
                "index": idx,
                "method": row.get("method"),
            })
            m = str(row.get("method") or "unknown")
            methods[m] = methods.get(m, 0) + 1
        if idx < len(image_links) and sleep_sec > 0:
            time.sleep(sleep_sec)

    diagnostics = {
        "total": len(image_links),
        "ok": len(recovered),
        "fail_stats": fail_stats,
        "methods": methods,
        "baidu_errors": baidu_errors,
    }
    return recovered, diagnostics
