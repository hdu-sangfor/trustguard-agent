#!/usr/bin/env python3
"""
R8b：编排器 Staging 翻转前就绪探测。

用法:
  ORCHESTRATOR_URL=http://localhost:18081 python scripts/orch_staging_readiness.py
  # 可选：同时探活 Evidence（GET /health）
  EVIDENCE_URL=http://localhost:18103 python scripts/orch_staging_readiness.py

退出码: 0 全部通过，1 存在失败。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 同目录模块
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from orch_readiness_checks import (
    collect_evidence_readiness_errors,
    collect_readiness_errors,
)  # noqa: E402


def main() -> int:
    base = (os.getenv("ORCHESTRATOR_URL") or "http://localhost:18081").rstrip("/")
    timeout = float(os.getenv("ORCH_READINESS_TIMEOUT", "15"))
    fail_on_sli_alerts = (os.getenv("ORCH_READINESS_FAIL_ON_SLI_ALERTS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    max_compile_fail_rate_raw = (os.getenv("ORCH_READINESS_MAX_COMPILE_FAIL_RATE") or "").strip()
    max_compile_fail_rate = None
    if max_compile_fail_rate_raw:
        try:
            max_compile_fail_rate = max(0.0, min(float(max_compile_fail_rate_raw), 1.0))
        except ValueError:
            print(
                "orch_staging_readiness: invalid ORCH_READINESS_MAX_COMPILE_FAIL_RATE "
                f"{max_compile_fail_rate_raw!r}",
                file=sys.stderr,
            )
            return 1

    try:
        import httpx
    except ImportError:
        print("orch_staging_readiness: need httpx (pip install httpx)", file=sys.stderr)
        return 1

    try:
        with httpx.Client(timeout=timeout) as client:
            errors = collect_readiness_errors(
                client,
                base,
                fail_on_sli_alerts=fail_on_sli_alerts,
                max_compile_fail_rate=max_compile_fail_rate,
            )
            evidence_base = (os.getenv("EVIDENCE_URL") or "").strip().rstrip("/")
            if evidence_base:
                errors.extend(collect_evidence_readiness_errors(client, evidence_base))
    except httpx.RequestError as e:
        errors = [f"transport: {e}"]

    if errors:
        print("orch_staging_readiness: FAILED", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    msg = f"orch_staging_readiness: OK base={base}"
    evidence_base = (os.getenv("EVIDENCE_URL") or "").strip().rstrip("/")
    if evidence_base:
        msg += f" evidence={evidence_base}"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
