from __future__ import annotations

import os
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "trustguard")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "trustguard")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "trustguard_agent")


def connection():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=DictCursor,
    )


def query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall() or [])


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with connection() as conn, conn.cursor() as cursor:
        return int(cursor.execute(sql, params))
