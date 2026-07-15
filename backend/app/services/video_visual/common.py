"""视频视觉提取公共：下载、OCR、时间轴合并、Whisper 音频通道。"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_log = logging.getLogger("sba.video_visual.common")

WORK_DIR = Path(tempfile.gettempdir()) / "sba_video_visual"
WORK_DIR.mkdir(parents=True, exist_ok=True)


def _log_event(module: str, action: str, **kv: Any) -> None:
    parts = "; ".join(f"{k}={v}" for k, v in kv.items())
    _log.info(
        "[链接沉淀文档-视频视觉提取|%s|video_visual|硬编执行|处理] %s; %s",
        module,
        action,
        parts,
    )


def _link_is_xhs(link: str) -> bool:
    return "xiaohongshu.com" in (link or "")


def _write_netscape_cookies(cookies: Dict[str, str], path: Path) -> None:
  """将 JSON cookie 字典写成 yt-dlp 可用的 Netscape 格式。"""
  lines = ["# Netscape HTTP Cookie File", ""]
  exp = str(int(time.time()) + 86400 * 30)
  for name, value in cookies.items():
    if not name or value is None:
      continue
    lines.append(
      f".xiaohongshu.com\tTRUE\t/\tFALSE\t{exp}\t{name}\t{value}"
    )
  path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prepare_xhs_ytdlp_cookies() -> Optional[str]:
  try:
    from app.services.xhs_local_browser import ensure_xhs_cookies_synced
    from app.services.cookie_manager import load_cookies

    ensure_xhs_cookies_synced()
    ck = load_cookies("xiaohongshu") or {}
    if not ck:
      return None
    out = WORK_DIR / f"xhs_ytdlp_{int(time.time())}.txt"
    _write_netscape_cookies(ck, out)
    return str(out)
  except Exception as exc:
    _log.warning(
      "[链接沉淀文档-视频视觉提取|common._prepare_xhs_ytdlp_cookies|cookie|硬编执行|失败] err=%s",
      exc,
    )
    return None


def download_video_for_visual(link: str, log: Optional[Callable[[str, str], None]] = None) -> Optional[str]:
  """下载视频；小红书链接注入磁盘 Cookie 后走 yt-dlp。"""
  link = (link or "").strip()
  if not link:
    return None

  def _cb(msg: str, level: str = "INFO") -> None:
    if log:
      log(msg, level)

  cookie_file: Optional[str] = None
  if _link_is_xhs(link):
    cookie_file = _prepare_xhs_ytdlp_cookies()
    if cookie_file:
      _cb(f"已写入小红书 yt-dlp Cookie: {cookie_file}")

  if cookie_file and _link_is_xhs(link):
    from video_downloader import VIDEO_DIR, extract_clean_url

    clean = extract_clean_url(link)
    ts = int(time.time() * 1000)
    out = VIDEO_DIR / f"vv-{ts}-xhs.mp4"
    cmd = [
      "yt-dlp",
      "--user-agent",
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
      "--referer",
      "https://www.xiaohongshu.com/",
      "--cookies",
      cookie_file,
      "--no-check-certificate",
      "--format",
      "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
      "--merge-output-format",
      "mp4",
      "-o",
      str(out),
      clean,
    ]
    _log_event("common.download_video_for_visual", "yt-dlp_xhs", link=clean[:80])
    try:
      r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")
      if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
        _cb(f"下载成功: {out}")
        return str(out)
      _cb(f"yt-dlp 失败: {(r.stderr or r.stdout or '')[:400]}", "WARNING")
    except Exception as exc:
      _cb(f"yt-dlp 异常: {exc}", "ERROR")

  from video_downloader import download_video

  return download_video(link, log_callback=_cb)


def probe_video_duration_sec(video_path: str) -> float:
  ffprobe = "ffprobe"
  try:
    import sys
    from pathlib import Path as _P

    for parent in _P(__file__).resolve().parents:
      cand = parent / "src" / "agent"
      if cand.is_dir() and str(cand) not in sys.path:
        sys.path.insert(0, str(cand))
        break
    from ffmpeg_path import get_ffmpeg_executables  # type: ignore

    _, fp = get_ffmpeg_executables()
    if fp:
      ffprobe = fp
  except Exception:
    pass
  try:
    r = subprocess.run(
      [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
      ],
      capture_output=True,
      text=True,
      timeout=30,
    )
    return max(0.0, float((r.stdout or "0").strip() or 0))
  except Exception:
    return 0.0


def ocr_image_path(image_path: str) -> str:
  """视频帧 OCR：优先 Tesseract（快），结果过短再走百度 OCR。"""
  try:
    import pytesseract
    from PIL import Image

    img = Image.open(image_path)
    txt = pytesseract.image_to_string(img, lang="chi_sim+eng")
    txt = re.sub(r"\s+", " ", (txt or "").strip())
    if len(txt) >= 6:
      return txt
  except Exception as exc:
    _log.debug("pytesseract OCR 失败: %s", exc)

  try:
    from app.services.doc_image_pipeline import _ocr_image

    txt = (_ocr_image(image_path) or "").strip()
    if txt:
      return re.sub(r"\s+", " ", txt)
  except Exception as exc:
    _log.warning("百度/MinerU OCR 失败: %s", exc)
  return ""


_WHISPER_PROMPT_ECHO_MARKERS = (
  "请使用标准简体中文进行转写",
  "请使用标准普通话",
  "保持语句通顺",
  "不要遗漏任何内容",
  "精密转写",
)


def _is_whisper_prompt_echo(text: str) -> bool:
  t = (text or "").strip()
  if not t or len(t) > 200:
    return False
  hits = sum(1 for m in _WHISPER_PROMPT_ECHO_MARKERS if m in t)
  return hits >= 2 or (hits >= 1 and len(t) < 100)


def _text_similar(a: str, b: str) -> float:
  a = re.sub(r"\s+", "", (a or "").strip())
  b = re.sub(r"\s+", "", (b or "").strip())
  if not a or not b:
    return 0.0
  return SequenceMatcher(None, a, b).ratio()


def dedupe_text_segments(
  segments: List[Dict[str, Any]],
  *,
  min_chars: int = 2,
  sim_threshold: float = 0.82,
) -> List[Dict[str, Any]]:
  """按时间顺序去重 OCR 片段。"""
  out: List[Dict[str, Any]] = []
  for seg in sorted(segments, key=lambda x: float(x.get("time_sec") or 0)):
    text = re.sub(r"\s+", " ", str(seg.get("text") or "").strip())
    if len(text) < min_chars:
      continue
    if out and _text_similar(text, str(out[-1].get("text") or "")) >= sim_threshold:
      continue
    out.append({**seg, "text": text})
  return out


def merge_transcript_plain(
  *,
  audio_text: str,
  visual_segments: List[Dict[str, Any]],
  title: str = "",
) -> str:
  """合并音频转写与画面 OCR，输出原文生文（不做摘要）。"""
  lines: List[str] = []
  if title:
    lines.append(f"# {title.strip()}")
    lines.append("")
  audio = (audio_text or "").strip()
  if audio:
    lines.append("## 音频转写")
    lines.append(audio)
    lines.append("")
  vis = dedupe_text_segments(visual_segments)
  if vis and not audio:
    # 纯画面课件：直接输出正文，不加「画面文字」小标题
    for seg in vis:
      lines.append(seg.get("text", ""))
    return "\n\n".join(l for l in lines if l).strip()
  if vis:
    lines.append("## 画面文字")
    for seg in vis:
      t = float(seg.get("time_sec") or 0)
      mm = int(t // 60)
      ss = int(t % 60)
      lines.append(f"[{mm:02d}:{ss:02d}] {seg.get('text', '')}")
    lines.append("")
  body = "\n".join(lines).strip()
  if not body:
    return audio or "\n".join(s.get("text", "") for s in vis)
  return body


def transcribe_audio(video_path: str) -> Tuple[str, Dict[str, Any]]:
  from app.services.transcribe_quality import assess_transcript, sanitize_transcript_for_pipeline
  from app.services.video_pipeline import invoke_speech_to_text

  result = invoke_speech_to_text(video_path, strict=False) or {}
  if isinstance(result, dict) and result.get("ok") is False:
    return "", result
  text = (
    (result.get("full_text") if isinstance(result, dict) else "")
    or (result.get("transcript") if isinstance(result, dict) else "")
    or ""
  ).strip()
  if text:
    text, _ = sanitize_transcript_for_pipeline(text)
  meta = result if isinstance(result, dict) else {}
  if _is_whisper_prompt_echo(text):
    _log.info(
      "[链接沉淀文档-视频视觉提取|common.transcribe_audio|whisper|硬编执行|降级] 提示词幻听，丢弃音频轨"
    )
    return "", {**meta, "transcribe_degraded": True, "reason": "prompt_echo"}
  if text:
    assessment = assess_transcript(
      text,
      transcribe_source=str(meta.get("transcribe_source") or ""),
      transcript_meta=meta,
    )
    if not assessment.ok and len(text) < 120:
      return "", {**meta, "transcribe_degraded": True, "reason": assessment.error_code}
  return text, meta


def extract_frames_to_dir(video_path: str, out_dir: Path, vf_filter: str, max_frames: int = 100) -> List[Tuple[float, Path]]:
  """通用 ffmpeg 抽帧，解析 showinfo 得 PTS。"""
  out_dir.mkdir(parents=True, exist_ok=True)
  pattern = str(out_dir / "frame_%04d.jpg")
  cmd = [
    "ffmpeg",
    "-hide_banner",
    "-loglevel",
    "info",
    "-i",
    video_path,
    "-vf",
    vf_filter,
    "-vsync",
    "vfr",
    "-frames:v",
    str(max_frames),
    "-q:v",
    "2",
    pattern,
  ]
  r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")
  stderr = r.stderr or ""
  times: List[float] = []
  for line in stderr.splitlines():
    m = re.search(r"pts_time:([0-9.]+)", line)
    if m:
      times.append(float(m.group(1)))
  files = sorted(out_dir.glob("frame_*.jpg"))
  pairs: List[Tuple[float, Path]] = []
  for i, fp in enumerate(files):
    t = times[i] if i < len(times) else float(i)
    pairs.append((t, fp))
  return pairs
