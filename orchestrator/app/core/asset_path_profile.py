"""从爬虫/聚类 URL 列表推导资产轮廓，用于修正「根页弱指纹」对框架的误判。"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# 明显静态资源后缀：不参与「动态路径」占比
_STATIC_EXTS = frozenset(
    {
        "js",
        "mjs",
        "css",
        "map",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
        "svg",
        "ico",
        "woff",
        "woff2",
        "ttf",
        "eot",
        "mp4",
        "mp3",
        "pdf",
        "zip",
        "gz",
        "tar",
    }
)

_DO_PATH_RE = re.compile(r"\.do(?:\?|#|$|/|,)", re.I)
_ACTION_PATH_RE = re.compile(r"\.action(?:\?|#|$|/|,)", re.I)


def _last_path_segment_ext(url: str) -> str:
    try:
        p = urlparse(url)
        seg = (p.path or "").rstrip("/").split("/")[-1]
        if "." in seg:
            return seg.rsplit(".", 1)[-1].lower()
    except Exception:
        pass
    return ""


def _is_dynamic_url(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return False
    low = u.lower()
    if _ACTION_PATH_RE.search(low) or _DO_PATH_RE.search(low):
        return True
    if ".jsp" in low or ".jspx" in low:
        return True
    if ".php" in low:
        return True
    ext = _last_path_segment_ext(u)
    if ext in _STATIC_EXTS:
        return False
    # 无后缀或未知后缀的 path 仍计为动态候选（避免全被静态过滤光）
    return True


def compute_asset_path_profile(urls: list[str]) -> dict[str, Any]:
    """
    基于 URL 路径后缀分布给出 stack_hint，供框架识别与 Nuclei 模板选型。
    设计意图：路径模式权重高于单点根页（如 Tomcat 默认页）的弱证据。
    """
    dynamic: list[str] = []
    for raw in urls or []:
        s = str(raw).strip()
        if _is_dynamic_url(s):
            dynamic.append(s)

    n = len(dynamic)
    out: dict[str, Any] = {
        "dynamic_total": n,
        "suffix_counts": {"action_like": 0, "jsp": 0, "do": 0, "php": 0},
        "java_suffix_ratio": 0.0,
        "php_ratio": 0.0,
        "stack_hint": "",
    }
    if n == 0:
        return out

    action_like = 0
    jsp = 0
    do_ = 0
    php = 0
    java_like_urls = 0
    for u in dynamic:
        low = u.lower()
        if _ACTION_PATH_RE.search(low) or low.endswith(".action"):
            action_like += 1
        if ".jsp" in low or ".jspx" in low:
            jsp += 1
        if _DO_PATH_RE.search(low) or low.endswith(".do"):
            do_ += 1
        if ".php" in low:
            php += 1
        if (
            _ACTION_PATH_RE.search(low)
            or low.endswith(".action")
            or ".jsp" in low
            or ".jspx" in low
            or _DO_PATH_RE.search(low)
            or low.endswith(".do")
        ):
            java_like_urls += 1

    java_ratio = java_like_urls / n
    php_ratio = php / n
    out["suffix_counts"] = {"action_like": action_like, "jsp": jsp, "do": do_, "php": php, "java_like_urls": java_like_urls}
    out["java_suffix_ratio"] = round(java_ratio, 4)
    out["php_ratio"] = round(php_ratio, 4)

    # 与产品约定：显著比例的 Java 动态后缀 → 偏向 Struts/Java 栈（Nuclei temlists/struts2）
    if n >= 5 and java_ratio >= 0.15:
        out["stack_hint"] = "struts2_heavy"
    elif n >= 5 and php_ratio >= 0.25:
        out["stack_hint"] = "php_heavy"

    return out


def maybe_override_framework_from_asset_profile(ctx: dict[str, Any]) -> bool:
    """
    若路径轮廓强烈指向 Java/Struts，而当前仅有泛 Tomcat/空框架，则写回 framework_target。
    返回是否发生了覆盖。
    """
    if not isinstance(ctx, dict):
        return False
    ap = ctx.get("asset_path_profile")
    if not isinstance(ap, dict):
        return False
    hint = str(ap.get("stack_hint") or "").strip()
    if hint != "struts2_heavy":
        return False

    prev = str(ctx.get("framework_target") or ctx.get("framework_hint") or "").strip().lower()
    if prev == "struts2":
        return False

    # 弱证据：空、泛 tomcat、servlet 容器页，可被路径分布覆盖
    weak = (
        not prev
        or prev == "tomcat"
        or ("tomcat" in prev and "struts" not in prev)
        or prev in ("java", "jsp")
    )
    if not weak:
        return False

    ctx["framework_target"] = "struts2"
    ctx["framework_hint"] = "struts2"
    ctx["framework_path_override"] = True
    ctx["framework_path_override_reason"] = (
        f"asset_path_profile:{hint};java_suffix_ratio={ap.get('java_suffix_ratio')};dynamic_total={ap.get('dynamic_total')}"
    )
    return True
