import os


def test_lite_entry_disables_heavy_startup_hooks():
    from app import lite_main

    startup_names = {getattr(fn, "__name__", "") for fn in lite_main.app.router.on_startup}
    assert startup_names == {"_startup_lite_services"}
    assert os.environ["SBA_LITE_MODE"] == "1"
    assert os.environ["RSS_SCHEDULER_ENABLED"] == "0"
    assert os.environ["SUBSCRIPTION_SCHEDULER_ENABLED"] == "0"


def test_lite_api_allowlist_is_narrow():
    from app import lite_main

    assert "/api/process/" in lite_main._ALLOWED_API_PREFIXES
    assert "/api/reader/" in lite_main._ALLOWED_API_PREFIXES
    assert not any(prefix.startswith("/api/chat") for prefix in lite_main._ALLOWED_API_PREFIXES)
    assert not any(prefix.startswith("/api/doc/rag") for prefix in lite_main._ALLOWED_API_PREFIXES)


def test_lite_http_shell_and_optional_feature_gate():
    from fastapi.testclient import TestClient
    from app import lite_main

    client = TestClient(lite_main.app)
    status = client.get("/api/lite/status")
    assert status.status_code == 200
    assert status.json()["mode"] == "lite"

    home = client.get("/")
    assert home.status_code == 200
    assert "/assets/js/lite-mode.js" in home.text

    assert client.get("/api/chat/models").status_code == 404
    assert client.get("/api/doc/rag/stats").status_code == 404
    assert client.get("/api/rss/feeds").status_code == 404
