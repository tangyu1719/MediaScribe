"""关注 UP 列表 — 近义词筛选单测。"""
from app.services.follow_up_search import expand_search_terms, filter_follow_ups, match_follow_up


def test_expand_synonym_agent():
    terms = expand_search_terms("智能体")
    assert "agent" in terms or "智能体" in terms


def test_filter_by_synonym():
    items = [
        {
            "creator_id": "a1",
            "display_name": "AI编程小朱",
            "search_blob": "java 面试 面经",
            "sample_titles": ["高德 agent 社招面经"],
            "already_subscribed": False,
        },
        {
            "creator_id": "a2",
            "display_name": "美食博主",
            "search_blob": "做饭 菜谱",
            "sample_titles": ["家常菜"],
            "already_subscribed": False,
        },
    ]
    out = filter_follow_ups(items, query="面经", subscribed="all")
    assert len(out) == 1
    assert out[0]["creator_id"] == "a1"


def test_subscribed_filter():
    items = [
        {"creator_id": "x", "search_blob": "test", "already_subscribed": True},
        {"creator_id": "y", "search_blob": "test", "already_subscribed": False},
    ]
    assert len(filter_follow_ups(items, subscribed="no")) == 1
    assert len(filter_follow_ups(items, subscribed="yes")) == 1
