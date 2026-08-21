# -*- coding: utf-8 -*-
"""XDR Proxy — signing proxy for frontend-to-XDR-Mock communication."""
from __future__ import annotations

import asyncio, json, ssl, uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from signing.signer import Signature

XDR_BASE = "http://localhost:8443"
XDR_AK = "test_ak_0001"
XDR_SK = "test_sk_0001_secret"
def _get_signer(): return Signature(ak=XDR_AK, sk=XDR_SK)
_signer = None  # created lazily per request

app = FastAPI(title="XDR Proxy", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
_triage_tasks: dict[str, dict[str, Any]] = {}

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

async def _xdr_get(path: str) -> dict:
    url = f"{XDR_BASE}{path}"
    headers = _get_signer().sign_headers("GET", url)
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.get(url, headers=headers, timeout=15.0)
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text[:512])
        return resp.json()

async def _xdr_post(path: str, body: dict | None = None) -> dict:
    url = f"{XDR_BASE}{path}"
    body_bytes = json.dumps(body).encode() if body else b"{}"
    headers = _get_signer().sign_headers("POST", url, body=body_bytes)
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(url, content=body_bytes, headers=headers, timeout=15.0)
        if resp.status_code >= 400:
            detail = resp.text[:512]
            try: detail = json.loads(detail)
            except Exception: pass
            raise HTTPException(status_code=resp.status_code, detail=detail)
        return resp.json()

# ── Health ──────────────────────────────────────────────────────
@app.get("/api/xdr-proxy/health")
async def xdr_proxy_health():
    try:
        result = await _xdr_get("/health")
        return {"status": "ok", "xdrMockStatus": result.get("status"), "serverTime": result.get("serverTime")}
    except HTTPException:
        return {"status": "error", "xdrMockStatus": "unreachable"}

# ── Alerts ──────────────────────────────────────────────────────
@app.post("/api/xdr-proxy/alerts/list")
async def list_alerts(request: Request):
    body = await request.json() if await request.body() else {}
    return await _xdr_post("/api/xdr/v1/alerts/list", body)

@app.get("/api/xdr-proxy/alerts/{alert_uuid}/proof")
async def alert_proof(alert_uuid: str):
    return await _xdr_get(f"/api/xdr/v1/alerts/{alert_uuid}/proof")

# ── Incidents ───────────────────────────────────────────────────
@app.post("/api/xdr-proxy/incidents/list")
async def list_incidents(request: Request):
    body = await request.json() if await request.body() else {}
    return await _xdr_post("/api/xdr/v1/incidents/list", body)

@app.get("/api/xdr-proxy/incidents/{incident_uuid}/proof")
async def incident_proof(incident_uuid: str):
    return await _xdr_get(f"/api/xdr/v1/incidents/{incident_uuid}/proof")

# ── Assets ──────────────────────────────────────────────────────
@app.post("/api/xdr-proxy/assets/list")
async def list_assets(request: Request):
    body = await request.json() if await request.body() else {}
    return await _xdr_post("/api/xdr/v1/assets/list", body)

# ── Whitelists ──────────────────────────────────────────────────
@app.post("/api/xdr-proxy/whitelists/list")
async def list_whitelists(request: Request):
    body = await request.json() if await request.body() else {}
    return await _xdr_post("/api/xdr/v1/whitelists/list", body)

@app.post("/api/xdr-proxy/whitelists/match")
async def match_whitelists(request: Request):
    body = await request.json() if await request.body() else {}
    return await _xdr_post("/api/trustguard-mock/v1/query/whitelist-match", body)

# ── Security Logs ───────────────────────────────────────────────
@app.post("/api/xdr-proxy/security-log/list")
async def list_security_logs(request: Request):
    body = await request.json() if await request.body() else {}
    return await _xdr_post("/api/xdr/v1/securitylog/list", body)

# ── Triage Tasks (MVP in-memory) ────────────────────────────────
@app.post("/api/xdr-proxy/triage/tasks")
async def create_triage_task(request: Request):
    body = await request.json()
    alert_uuid = body.get("alertUuid")
    if not alert_uuid:
        raise HTTPException(status_code=400, detail="alertUuid is required")
    task_id = str(uuid.uuid4())
    task = {
        "taskId": task_id, "alertUuid": alert_uuid,
        "status": "PENDING", "verdict": None, "confidence": None,
        "severity": None, "summary": "", "reasoning": "",
        "ragEnabled": bool(body.get("ragEnabled", False)),
        "createdAt": _now_iso(), "finishedAt": None,
    }
    _triage_tasks[task_id] = task
    # 启动异步研判 —— 先 yield 让 HTTP 响应先返回，确保前端看到 PENDING 状态
    asyncio.create_task(_run_triage(task_id))
    await asyncio.sleep(0)  # yield event loop, 让后台任务被调度但不阻塞 HTTP 响应
    return {"code": "0", "data": task}

@app.get("/api/xdr-proxy/triage/tasks")
async def list_triage_tasks():
    tasks = sorted(_triage_tasks.values(), key=lambda t: t["createdAt"], reverse=True)
    return {"code": "0", "data": tasks}

@app.get("/api/xdr-proxy/triage/tasks/{task_id}")
async def get_triage_task(task_id: str):
    task = _triage_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="triage task not found")
    return {"code": "0", "data": task}

