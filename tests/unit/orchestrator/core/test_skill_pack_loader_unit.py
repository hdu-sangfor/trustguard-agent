import json
import os
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

_ORCH_ROOT = str(REPO_ROOT / "orchestrator")
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)


def test_load_custom_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILLS_ROOT", str(tmp_path))
    (tmp_path / "demo-skill").mkdir()
    (tmp_path / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo\nversion: 1\n---\n\n# Body here\n",
        encoding="utf-8",
    )
    (tmp_path / "demo-skill" / "_meta.json").write_text(
        json.dumps({"slug": "demo-skill", "entrypoint": "scripts/x.py"}),
        encoding="utf-8",
    )

    from app.core.skill_pack_loader import load_skill_pack

    pack = load_skill_pack("demo-skill")
    assert pack.skill_id == "demo-skill"
    assert pack.skill_front_matter is not None
    assert pack.skill_front_matter.get("name") == "demo"
    assert pack.skill_md_body and "Body here" in pack.skill_md_body
    assert pack.meta.get("slug") == "demo-skill"
    assert pack.meta_path


def test_unknown_pack_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILLS_ROOT", str(tmp_path))
    from app.core.skill_pack_loader import SkillPackLoadError, load_skill_pack

    with pytest.raises(SkillPackLoadError) as ei:
        load_skill_pack("no-such-skill")
    assert ei.value.code == "SKILL_PACK_UNKNOWN_SKILL"


def test_missing_skill_md(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILLS_ROOT", str(tmp_path))
    (tmp_path / "empty-skill").mkdir()
    from app.core.skill_pack_loader import SkillPackLoadError, load_skill_pack

    with pytest.raises(SkillPackLoadError) as ei:
        load_skill_pack("empty-skill")
    assert ei.value.code == "SKILL_PACK_MISSING_SKILL_MD"


def test_invalid_skill_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILLS_ROOT", str(Path(".").resolve()))
    from app.core.skill_pack_loader import SkillPackLoadError, load_skill_pack

    with pytest.raises(SkillPackLoadError) as ei:
        load_skill_pack("bad/id")
    assert ei.value.code == "SKILL_PACK_INVALID_ID"


def test_http_get_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILLS_ROOT", str(tmp_path))
    (tmp_path / "http-s").mkdir()
    (tmp_path / "http-s" / "SKILL.md").write_text("# X\n", encoding="utf-8")

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.get("/v1/orchestrator/skills/http-s/pack")
    assert r.status_code == 200
    body = r.json()
    assert body["skill_id"] == "http-s"
    assert body["skill_md_raw"] == "# X\n"


def test_load_dispatcher_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = REPO_ROOT
    skills_root = repo / "skills"
    if not (skills_root / "dispatcher" / "SKILL.md").is_file():
        pytest.skip("skills/dispatcher not available")
    monkeypatch.setenv("SKILLS_ROOT", str(skills_root))
    monkeypatch.setenv("TOOLS_REGISTRY_YAML", str(repo / "docker" / "tools_registry.yaml"))

    from app.core.skill_pack_loader import load_skill_pack

    pack = load_skill_pack("dispatcher")
    assert pack.skill_id == "dispatcher"
    assert pack.skill_md_raw
    assert pack.meta.get("slug") == "dispatcher" or pack.meta.get("entrypoint")
    assert pack.registry_entry is not None
    assert pack.registry_entry.get("runner") == "python"
