# -*- coding: utf-8 -*-
"""服务端签名校验：FastAPI 依赖。

按收到的请求要素重建 CanonicalRequest，与 Authorization 中的 Signature 常量时间比较。
关键：用收到的原始 header 值与 sign-date 原样重建，不重排不重格式化。
"""
from __future__ import annotations

import hmac
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from . import canonical as C
from ..config import get_config

# 不需要签名的路径前缀/方法
OPEN_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


def _get_header_ci(request: Request, name: str) -> str | None:
    """大小写不敏感取 header（Starlette headers 本身大小写不敏感）。"""
    return request.headers.get(name)


def _parse_sign_date(sign_date: str) -> datetime | None:
    """解析 sign-date。

    原 SDK 用客户端本地时间生成 sign-date（标字面量 Z），故服务端按 naive 本地时间对比，
    容忍客户端与服务端的时区差异。带时区偏移的按其自身时区解析后转为本地。
    """
    if not sign_date:
        return None
    # 带字面量 Z 或无时区：按 naive 解析（与服务端 naive now 对比）
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S"):
        try:
            return datetime.strptime(sign_date, fmt)
        except ValueError:
            continue
    # 带时区偏移（Java 风格）
    for fmt in ("%Y%m%dT%H%M%S%z",):
        try:
            dt = datetime.strptime(sign_date, fmt)
            return dt.astimezone().replace(tzinfo=None)
        except ValueError:
            continue
    return None


def verify_request(request: Request) -> str:
    """FastAPI 依赖：校验请求签名，返回 ak。失败抛 401。"""
    path = request.url.path
    if path in OPEN_PATHS or path.startswith("/docs") or path.startswith("/openapi"):
        return "anonymous"

    cfg = get_config()
    credentials: dict = cfg.get("credentials", {})
    window = int(cfg.get("sign_date_window_seconds", 900))

    auth_value = _get_header_ci(request, C.AUTH_HEADER_KEY)
    if not auth_value:
        raise HTTPException(status_code=401, detail="missing Authorization header")

    parts = C.parse_authorization(auth_value)
    ak = parts.get(C.ACCESS)
    signed_headers_str = parts.get(C.SIGNED_HEADERS, "")
    claimed_sig = parts.get(C.SIGNATURE, "")

    if not ak or not claimed_sig:
        raise HTTPException(status_code=401, detail="invalid Authorization header")

    sk = credentials.get(ak)
    if sk is None:
        raise HTTPException(status_code=401, detail=f"unknown access key: {ak}")

    # sign-date 校验
    sign_date = _get_header_ci(request, C.SIGN_DATE_KEY) or ""
    if not sign_date:
        raise HTTPException(status_code=401, detail="missing sign-date header")
    sd = _parse_sign_date(sign_date)
    if sd is None:
        raise HTTPException(status_code=401, detail=f"unparsable sign-date: {sign_date}")
    now = datetime.now()
    if abs((now - sd).total_seconds()) > window:
        raise HTTPException(status_code=401, detail="sign-date expired")

    # 校验 sdk-host / sdk-content-type / sign-date 必须存在（客户端注入）
    for h in (C.SDK_HOST_KEY, C.SDK_CONTENT_TYPE_KEY, C.SIGN_DATE_KEY):
        if _get_header_ci(request, h) is None:
            raise HTTPException(status_code=401, detail=f"missing signed header: {h}")

    # 重建 canonical：按 SignedHeaders 列表顺序取收到的原始 header 值
    signed_headers = signed_headers_str.split(";") if signed_headers_str else []
    header_lines = []
    for h in signed_headers:
        val = _get_header_ci(request, h)
        if val is None:
            raise HTTPException(
                status_code=401, detail=f"signed header not present: {h}"
            )
        header_lines.append(f"{h}:{val}\n")
    header_str = "".join(header_lines)

    method = request.method.upper()
    raw_query = request.url.query
    query = C.query_transform_from_raw(raw_query)

    # 读取 body（已由 FastAPI 缓存需小心；此处用 receive 读取原始字节）
    body_bytes = b""
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        body_bytes = yield_from_body(request)

    canonical = C.build_canonical(
        method, path, query, header_str, signed_headers_str, body_bytes
    )
    hashed = C.sha256_hex_upper(canonical.encode("utf-8"))
    total_str = C.TOTAL_STR % (sign_date, hashed)
    expected = C.hmac_sha256_hex_upper(sk, total_str)

    if not hmac.compare_digest(expected, claimed_sig.upper()):
        raise HTTPException(status_code=401, detail="signature mismatch")
    return ak


