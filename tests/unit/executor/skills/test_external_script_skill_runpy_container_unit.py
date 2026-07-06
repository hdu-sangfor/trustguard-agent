from __future__ import annotations

import json
from pathlib import Path

from tests.executor_test_env import executor_sys_path_isolated


def test_runpy_container_command_uses_python_entrypoint(monkeypatch, tmp_path: Path) -> None:
    with executor_sys_path_isolated():
        from app.skills.external_script_skill import ExternalScriptSkill

        skill_dir = tmp_path / "skills" / "demo-skill"
        run_py = skill_dir / "run.py"
        run_py.parent.mkdir(parents=True, exist_ok=True)
        run_py.write_text("print('ok')\n", encoding="utf-8")

        captured: dict[str, object] = {}

        class _Proc:
            returncode = 0
            stdout = '{"status":"SUCCESS","parsed_artifacts":{},"raw_stdout":"","raw_stderr":"","duration_ms":1}'
            stderr = ""

        def _fake_run(cmd, capture_output, text, timeout):  # noqa: ANN001
            captured["cmd"] = list(cmd)
            return _Proc()

        monkeypatch.setattr("app.skills.external_script_skill.subprocess.run", _fake_run)
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))

        skill = ExternalScriptSkill("demo-skill", "RECON", run_py)
        payload = {
            "task_id": "task-demo",
            "skill_id": "demo-skill",
            "target": "http://example.com",
            "params": {},
            "context": {},
            "allowed_target": None,
        }

        proc = skill._run_in_container(payload=payload, timeout=30)
        cmd = captured.get("cmd")

        assert proc.returncode == 0
        assert isinstance(cmd, list)
        assert "--entrypoint" in cmd
        i = cmd.index("--entrypoint")
        assert cmd[i + 1] == "python3"
        assert "run.py" in cmd
        assert json.dumps(payload, ensure_ascii=False) in cmd


def test_nonzero_returncode_cannot_be_masked_as_success(monkeypatch, tmp_path: Path) -> None:
    with executor_sys_path_isolated():
        from app.models import SkillRequest
        from app.skills.external_script_skill import ExternalScriptSkill

        skill_dir = tmp_path / "skills" / "demo-skill"
        run_py = skill_dir / "run.py"
        run_py.parent.mkdir(parents=True, exist_ok=True)
        run_py.write_text("print('ok')\n", encoding="utf-8")

        class _Proc:
            returncode = 2
            stdout = '{"status":"SUCCESS","parsed_artifacts":{"foo":"bar"}}'
            stderr = "python3: can\\'t open file '/skill/run.py': [Errno 2] No such file or directory"

        def _fake_run(cmd, capture_output, text, timeout):  # noqa: ANN001
            return _Proc()

        monkeypatch.setattr("app.skills.external_script_skill.subprocess.run", _fake_run)
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))

        skill = ExternalScriptSkill("demo-skill", "RECON", run_py)
        req = SkillRequest(task_id="task1", skill_id="demo-skill", target="http://example.com", params={})
        result = skill.execute(req)

        assert result.status == "FAILED"
        assert (result.parsed_artifacts or {}).get("returncode") == 2
        assert (result.parsed_artifacts or {}).get("status_masked_by_payload") is True


def test_runpy_missing_file_triggers_fallback_to_execute_script(monkeypatch, tmp_path: Path) -> None:
    with executor_sys_path_isolated():
        from app.skills.external_script_skill import ExternalScriptSkill

        skill_dir = tmp_path / "skills" / "demo-fallback-skill"
        run_py = skill_dir / "run.py"
        run_py.parent.mkdir(parents=True, exist_ok=True)
        run_py.write_text("print('ok')\n", encoding="utf-8")

        calls: list[list[str]] = []

        class _ProcFail:
            returncode = 2
            stdout = ""
            stderr = "python3: can't open file '/skill/run.py': [Errno 2] No such file or directory"

        class _ProcOk:
            returncode = 0
            stdout = '{"status":"SUCCESS","parsed_artifacts":{"ok":true}}'
            stderr = ""

        def _fake_run(cmd, capture_output, text, timeout):  # noqa: ANN001
            calls.append(list(cmd))
            if len(calls) == 1:
                return _ProcFail()
            return _ProcOk()

        monkeypatch.setattr("app.skills.external_script_skill.subprocess.run", _fake_run)
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))

        skill = ExternalScriptSkill("demo-fallback-skill", "RECON", run_py)
        payload = {
            "task_id": "task-demo",
            "skill_id": "demo-fallback-skill",
            "target": "http://example.com",
            "params": {},
            "context": {},
            "allowed_target": None,
        }

        proc = skill._run_in_container(payload=payload, timeout=30)

        assert proc.returncode == 0
        assert len(calls) == 2
        assert "run.py" in calls[0]
        assert "scripts/execute.py" in calls[1]


def test_attach_kb_features_resolves_skill_root_for_runpy(monkeypatch, tmp_path: Path) -> None:
    with executor_sys_path_isolated():
        from app.models import SkillRequest
        from app.skills.external_script_skill import ExternalScriptSkill

        skill_dir = tmp_path / "skills" / "demo-kb"
        run_py = skill_dir / "run.py"
        run_py.parent.mkdir(parents=True, exist_ok=True)
        run_py.write_text("print('ok')\n", encoding="utf-8")
        (skill_dir / "kb_features.py").write_text(
            "def extract_kb_features(artifacts, ctx):\n"
            "    return {'ok': True, 'marker': artifacts.get('x')}\n",
            encoding="utf-8",
        )

        monkeypatch.setattr("app.kb_feature_policy.extract_kb_features_declared", lambda _: True)
        skill = ExternalScriptSkill("demo-kb", "RECON", run_py)
        req = SkillRequest(task_id="t", skill_id="demo-kb", target="http://example.com", params={})
        artifacts = {"x": "v"}
        skill._attach_kb_features_if_declared(req, artifacts)
        assert artifacts.get("kb_features", {}).get("ok") is True
        assert artifacts.get("kb_features", {}).get("marker") == "v"
