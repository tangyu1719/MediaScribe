"""流水线历史 MariaDB/SQLite 持久化单元测试。"""
import os
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def sqlite_db_url(request):
    db_dir = Path(__file__).resolve().parents[1] / "output" / "pytest_history"
    db_dir.mkdir(parents=True, exist_ok=True)
    db = db_dir / f"test_{request.node.name}_{uuid.uuid4().hex[:8]}.sqlite3"
    url = f"sqlite:///{db.as_posix()}"
    old = os.environ.get("SBA_DATABASE_URL")
    os.environ["SBA_DATABASE_URL"] = url
    import app.services.pipeline_history_store as store

    store._engine = None
    store._SessionLocal = None
    store._migrated_from_json = False
    yield url
    store._engine = None
    store._SessionLocal = None
    store._migrated_from_json = False
    if old is None:
        os.environ.pop("SBA_DATABASE_URL", None)
    else:
        os.environ["SBA_DATABASE_URL"] = old
    if db.is_file():
        try:
            db.unlink()
        except OSError:
            pass


def test_upsert_and_list(sqlite_db_url):
    from app.services import pipeline_history_store as store

    store.get_engine()
    entry = {
        "id": "task_test_001",
        "task_id": "task_test_001",
        "link": "https://www.xiaohongshu.com/explore/abc",
        "url_hash": "hashabc",
        "platform": "小红书",
        "status": "completed",
        "progress": 100,
        "link_title": "测试标题",
        "doc_path": "/tmp/out.md",
        "pipeline_stages": {"extract": {"status": "completed", "label": "提取"}},
        "logs": [{"message": "ok"}],
        "created_at": "2026-05-25T10:00:00",
        "updated_at": "2026-05-25T10:01:00",
    }
    store.upsert_task(entry)
    row = store.get_by_link_or_hash(link=entry["link"])
    assert row is not None
    assert row["id"] == "task_test_001"
    assert row["status"] == "completed"
    assert row["pipeline_stages"]["extract"]["status"] == "completed"

    entry["progress"] = 50
    entry["status"] = "processing"
    store.upsert_task(entry)
    row2 = store.get_by_task_id("task_test_001")
    assert row2["progress"] == 50

    entry["status"] = "completed"
    store.upsert_task(entry)
    tasks = store.list_tasks(limit=10)
    assert len(tasks) == 1

    assert store.clear_completed() == 1
    assert store.count_tasks() == 0


def test_migrate_from_json(sqlite_db_url):
    from app.services import pipeline_history_store as store

    store._migrated_from_json = False
    jf = Path(__file__).resolve().parents[1] / "output" / "pytest_history" / "history_migrate.json"
    jf.write_text(
        '{"tasks":[{"id":"m1","link":"https://x.com/1","status":"completed","progress":100}]}',
        encoding="utf-8",
    )
    store.get_engine()
    n = store.ensure_migrated_from_json_file(jf)
    assert n == 1
    assert store.count_tasks() == 1
