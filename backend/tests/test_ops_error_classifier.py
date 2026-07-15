"""运维失败类型标准化提取（标准码 T1001 等）。"""
from app.services.ops_error_classifier import (
    classify_task_failure,
    extract_error_code,
    normalize_error_info,
)
from app.services.ops_exception_proposals import get_proposal_for_error


def test_extract_transcribe_ffmpeg_from_log_line():
    msg = "[转写失败] T1001: 找不到 ffmpeg/ffprobe，Whisper 无法解析视频音轨"
    assert extract_error_code(msg) == "T1001"


def test_extract_legacy_transcribe_code():
    msg = "[转写失败] transcribe_ffmpeg_missing: 找不到 ffmpeg/ffprobe，Whisper 无法解析视频音轨"
    assert extract_error_code(msg) == "T1001"


def test_classify_ffmpeg_missing_by_fixed_msg():
    cls = classify_task_failure(
        error_message="找不到 ffmpeg/ffprobe，Whisper 无法解析视频音轨",
        stage="transcribe",
    )
    assert cls["error_code"] == "T1001"
    assert cls["error_message"] == "找不到 ffmpeg/ffprobe，Whisper 无法解析视频音轨"
    assert cls["module"] == "T"
    assert cls["match_source"] in ("catalog_msg", "pattern", "explicit_code", "pattern_message")


def test_classify_prefers_transcribe_error_code_on_task():
    cls = classify_task_failure(
        error_message="some wrapper",
        task={"transcribe_error_code": "T1001", "error": "wrapper"},
        stage="transcribe",
    )
    assert cls["error_code"] == "T1001"


def test_normalize_error_info_fields():
    err = normalize_error_info(
        {"message": "找不到 ffmpeg/ffprobe，Whisper 无法解析视频音轨"},
        stage="transcribe",
    )
    assert err["error_code"] == "T1001"
    assert err["failure_module"] == "T"
    assert "T1001" in err["failure_summary"]


def test_proposal_for_ffmpeg_missing():
    prop = get_proposal_for_error(
        "找不到 ffmpeg/ffprobe，Whisper 无法解析视频音轨",
        context="transcribe",
    )
    assert prop["ok"] is True
    assert prop.get("classification", {}).get("error_code") == "T1001"
    primary = prop.get("primary") or {}
    assert primary.get("code") == "T1001"
