"""
KB-R1c：从 nmap parsed_artifacts 抽取结构化微特征（供 Experience Chunk / 检索意图投影）。

约定：`extract_kb_features(artifacts, context) -> dict`（可 JSON 序列化，体积须小）。
"""

from __future__ import annotations

from typing import Any, Dict


def extract_kb_features(artifacts: Dict[str, Any] | None, context: Dict[str, Any] | None) -> Dict[str, Any]:
    a = artifacts if isinstance(artifacts, dict) else {}
    open_ports = a.get("open_ports")
    if not isinstance(open_ports, list):
        open_ports = []
    ports_i: list[int] = []
    for p in open_ports[:48]:
        try:
            ports_i.append(int(p))
        except (TypeError, ValueError):
            continue

    services = a.get("services")
    names: list[str] = []
    if isinstance(services, list):
        for s in services[:32]:
            if not isinstance(s, dict):
                continue
            n = str(s.get("name") or "").strip()
            if n:
                names.append(n)

    port_states = a.get("port_states")
    open_count = 0
    if isinstance(port_states, dict):
        for k, plist in port_states.items():
            if str(k).lower() != "open" or not isinstance(plist, list):
                continue
            open_count += len(plist)

    intent_parts = [f"nmap ports={len(ports_i)}"]
    if names:
        intent_parts.append("svc=" + ",".join(sorted(set(names))[:8]))
    if ports_i:
        intent_parts.append("sample=" + ",".join(str(x) for x in ports_i[:12]))
    intent_projection = " ".join(intent_parts)[:320]

    return {
        "skill_id": "nmap",
        "open_port_count": len(ports_i),
        "open_ports_head": ports_i[:24],
        "service_names_head": names[:16],
        "port_states_open_total": open_count,
        "intent_projection": intent_projection,
    }
