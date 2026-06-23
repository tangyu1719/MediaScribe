"""统一文档沉淀服务 —— 原样搬运 video_gui.py _run_document_consolidation + 标题提取 + 运维Agent

所有平台（视频/小红书/抖音/微信公众号/多模态）共享此模块。
"""
from __future__ import annotations
import concurrent.futures
import hashlib
import re
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve()
_AGENT_DIR = None
for _p in _HERE.parents:
    if (_p / "src" / "agent").is_dir():
        _AGENT_DIR = (_p / "src" / "agent").resolve()
        break
if _AGENT_DIR and str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from .pipeline_executor import get_llm_executor
from .pipeline_comments import compose_summary_input, prepare_comments_for_summary
from .pipeline_logging import (
    enrich_pipeline_llm_cfg,
    invoke_llm_via_gateway,
    llm_timeout_for_text_len,
    log_llm_done,
    log_llm_prepare,
    pipeline_log,
)
from .llm_agent_signals import (
    _JSON_OUTPUT_RULE_ARTICLE_WITH_SIGNAL,
    _JSON_OUTPUT_RULE_SUMMARY_WITH_SIGNAL,
    input_stats_block,
    parse_agent_status,
)
from .json_llm_output import (
    LLM_JSON_PARSE_FAILED,
    build_json_retry_user_suffix,
    normalize_llm_string_escapes,
    parse_llm_json_object,
)
from .pipeline_output_quality import LLMInputRejectedError

_JSON_OUTPUT_RULE_ARTICLE = _JSON_OUTPUT_RULE_ARTICLE_WITH_SIGNAL
_JSON_OUTPUT_RULE_SUMMARY = _JSON_OUTPUT_RULE_SUMMARY_WITH_SIGNAL
_LLM_JSON_MAX_RETRY = 2

_DEFAULT_SUMMARY_PROMPT = (
    "请对以下文本进行总结，提取关键知识点，整理成结构化的格式。\n"
    "要求：\n"
    "1. 第一行必须是一个简洁的中文标题（不超过20个字符，不要包含#号）\n"
    "2. 后续内容按逻辑分段整理\n"
    "{text}"
)

_TITLE_SKIP_HINTS = (
    "第一行", "标题", "不要", "要求", "输出", "格式", "不对", "哦不对",
    "总结要点", "本次转写", "要点：", "要点:", "核心主题", "关键要点",
    "以下为", "以下是从",
)

# ─── 从 video_gui.py:7450 原样搬运 _build_article_from_text ───

