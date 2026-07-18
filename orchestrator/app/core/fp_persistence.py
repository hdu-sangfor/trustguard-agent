"""
FP 数据 MySQL 持久化。

因 orchestrator 本身不直连 MySQL（由 Gateway/Evidence 负责），
这里复用 pymysql 直连模式，采用 autocommit + DictCursor，
与 gateway/evidence 的 _conn()/_execute() 模式一致。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import pymysql

logger = logging.getLogger(__name__)

MYSQL_HOST = os.getenv("MYSQL_HOST", "mysql")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "trustguard")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "trustguard")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "trustguard_agent")


def _conn():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with _conn() as conn:
        with conn.cursor() as cur:
            return int(cur.execute(sql, params))


def _query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall() or [])


def upsert_fp_record(record: dict[str, Any]) -> None:
    """写入或更新一条 FP 记录到 tg_fp_findings 表。"""
    try:
        _execute(
            """
            INSERT INTO tg_fp_findings
              (fp_id, task_id, finding_signature, template_id, url, title, severity,
               source_skill_id, source_phase, current_verdict, verification_source,
               verification_reasoning, detected_at, verified_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              current_verdict = VALUES(current_verdict),
              verification_source = VALUES(verification_source),
              verification_reasoning = VALUES(verification_reasoning),
              verified_at = VALUES(verified_at),
              updated_at = NOW()
            """,
            (
                record.get("fp_id", ""),
                record.get("task_id", ""),
                record.get("finding_signature", ""),
                record.get("template_id", ""),
                record.get("url", ""),
                record.get("title", ""),
                record.get("severity", "info"),
                record.get("source_skill_id", ""),
                record.get("source_phase", ""),
                record.get("current_verdict", "UNVERIFIED"),
                record.get("verification_source", ""),
                record.get("verification_reasoning", ""),
                _to_dt(record.get("detected_at")),
                _to_dt(record.get("verified_at")),
            ),
        )
    except Exception:
        logger.debug("fp_persistence: upsert failed for %s", record.get("fp_id"))


def load_fp_records(task_id: str) -> list[dict[str, Any]]:
    """从 MySQL 加载指定任务的所有 FP 记录。"""
    try:
        return _query(
            """
            SELECT fp_id, task_id, finding_signature, template_id, url, title, severity,
                   source_skill_id, source_phase, current_verdict, verification_source,
                   verification_reasoning, detected_at, verified_at
            FROM tg_fp_findings
            WHERE task_id = %s
            ORDER BY detected_at ASC
            """,
            (task_id,),
        )
    except Exception:
        logger.debug("fp_persistence: load failed for task %s", task_id)
        return []


def _to_dt(val: Any) -> Any:
    """确保 DATETIME 值可被 MySQL 接受；空字符串和 None 转为 NULL。"""
    if not val:
        return None
    return str(val)


def build_summary_from_db(task_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """从 DB 记录构建与 get_fp_summary() 相同结构的响应。"""
    total = len(records)
    verdicts = {"UNVERIFIED": 0, "SUSPICIOUS": 0, "FALSE_POSITIVE": 0, "TRUE_POSITIVE": 0, "INCONCLUSIVE": 0}
    for r in records:
        v = r.get("current_verdict", "UNVERIFIED")
        if v in verdicts:
            verdicts[v] += 1

    fp_count = verdicts["FALSE_POSITIVE"]
    tp_count = verdicts["TRUE_POSITIVE"]
    resolved = fp_count + tp_count
    fp_rate = (fp_count / resolved) if resolved > 0 else 0.0

    return {
        "taskId": task_id,
        "total": total,
        "unverified": verdicts["UNVERIFIED"],
        "suspicious": verdicts["SUSPICIOUS"],
        "falsePositives": fp_count,
        "truePositives": tp_count,
        "inconclusive": verdicts["INCONCLUSIVE"],
        "falsePositiveRate": round(fp_rate, 4),
        "findings": [
            {
                "fpId": r.get("fp_id"),
                "taskId": r.get("task_id"),
                "templateId": r.get("template_id"),
                "url": r.get("url"),
                "title": r.get("title"),
                "severity": r.get("severity"),
                "sourceSkillId": r.get("source_skill_id"),
                "sourcePhase": r.get("source_phase"),
                "currentVerdict": r.get("current_verdict"),
                "verificationSource": r.get("verification_source") or None,
                "verificationReasoning": r.get("verification_reasoning") or None,
                "detectedAt": str(r.get("detected_at") or ""),
                "verifiedAt": str(r.get("verified_at") or "") or None,
            }
            for r in records
        ],
    }
