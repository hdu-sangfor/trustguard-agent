"""Unit tests for information maturity + skill preflight (orchestrator)."""

import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest


def _orch_path():
    return str(REPO_ROOT / "orchestrator")


@pytest.fixture
def im_mod():
    root = _orch_path()
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, root)
    import importlib

    mod = importlib.import_module("app.core.information_maturity")
    yield mod
    sys.path.remove(root)
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


@pytest.fixture
def pf_mod():
    root = _orch_path()
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, root)
    import importlib

    mod = importlib.import_module("app.core.skill_preflight")
    yield mod
    sys.path.remove(root)
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def test_iml_levels_from_http_enum(im_mod):
    ctx = {
        "http-enum_url": "http://127.0.0.1:8080",
        "http-enum_http_status": "200",
        "http-enum_headers": {"Server": "Jetty/9.x"},
        "http-enum_title": "Struts2 Showcase",
        "http-enum_raw_preview": '<form action="/doUpload.action"',
    }
    level, detail = im_mod.compute_information_maturity(ctx, "http://127.0.0.1:8080")
    assert level >= 3
    assert detail.get("l1_connectivity")


def test_preflight_injects_dispatcher_run_id_and_seeds(pf_mod):
    import app.models as models  # noqa: WPS433

    state = models.TaskState(task_id="task-x", name="n", target="http://t:8080")
    state.target_context = {
        "http-enum_raw_preview": '<form action="/a.action"',
        "http-enum_url": "http://t:8080/",
    }
    iml = 3
    actions = [models.ActionItem(skill_id="dispatcher", params={"target": "http://t:8080"})]
    out, skipped = pf_mod.preflight_actions(state, actions, iml=iml)
    assert not skipped
    assert out[0].params.get("run_id")
    assert out[0].params.get("seed_urls")


def test_iml_negative_connectivity_resets_to_zero(im_mod):
    ctx = {
        "http-enum_url": "http://127.0.0.1:8080",
        "http-enum_returncode": 7,
        "http-enum_http_status": "",
        "target_unreachable": False,
    }
    level, detail = im_mod.compute_information_maturity(ctx, "http://127.0.0.1:8080")
    assert level == 0
    assert detail.get("reason") == "negative_connectivity_evidence"


def test_iml_target_unreachable(im_mod):
    ctx = {"target_unreachable": True, "http-enum_http_status": "200"}
    level, _ = im_mod.compute_information_maturity(ctx, "http://x")
    assert level == 0


def test_iml_katana_counts_ignored_when_discovery_file_missing(im_mod, tmp_path):
    tid = "task-fs"
    rid = "run123"
    ctx = {
        "http-enum_url": "http://127.0.0.1:8080/",
        "http-enum_http_status": "200",
        "katana_url_counts": {"total_raw": 5, "katana": 5},
        "web_vuln_run_id": rid,
    }
    ws = str(tmp_path)
    # 不落盘 katana_urls.txt —— 应不信任 katana 计数，L4 仅靠 katana 时不成立
    level, detail = im_mod.compute_information_maturity(ctx, "http://127.0.0.1:8080/", task_id=tid, workspace_root=ws)
    assert detail.get("katana_discovery_present") is False
    assert detail.get("l4_endpoint_assets") is False
    assert level < 4


def test_preflight_skips_nuclei_when_starved(pf_mod):
    import app.models as models  # noqa: WPS433

    state = models.TaskState(task_id="task-y", name="n", target="http://t:8080")
    state.target_context = {
        "katana_url_counts": {"katana": 0, "dirsearch": 0, "total_raw": 0},
    }
    actions = [
        models.ActionItem(skill_id="nuclei", params={"target": "http://t:8080"}),
    ]
    out, skipped = pf_mod.preflight_actions(state, actions, iml=2)
    assert len(out) == 0
    assert skipped and skipped[0]["reason"] == "pipeline_input_starved"


def test_preflight_nuclei_sniper_from_tech_evidence(pf_mod):
    import app.models as models  # noqa: WPS433

    state = models.TaskState(task_id="task-snipe", name="n", target="http://x:8080")
    state.target_context = {
        "katana_url_counts": {"total_raw": 1, "katana": 1},
        "tech_stack_evidence": [{"signal": "Apache Struts2", "url": "http://x/upload.action"}],
    }
    actions = [
        models.ActionItem(
            skill_id="nuclei",
            params={"target": "http://x:8080", "tags": "sqli,xss,cve,generic"},
        ),
    ]
    out, skipped = pf_mod.preflight_actions(state, actions, iml=4)
    assert not skipped
    tags = out[0].params.get("tags") or []
    assert isinstance(tags, list)
    low = [str(t).lower() for t in tags]
    assert "struts2" in low
    assert "java" in low
    assert out[0].params.get("nuclei_sniper_preflight") is True
    assert int(out[0].params.get("rate_limit") or 0) <= 30


def test_preflight_nuclei_sniper_from_action_url_path(pf_mod):
    import app.models as models  # noqa: WPS433

    state = models.TaskState(task_id="task-path", name="n", target="http://h:8080")
    state.target_context = {
        "katana_url_counts": {"total_raw": 1, "katana": 1},
        "dispatcher_suspicious_signals": [
            {"url": "http://h:8080/login.action", "reason": "path_keyword", "confidence": 0.4}
        ],
    }
    actions = [models.ActionItem(skill_id="nuclei", params={"target": "http://h:8080"})]
    out, skipped = pf_mod.preflight_actions(state, actions, iml=3)
    assert not skipped
    tags = out[0].params.get("tags") or []
    assert isinstance(tags, list)
    low = [str(t).lower() for t in tags]
    assert "struts" in low or "java" in low
    assert out[0].params.get("nuclei_sniper_preflight") is True
