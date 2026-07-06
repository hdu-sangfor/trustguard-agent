"""R8b：orch_readiness_checks 与编排器 ASGI 探活。"""

from __future__ import annotations

import sys
from pathlib import Path
from tests.paths import REPO_ROOT

_SCRIPTS = str(REPO_ROOT / "scripts")
_ORCH = str(REPO_ROOT / "orchestrator")
for _p in (_SCRIPTS, _ORCH):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def test_collect_readiness_errors_empty_on_app():
    from fastapi.testclient import TestClient

    from orch_readiness_checks import collect_readiness_errors

    from app.main import app

    client = TestClient(app)
    err = collect_readiness_errors(client, "", fail_on_sli_alerts=False)
    assert err == []


def test_collect_evidence_readiness_errors_ok():
    from orch_readiness_checks import collect_evidence_readiness_errors

    class _R:
        status_code = 200

        def json(self):
            return {"status": "ok"}

    class _C:
        def get(self, url: str):
            assert url.endswith("/health")
            return _R()

    assert collect_evidence_readiness_errors(_C(), "http://evidence:18103") == []


def test_collect_evidence_readiness_errors_bad_body():
    from orch_readiness_checks import collect_evidence_readiness_errors

    class _R:
        status_code = 200

        def json(self):
            return {"status": "degraded"}

    class _C:
        def get(self, url: str):
            return _R()

    err = collect_evidence_readiness_errors(_C(), "http://evidence:18103")
    assert len(err) == 1
    assert "evidence /health body" in err[0]


def test_collect_readiness_errors_compile_fail_rate_threshold():
    from orch_readiness_checks import collect_readiness_errors

    class _R:
        def __init__(self, status_code: int, body: dict):
            self.status_code = status_code
            self._body = body

        def json(self):
            return self._body

    class _C:
        def get(self, url: str):
            if url.endswith("/health"):
                return _R(200, {"status": "ok"})
            if url.endswith("/v1/orchestrator/sli/snapshot"):
                return _R(
                    200,
                    {
                        "schema_version": "orch-sli-v1",
                        "counters": {},
                        "alerts": [],
                        "compile_fail_rate": 0.20,
                    },
                )
            if url.endswith("/v1/orchestrator/mq-status"):
                return _R(200, {"mode": "http"})
            raise AssertionError(url)

    err = collect_readiness_errors(_C(), "http://orch:18081", fail_on_sli_alerts=False, max_compile_fail_rate=0.10)
    assert any("compile_fail_rate too high" in e for e in err)
