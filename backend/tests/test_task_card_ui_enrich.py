"""作者主页 URL 与任务卡片 enrich 测试。"""
from app.services.author_profile_url import build_author_profile_url, detect_platform_from_link
from app.services.ops import ops_find_report_for_task
from app.services.task_source_meta import enrich_task_source_fields


def test_xhs_author_profile_url():
    url = build_author_profile_url(
        platform="小红书",
        author_id="5f8a1b2c3d4e5f6789012345",
    )
    assert url == "https://www.xiaohongshu.com/user/profile/5f8a1b2c3d4e5f6789012345"


def test_douyin_author_profile_url():
    sec = "MS4wLjABAAAAtest_user_sec_uid_1234567890"
    url = build_author_profile_url(platform="抖音", author_id=sec)
    assert url == f"https://www.douyin.com/user/{sec}"


def test_bilibili_author_profile_url():
    url = build_author_profile_url(platform="B站", author_id="12345678")
    assert url == "https://space.bilibili.com/12345678"


def test_weixin_author_profile_url_from_link():
    link = "https://mp.weixin.qq.com/s?__biz=MzAxTest1234567890==&mid=1"
    assert detect_platform_from_link(link) == "微信"
    url = build_author_profile_url(platform="微信", link=link)
    assert "__biz=MzAxTest1234567890==" in url
    assert url.startswith("https://mp.weixin.qq.com/mp/profile_ext")


def test_enrich_failed_task_ops_report_lookup():
    row = enrich_task_source_fields(
        {
            "task_id": "nonexistent_task_for_test",
            "status": "failed",
            "platform": "小红书",
            "link": "https://www.xiaohongshu.com/explore/test",
            "import_source": "manual",
        }
    )
    assert row.get("author_profile_url") == ""
    # 无报告时不应报错
    assert "ops_report_id" not in row or not row.get("ops_report_id")


def test_enrich_author_profile_on_task():
    row = enrich_task_source_fields(
        {
            "task_id": "t1",
            "status": "completed",
            "platform": "B站",
            "author_name": "测试UP",
            "author_id": "99887766",
            "import_source": "manual",
        }
    )
    assert row["author_profile_url"] == "https://space.bilibili.com/99887766"


def test_ops_find_report_for_task_empty():
    assert ops_find_report_for_task("") is None
    assert ops_find_report_for_task("definitely_no_such_task_id_abc") is None
