from __future__ import annotations

from app.knowledge.query_builder import build_penetration_knowledge_query


def test_query_includes_framework_and_security_context_hints() -> None:
    candidate = build_penetration_knowledge_query(
        phase="VULN_SCAN",
        query_text=(
            "phase=VULN_SCAN target_scheme=http "
            "available_skills=nuclei,curl-raw,exploit-thinkphp"
        ),
        target_context={
            "framework_target": "thinkphp",
            "framework_hint": "ThinkPHP V5",
            "fingerprints": [
                "ThinkPHP V5",
                "PHP | http://host.docker.internal:8080",
            ],
            "confirmed_cve": ["CVE-2018-20148"],
            "vuln_confirmed": True,
            "exploit_ready": True,
        },
    )

    assert candidate is not None
    assert candidate.query.startswith("framework_target=thinkphp\n")
    assert "framework_hint=ThinkPHP V5" in candidate.query
    assert "fingerprints=[\"ThinkPHP V5\",\"PHP | [url]" in candidate.query
    assert "confirmed_cve=[\"CVE-2018-20148\"]" in candidate.query
    assert "vuln_confirmed=true" in candidate.query
    assert "exploit_ready=true" in candidate.query


def test_framework_change_invalidates_knowledge_cache_fingerprint() -> None:
    php = build_penetration_knowledge_query(
        phase="VULN_SCAN",
        query_text="phase=VULN_SCAN",
        target_context={"framework_target": "php"},
    )
    thinkphp = build_penetration_knowledge_query(
        phase="VULN_SCAN",
        query_text="phase=VULN_SCAN",
        target_context={"framework_target": "thinkphp"},
    )

    assert php is not None and thinkphp is not None
    assert php.fingerprint != thinkphp.fingerprint


def test_security_hints_are_preserved_when_base_query_is_long() -> None:
    candidate = build_penetration_knowledge_query(
        phase="VULN_SCAN",
        query_text="x" * 4000,
        target_context={
            "framework_target": "thinkphp",
            "fingerprints": ["ThinkPHP V5"],
        },
    )

    assert candidate is not None
    assert len(candidate.query) == 2000
    assert candidate.query.startswith(
        "framework_target=thinkphp\nfingerprints=[\"ThinkPHP V5\"]\n"
    )


def test_query_without_security_context_preserves_existing_behavior() -> None:
    candidate = build_penetration_knowledge_query(
        phase="RECON",
        query_text="  phase=RECON\n target_scheme=http  ",
        target_context={},
    )

    assert candidate is not None
    assert candidate.query == "phase=RECON target_scheme=http"
