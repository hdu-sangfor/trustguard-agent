"""manager_agent：suspicious_signals / tech_stack_evidence 信号驱动 Todo。"""
import importlib
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


def _load_manager():
    root = REPO_ROOT
    orch_root = str(root / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        return importlib.import_module("app.core.manager_agent"), importlib.import_module("app.models")
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_pentest_manager_agent_class_aliases_module_functions():
    mgr, _models = _load_manager()
    assert mgr.PentestManagerAgent.ensure_todos_from_context is mgr.ensure_todos_from_context


def test_collect_suspicious_and_tech_items():
    mgr, models = _load_manager()
    ctx = {
        "dispatcher_suspicious_signals": [{"url": "http://x/u", "reason": "path_keyword", "confidence": 0.4}],
        "nuclei_tech_stack_evidence": [{"signal": "struts", "template_id": "t", "url": "http://x", "severity": "info"}],
    }
    assert len(mgr.collect_suspicious_signal_items(ctx)) == 1
    assert len(mgr.collect_tech_stack_evidence_items(ctx)) == 1


def test_ensure_todos_adds_signal_entries_in_vuln_scan_phase():
    mgr, models = _load_manager()
    state = models.TaskState(task_id="t1", name="n", target="http://target/")
    state.current_phase = models.Phase.VULN_SCAN
    state.target_context = {
        "dispatcher_suspicious_signals": [
            {"url": "http://target/upload", "reason": "path_keyword", "confidence": 0.4}
        ],
        "tech_stack_evidence": [
            {
                "signal": "struts",
                "severity": "info",
                "template_id": "tid",
                "url": "http://target/a",
                "source": "nuclei_info_low",
            }
        ],
    }
    mgr.ensure_todos_from_context(state)
    todos = mgr.get_todos_from_state(state)
    descs = " ".join(t.description for t in todos)
    assert "[signals] ETL suspicious_signals" in descs
    assert "[signals] Nuclei info/low tech_stack" in descs


def test_exploit_phase_adds_tech_and_suspicious_followups():
    mgr, models = _load_manager()
    state = models.TaskState(task_id="t2", name="n", target="http://target/")
    state.current_phase = models.Phase.EXPLOIT
    state.target_context = {
        "dispatcher_suspicious_signals": [
            {"url": "http://target/z", "reason": "struts_action", "confidence": 0.35}
        ],
        "tech_stack_evidence": [
            {"signal": "struts", "severity": "info", "template_id": "x", "url": "http://target/", "source": "nuclei_info_low"}
        ],
    }
    mgr.ensure_todos_from_context(state)
    todos = mgr.get_todos_from_state(state)
    descs = [t.description for t in todos if t.phase == "EXPLOIT"]
    assert any("Tech stack from Nuclei info/low" in d for d in descs)
    assert any("ETL suspicious" in d for d in descs)