def yield_from_body(request: Request) -> bytes:
    """同步读取请求体字节。FastAPI 中 request.body() 是 async，此处用 receive。

    为支持同步依赖，提供 async 版本 verify_request_async（实际中间件用）。
    本同步函数仅在非 FastAPI 上下文测试用。
    """
    # FastAPI Request.body 是协程；同步上下文无法直接 await。
    # 真正的校验在 async 依赖中进行，见 verify_request_async。
    raise RuntimeError("use verify_request_async in FastAPI context")


async def _read_body(request: Request) -> bytes:
    return await request.body()


async def verify_request_async(request: Request) -> str:
    """异步 FastAPI 依赖：校验签名，返回 ak。"""
    path = request.url.path
    if path in OPEN_PATHS or path.startswith("/docs") or path.startswith("/openapi"):
        return "anonymous"

    cfg = get_config()
    credentials: dict = cfg.get("credentials", {})
    window = int(cfg.get("sign_date_window_seconds", 900))

    auth_value = _get_header_ci(request, C.AUTH_HEADER_KEY)
    if not auth_value:
        raise HTTPException(status_code=401, detail="missing Authorization header")

    parts = C.parse_authorization(auth_value)
    ak = parts.get(C.ACCESS)
    signed_headers_str = parts.get(C.SIGNED_HEADERS, "")
    claimed_sig = parts.get(C.SIGNATURE, "")

    if not ak or not claimed_sig:
        raise HTTPException(status_code=401, detail="invalid Authorization header")

    sk = credentials.get(ak)
    if sk is None:
        raise HTTPException(status_code=401, detail=f"unknown access key: {ak}")

    sign_date = _get_header_ci(request, C.SIGN_DATE_KEY) or ""
    if not sign_date:
        raise HTTPException(status_code=401, detail="missing sign-date header")
    sd = _parse_sign_date(sign_date)
    if sd is None:
        raise HTTPException(status_code=401, detail=f"unparsable sign-date: {sign_date}")
    now = datetime.now()
    if abs((now - sd).total_seconds()) > window:
        raise HTTPException(status_code=401, detail="sign-date expired")

    for h in (C.SDK_HOST_KEY, C.SDK_CONTENT_TYPE_KEY, C.SIGN_DATE_KEY):
        if _get_header_ci(request, h) is None:
            raise HTTPException(status_code=401, detail=f"missing signed header: {h}")

    signed_headers = signed_headers_str.split(";") if signed_headers_str else []
    header_lines = []
    for h in signed_headers:
        val = _get_header_ci(request, h)
        if val is None:
            raise HTTPException(
                status_code=401, detail=f"signed header not present: {h}"
            )
        header_lines.append(f"{h}:{val}\n")
    header_str = "".join(header_lines)

    method = request.method.upper()
    raw_query = request.url.query
    query = C.query_transform_from_raw(raw_query)

    body_bytes = (
        await _read_body(request)
        if method in ("POST", "PUT", "PATCH", "DELETE")
        else b""
    )

    canonical = C.build_canonical(
        method, path, query, header_str, signed_headers_str, body_bytes
    )
    hashed = C.sha256_hex_upper(canonical.encode("utf-8"))
    total_str = C.TOTAL_STR % (sign_date, hashed)
    expected = C.hmac_sha256_hex_upper(sk, total_str)

    if not hmac.compare_digest(expected, claimed_sig.upper()):
        raise HTTPException(status_code=401, detail="signature mismatch")
    return ak
