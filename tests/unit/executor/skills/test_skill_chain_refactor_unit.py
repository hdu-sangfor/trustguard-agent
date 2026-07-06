import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from tests.paths import REPO_ROOT


_ROOT = REPO_ROOT
_EXECUTOR = str(_ROOT / "executor")
_ORCHESTRATOR = str(_ROOT / "orchestrator")


def _clear_app_modules() -> None:
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


@contextmanager
def _executor_import_scope():
    _clear_app_modules()
    sys.path.insert(0, _EXECUTOR)
    try:
        yield
    finally:
        if _EXECUTOR in sys.path:
            sys.path.remove(_EXECUTOR)
        _clear_app_modules()


@contextmanager
def _orchestrator_import_scope():
    _clear_app_modules()
    sys.path.insert(0, _ORCHESTRATOR)
    try:
        yield
    finally:
        if _ORCHESTRATOR in sys.path:
            sys.path.remove(_ORCHESTRATOR)
        _clear_app_modules()


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_dynamic_loader_detects_external_skills():
    with _executor_import_scope():
        from app.skills.dynamic_loader import load_skills

        loaded = load_skills()
        assert "nmap" in loaded
        assert "baidu-search" in loaded
        assert "http-enum" in loaded
        assert loaded["nmap"].script_path is not None


def test_registry_uses_external_script_skill():
    with _executor_import_scope():
        from app.skills.registry import refresh_skills, get_skill
        from app.skills.external_script_skill import ExternalScriptSkill

        ids = refresh_skills()
        assert "nmap" in ids
        skill = get_skill("nmap")
        assert isinstance(skill, ExternalScriptSkill)


def test_nmap_parser_only_collects_open_ports():
    module = _load_module(
        "nmap_execute",
        _ROOT / "skills" / "nmap" / "scripts" / "execute.py",
    )
    sample = """
PORT     STATE    SERVICE
22/tcp   open     ssh
80/tcp   closed   http
443/tcp  filtered https
"""
    parsed = module._parse_nmap(sample)
    assert parsed.get("open_ports") == [22]
    assert parsed.get("services") == [{"port": 22, "name": "ssh"}]
    assert parsed.get("port_states", {}).get("closed") == [80]


def test_loop_guard_triggers_on_repeated_signatures():
    with _orchestrator_import_scope():
        models_mod = _load_module(
            "orchestrator_models",
            _ROOT / "orchestrator" / "app" / "models.py",
        )
        loop_guard_mod = _load_module(
            "orchestrator_loop_guard",
            _ROOT / "orchestrator" / "app" / "core" / "loop_guard.py",
        )

        state = models_mod.TaskState(task_id="t1", name="n", target="127.0.0.1")
        assert loop_guard_mod.update_loop_guard(state, "sig1") is False
        assert loop_guard_mod.update_loop_guard(state, "sig1") is False
        assert loop_guard_mod.update_loop_guard(state, "sig1") is True


def test_loop_guard_triggers_on_window_repeat_pattern():
    with _orchestrator_import_scope():
        models_mod = _load_module(
            "orchestrator_models_window",
            _ROOT / "orchestrator" / "app" / "models.py",
        )
        loop_guard_mod = _load_module(
            "orchestrator_loop_guard_window",
            _ROOT / "orchestrator" / "app" / "core" / "loop_guard.py",
        )

        state = models_mod.TaskState(task_id="t2", name="n", target="127.0.0.1")
        assert loop_guard_mod.update_loop_guard(state, "A") is False
        assert loop_guard_mod.update_loop_guard(state, "B") is False
        assert loop_guard_mod.update_loop_guard(state, "A") is False
        assert loop_guard_mod.update_loop_guard(state, "B") is False
        # 最近窗口中 A 出现第 3 次，应触发
        assert loop_guard_mod.update_loop_guard(state, "A") is True
