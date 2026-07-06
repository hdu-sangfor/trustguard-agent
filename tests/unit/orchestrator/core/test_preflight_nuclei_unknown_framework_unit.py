import os
import sys
from tests.paths import REPO_ROOT

_ORCH_ROOT = str(REPO_ROOT / "orchestrator")
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)


def test_unknown_framework_clamps_nuclei_tags_to_generic_baseline():
    from app.models import TaskState
    from app.core.preflight_context import PreflightContext
    from app.core.preflight_nuclei import apply_nuclei_preflight

    state = TaskState(task_id="t-nuclei", name="n", target="http://127.0.0.1:8080")
    ctx = {"framework_target": "GENERIC_WEB"}
    pctx = PreflightContext(state=state, ctx=ctx, iml=4, seeds=[], run_id_key="web_vuln_run_id")
    params = {
        "target": state.target,
        "mode": "scan",
        "tags": ["safe-poc", "struts2", "thinkphp", "misconfig"],
    }

    apply_nuclei_preflight(params, pctx)
    tags = [str(x).lower() for x in (params.get("nuclei_tags") or [])]
    assert "struts2" not in tags
    assert "thinkphp" not in tags
    assert "misconfig" in tags
    assert params.get("nuclei_unknown_framework_profile") is True
