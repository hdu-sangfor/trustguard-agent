"""
pytest 配置与公共 fixture。
集成测试默认假定Gateway运行在 http://localhost:18080；
可通过环境变量 OVERRIDE_GATEWAY_URL 覆盖。

运行集成测试前请确保：
  - MySQL 已启动且Gateway能连接（如 localhost:3305/3306，库 trustguard_agent，表见 docker/mysql-init.d）
  - Gateway已启动（docker compose up gateway，或在 gateway/ 下 uvicorn app.main:app --port 18080）
  - 可选：编排器、Evidence已启动（创建/停止/恢复任务会调编排器）
"""
import os
from pathlib import Path

import httpx
import pytest

from tests.paths import REPO_ROOT

GATEWAY_BASE = os.getenv("OVERRIDE_GATEWAY_URL", "http://localhost:18080")
INTEGRATION_TIMEOUT = float(os.getenv("INTEGRATION_HTTP_TIMEOUT", "60.0"))
TESTS_ROOT = REPO_ROOT / "tests"


def _relative_test_parts(path: Path) -> tuple[str, ...]:
    try:
        return path.resolve().relative_to(TESTS_ROOT).parts
    except ValueError:
        return path.parts


def _should_prepare_executor_app(path: Path) -> bool:
    parts = _relative_test_parts(path)
    if len(parts) >= 2 and parts[0] == "unit" and parts[1] == "executor":
        return True
    return path.name in {
        "test_execution_store_finish_v1_unit.py",
        "test_executor_health_v1_execution_plane_unit.py",
        "test_r4g_c_executor_mq_worker_e2e_lite_unit.py",
        "test_runner_core_incremental_unit.py",
        "test_runner_core_usage_unit.py",
        "test_worker_daemon_sniff_pool_unit.py",
    }


@pytest.fixture(autouse=True)
def _trustguard_resolve_app_package(request: pytest.FixtureRequest) -> None:
    """编排器与执行器共用顶层包名 `app`；每个测试前按所在文件解析到对应服务源码树。"""
    from tests.executor_test_env import prepare_executor_app_import
    from tests.orchestrator_test_env import prepare_orchestrator_app_import

    parts = _relative_test_parts(request.node.path)
    if _should_prepare_executor_app(request.node.path):
        prepare_executor_app_import()
    elif len(parts) >= 2 and parts[0] == "unit" and parts[1] in {"dev_mq", "evidence", "skills", "scripts"}:
        pass
    elif parts and parts[0] in {"e2e", "integration", "smoke"}:
        pass
    else:
        prepare_orchestrator_app_import()
    yield


@pytest.fixture(scope="session")
def gateway_base_url():
    return GATEWAY_BASE


@pytest.fixture(scope="session")
def gateway_url(gateway_base_url):
    """集成测试用：若Gateway不可达，则跳过所有 integration 用例。用 GET /health 探活。"""
    try:
        r = httpx.get(
            f"{gateway_base_url}/health",
            timeout=10.0,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
        pytest.skip(
            f"Gateway不可达 ({gateway_base_url})，请先启动Gateway与 MySQL。错误: {e}"
        )
    if r.status_code != 200:
        pytest.skip(f"Gateway /health 返回非 200: {r.status_code} - {r.text[:200]}")
    return gateway_base_url