def _build_article_from_text(raw_text: str) -> str:
    """硬编码清洗原始文本为可读文章段落（video_gui.py:7450 原样搬运）"""
    text = (raw_text or "").strip()
    if not text: return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\f", "\n")
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines: return ""

    def _is_noise_line(ln: str) -> bool:
        if re.fullmatch(r"(第\s*\d+\s*页\s*/\s*共\s*\d+\s*页|第\s*\d+\s*页|\d+\s*/\s*\d+|\d+)", ln): return True
        if re.search(r"(机密|内部资料|仅供学习|版权所有|未经授权|请勿外传)", ln): return True
        return False

    cleaned = []
    for ln in lines:
        ln2 = re.sub(r"^\s*-\s*\[[0-9:\s]+\]\s*", "", ln).strip()
        ln2 = re.sub(r"^\s*\[[0-9:\s]+\]\s*", "", ln2).strip()
        ln2 = re.sub(r"^\s*\d+\s*[.\)]\s*(录音|图片|帖子|内容)\s*[:：]\s*", "", ln2).strip()
        ln2 = re.sub(r"^\s*(录音|图片|帖子|内容)\s*[:：]\s*", "", ln2).strip()
        if not ln2: continue
        if _is_noise_line(ln2): continue
        cleaned.append(ln2)
    if not cleaned: return ""

    norm_counts = {}
    norm_map = []
    for ln in cleaned:
        norm = re.sub(r"\s+", "", ln)
        if 4 <= len(norm) <= 28:
            norm_counts[norm] = norm_counts.get(norm, 0) + 1
        norm_map.append((ln, norm))

    def _is_repeated_header_footer(ln, norm):
        c = norm_counts.get(norm, 0)
        if c < 3: return False
        if re.search(r"[。！？；:：]", ln): return False
        if re.search(r"(20\d{2}|年|月|日|公司|集团|学院|大学|研究院|课程|讲义|PPT)", ln): return False
        return True

    cleaned2 = []
    for ln, norm in norm_map:
        if _is_repeated_header_footer(ln, norm): continue
        cleaned2.append(ln)
    if not cleaned2: return ""

    end_punct = set("。！？；.!?;")

    def _looks_like_title(ln: str) -> bool:
        if len(ln) <= 20 and re.fullmatch(r"(第?\s*[一二三四五六七八九十0-9]+\s*[章节部分].*|[一二三四五六七八九十0-9]+\s*[、.].+)", ln): return True
        if len(ln) <= 14 and re.search(r"(概述|目录|总结|结论|背景|目的|方法|结果|参考|附录)", ln): return True
        return False

    def _is_list_item(ln: str) -> bool:
        return bool(re.match(r"^\s*([-*]|(\d+)[\.\)、]|[（(]?\d+[）)]|[一二三四五六七八九十]+[、.]).+", ln))

    paras = []
    buf = ""
    for ln in cleaned2:
        if not buf:
            buf = ln; continue
        if ln[0] in end_punct or _looks_like_title(ln) or _is_list_item(ln) or _looks_like_title(buf):
            paras.append(buf); buf = ln; continue
        if buf[-1] in end_punct:
            paras.append(buf); buf = ln; continue
        buf += ln
    if buf: paras.append(buf)

    return "\n\n".join(paras)


# ─── 从 video_gui.py:8102 原样搬运 _is_bad_summary_text ───

def _is_bad_summary(s: str) -> bool:
    """检测摘要是否为无效内容（video_gui.py:8102 原样搬运）"""
    ss = (s or "").strip()
    if not ss: return True
    bad_markers = [
        "无意义乱码", "无法完成结构化总结", "无法识别出有效", "请你提供清晰",
        "语句不通顺且逻辑混乱", "无法进行处理", "待总结内容补充提示",
        "未获取到需进行总结提炼", "请补充提供对应的实际内容",
    ]
    if any(m in ss for m in bad_markers): return True
    if len(ss) < 80: return True
    return False


# ─── 从 video_gui.py:7652 原样搬运 _clean_title ───

def clean_title(title: str, platform: str = "") -> str:
    """统一标题清理（video_gui.py:7652 原样搬运）"""
    if not title: return "未命名文档"
    if platform == "小红书":
        title = re.sub(r' - 小红书$', '', title)
    if platform == "抖音":
        title = re.sub(r'\s*[-–—]\s*抖音.*$', '', title)
    title = re.sub(r'[\\/:*?"<>|&]', '', title)
    title = re.sub(r'^[\d\.\s]+', '', title)
    return title[:50].strip()


# ─── 从 video_gui.py:11508 原样搬运 extract_title_from_summary ───

def _is_bad_title_candidate(line: str) -> bool:
    s = (line or "").strip()
    if not s or len(s) <= 3:
        return True
    if any(h in s for h in _TITLE_SKIP_HINTS):
        return True
    if s.endswith(("：", ":", "，", ",")):
        return True
    if re.match(r"^#{2,}\s", s):
        return True
    return False


def _normalize_extracted_title(raw: str) -> str:
    title = clean_title(raw)
    title = re.sub(r'[\\/:*?"<>|]', "", title)
    title = re.sub(r"^[\d\.\s]+", "", title)
    title = title.rstrip("：:，,").strip()
    return title[:20].strip().replace(" ", "_")


