"""Executor：Katana 外层超时但 workspace 已有 katana_urls.txt 时恢复为 SUCCESS。"""

import importlib
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


def _cleanup_app_modules(exec_root: str) -> None:
    if exec_root in sys.path:
        sys.path.remove(exec_root)
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def _exec_modules(tmp_path, monkeypatch):
    root = REPO_ROOT
    exec_root = str(root / "executor")
    _cleanup_app_modules(exec_root)
    sys.path.insert(0, exec_root)
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    try:
        ws = importlib.import_module("app.core.workspace_store")
        monkeypatch.setattr(ws, "WORKSPACE_ROOT", tmp_path)
        main = importlib.import_module("app.main")
        return main, ws
    except Exception:
        _cleanup_app_modules(exec_root)
        raise


def test_find_latest_katana_urls_file(tmp_path, monkeypatch):
    root = REPO_ROOT
    exec_root = str(root / "executor")
    main, ws = _exec_modules(tmp_path, monkeypatch)
    task_id = "task-xyz"
    safe = ws._safe_name(task_id)
    ku = tmp_path / safe / "web-vuln" / "rid99" / "discovery" / "katana_urls.txt"
    ku.parent.mkdir(parents=True, exist_ok=True)
    ku.write_text("http://host/a\n", encoding="utf-8")
    try:
        found = ws.find_latest_katana_urls_file(task_id)
        assert found == ku
        assert ws.count_http_url_lines(found) == 1
    finally:
        _cleanup_app_modules(exec_root)


def test_recover_katana_if_workspace_has_output(tmp_path, monkeypatch):
    root = REPO_ROOT
    exec_root = str(root / "executor")
    main, ws = _exec_modules(tmp_path, monkeypatch)
    task_id = "task-rec"
    safe = ws._safe_name(task_id)
    ku = tmp_path / safe / "web-vuln" / "r1" / "discovery" / "katana_urls.txt"
    ku.parent.mkdir(parents=True, exist_ok=True)
    ku.write_text("https://t/x\n", encoding="utf-8")

    models = importlib.import_module("app.models")
    req = models.SkillRequest(task_id=task_id, skill_id="katana", target="https://t/", params={})
    res = models.SkillResult(
        status="TIMEOUT",
        parsed_artifacts={"error": "timeout"},
        raw_stdout="",
        raw_stderr="outer",
        duration_ms=100,
    )
    try:
        out = main._recover_katana_if_workspace_has_output(req, res)
        assert out.status == "SUCCESS"
        assert (out.parsed_artifacts or {}).get("workspace_recovery") is True
        assert (out.parsed_artifacts or {}).get("run_id") == "r1"
    finally:
        _cleanup_app_modules(exec_root)


def test_recover_skips_when_no_katana_file(tmp_path, monkeypatch):
    root = REPO_ROOT
    exec_root = str(root / "executor")
    main, ws = _exec_modules(tmp_path, monkeypatch)
    models = importlib.import_module("app.models")
    req = models.SkillRequest(task_id="empty", skill_id="katana", target="http://x", params={})
    res = models.SkillResult(status="TIMEOUT", parsed_artifacts={}, raw_stdout="", raw_stderr="", duration_ms=1)
    try:
        out = main._recover_katana_if_workspace_has_output(req, res)
        assert out.status == "TIMEOUT"
    finally:
        _cleanup_app_modules(exec_root)
