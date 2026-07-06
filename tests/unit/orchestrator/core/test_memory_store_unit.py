import os
import sys
from tests.paths import REPO_ROOT

_ORCH_ROOT = str(REPO_ROOT / "orchestrator")
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)


def test_action_signature_ignores_noise_params():
    from app.core.memory_store import build_action_signature

    p1 = {
        "method": "GET",
        "path": "/manager/html",
        "headers": {"User-Agent": "A", "X-Request-Id": "111", "Content-Type": "text/plain"},
        "timestamp": 1234567890,
    }
    p2 = {
        "method": "GET",
        "path": "/manager/html",
        "headers": {"User-Agent": "B", "X-Request-Id": "222", "Content-Type": "text/plain"},
        "timestamp": 2234567890,
    }
    s1 = build_action_signature("curl-raw", "http://host.docker.internal:8080/manager/html", p1)
    s2 = build_action_signature("curl-raw", "http://host.docker.internal:8080/manager/html", p2)
    assert s1 == s2


def test_action_signature_includes_skill_specific_params():
    """katana_depth 等细粒度参数应进入签名，避免被误判为同一动作。"""
    from app.core.memory_store import build_action_signature

    base = "http://host.docker.internal:8080/"
    a = build_action_signature("katana", base, {"katana_depth": "2", "skip_dirsearch": True})
    b = build_action_signature("katana", base, {"katana_depth": "5", "skip_dirsearch": True})
    assert a != b


def test_action_signature_ignores_run_id_and_output_dir():
    from app.core.memory_store import build_action_signature

    base = "http://host.docker.internal:8080/"
    a = build_action_signature("katana", base, {"katana_depth": "2", "run_id": "abc", "output_dir": "/tmp/a"})
    b = build_action_signature("katana", base, {"katana_depth": "2", "run_id": "xyz", "output_dir": "/tmp/b"})
    assert a == b


def test_apply_fact_updates_add_and_remove():
    from app.models import TaskState
    from app.core.memory_store import apply_fact_updates

    state = TaskState(task_id="t1", name="n", target="http://x")
    state.confirmed_facts = ["A", "B"]
    added, removed = apply_fact_updates(state, ["C", "A"], ["B"])
    assert added == ["C"]
    assert removed == ["B"]
    assert state.confirmed_facts == ["A", "C"]


def test_infer_http_probe_targets_from_services_and_ports():
    from app.core.memory_store import infer_http_probe_targets

    arts = {
        "open_ports": [22, 8080],
        "services": [
            {"port": 8080, "service": "http"},
            {"port": 3306, "service": "mysql"},
        ],
    }
    targets = infer_http_probe_targets(
        task_target="http://host.docker.internal:8080",
        skill_id="nmap",
        target="host.docker.internal",
        resolved_artifacts=arts,
    )
    assert any("8080" in t for t in targets)

