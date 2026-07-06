"""_execute_impl：可选 ArtifactSniffPool 从 raw_stderr 解析 S-06，写入 SkillResult.artifact_refs_v1。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.executor_test_env import executor_sys_path_isolated


@pytest.mark.asyncio
async def test_execute_impl_merges_stderr_sniff_into_artifact_refs_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with executor_sys_path_isolated():
        from app import main
        from app.micro_executor.protocol import build_artifact_notice
        from app.models import SkillRequest, SkillResult
        from app.worker_daemon.sniff_pool import ArtifactSniffPool

        notice = build_artifact_notice("wsref:sniff/1", skill_id="nmap", request_id="r-sniff")
        fake = MagicMock()
        fake.execute = lambda req: SkillResult(
            status="SUCCESS",
            parsed_artifacts={},
            raw_stdout="",
            raw_stderr=f"noise\n{notice}\n",
            duration_ms=5,
        )
        monkeypatch.setattr(main, "get_skill", lambda _sid: fake)
        monkeypatch.setattr(main, "_is_target_allowed", lambda *a, **kw: True)
        monkeypatch.setattr(main, "prepare_artifact_slot", MagicMock(side_effect=RuntimeError("no artifact")))
        monkeypatch.setattr(main, "write_artifact", MagicMock(return_value="wsref:written/1"))

        req = SkillRequest(
            task_id="t1",
            skill_id="nmap",
            target="http://example.com",
            allowed_target="http://example.com",
            request_id="r-sniff",
        )
        pool = ArtifactSniffPool(request_id="r-sniff")
        result = await main._execute_impl(req, artifact_sniff_pool=pool)
        assert result.artifact_refs_v1 == ["wsref:sniff/1"]
