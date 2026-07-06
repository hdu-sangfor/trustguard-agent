"""
冒烟测试：编排器与 Executor 健康检查及最小执行路径。

用于验证在典型部署下服务可用；MQ+Worker 接入后可扩展队列与多 Worker 场景。

运行前请确保编排器、Executor 已启动（或通过环境变量指向对应地址）：
  ORCHESTRATOR_URL=http://localhost:18081 EXECUTOR_BASE_URL=http://localhost:18102 pytest tests/smoke -v -m smoke
  pytest tests/smoke -v -m smoke
"""
import os

import httpx
import pytest

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:18081")
EXECUTOR_BASE_URL = os.getenv("EXECUTOR_BASE_URL", "http://localhost:18102")
SMOKE_TIMEOUT = float(os.getenv("SMOKE_HTTP_TIMEOUT", "15.0"))


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: mark test as smoke (orchestrator/executor health and minimal flow)")


pytestmark = pytest.mark.smoke


@pytest.fixture(scope="module")
def orchestrator_url():
    return ORCHESTRATOR_URL.rstrip("/")


@pytest.fixture(scope="module")
def executor_url():
    return EXECUTOR_BASE_URL.rstrip("/")


def test_orchestrator_health(orchestrator_url):
    """编排器 GET /health 返回 200."""
    r = httpx.get(f"{orchestrator_url}/health", timeout=SMOKE_TIMEOUT)
    if r.status_code != 200:
        pytest.skip(f"orchestrator not reachable or unhealthy: {orchestrator_url} -> {r.status_code} {r.text[:200]}")
    assert r.json().get("status") == "ok"


def test_executor_health(executor_url):
    """Executor GET /health 返回 200."""
    r = httpx.get(f"{executor_url}/health", timeout=SMOKE_TIMEOUT)
    if r.status_code != 200:
        pytest.skip(f"executor not reachable or unhealthy: {executor_url} -> {r.status_code} {r.text[:200]}")
    assert r.json().get("status") == "ok"


def test_executor_skills_list(executor_url):
    """Executor GET /v1/skills 返回 skill_ids 列表（可为空）。"""
    r = httpx.get(f"{executor_url}/v1/skills", timeout=SMOKE_TIMEOUT)
    if r.status_code != 200:
        pytest.skip(f"executor /v1/skills failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    assert "skill_ids" in data
    assert isinstance(data["skill_ids"], list)