def extract_title_from_summary(summary: str, link: str = "", log_cb=None) -> str:
    """从AI摘要中提取标题（video_gui.py:11508 原样搬运）"""
    try:
        if summary:
            lines = summary.split('\n')
            # 优先：摘要首段一级标题 # xxx
            for line in lines:
                line = line.strip()
                if not line.startswith("#"):
                    continue
                if line.startswith("##"):
                    continue
                title = _normalize_extracted_title(line.lstrip("#").strip())
                if title and not _is_bad_title_candidate(title):
                    if log_cb:
                        log_cb(f"从AI摘要中提取标题：{title}")
                    return title
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if len(line) <= 5 or _is_bad_title_candidate(line):
                    continue
                title = _normalize_extracted_title(line)
                if title and title != "_" and len(title) > 3:
                    if log_cb:
                        log_cb(f"从AI摘要中提取标题：{title}")
                    return title

        # 从链接提取
        bv = re.search(r'BV[0-9A-Za-z]{10}', link or "")
        if bv:
            if log_cb: log_cb(f"从链接提取BV号：{bv.group(0)}")
            return bv.group(0)
        num = re.search(r'\d+', (link or "").split('/')[-1])
        if num:
            if log_cb: log_cb(f"从链接提取数字：{num.group(0)}")
            return num.group(0)
    except Exception:
        pass
    return "内容分析"


# ─── 从 video_gui.py:7672 原样搬运 _get_article_text ───

def get_article_text(result_data: Dict) -> str:
    """统一正文获取（video_gui.py:7672 原样搬运）"""
    article = (result_data.get('article') or '').strip()
    if not article:
        raw = (result_data.get('transcript') or result_data.get('full_text') or '').strip()
        article = _build_article_from_text(raw) if raw else ''
        if article:
            result_data['article'] = article
    return article


# ─── 核心：_run_document_consolidation（video_gui.py:7843 原样搬运） ───

