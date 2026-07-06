import asyncio
import importlib
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


class _CaptureDispatcher:
    def __init__(self, models_mod):
        self.models_mod = models_mod
        self.last_context = None

    async def dispatch(
        self,
        *,
        task_id: str,
        skill_id: str,
        target: str,
        params: dict,
        allowed_target: str | None = None,
        context: dict | None = None,
        execution_kind: str | None = None,
    ):
        self.last_context = dict(context or {})
        return self.models_mod.ExecuteSkillResponse(
            status="SUCCESS",
            parsed_artifacts={},
            raw_stdout="",
            raw_stderr="",
            duration_ms=1,
        )


def _load_orchestrator_modules():
    root = REPO_ROOT
    orch_root = str(root / "orchestrator")
    # avoid collision with executor-side "app"
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        models_mod = importlib.import_module("app.models")
        agent_tools_mod = importlib.import_module("app.core.agent_tools")
        return models_mod, agent_tools_mod
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_web_vuln_pipeline_receives_fingerprint_context():
    models_mod, agent_tools_mod = _load_orchestrator_modules()
    state = models_mod.TaskState(task_id="t1", name="n", target="http://x")
    state.target_context.update(
        {
            "fingerprints": ["Jetty(9.2.11)", "Struts2 Showcase"],
            "whatweb-fingerprint_raw_preview": "Apache Tomcat / Spring Boot",
        }
    )

    dispatcher = _CaptureDispatcher(models_mod)
    executor = agent_tools_mod.SkillExecutor(dispatcher=dispatcher)
    call_ctx = agent_tools_mod.SkillCallContext(
        task_id="t1",
        phase=models_mod.Phase.RECON,
        skill_id="web-vuln-pipeline",
        target="http://x",
        params={},
        allowed_target="http://x",
    )
    asyncio.run(
        executor.execute_skill_dispatch_only(
            state=state,
            call_ctx=call_ctx,
            enable_executor=True,
            available_skills=["web-vuln-pipeline"],
        )
    )
    assert dispatcher.last_context is not None
    assert "fingerprint" in dispatcher.last_context
    assert "jetty" in dispatcher.last_context["fingerprint"].lower()


def test_non_web_vuln_skill_not_inject_fingerprint_context():
    models_mod, agent_tools_mod = _load_orchestrator_modules()
    state = models_mod.TaskState(task_id="t2", name="n", target="http://x")
    state.target_context["fingerprints"] = ["Jetty(9.2.11)"]

    dispatcher = _CaptureDispatcher(models_mod)
    executor = agent_tools_mod.SkillExecutor(dispatcher=dispatcher)
    call_ctx = agent_tools_mod.SkillCallContext(
        task_id="t2",
        phase=models_mod.Phase.RECON,
        skill_id="nmap",
        target="http://x",
        params={},
        allowed_target="http://x",
    )
    asyncio.run(
        executor.execute_skill_dispatch_only(
            state=state,
            call_ctx=call_ctx,
            enable_executor=True,
            available_skills=["nmap"],
        )
    )
    assert dispatcher.last_context is not None
    assert "fingerprint" not in dispatcher.last_context


def test_dispatcher_receives_http_enum_fallback_urls():
    models_mod, agent_tools_mod = _load_orchestrator_modules()
    raw = (
        '<form action="/doUpload.action;jsessionid=XYZ" method="post"></form>'
    )
    state = models_mod.TaskState(task_id="t3", name="n", target="http://host:8080/")
    state.target_context.update(
        {
            "http-enum_url": "http://host.docker.internal:8080/",
            "http-enum_raw_preview": "HTTP/1.1 200 OK\n\n" + raw,
        }
    )
    dispatcher = _CaptureDispatcher(models_mod)
    executor = agent_tools_mod.SkillExecutor(dispatcher=dispatcher)
    call_ctx = agent_tools_mod.SkillCallContext(
        task_id="t3",
        phase=models_mod.Phase.VULN_SCAN,
        skill_id="dispatcher",
        target="http://host.docker.internal:8080/",
        params={"operation": "prepare", "run_id": "r1"},
        allowed_target="http://host.docker.internal:8080/",
    )
    asyncio.run(
        executor.execute_skill_dispatch_only(
            state=state,
            call_ctx=call_ctx,
            enable_executor=True,
            available_skills=["dispatcher"],
        )
    )
    assert dispatcher.last_context is not None
    assert dispatcher.last_context.get("http_enum_fallback_urls")
    assert "jsessionid" not in dispatcher.last_context["http_enum_fallback_urls"][0].lower()
