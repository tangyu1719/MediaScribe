"""ffmpeg 路径解析：缓存与绝对路径。"""
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

from ffmpeg_path import get_ffmpeg_executables, invalidate_ffmpeg_cache  # noqa: E402


def test_get_ffmpeg_executables_returns_pair_or_none():
    invalidate_ffmpeg_cache()
    ff, fp = get_ffmpeg_executables(force=True)
    # CI/开发机可能无 ffmpeg；只断言返回类型
    assert ff is None or str(ff).endswith(("ffmpeg", "ffmpeg.exe"))
    assert fp is None or str(fp).endswith(("ffprobe", "ffprobe.exe"))
    if ff and fp:
        ff2, fp2 = get_ffmpeg_executables()
        assert ff2 == ff and fp2 == fp
