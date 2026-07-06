import json
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


def main() -> None:
    root = REPO_ROOT
    sys.path.insert(0, str(root / "orchestrator"))
    sys.path.insert(0, str(root / "skills" / "nuclei" / "scripts"))

    print("[SMOKE] importing modules ...")
    from app.core.agent_tools import _truncate_artifacts_for_context  # type: ignore
    from app.core.memory_store import add_tier1_fact  # type: ignore
    from app.models import TaskState  # type: ignore
    import execute  # type: ignore  # nuclei execute.py

    print("[SMOKE] _build_llm_ready_artifacts ...")
    artifacts = {
        "vulnerabilities": [
            {"severity": "high", "url": "http://target/high1", "template_id": "cve-2024-0001"},
            {"severity": "low", "url": "http://target/low1", "template_id": "info-template"},
        ],
        "severity_histogram": {"high": 1, "low": 1},
        "tech_stack_evidence": [f"sig-{i}" for i in range(40)],
        "raw_preview": ["uid=0(root)"],
        "diagnostics": {"nuclei_rc": 0},
    }
    llm_ready = execute._build_llm_ready_artifacts(artifacts)  # type: ignore[attr-defined]
    print("llm_ready.keys =", sorted(llm_ready.keys()))
    print("llm_ready.tech_stack_evidence_len =", len(llm_ready.get("tech_stack_evidence", [])))

    print("[SMOKE] _truncate_artifacts_for_context blacklist ...")
    raw = {
        "status": "OK",
        "raw_stdout": "X" * 5000,
        "raw_stderr": "Y" * 5000,
        "nested": {"raw_stdout": "Z" * 100, "note": "keep-me"},
    }
    truncated = _truncate_artifacts_for_context(raw)
    print("ctx_has_raw_stdout =", "raw_stdout" in truncated)
    print("ctx_has_raw_stderr =", "raw_stderr" in truncated)
    print("ctx_nested_keys =", sorted(truncated.get("nested", {}).keys()))

    print("[SMOKE] add_tier1_fact dedupe/window ...")
    state = TaskState(task_id="t1", name="n", target="x")
    for i in range(60):
        # 每两个 i 命中同一路径，验证去重与窗口
        add_tier1_fact(state, f"[Scanner_Unverified] tpl detected on: /path{i//2}")
    facts = state.confirmed_facts or []
    print("tier1_facts_len =", len(facts))
    print("tier1_facts_sample =", json.dumps(facts[:5], ensure_ascii=False))


if __name__ == "__main__":
    main()

