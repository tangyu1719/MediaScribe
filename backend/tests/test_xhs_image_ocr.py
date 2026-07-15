"""小红书 OCR：百度错误码重试 + MinerU 本地降级。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.xhs_image_ocr import (
    BAIDU_OCR_QUOTA_CODES,
    BAIDU_OCR_RETRY_CODES,
    _baidu_ocr_with_retry,
    baidu_error_label,
    ocr_image_bytes,
    ocr_one_xhs_image,
)


def test_baidu_error_labels():
    assert "每日" in baidu_error_label(17)
    assert "QPS" in baidu_error_label(18)


def test_quota_code_no_retry_goes_local():
    fake_bytes = b"x" * 1200
    quota_resp = {"error_code": 17, "error_msg": "Open api daily request limit reached"}
    mock_client = MagicMock()
    mock_client.basicAccurate.return_value = quota_resp

    with patch.dict("os.environ", {"BAIDU_OCR_API_KEY": "k", "BAIDU_OCR_SECRET_KEY": "s"}, clear=False):
        with patch("aip.AipOcr", return_value=mock_client):
            text, meta = _baidu_ocr_with_retry(fake_bytes)
    assert text == ""
    assert meta["error_code"] == 17
    assert meta["degraded_reason"] == "baidu_quota_17"
    assert 17 in BAIDU_OCR_QUOTA_CODES


def test_qps_code_retries_then_degrades():
    fake_bytes = b"x" * 1200
    mock_client = MagicMock()
    mock_client.basicAccurate.side_effect = [
        {"error_code": 18, "error_msg": "qps"},
        {"error_code": 18, "error_msg": "qps"},
        {"error_code": 18, "error_msg": "qps"},
    ]

    with patch.dict("os.environ", {"BAIDU_OCR_API_KEY": "k", "BAIDU_OCR_SECRET_KEY": "s"}, clear=False):
        with patch("aip.AipOcr", return_value=mock_client):
            with patch("app.services.xhs_image_ocr.time.sleep"):
                text, meta = _baidu_ocr_with_retry(fake_bytes)
    assert text == ""
    assert meta["error_code"] == 18
    assert mock_client.basicAccurate.call_count == 3
    assert 18 in BAIDU_OCR_RETRY_CODES


def test_baidu_fail_local_tesseract_ok():
    fake_bytes = b"x" * 1200
    mp = MagicMock()
    mp._local_ocr_fallback.return_value = "拿到 offer 字节面试"
    mp._merge_ocr_texts.side_effect = lambda a, b: (a or "") + ("\n" + b if b else "")

    with patch("app.services.xhs_image_ocr._baidu_ocr_with_retry", return_value=("", {"degraded_reason": "baidu_quota_17", "error_code": 17})):
        with patch("app.services.xhs_image_ocr._get_mineru", return_value=mp):
            text, method, meta = ocr_image_bytes(fake_bytes)
    assert "offer" in text
    assert method == "local_tesseract"
    assert meta.get("reason") == "baidu_quota_17"


def test_ocr_one_image_end_to_end_mock():
    fake_bytes = b"x" * 1200
    with patch("app.services.xhs_image_ocr.download_xhs_image", return_value=(fake_bytes, "")):
        with patch(
            "app.services.xhs_image_ocr.ocr_image_bytes",
            return_value=("面试内容", "local_tesseract", {"reason": "baidu_quota_17"}),
        ):
            row = ocr_one_xhs_image("http://img/1", 1, note_url="https://www.xiaohongshu.com/explore/x")
    assert row["ok"] is True
    assert row["method"] == "local_tesseract"
