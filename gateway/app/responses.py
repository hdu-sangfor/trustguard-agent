from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def ok(data: Any = None, message: str = "success") -> dict[str, Any]:
    return {"code": "0", "message": message, "data": data}


def fail(
    message: str,
    code: str = "BAD_REQUEST",
    data: Any = None,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "data": data},
    )
