# -*- coding: utf-8 -*-
"""CanonicalRequest 构建逻辑。

完全复现深信服 XDR OpenAPI 的签名算法（与官方 Python/Go/Java SDK 字节级一致）。
客户端（signer）与服务端（verifier）共用本模块的纯函数。

算法权威来源：OpenAPIDocument/python/authCodeDemo/aksk_py3.py
（Go/Java 实现等价；本模块以 Python py3 为基准，verifier 兼容 Go/Java 的差异）。
"""
from __future__ import annotations

import binascii
import hashlib
import hmac
import struct
import urllib.parse
from urllib.parse import urlparse

# ---- 常量（与 SDK constants 对齐）-----------------------------------------
EXTEND_HEADER = "algorithm=HMAC-SHA256, Access=%s, SignedHeaders=%s, Signature=%s"
TOTAL_STR = "HMAC-SHA256\n%s\n%s"
AUTH_HEADER_KEY = "Authorization"
SIGNED_HEADERS = "SignedHeaders"
SIGNATURE = "Signature"
ACCESS = "Access"
SDK_HOST_KEY = "sdk-host"
CONTENT_TYPE_KEY = "content-type"
SDK_CONTENT_TYPE_KEY = "sdk-content-type"
DEFAULT_CONTENT_TYPE = "application/json"
SIGN_DATE_KEY = "sign-date"

# 空请求体的 payload hash = SHA256(b"") 大写 hex
EMPTY_PAYLOAD_HASH = "E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855"


def sha256_hex_upper(data: bytes) -> str:
    """SHA256 摘要 → 大写 hex。"""
    return binascii.hexlify(hashlib.sha256(data).digest()).decode("utf-8").upper()


def hmac_sha256_hex_upper(secret_key: str, data: str) -> str:
    """HMAC-SHA256 → 大写 hex。key/message 均按 UTF-8 编码（与 SDK 一致）。"""
    mac = hmac.new(
        secret_key.encode("utf-8"), data.encode("utf-8"), hashlib.sha256
    )
    return binascii.hexlify(mac.digest()).decode("utf-8").upper()


def url_transform(path: str) -> str:
    """URL path 转义 + 强制末尾 '/'。

    SDK 输入是完整 url，此处接受纯 path（verifier 侧只有 path）。
    """
    relative_path = path
    if not relative_path.endswith("/"):
        relative_path += "/"
    return urllib.parse.quote(relative_path, encoding="utf-8")


def query_transform_from_params(params: dict) -> str:
    """客户端侧：从 params dict 构建规范 query 串。

    SDK Python py3：sorted by key → urlencode → %3D 替换为 =。
    """
    items = sorted(params.items(), key=lambda x: x[0])
    return urllib.parse.urlencode(items).replace("%3D", "=")


def query_transform_from_raw(raw_query: str) -> str:
    """服务端侧：从原始 query 字符串构建规范 query 串。

    兼容 Go/Java 行为：split '&' → 整 token 字典序排序 → 每个 token 必须含 '='
    → unescape+escape → %3D→'=' → '&' 拼接。空 query 返回 ''。
    """
    if not raw_query:
        return ""
    tokens = raw_query.split("&")
    # 过滤空 token（Go 实现会拒绝空 token，此处跳过）
    tokens = [t for t in tokens if t]
    tokens.sort()
    out = []
    for token in tokens:
        # 确保含 '='（Java：无 '=' 则补 '='）
        if "=" not in token:
            token = token + "="
        # unescape 再 escape，保持与客户端 urlencode 一致（quote_plus 语义）
        key, _, value = token.partition("=")
        encoded = urllib.parse.quote_plus(key) + "=" + urllib.parse.quote_plus(value)
        out.append(encoded)
    return "&".join(out)


def _remove_spaces(b: bytearray) -> bytearray:
    """移除所有 ASCII 空格(0x20)字节。"""
    j = 0
    for i in range(len(b)):
        if b[i] != 32:
            if i != j:
                b[j] = b[i]
            j += 1
    return b[:j]


def payload_transform(payload) -> str:
    """请求体变换：去空格 → 有符号字节排序 → SHA256 → 大写 hex。

    SDK 顺序为「先排序再去空格」，但排序与去空格可交换（去空格不改变剩余字节相对序），
    结果一致。此处沿用 SDK 顺序：先排序，再去空格。
    """
    if payload is None:
        payload = ""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    elif isinstance(payload, (bytes, bytearray)):
        payload = bytes(payload)
    else:
        payload = str(payload).encode("utf-8")

    if not payload:
        return EMPTY_PAYLOAD_HASH

    # 有符号 int8 解包后排序（0x80-0xFF 视为负数，排在 0x00-0x7F 之前）
    byte_values = [struct.unpack("b", bytes([byt]))[0] for byt in payload]
    byte_values.sort()
    new_payload = bytearray()
    for byte_value in byte_values:
        new_payload.append(byte_value & 0xFF)
    new_payload = _remove_spaces(new_payload)
    return sha256_hex_upper(bytes(new_payload))


def sign_header_handler(headers: dict) -> tuple[str, str]:
    """构建 header block 与 signed-headers 列表。

    返回 (header_str, sign_header_str)：
    - header_str: 每行 "key:value\\n"，按 key 小写排序
    - sign_header_str: "key;key;..." 无尾分号
    header key 用原始大小写，仅排序时大小写不敏感。
    """
    header_keys = [(k, v) for k, v in headers.items()]
    header_keys.sort(key=lambda x: x[0].lower())
    header_builder = []
    sign_header_builder = []
    for key, value in header_keys:
        header_builder.append(f"{key}:{value}\n")
        sign_header_builder.append(f"{key};")
    sign_header_str = "".join(sign_header_builder)
    header_str = "".join(header_builder)
    if sign_header_str:
        sign_header_str = sign_header_str[:-1]  # 去尾分号
    return header_str, sign_header_str


def build_canonical(
    method: str,
    path: str,
    query: str,
    header_str: str,
    sign_header_str: str,
    payload,
) -> str:
    """拼接 CanonicalRequest（五行，\\n 分隔）。"""
    return "".join(
        [
            method,
            "\n",
            url_transform(path),
            "\n",
            query,
            "\n",
            header_str,
            sign_header_str,
            "\n",
            payload_transform(payload),
        ]
    )


def build_canonical_from_parts(
    method: str,
    path: str,
    raw_query: str,
    headers: dict,
    payload,
) -> tuple[str, str]:
    """便捷封装：从原始要素构建 canonical，返回 (canonical_str, sign_header_str)。"""
    header_str, sign_header_str = sign_header_handler(headers)
    query = query_transform_from_raw(raw_query)
    canonical = build_canonical(
        method, path, query, header_str, sign_header_str, payload
    )
    return canonical, sign_header_str


def parse_authorization(auth_value: str) -> dict:
    """解析 Authorization header 值为 dict。

    格式：algorithm=HMAC-SHA256, Access=<ak>, SignedHeaders=<h1;h2>, Signature=<hex>
    兼容 Go 的 Algorithm=（大写 A）。按 ', ' 分段，每段按首个 '=' 切分。
    """
    result: dict[str, str] = {}
    if not auth_value:
        return result
    # 按 ", " 分段；Signature 是 hex 无逗号，SignedHeaders 含 ';' 无逗号，故安全
    segments = auth_value.split(", ")
    for seg in segments:
        if "=" not in seg:
            continue
        k, _, v = seg.partition("=")
        # 归一化 key：algorithm/Algorithm → algorithm
        key = k.strip()
        if key.lower() == "algorithm":
            key = "algorithm"
        result[key] = v
    return result
