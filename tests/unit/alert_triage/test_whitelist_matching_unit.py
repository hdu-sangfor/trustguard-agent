from alert_triage.whitelist_matching import match_whitelist_records


def _context(user: str = r"CORP\ops-automation"):
    return {
        "alert": {
            "hostIp": "192.168.2.15",
            "occurTimestamp": 1_700_000_000,
            "filePath": r"C:\Ops\approved_inventory.ps1",
            "userName": user,
            "changeTicket": "CHG-2026-0718-001",
        },
        "proof": {},
        "endpoint_logs": [],
        "assets": [],
    }


def _rule(**overrides):
    record = {
        "whiteId": "WL-1",
        "status": 1,
        "hostIp": "192.168.2.15",
        "isHostAll": False,
        "isUnlimited": False,
        "timeRange": {"start": 1_699_000_000, "end": 1_701_000_000},
        "ruleList": [
            {"field": "filePath", "operator": "equals", "value": r"C:\Ops\approved_inventory.ps1"},
            {"field": "userName", "operator": "equals", "value": r"CORP\ops-automation"},
            {"field": "changeTicket", "operator": "equals", "value": "CHG-2026-0718-001"},
        ],
    }
    record.update(overrides)
    return record


def test_all_constraints_must_match():
    context = _context()
    assert match_whitelist_records([_rule()], **context)[0]["whiteId"] == "WL-1"
    assert match_whitelist_records([_rule()], **_context(r"CORP\other")) == []


def test_expired_disabled_and_wrong_host_rules_do_not_match():
    context = _context()
    assert match_whitelist_records([_rule(timeRange={"start": 1, "end": 2})], **context) == []
    assert match_whitelist_records([_rule(status=0)], **context) == []
    assert match_whitelist_records([_rule(hostIp="10.0.0.1")], **context) == []


def test_unstructured_legacy_candidate_remains_compatible():
    assert match_whitelist_records([{"id": "legacy-1", "name": "candidate"}], **_context())
