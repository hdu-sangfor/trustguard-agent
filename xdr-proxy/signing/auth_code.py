# -*- coding: utf-8 -*-
"""authCode → ak/sk 解码。

复现官方 SDK 的 AES 解密逻辑：
hexdecode(authCode) → UTF-8 字符串 → split('|') 14 段 →
seed = fields[0..6] + fields[11]，'+' 拼接 → SHA256 得 AES-256 key →
AES/CBC/NoPadding, IV=16 零字节 解 fields[9](ak)/fields[10](sk)。

依赖 pycryptodome（SDK 同款）。若未安装，仅 ak/sk 直接配置模式可用。
"""
from __future__ import annotations

import binascii
import hashlib

AUTH_CODE_PARAMS = "%s+%s+%s+%s+%s+%s+%s+%s"
AUTH_CODE_PARAMS_NUM = 14

try:
    from Crypto.Cipher import AES  # type: ignore

    _HAS_CRYPTO = True
except Exception:  # pragma: no cover - pycryptodome 可选
    _HAS_CRYPTO = False


def _calculate_aes_secret(builders: list[str]) -> bytes:
    build_str = AUTH_CODE_PARAMS % (
        builders[0], builders[1], builders[2], builders[3],
        builders[4], builders[5], builders[6], builders[11],
    )
    return hashlib.sha256(build_str.encode("utf-8")).digest()


def _aes_cbc_decrypt(cipher_text_hex: str, key: bytes) -> str:
    if not _HAS_CRYPTO:
        raise RuntimeError(
            "pycryptodome 未安装，无法解码 authCode；请用 ak/sk 直接配置"
        )
    cipher = AES.new(key, AES.MODE_CBC, bytearray(AES.block_size))
    plain = cipher.decrypt(bytes.fromhex(cipher_text_hex))
    # Java 会 trim；Python/Go 不 trim。去尾空白/NUL 以兼容
    return plain.decode("utf-8").rstrip("\x00").strip()


def decode_auth_code(auth_code: str) -> tuple[str, str]:
    """从 authCode 解出 (ak, sk)。"""
    builder_str = binascii.unhexlify(auth_code)
    builders = builder_str.decode("utf-8").split("|")
    if len(builders) != AUTH_CODE_PARAMS_NUM:
        raise ValueError(
            f"auth code decode error: expect {AUTH_CODE_PARAMS_NUM} fields, "
            f"got {len(builders)}"
        )
    aes_secret = _calculate_aes_secret(builders)
    ak = _aes_cbc_decrypt(builders[9], aes_secret)
    sk = _aes_cbc_decrypt(builders[10], aes_secret)
    return ak, sk
