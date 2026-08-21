# -*- coding: utf-8 -*-
"""客户端签名器：为出站请求生成签名 header。

与官方 SDK Signature 类行为一致：签名后注入
Authorization / sdk-host / sdk-content-type / sign-date 四个 header。
签名后不可再修改请求（与 readme.pdf 约束一致）。
"""
from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import canonical as C
from .auth_code import decode_auth_code


class Signature:
    """与官方 SDK 同名的签名器，支持 ak/sk 直接配置或 authCode 解码。"""

    def __init__(self, auth_code: str | None = None, ak: str | None = None, sk: str | None = None):
        if ak and sk:
            self._access_key = ak
            self._secret_key = sk
        elif auth_code:
            self._access_key, self._secret_key = decode_auth_code(auth_code)
        else:
            raise ValueError("signature init error: need (ak, sk) or auth_code")

    # ---- 便捷方法：直接对一个请求要素签名，返回应注入的 header dict ----
    def sign_headers(
        self,
        method: str,
        url: str,
        headers: dict | None = None,
        params: dict | None = None,
        body=None,
        sign_date: str | None = None,
    ) -> dict:
        """计算签名并返回完整 header dict（含原 header + 注入的四个签名 header）。

        body 可为 str/bytes/dict/None。dict 会被 json.dumps。
        """
        headers = dict(headers) if headers else {}
        host = urlparse(url).netloc

        # header_check：注入 sdk-host / sdk-content-type / sign-date
        if C.SDK_HOST_KEY not in headers:
            headers[C.SDK_HOST_KEY] = host
        if C.CONTENT_TYPE_KEY not in headers:
            headers[C.SDK_CONTENT_TYPE_KEY] = C.DEFAULT_CONTENT_TYPE
        else:
            headers[C.SDK_CONTENT_TYPE_KEY] = headers[C.CONTENT_TYPE_KEY]
        if C.SIGN_DATE_KEY not in headers:
            sign_date = sign_date or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            headers[C.SIGN_DATE_KEY] = sign_date
        else:
            sign_date = headers[C.SIGN_DATE_KEY]

        # payload 归一化
        if body is None:
            payload = ""
        elif isinstance(body, (dict, list)):
            payload = _json.dumps(body)
        elif isinstance(body, bytes):
            payload = body
        else:
            payload = str(body)

        # header block + signed headers
        header_str, sign_header_str = C.sign_header_handler(headers)

        # canonical
        path = urlparse(url).path
        raw_query = urlparse(url).query
        # 客户端用 params dict（若有），否则用 url 自带 query
        if params:
            query = C.query_transform_from_params(params)
        else:
            query = C.query_transform_from_raw(raw_query)

        canonical = C.build_canonical(
            method.upper(), path, query, header_str, sign_header_str, payload
        )
        hashed = C.sha256_hex_upper(canonical.encode("utf-8"))
        total_str = C.TOTAL_STR % (sign_date, hashed)
        signature = C.hmac_sha256_hex_upper(self._secret_key, total_str)

        headers[C.AUTH_HEADER_KEY] = C.EXTEND_HEADER % (
            self._access_key, sign_header_str, signature
        )
        return headers

    def sign_request(self, req) -> None:
        """兼容官方 SDK 接口：对 requests.Request 对象就地签名。

        req 需有 .method/.url/.headers/.params/.data/.json（requests.Request 语义）。
        """
        method = req.method
        url = req.url
        headers = dict(req.headers) if req.headers else {}
        params = dict(req.params) if getattr(req, "params", None) else None

        if getattr(req, "data", None) is not None:
            body = req.data
        elif getattr(req, "json", None) is not None:
            body = req.json
        else:
            body = ""

        signed = self.sign_headers(method, url, headers, params, body)
        req.headers = signed
