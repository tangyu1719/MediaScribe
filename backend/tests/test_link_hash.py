"""链接粘贴文本提取与规范化。"""
import importlib.util
from pathlib import Path

_SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SERVICES / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


link_doc_routing = _load("link_doc_routing")
link_hash = _load("link_hash")


DOUYIN_SHARE = (
    "4.33 复制打开抖音，看看【鼎捷智造的作品】鼎捷AI智能套件：让系统会思考，与人并肩协作，用... "
    "https://v.douyin.com/mUXkHOWXC_8/ r@e.OK 01/30 oQK:/ #"
)


def test_coerce_pasted_link_from_douyin_share_text():
    got = link_hash.coerce_pasted_link(DOUYIN_SHARE)
    assert got == "https://v.douyin.com/mUXkHOWXC_8/"


def test_normalize_link_for_hash_does_not_crash_on_share_text():
    norm = link_hash.normalize_link_for_hash(DOUYIN_SHARE)
    assert norm.startswith("https://v.douyin.com/")
    assert link_hash.url_hash(DOUYIN_SHARE) == link_hash.url_hash("https://v.douyin.com/mUXkHOWXC_8/")


def test_extract_http_urls_strips_trailing_punctuation():
    urls = link_doc_routing.extract_http_urls("见 https://v.douyin.com/abc123/ 复制")
    assert urls == ["https://v.douyin.com/abc123/"]