# ── MVP triage engine ──────────────────────────────────────────
async def _run_triage(task_id: str):
    task = _triage_tasks.get(task_id)
    if not task: return
    task["status"] = "RUNNING"
    alert_uuid = task["alertUuid"]
    await asyncio.sleep(0.5)   # 短延迟：让前端先 poll 到 RUNNING
    await asyncio.sleep(2.5)  # 模拟真实分析耗时
    try:
        alert_proof_data = await _xdr_get(f"/api/xdr/v1/alerts/{alert_uuid}/proof")
        ad = alert_proof_data.get("data", alert_proof_data)
        task["alertSummary"] = {
            "name": ad.get("name", ""),
            "uuId": ad.get("uuId", alert_uuid),
            "severity": ad.get("severity"),
            "threatDefine": ad.get("threatDefine"),
            "direction": ad.get("direction"),
            "proofType": ad.get("proofType"),
            "proofSummary": _summarize_proof(ad.get("proof", {})),
        }
    except HTTPException as e:
        task["status"] = "FAILED"
        task["errors"] = [f"Alert fetch failed: {e.detail}"]
        return

    # Whitelist check
    try:
        wl_resp = await _xdr_post("/api/xdr/v1/whitelists/list", {"page": 1, "pageSize": 10, "filter": {"status": 1}})
        wl_items = _extract_list(wl_resp)
        task["matchedWhitelists"] = wl_items[:3]
    except HTTPException:
        task["matchedWhitelists"] = []

    # RAG placeholder
    task["ragCitations"] = []
    task["ragDegraded"] = True
    task["ragNote"] = "RAG 服务未接入，本期暂以占位模式运行"

    # Verdict
    alert_name = (task.get("alertSummary", {}).get("name") or "").lower()
    sev = task.get("alertSummary", {}).get("severity", 0)
    wl_matched = len(task.get("matchedWhitelists", [])) > 0

    verdict, conf, reasoning = _rule_verdict(alert_name, sev or 0, wl_matched, task.get("alertSummary", {}))
    proof_text = task.get("alertSummary", {}).get("proofSummary", "")
    if not proof_text or len(proof_text) < 20:
        task["missingEvidence"] = ["告警 proof 缺失或不完整"]
        if verdict == "suspicious":
            conf = min(conf, 0.45)
            reasoning += " 注意：proof 信息量过少，置信度已下调。建议补充证据后复核。"
        elif verdict == "insufficient_evidence":
            reasoning = "告警证据不足：proof 字段缺失或信息量过少。无法做出可靠研判。"
    else:
        task["missingEvidence"] = []

    task.update(status="DONE", finishedAt=_now_iso(), verdict=verdict, confidence=conf,
                severity=sev, summary=f'告警「{ad.get("name","未知")}」研判完成',
                reasoning=reasoning, recommendedActions=_gen_actions(verdict),
                warnings=["RAG 服务不可用，研判仅基于 XDR 原始证据"] if task["ragDegraded"] else [])

def _summarize_proof(proof: dict) -> str:
    parts = []
    if proof.get("description"): parts.append(str(proof["description"])[:200])
    chain = proof.get("processChain")
    if isinstance(chain, dict) and chain: parts.append(f"进程链: {json.dumps(chain,ensure_ascii=False)[:200]}")
    story = proof.get("attackStory")
    if isinstance(story, list) and story: parts.append(f"攻击故事: {len(story)} 步骤")
    log_ids = proof.get("logIds")
    if isinstance(log_ids, list) and log_ids: parts.append(f"关联日志: {len(log_ids)} 条")
    return " | ".join(parts) if parts else "无详细证据"

def _rule_verdict(name: str, sev: int, wl: bool, summary: dict) -> tuple[str,float,str]:
    proof = summary.get("proofSummary", "")
    if wl and ("powershell" in name or "资产盘点" in proof):
        return ("false_positive", 0.92, "该告警触发了已知的已审批资产盘点脚本，已在白名单中。建议维持白名单策略。")
    suspicious = ["ransomware","勒索","webshell","反弹shell","reverse shell","mimikatz","psexec","凭证窃取","提权","privilege escalation","钓鱼","phishing","病毒","trojan","木马","shell","后门","backdoor","挖矿","miner","cobalt strike","cs beacon"]
    if any(kw in name or kw in proof.lower() for kw in suspicious):
        return ("true_positive", 0.88, f"告警证据包含明确恶意特征关键字，严重级别 {sev}。建议立即隔离终端、封禁来源IP。")
    if sev >= 3:
        return ("suspicious", 0.65, f"告警严重级别 {sev}，具有风险但证据不充分。建议持续监控并补充日志后重新研判。")
    return ("insufficient_evidence", 0.35, "当前告警证据不够充分，无法形成明确研判结论。建议补充终端进程链和网络日志。")

def _gen_actions(verdict: str) -> list[dict]:
    if verdict == "true_positive":
        return [
            {"action":"isolate_endpoint","label":"隔离终端","category":"manual_required"},
            {"action":"block_ip","label":"封禁来源IP","category":"manual_required"},
        ]
    elif verdict == "suspicious":
        return [
            {"action":"monitor","label":"持续监控72h","category":"safe_auto"},
            {"action":"manual_review","label":"提交人工研判","category":"manual_required"},
        ]
    elif verdict == "false_positive":
        return [
            {"action":"keep_whitelist","label":"维持白名单","category":"safe_auto"},
            {"action":"close_alert","label":"关闭告警","category":"manual_required"},
        ]
    else:
        return [
            {"action":"collect_logs","label":"补充日志","category":"manual_required"},
            {"action":"manual_review","label":"人工研判","category":"manual_required"},
        ]

def _extract_list(resp: dict) -> list:
    if "data" in resp:
        d = resp["data"]
        if isinstance(d, dict): return d.get("list", d.get("items", d.get("data", [])))
        if isinstance(d, list): return d
    return resp.get("list", resp.get("items", resp.get("data", [])))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=18090)
