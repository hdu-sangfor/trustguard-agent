from __future__ import annotations

from app.runtime import graph


def test_graph_does_not_require_authorization_field(monkeypatch):
    monkeypatch.setattr(graph._adapter, "build_draft", lambda _message: {
        "name": "demo",
        "target": "https://test.example.com",
        "authorization_confirmed": False,
    })

    state = graph.run_graph("scan test.example.com")

    assert state["status"] == "NEEDS_CONFIRMATION"
    assert state["missing_fields"] == []
    assert state["activities"][-1]["status"] == "done"


def test_graph_builds_auditable_summary_without_private_cot(monkeypatch):
    monkeypatch.setattr(graph._adapter, "build_draft", lambda _message: {
        "name": "demo",
        "target": "https://test.example.com",
        "authorization_confirmed": True,
    })

    state = graph.run_graph("authorized scan")

    assert state["status"] == "NEEDS_CONFIRMATION"
    assert [item["title"] for item in state["activities"]] == [
        "理解任务意图",
        "检查目标与安全边界",
        "生成任务草稿",
    ]
    assert "私有思维链" in state["activities"][0]["detail"]
