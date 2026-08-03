from __future__ import annotations

import sys

import pytest

from tests.paths import REPO_ROOT


SUPERVISOR_ROOT = str(REPO_ROOT / "supervisor")
if SUPERVISOR_ROOT not in sys.path:
    sys.path.insert(0, SUPERVISOR_ROOT)


@pytest.fixture(autouse=True)
def _trustguard_resolve_app_package():
    """Keep the shared top-level ``app`` package bound to Supervisor here."""
    yield
