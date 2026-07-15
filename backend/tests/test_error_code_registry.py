"""标准错误码注册表测试。"""
import sys
from pathlib import Path

_AGENT = None
for _p in Path(__file__).resolve().parents:
    _cand = _p / "src" / "agent"
    if _cand.is_dir():
        _AGENT = _cand.resolve()
        break
if _AGENT and str(_AGENT) not in sys.path:
    sys.path.insert(0, str(_AGENT))

from error_code_registry import (  # noqa: E402
    ERROR_MODULES,
    T1001,
    classify_by_message,
    extract_error_code_from_text,
    is_standard_error_code,
    resolve_error_code,
)


def test_module_letters_defined():
    assert "K" in ERROR_MODULES
    assert "T" in ERROR_MODULES
    assert ERROR_MODULES["T"]["name"] == "语音转写"


def test_resolve_legacy_transcribe_ffmpeg():
    assert resolve_error_code("transcribe_ffmpeg_missing") == T1001
    assert resolve_error_code("TRANSCRIBE_FFMPEG_MISSING") == T1001


def test_extract_from_log_line():
    msg = "[转写失败] T1001: 找不到 ffmpeg/ffprobe，Whisper 无法解析视频音轨"
    assert extract_error_code_from_text(msg) == T1001


def test_extract_legacy_log_still_works():
    msg = "[转写失败] transcribe_ffmpeg_missing: 找不到 ffmpeg/ffprobe，Whisper 无法解析视频音轨"
    assert extract_error_code_from_text(msg) == T1001


def test_classify_by_fixed_message():
    cls = classify_by_message("找不到 ffmpeg/ffprobe，Whisper 无法解析视频音轨", stage="transcribe")
    assert cls["error_code"] == T1001
    assert cls["module"] == "T"
    assert is_standard_error_code(cls["error_code"])


def test_pipe_alias_resolves():
    assert resolve_error_code("PIPE_INVALID_INPUT_EMPTY") == "P1001"


def test_classify_torch_dll_error():
    msg = "ImportError: DLL load failed while importing _C: 找不到指定的模块。"
    cls = classify_by_message(msg, stage="transcribe", hint_code="T1001")
    assert cls["error_code"] == "T1012"
    assert cls["match_source"] == "pattern_message"


def test_resolve_legacy_torch_dll():
    assert resolve_error_code("transcribe_torch_dll_failed") == "T1012"


def test_get_error_remediation_t1012():
    from error_code_registry import get_error_remediation

    remedy = get_error_remediation("T1012")
    assert remedy["error_code"] == "T1012"
    assert "fix_torch_env" in remedy["remediation_md"]
    assert len(remedy["remediation"]) >= 3
