from __future__ import annotations

from types import SimpleNamespace

from app.services import span_tool_interceptor as interceptor


class _FakeTaskStateStore:
    def __init__(self) -> None:
        self.created: dict = {}
        self.finished: dict = {}
        self.decision: dict = {}
        self.events: list[dict] = []

    def create_step(
        self,
        *,
        session_id: str,
        task_id: str,
        step_type: str,
        status: str,
        input_payload: dict,
    ) -> SimpleNamespace:
        self.created = {
            "session_id": session_id,
            "task_id": task_id,
            "step_type": step_type,
            "status": status,
            "input_payload": input_payload,
        }
        return SimpleNamespace(step_id="state-step-1")

    def upsert_open_layer(self, *, step_id: str, open_layer: dict) -> None:
        assert step_id == "state-step-1"
        assert open_layer["decision"] == "continue"

    def append_event(self, **payload: object) -> None:
        self.events.append(dict(payload))

    def record_step_finished(self, **payload: object) -> None:
        self.finished = dict(payload)

    def record_step_decision(self, **payload: object) -> None:
        self.decision = dict(payload)


def test_tool_span_maps_span_and_task_state_step_ids(monkeypatch) -> None:
    store = _FakeTaskStateStore()
    monkeypatch.setattr(interceptor, "_task_store", store)
    monkeypatch.setattr(
        interceptor,
        "_span_create_step",
        lambda *_args, **_kwargs: {"step_id": "span-step-1"},
    )
    monkeypatch.setattr(interceptor, "_span_start", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(interceptor, "_span_finish", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(interceptor, "_span_patch_snapshot", lambda *_args, **_kwargs: None)

    handle = interceptor.begin_tool_span(
        task_id="task-1",
        session_id="session-1",
        tool_name="rag_search",
        tool_args={"query": "checkpoint"},
        react_round=1,
    )
    interceptor.end_tool_span(
        handle,
        tool_args={"query": "checkpoint"},
        raw_out={"items": ["ok"]},
    )

    assert handle.step_id == "span-step-1"
    assert handle.state_step_id == "state-step-1"
    assert store.created["input_payload"]["span_step_id"] == "span-step-1"
    assert store.events[0]["step_id"] == "state-step-1"
    assert store.events[0]["payload"]["span_step_id"] == "span-step-1"
    assert store.finished["step_id"] == "state-step-1"
    assert store.decision["step_id"] == "state-step-1"