def run_document_consolidation(
    text: str,
    llm_cfg: Dict,
    user_prompt: str = "",
    stage_label: str = "文档沉淀",
    summary_after_article: bool = True,
    skip_summary: bool = False,
    comments_text: str = "",
    log_cb: Callable = None,
    ops_cb: Callable = None,  # 运维Agent回调：(link, error_message, stage, error_type) -> None
) -> Dict:
    """
    统一文档沉淀（video_gui.py:7843 原样搬运）：
    - 输入：原始文本；可选 comments_text（整理后正文 + 评论一并送入摘要 Agent）
    - 输出：{"ai_summary": "...", "article": "...", "article_source": "llm_polish|heuristic_fallback"}
    """
    task_id = (llm_cfg.get("_task_id") or stage_label or "doc").strip()
    chain = llm_cfg.get("_log_chain") or "链接沉淀文档-文档沉淀"
    log_module = "document_consolidation.run_document_consolidation"
    log_obj = (stage_label or "文档沉淀")[:80]

    def log(msg, level="INFO"):
        if log_cb:
            log_cb(msg, level)

    def _plog(phase: str, action: str, level: str = "INFO", **kw):
        pipeline_log(
            task_id, chain, log_module, log_obj, phase, "Agent执行", action, level,
            log_cb=log_cb, **kw,
        )

    llm_cfg = enrich_pipeline_llm_cfg(llm_cfg)
    llm_meta: Dict = {"summary": {}, "article": {}}

    # 视频转写二次门禁（图文/OCR 路径不受影响）
    if "视频" in (stage_label or ""):
        from .transcribe_quality import assess_transcript

        gate = assess_transcript((text or "").strip())
        if not gate.ok:
            _plog(
                "转写门禁",
                "阻断原文整理",
                "ERROR",
                error_code=gate.error_code,
                char_len=gate.char_len,
                repetition_ratio=gate.repetition_ratio,
            )
            return {
                "ai_summary": "",
                "article": "",
                "article_source": "blocked",
                "error_code": gate.error_code,
                "error_message": gate.error_message,
                "transcribe_degraded": gate.transcribe_degraded,
            }

    def _render_summary_prompt(summary_prompt: str, body: str) -> str:
        body = (body or "").strip()
        prompt_tpl = (summary_prompt or _DEFAULT_SUMMARY_PROMPT).strip()
        try:
            rendered = prompt_tpl.format(
                text=body,
                transcript=body,
                raw_text=body,
                article=body,
            )
        except Exception:
            rendered = (
                prompt_tpl
                .replace("{text}", body)
                .replace("{transcript}", body)
                .replace("{raw_text}", body)
                .replace("{article}", body)
            )
        if body and body not in rendered:
            rendered = (rendered.rstrip() + "\n\n" + body).strip()
        return rendered

    def call_llm(
        messages,
        temperature=0.3,
        max_tokens=4096,
        *,
        role: str,
        routes: Dict,
    ) -> str:
        agent_name = (routes.get("agent_name") or "").strip()
        if not agent_name:
            agent_name = "summary_agent" if "摘要" in role else "doc_standardize_agent"
        task_type = (routes.get("task_type") or "summary").strip()
        timeout_sec = float(routes.get("timeout_sec") or 150.0)
        backup = (routes.get("backup_endpoint") or "").strip()
        primary = (routes.get("primary_endpoint") or "").strip()
        retry_indices = [0, 1] if backup and backup != primary else [0]
        primary_failed_detail: Optional[str] = None
        last_err = ""

        for idx in retry_indices:
            label = "主" if idx == 0 else "备"
            t0 = time.time()
            ret = invoke_llm_via_gateway(
                llm_cfg,
                agent_name=agent_name,
                task_type=task_type,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_sec=timeout_sec,
                retry_index=idx,
            )
            elapsed = int((time.time() - t0) * 1000)
            out = (ret.get("text") or "").strip()
            used_model = (ret.get("model") or primary or "").strip()
            routes_out = {**routes, "primary_endpoint": used_model or routes.get("primary_endpoint")}

            if ret.get("ok") and out:
                log_llm_done(
                    task_id, chain, log_module, log_obj,
                    role=role, routes=routes_out, ok=True,
                    out_len=len(out), elapsed_ms=elapsed,
                )
                if label == "备" and primary_failed_detail:
                    try:
                        from .ops_hooks import ops_dispatch_volcengine_degraded
                        ops_dispatch_volcengine_degraded(
                            primary_failed_detail, primary, used_model
                        )
                    except Exception:
                        pass
                return out

            last_err = str(ret.get("error") or "gateway_invoke_failed")
            log_llm_done(
                task_id, chain, log_module, log_obj,
                role=role, routes=routes_out, ok=False, elapsed_ms=elapsed, error=last_err,
            )
            if label == "主":
                primary_failed_detail = last_err
            log(f"[{stage_label}] {role} 网关调用失败({label}): {last_err}", "WARNING")

        if last_err in ("no_active_model", "") and not primary:
            _plog(
                "调用",
                f"{role} 网关无可用模型（请检查 api_gateway_nodes / agent_route_rules）",
                "WARNING",
                agent_name=agent_name,
                task_type=task_type,
            )
        else:
            _plog(
                "调用",
                f"{role} 网关调用失败",
                "WARNING",
                agent_name=agent_name,
                error=last_err[:200],
            )
        return ""

    def _parse_llm_with_retry(
        raw_out: str,
        *,
        role: str,
        required_keys: Sequence[str],
        build_messages_for_retry,
        plain_text_fallback: Callable[[str], str],
    ) -> Tuple[str, Dict[str, Any]]:
        """解析 JSON；失败则自动重试，仍失败则降级为纯文本。"""
        meta: Dict[str, Any] = {"json_parse": {}, "json_retries": 0}
        last_code = ""
        for attempt in range(_LLM_JSON_MAX_RETRY + 1):
            parsed = parse_llm_json_object(raw_out, required_keys=required_keys)
            meta["json_parse"] = {
                "ok": parsed.ok,
                "error_code": parsed.error_code,
                "repaired": parsed.repaired,
                "preview": parsed.raw_preview,
                "attempt": attempt,
            }
            if parsed.ok:
                status, payload = parse_agent_status(parsed.data)
                if status == "reject":
                    raise LLMInputRejectedError(
                        str(payload.get("reject_code") or "LLM_INPUT_REJECTED"),
                        str(payload.get("reject_reason") or f"{role} 拒答：输入不足以生成有效输出"),
                        reject_reason=str(payload.get("reject_reason") or ""),
                    )
                if role == "原文整理Agent":
                    art = normalize_llm_string_escapes(str(parsed.data.get("article") or ""))
                    if not art.strip():
                        raise LLMInputRejectedError(
                            "LLM_INPUT_REJECTED",
                            "原文整理返回空 article",
                        )
                    return art, meta
                title = normalize_llm_string_escapes(str(parsed.data.get("title") or "").strip())
                summary = normalize_llm_string_escapes(str(parsed.data.get("summary") or "").strip())
                if title and summary:
                    return f"{title}\n\n{summary}", meta
                if summary:
                    return summary, meta
            last_code = parsed.error_code or LLM_JSON_PARSE_FAILED
            if attempt >= _LLM_JSON_MAX_RETRY:
                break
            meta["json_retries"] = attempt + 1
            _plog(
                "JSON解析",
                f"{role} JSON 失败，自动重试",
                "WARNING",
                error_code=last_code,
                preview=parsed.raw_preview,
                attempt=attempt + 1,
            )
            retry_messages = build_messages_for_retry(last_code)
            retry_out = call_llm(
                retry_messages,
                temperature=0.2,
                max_tokens=8192 if "摘要" in role else 4096,
                role=f"{role}(JSON重试{attempt + 1})",
                routes=llm_meta.get("summary" if "摘要" in role else "article", {}),
            )
            raw_out = retry_out or raw_out

        _plog(
            "JSON解析",
            f"{role} JSON 修补/重试仍失败，降级纯文本",
            "WARNING",
            error_code=last_code,
        )
        return normalize_llm_string_escapes(plain_text_fallback(raw_out)), meta

    def do_summarize(input_text: str, is_retry: bool = False) -> str:
        role_label = "摘要Agent" + ("(二次)" if is_retry else "")
        routes = log_llm_prepare(
            task_id, chain, log_module, log_obj,
            role=role_label,
            text_len=len(input_text or ""),
            cfg=llm_cfg,
            agent_name="summary_agent",
            task_type="summary",
        )
        body = (input_text or "").strip()
        timeout_sec, timeout_desc = llm_timeout_for_text_len(len(body))
        routes = {**routes, "timeout_sec": timeout_sec}
        llm_meta["summary"] = {**routes, "is_retry": is_retry}
        log(f"[{stage_label}] 摘要输入 {len(body)} 字符，超时 {timeout_desc}", "INFO")
        try:
            system_prompt = (llm_cfg.get("system_prompt") or "你是一个专业的内容分析助手。").strip()
            rules = (llm_cfg.get("rules") or "").strip() + _JSON_OUTPUT_RULE_SUMMARY
            if is_retry:
                rendered = (
                    "请对以下文本进行高质量中文总结与要点提炼。\n"
                    "若输入仍不足以忠实摘要，必须输出 status=reject，禁止编造。\n\n"
                    + body
                    + input_stats_block(len(body))
                )
            else:
                summary_prompt = (llm_cfg.get("summary_prompt") or _DEFAULT_SUMMARY_PROMPT).strip()
                rendered = _render_summary_prompt(summary_prompt, body) + input_stats_block(len(body))
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": (f"分析规则：\n{rules}\n\n{rendered}").strip()},
            ]
            up = (user_prompt or llm_cfg.get("user_prompt") or "").strip()
            if up:
                messages.append({"role": "user", "content": up})
            out = call_llm(
                messages,
                temperature=0.3,
                max_tokens=8192,
                role=role_label,
                routes=routes,
            )
            if not out:
                return ""

            def _build_retry_messages(code: str):
                retry_msgs = [dict(m) for m in messages]
                suffix = build_json_retry_user_suffix(code, ("title", "summary"))
                retry_msgs[-1] = {
                    "role": "user",
                    "content": (retry_msgs[-1].get("content") or "") + suffix,
                }
                return retry_msgs

            parsed_text, json_meta = _parse_llm_with_retry(
                out,
                role=role_label,
                required_keys=("title", "summary"),
                build_messages_for_retry=_build_retry_messages,
                plain_text_fallback=lambda raw: (raw or "").strip(),
            )
            llm_meta.setdefault("summary_json", json_meta)
            return parsed_text
        except LLMInputRejectedError:
            raise
        except Exception as e:
            log(f"[{stage_label}] 摘要异常：{e}", "WARNING")
            log_llm_done(task_id, chain, log_module, log_obj, role=role_label, routes=routes, ok=False, error=str(e))
            return ""

    def do_article(raw: str) -> str:
        routes = log_llm_prepare(
            task_id, chain, log_module, log_obj,
            role="原文整理Agent",
            text_len=len(raw or ""),
            cfg=llm_cfg,
            agent_name="doc_standardize_agent",
            task_type="summary",
        )
        llm_meta["article"] = routes
        try:
            polish_prompt = llm_cfg.get("article_polish_prompt", "")
            article_system = llm_cfg.get("article_system_prompt", "你是一个专业的内容整理助手。")
            article_rules = (llm_cfg.get("article_rules", "") or "").strip() + _JSON_OUTPUT_RULE_ARTICLE
            if not polish_prompt:
                _plog("原文整理", "未配置 article_polish_prompt，使用启发式清洗", source="heuristic_fallback")
                return _build_article_from_text(raw)

            prompt = polish_prompt.replace("{transcript}", raw) + input_stats_block(len(raw or ""))
            messages = [
                {"role": "system", "content": article_system},
                {"role": "user", "content": (article_rules + "\n\n" + prompt) if article_rules else prompt},
            ]
            result = call_llm(messages, temperature=0.3, max_tokens=4096, role="原文整理Agent", routes=routes)
            if not result:
                return _build_article_from_text(raw)

            def _build_retry_messages(code: str):
                retry_msgs = [dict(m) for m in messages]
                suffix = build_json_retry_user_suffix(code, ("article",))
                retry_msgs[-1] = {
                    "role": "user",
                    "content": (retry_msgs[-1].get("content") or "") + suffix,
                }
                return retry_msgs

            parsed_text, json_meta = _parse_llm_with_retry(
                result,
                role="原文整理Agent",
                required_keys=("article",),
                build_messages_for_retry=_build_retry_messages,
                plain_text_fallback=lambda raw: _build_article_from_text(raw or raw_text),
            )
            llm_meta.setdefault("article_json", json_meta)
            return parsed_text if parsed_text else _build_article_from_text(raw)
        except LLMInputRejectedError:
            raise
        except Exception as e:
            log(f"[{stage_label}] 原文整理异常：{e}", "WARNING")
            log_llm_done(task_id, chain, log_module, log_obj, role="原文整理Agent", routes=routes, ok=False, error=str(e))
            return _build_article_from_text(raw)

    ai_summary = ""
    article_text = ""
    raw_text = (text or "").strip()
    comments_raw = (comments_text or "").strip()
    has_comments = bool(comments_raw)
    comments_block = ""
    comments_viewpoint = ""
    comments_summary_mode = "none"

    # LLM 沉淀前校验（空/占位/junk 禁止进入 Agent）
    from .pipeline_output_quality import assess_consolidation_input

    in_gate = assess_consolidation_input(raw_text, stage_label=stage_label)
    if not in_gate.ok:
        _plog(
            "入口",
            "文档沉淀前校验失败",
            "ERROR",
            error_code=in_gate.error_code,
            text_len=in_gate.text_len,
        )
        return {
            "ai_summary": "",
            "article": "",
            "article_source": "blocked",
            "error_code": in_gate.error_code,
            "error_message": in_gate.error_message,
            "comments_viewpoint": "",
            "comments_summary_mode": "none",
            "llm_meta": llm_meta,
            "extracted_metadata": {},
        }

    if has_comments and not summary_after_article:
        log(f"[{stage_label}] 已抓取评论，摘要将与整理后正文合并（强制顺序摘要）", "INFO")
        summary_after_article = True

    def _prepare_comments_block(article_ctx: str) -> None:
        nonlocal comments_block, comments_viewpoint, comments_summary_mode
        if not has_comments:
            return
        comments_user = (llm_cfg.get("comments_user_prompt") or "").strip()
        comments_block, comments_viewpoint, comments_summary_mode = prepare_comments_for_summary(
            comments_raw,
            article_context=article_ctx,
            llm_cfg=llm_cfg,
            comments_user_prompt=comments_user,
            log_cb=log_cb,
            stage_label=stage_label,
        )

    _plog(
        "入口",
        "文档沉淀开始",
        text_len=len(raw_text),
        summary_after_article=summary_after_article,
        skip_summary=skip_summary,
        comments_len=len(comments_raw),
    )

    if skip_summary:
        log(f"[{stage_label}] 仅原文整理模式（跳过摘要 Agent）")
        try:
            article_text = do_article(raw_text)
        except LLMInputRejectedError as rej:
            _plog("出口", "原文整理拒答", "ERROR", error_code=rej.error_code, reason=rej.message)
            return {
                "ai_summary": "",
                "article": "",
                "article_source": "blocked",
                "error_code": rej.error_code,
                "error_message": rej.message,
                "comments_viewpoint": "",
                "comments_summary_mode": "none",
                "llm_meta": llm_meta,
                "extracted_metadata": {},
            }
        article_source = "llm_polish"
        if not (article_text and article_text.strip()):
            article_source = "heuristic_fallback"
            article_text = raw_text
        _plog(
            "出口",
            "文档沉淀完成（仅原文）",
            ai_summary_len=0,
            article_len=len(article_text or ""),
            article_source=article_source,
        )
        return {
            "ai_summary": "",
            "article": article_text,
            "article_source": article_source,
            "comments_viewpoint": "",
            "comments_summary_mode": "none",
            "llm_meta": llm_meta,
            "extracted_metadata": {},
        }

    try:
        if summary_after_article:
            log(f"[{stage_label}] 顺序执行：先原文整理，再评论观点（可选），再摘要" + ("（含评论）" if has_comments else ""))
            article_text = do_article(raw_text)
            _prepare_comments_block(article_text or raw_text)
            summary_input = compose_summary_input(article_text or raw_text, comments_block)
            ai_summary = do_summarize(summary_input)
        else:
            log(f"[{stage_label}] 并发：摘要 + 原文整理（共享 LLM 池）")
            _prepare_comments_block(raw_text)
            llm_ex = get_llm_executor()
            merged_raw = compose_summary_input(raw_text, comments_block)
            f_s = llm_ex.submit(lambda: do_summarize(merged_raw))
            f_a = llm_ex.submit(do_article, raw_text)
            concurrent.futures.wait([f_s, f_a], timeout=300)
            ai_summary = f_s.result() or ""
            article_text = f_a.result() or ""
    except LLMInputRejectedError as rej:
        _plog("出口", "LLM Agent 拒答", "ERROR", error_code=rej.error_code, reason=rej.message)
        return {
            "ai_summary": "",
            "article": "",
            "article_source": "blocked",
            "error_code": rej.error_code,
            "error_message": rej.message,
            "comments_viewpoint": comments_viewpoint,
            "comments_summary_mode": comments_summary_mode,
            "llm_meta": llm_meta,
            "extracted_metadata": {},
        }

    article_source = "llm_polish"
    if not (article_text and article_text.strip()):
        article_source = "heuristic_fallback"
        article_text = raw_text
    else:
        # 质量告警
        raw_len = len(raw_text)
        art_len = len((article_text or "").strip())
        min_reasonable = max(120, int(raw_len * 0.35)) if raw_len > 0 else 120
        if raw_len >= 300 and art_len < min_reasonable:
            log(f"[{stage_label}] 原文整理结果疑似过短（raw={raw_len}, article={art_len}），触发运维质检", "WARNING")
            if ops_cb:
                try:
                    ops_cb(
                        link=stage_label,
                        error_message=f"原文整理结果疑似过短（raw={raw_len}, article={art_len}）",
                        stage="ai_analysis",
                        error_type="ArticleQualityGateWarn",
                    )
                except Exception:
                    pass

    # 摘要质量门禁
    if _is_bad_summary(ai_summary):
        log(f"[{stage_label}] 摘要结果不合格，触发二次摘要", "WARNING")
        retry_input = compose_summary_input(article_text or raw_text, comments_block)
        retry = do_summarize(retry_input, is_retry=True)
        if retry and not _is_bad_summary(retry):
            ai_summary = retry
        else:
            log(f"[{stage_label}] 二次摘要仍不合格，触发运维告警", "WARNING")
            if ops_cb:
                try:
                    ops_cb(
                        link=stage_label,
                        error_message=f"二次摘要仍不合格（raw={raw_len}）",
                        stage="ai_analysis",
                        error_type="SummaryQualityGateFail",
                    )
                except Exception:
                    pass
            # 二次摘要仍不合格：不再用原文截取冒充摘要，直接阻断
            if not ai_summary or _is_bad_summary(ai_summary):
                from .pipeline_output_quality import LLM_INPUT_REJECTED

                _plog("出口", "摘要质量门禁失败", "ERROR", error_code=LLM_INPUT_REJECTED)
                return {
                    "ai_summary": "",
                    "article": article_text,
                    "article_source": "blocked",
                    "error_code": LLM_INPUT_REJECTED,
                    "error_message": "摘要结果不合格且二次摘要仍失败，禁止降级为原文截取",
                    "comments_viewpoint": comments_viewpoint,
                    "comments_summary_mode": comments_summary_mode,
                    "llm_meta": llm_meta,
                    "extracted_metadata": {},
                }

    _plog(
        "出口",
        "文档沉淀完成",
        ai_summary_len=len(ai_summary or ""),
        article_len=len(article_text or ""),
        article_source=article_source,
        summary_model=llm_meta.get("summary", {}).get("primary_endpoint", ""),
        article_model=llm_meta.get("article", {}).get("primary_endpoint", ""),
    )

    extracted_metadata: Dict = {}
    try:
        from .link_meta_extract import extract_link_metadata, get_meta_extract_config

        meta_cfg = get_meta_extract_config(llm_cfg)
        if meta_cfg.get("enabled") and meta_cfg.get("fields"):
            extracted_metadata = extract_link_metadata(
                body_text=article_text or raw_text,
                summary_text=ai_summary or "",
                llm_cfg=llm_cfg,
                fields=meta_cfg.get("fields") or [],
                task_note=str(llm_cfg.get("_task_note") or ""),
                task_keywords=str(llm_cfg.get("_task_keywords") or ""),
                log_cb=log_cb,
            )
            if extracted_metadata:
                _plog("元数据", "结构化元数据提取完成", field_count=len(extracted_metadata))
    except Exception as ex:
        log(f"[{stage_label}] 元数据提取异常：{ex}", "WARNING")

    return {
        "ai_summary": ai_summary,
        "article": article_text,
        "article_source": article_source,
        "comments_viewpoint": comments_viewpoint,
        "comments_summary_mode": comments_summary_mode,
        "llm_meta": llm_meta,
        "extracted_metadata": extracted_metadata,
    }
