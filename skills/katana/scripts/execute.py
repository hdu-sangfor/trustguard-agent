"""
katana: Katana (+ optional Dirsearch) → discovery/katana_urls.txt (+ dirsearch.json).
Defaults: depth=3, concurrency=40; stream to files (no huge stdout).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlparse

_ROOT = Path(__file__).resolve().parent.parent
_COMMON = _ROOT / "common"
if _COMMON.is_dir() and str(_COMMON) not in sys.path:
    sys.path.insert(0, str(_COMMON))

from etl import in_scope, scope_hosts_for_payload  # noqa: E402
from katana_io import (
    dirsearch_asset_seeds_from_json,
    dirsearch_urls_from_json,
    katana_urls_from_file,
)  # noqa: E402
from workspace_manifest import (  # noqa: E402
    discovery_dir,
    new_run_id,
    run_root,
    safe_task_id,
)
from workspace_resolve import resolve_under_workspace  # noqa: E402

_DEFAULT_KATANA_CRAWL_OUT_SCOPE: tuple[str, ...] = (
    r"(?i)(logout|signout|log-out|delete|remove|destroy|reboot|shutdown|insert|update)(/|$|\?)",
)
_DEFAULT_DIRSEARCH_WORDLIST_CATEGORIES = "common,web,java/spring,java/jsp"
_DEFAULT_KATANA_CRAWL_DURATION = "60s"
_DEFAULT_KATANA_EXCLUDE_EXTENSIONS = "css,js,mjs,png,jpg,jpeg,gif,svg,ico,webp,woff,woff2,ttf,map"
_DEFAULT_KATANA_SEED_WORDLIST_FILENAME = "katana_seed_paths.txt"
# 首轮 Katana -o 里几乎只有种子 URL 时追加的同源入口（.action/.do 不能作 seed，见 _asset_seed_is_actionish）
_DEFAULT_SPARSE_PROBE_PATHS: tuple[str, ...] = (
    "/showcase.jsp",
    "/index.jsp",
    "/welcome.jsp",
    "/home.jsp",
)


def _truthy(v: object) -> bool:
    if v is True:
        return True
    if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on"):
        return True
    return False


def _pick_target_url(params: dict[str, object], payload: dict[str, object]) -> str:
    for key in ("target_url", "url", "scan_url", "endpoint", "target"):
        v = params.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    t = payload.get("target")
    return str(t).strip() if t is not None else ""


def _run(cmd: list[str], timeout: int, cwd: str | None = None) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return -1, "", str(e)


def _parse_duration_seconds(raw: object) -> int:
    """Parse Katana -ct values like 60s, 2m — used to size subprocess budget."""
    s = str(raw or "").strip().lower()
    if not s:
        return 0
    try:
        if s.endswith("ms"):
            return max(0, int(s[:-2]) // 1000)
        if s.endswith("s"):
            return max(0, int(s[:-1] or 0))
        if s.endswith("m"):
            return max(0, int(s[:-1] or 0) * 60)
        if s.endswith("h"):
            return max(0, int(s[:-1] or 0) * 3600)
        if s.endswith("d"):
            return max(0, int(s[:-1] or 0) * 86400)
        return max(0, int(s))
    except ValueError:
        return 0


def _parse_katana_known_files(raw: str) -> list[str]:
    allowed = {"all", "robotstxt", "sitemapxml"}
    items: list[str] = []
    for tok in re.split(r"[,\s]+", (raw or "").strip().lower()):
        if tok in allowed and tok not in items:
            items.append(tok)
    return items


def _find_primary_dirsearch_wordlist() -> str | None:
    for p in (
        "/usr/local/dirsearch/db/dicc.txt",
        "/usr/local/dirsearch/db/categories/common.txt",
        "/usr/local/dirsearch/db/categories/web.txt",
    ):
        if os.path.exists(p):
            return p
    return None


def _skill_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _find_supplemental_wordlists() -> list[str]:
    extra = _skill_dir() / "wordlists" / "extra_j2ee_paths.txt"
    if extra.exists():
        return [str(extra)]
    for p in ("/skill/wordlists/extra_j2ee_paths.txt",):
        if os.path.exists(p):
            return [p]
    return []


def _merge_wordlist_arg(primary: str | None, extra: list[str]) -> str | None:
    parts = [p for p in ([primary] if primary else []) + extra if p and os.path.exists(p)]
    if not parts:
        return None
    return ",".join(parts)


def _load_default_sparse_probe_paths() -> list[str]:
    """
    从 skills/katana/wordlists/katana_seed_paths.txt 读取补种路径清单。
    文件不存在或为空时回退到内置默认值。
    """
    candidates = (
        _skill_dir() / "wordlists" / _DEFAULT_KATANA_SEED_WORDLIST_FILENAME,
        Path("/skill/wordlists") / _DEFAULT_KATANA_SEED_WORDLIST_FILENAME,
    )
    for fp in candidates:
        try:
            if not fp.exists():
                continue
            out: list[str] = []
            for raw in fp.read_text(encoding="utf-8", errors="replace").splitlines():
                s = (raw or "").strip()
                if not s or s.startswith("#"):
                    continue
                if not s.startswith("/"):
                    s = "/" + s
                out.append(s)
            if out:
                return out
        except Exception:
            continue
    return list(_DEFAULT_SPARSE_PROBE_PATHS)


def _spa_headless_from_params(params: dict[str, Any], context: dict[str, Any]) -> bool:
    if _truthy(params.get("headless_js")) or _truthy(params.get("katana_js_render")):
        return True
    ff = str(params.get("frontend_framework") or context.get("frontend_framework") or "").lower()
    if any(x in ff for x in ("vue", "react", "angular", "spa")):
        return True
    meta = context.get("metadata")
    if isinstance(meta, dict):
        st = str(meta.get("stack") or meta.get("framework") or "").lower()
        if any(x in st for x in ("vue", "react", "angular")):
            return True
    return False


def emit_result(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _to_ws_rel(path: Path, ws: str) -> str:
    try:
        return path.resolve().relative_to(Path(ws).resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _strip_matrix_params(u: str) -> str:
    # Katana/ETL 内部会处理 ;jsessionid=...，这里为 visited 指纹做同等的最小化处理
    return re.sub(r";jsessionid=[^/?#]+", "", u or "", flags=re.I)


def _normalize_asset_url_for_visit(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = f"http://{u}"
    if "#" in u:
        u = u.split("#", 1)[0].strip()
    u = _strip_matrix_params(u)
    p = urlparse(u)
    scheme = (p.scheme or "http").lower()
    netloc = (p.netloc or "").lower()
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    # visited 指纹只关心 URL 形态；查询值只按 key 折叠（id=1 与 id=2 视为同一节点）
    qkeys: list[str] = []
    try:
        for k, _v in parse_qsl(p.query or "", keep_blank_values=True):
            if k is not None:
                qkeys.append(str(k).strip().lower())
    except Exception:
        qkeys = []
    qkeys_uniq = sorted({x for x in qkeys if x})
    if qkeys_uniq:
        return f"{scheme}://{netloc}{path}?{'&'.join(qkeys_uniq)}"
    return f"{scheme}://{netloc}{path}"


def _path_depth(url: str) -> int:
    try:
        p = urlparse(url)
        path = p.path or "/"
    except Exception:
        path = "/"
    path = path.strip("/")
    if not path:
        return 0
    return len([s for s in path.split("/") if s])


def _seed_strict_crawl_scope_regex(seed: str) -> str | None:
    """
    将爬取范围限制在种子 URL 的 origin（scheme + host + port），避免跟到 SSO/WAF 外域。
    供 katana -cs 使用；与 -fs fqdn 叠加时以 PD 工具语义为准（通常互补）。
    """
    u = (seed or "").strip()
    if not u.startswith(("http://", "https://")):
        u = f"http://{u}"
    try:
        p = urlparse(u)
        host = (p.hostname or "").strip().lower()
        if not host:
            return None
        scheme = (p.scheme or "http").lower()
        other = "https" if scheme == "http" else "http"
        port = p.port
        he = re.escape(host)
        if port:
            pat = (
                rf"^({scheme}|{other})://{he}:{port}(/|\?|#|$)"
            )
        else:
            pat = rf"^({scheme}|{other})://{he}(:[0-9]+)?(/|\?|#|$)"
        return pat
    except Exception:
        return None


def _cluster_urls_for_targets(urls: list[str], max_items: int = 20, fallback_target: str = "") -> list[str]:
    """
    RECON 降维：去掉 query/matrix 噪声后，按目录层级+后缀聚类，抽取代表端点。
    目标是从海量 URL 压缩到 10-20 个高价值入口。
    """
    buckets: dict[tuple[str, str, int], str] = {}
    for raw in urls:
        u = (raw or "").strip()
        if not u.startswith(("http://", "https://")):
            continue
        try:
            p = urlparse(u)
        except Exception:
            continue
        host = (p.netloc or "").lower()
        path = re.sub(r";[^/?#]*", "", p.path or "/")
        if not path:
            path = "/"
        norm = f"{(p.scheme or 'http').lower()}://{host}{path}"
        segs = [x for x in path.strip("/").split("/") if x]
        depth = len(segs)
        suffix = segs[-1].rsplit(".", 1)[-1].lower() if segs and "." in segs[-1] else ""
        key = (host, suffix, depth)
        old = buckets.get(key)
        if old is None or len(norm) < len(old):
            buckets[key] = norm
    picked = sorted(set(buckets.values()))
    picked = picked[: max(1, int(max_items))]
    if picked:
        return picked
    fb = (fallback_target or "").strip()
    if fb and not fb.startswith(("http://", "https://")):
        fb = f"http://{fb}"
    if fb.startswith(("http://", "https://")):
        return [fb]
    return []


def _asset_seed_is_blacklisted(u: str) -> bool:
    low = (u or "").lower()
    return any(
        x in low
        for x in (
            "logout",
            "signout",
            "log-out",
            "/delete",
            "/remove",
            "/destroy",
            "/reboot",
            "/shutdown",
            "/insert",
            "/update",
        )
    )


def _asset_seed_is_actionish(u: str) -> bool:
    # 只根据 URL path 判断 action/do 端点，避免域名里出现 ".do" 子串误杀
    try:
        p = urlparse(u or "")
        path = (p.path or "").lower()
    except Exception:
        path = (u or "").lower()
    # 去掉 jsessionid matrix param，避免 "/foo.action;jsessionid=..." 最后一段不以 ".action" 结尾
    path = re.sub(r";jsessionid=[^/?#]+", "", path, flags=re.I)
    last = path.rstrip("/").split("/")[-1] if path else ""
    if not last:
        return False
    return last.endswith(".action") or last.endswith(".do")


def _asset_seed_allowed_for_queue(
    u: str,
    *,
    max_path_depth: int,
    banned_exts: set[str],
    html_like_exts: set[str],
) -> bool:
    if not u or _asset_seed_is_blacklisted(u) or _asset_seed_is_actionish(u):
        return False
    if max_path_depth >= 0 and _path_depth(u) > max_path_depth:
        return False
    try:
        p = urlparse(u)
        path = p.path or ""
    except Exception:
        path = ""
    last = path.rstrip("/").split("/")[-1] if path else ""
    ext = ""
    if "." in last:
        ext = last.rsplit(".", 1)[-1].lower()
    if ext and ext in banned_exts:
        return False
    if not ext:
        return True
    if ext in html_like_exts:
        return True
    return False


def _sparse_round_unique_urls(round_path: Path, seed: str) -> set[str]:
    if not round_path.exists():
        return set()
    return {_normalize_asset_url_for_visit(u) for u in katana_urls_from_file(round_path, fallback_base=seed) if u}


def _origin_probe_urls(seed: str, path_suffixes: list[str]) -> list[str]:
    u = (seed or "").strip()
    if not u.startswith(("http://", "https://")):
        u = f"http://{u}"
    p = urlparse(u)
    if not p.scheme or not p.netloc:
        return []
    root = f"{p.scheme}://{p.netloc}/"
    out: list[str] = []
    for raw in path_suffixes:
        s = (raw or "").strip()
        if not s:
            continue
        if not s.startswith("/"):
            s = "/" + s
        out.append(urljoin(root, s))
    return out


def main() -> int:
    start = time.perf_counter()
    raw_argv = sys.argv[1] if len(sys.argv) > 1 and str(sys.argv[1]).strip() else "{}"
    try:
        payload = json.loads(raw_argv)
    except Exception:
        emit_result(
            {
                "status": "FAILED",
                "parsed_artifacts": {"error": "invalid JSON argv"},
                "raw_stdout": "",
                "raw_stderr": "invalid json",
                "duration_ms": 0,
            }
        )
        return 1

    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    target = _pick_target_url(params, payload)
    if not target:
        emit_result(
            {
                "status": "FAILED",
                "parsed_artifacts": {"error": "params.target_url / url / target required"},
                "raw_stdout": "",
                "raw_stderr": "missing target",
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
        )
        return 1
    if not target.startswith(("http://", "https://")):
        target = f"http://{target}"

    task_id = safe_task_id(str(payload.get("task_id") or "local"))
    run_id = str(params.get("run_id") or "").strip() or new_run_id()

    ws = os.environ.get("WORKSPACE_ROOT", "/tmp")
    pre_base = str(context.get("executor_artifact_base") or "").strip()
    rr = run_root(ws, task_id, run_id)
    if pre_base:
        try:
            p = Path(pre_base)
            if not p.is_absolute():
                p = Path(ws) / p
            if len(p.parts) >= 4:
                task_root = p.parents[2]
                rr = task_root / "web-vuln" / run_id
        except Exception:
            pass

    try:
        rr.mkdir(parents=True, exist_ok=True)
    except Exception:
        rr = run_root(ws, task_id, run_id)
        rr.mkdir(parents=True, exist_ok=True)

    # 任务私有 discovery 目录：仅按 task_id/run_id 落盘，取消 host 级共享 targets 目录
    dd = discovery_dir(rr)
    dd.mkdir(parents=True, exist_ok=True)

    total_timeout = int(params.get("timeout") or params.get("discovery_timeout") or 600)
    budget_k = max(60, int(params.get("katana_timeout") or total_timeout // 2))
    budget_d = max(30, total_timeout - budget_k - 15)

    # 默认深度提升到 3，避免只停留在 seed/一级导航
    katana_depth = str(int(params.get("katana_depth") or 3))
    crawl_duration = str(params.get("katana_crawl_duration") or _DEFAULT_KATANA_CRAWL_DURATION).strip()
    if not crawl_duration:
        crawl_duration = _DEFAULT_KATANA_CRAWL_DURATION
    ct_sec = _parse_duration_seconds(crawl_duration)
    # 单阶段 Katana：子进程超时至少覆盖 -ct + 缓冲，避免未到点就被外层掐死
    budget_k = max(budget_k, ct_sec + 25 if ct_sec else 0)
    exclude_ext = str(params.get("katana_exclude_extensions") or _DEFAULT_KATANA_EXCLUDE_EXTENSIONS).strip()
    katana_conc = str(int(params.get("katana_concurrency") or 40))
    risk = str(params.get("risk_profile") or "normal").lower()
    if risk == "waf_suspected":
        katana_conc = str(min(int(katana_conc), 15))

    katana_form_extraction = _truthy(params.get("katana_form_extraction", True))
    # 默认开启 JS 爬取；显式传 false 可关闭（例如资源受限环境）
    katana_js_crawl = _truthy(params.get("katana_js_crawl", True))
    _kf_raw = params.get("katana_known_files")
    katana_known_files = "robotstxt,sitemapxml" if _kf_raw is None else str(_kf_raw).strip()

    auth_header = str(params.get("auth_header") or "").strip()

    deny_extra: list[str] = []
    kdp = params.get("katana_deny_patterns")
    if isinstance(kdp, list):
        deny_extra = [str(x) for x in kdp if str(x).strip()]
    elif isinstance(kdp, str) and kdp.strip():
        deny_extra = [kdp.strip()]

    katana_out = dd / "katana_urls.txt"
    dirsearch_json = dd / "dirsearch.json"

    wl_primary = _find_primary_dirsearch_wordlist()
    wl_merged = _merge_wordlist_arg(wl_primary, _find_supplemental_wordlists())
    wl = wl_merged or wl_primary
    _wlc = params.get("dirsearch_wordlist_categories")
    dir_wl_cats = _DEFAULT_DIRSEARCH_WORDLIST_CATEGORIES if _wlc is None else str(_wlc).strip()
    _disc = params.get("dirsearch_include_status_codes")
    dir_include_status = "100-999" if _disc is None else str(_disc).strip()
    default_ext = "php,jsp,html,htm,js,txt,action,do,aspx"
    dir_threads = int(params.get("dirsearch_threads") or 20)
    if risk == "waf_suspected":
        dir_threads = min(dir_threads, 8)

    # 按你的要求：从 katana 链路中去除内置 dirsearch，只保留“一个”外部 dirsearch。
    # 因此默认跳过；如需要临时恢复旧行为，显式传入 skip_dirsearch=false。
    skip_dirsearch = _truthy(params.get("skip_dirsearch", True))

    # =========================
    # 事件驱动：Katana 重入回灌
    # =========================
    # 仅做局部“微型调度器”，避免改动 orchestrator 全域状态机。
    katana_max_assets = int(params.get("katana_max_assets") or 8)
    katana_max_path_depth = int(params.get("katana_max_path_depth") or 4)
    katana_dirsearch_per_asset = _truthy(params.get("katana_dirsearch_per_asset", False))
    katana_dirsearch_max_assets = int(params.get("katana_dirsearch_max_assets") or 100)
    katana_dirsearch_asset_batch_limit = int(params.get("katana_dirsearch_asset_batch_limit") or 50)
    katana_katana_round_budget_ratio = float(params.get("katana_katana_round_budget_ratio") or 1.0)
    dirsearch_round_budget_ratio = float(params.get("dirsearch_round_budget_ratio") or 1.0)

    banned_exts = {x.strip().lower() for x in (exclude_ext or "").split(",") if x.strip()}
    html_like_exts = {"jsp", "jspx", "html", "htm", "xhtml", "php", "aspx"}

    katana_out_final = dd / "katana_urls.txt"
    clustered_targets_final = dd / "clustered_targets.txt"
    dirsearch_json_final = dd / "dirsearch.json"
    # 兜底任务私有落盘目录：即使共享目录映射异常，仍保证 task/web-vuln/<run_id>/discovery 可读
    task_discovery_dir = discovery_dir(rr)
    task_katana_out_final = task_discovery_dir / "katana_urls.txt"
    task_clustered_targets_final = task_discovery_dir / "clustered_targets.txt"
    task_dirsearch_json_final = task_discovery_dir / "dirsearch.json"

    phases_completed: list[str] = []
    partial = False
    katana_rounds = 0
    enqueued_assets = 0
    processed_assets = 0

    diag: dict[str, Any] = {
        "work_dir": _to_ws_rel(rr, ws),
        "run_id": run_id,
        "katana_depth": katana_depth,
        "katana_concurrency": katana_conc,
        "katana_crawl_duration": crawl_duration,
        "katana_exclude_extensions": exclude_ext,
        "katana_js_crawl": katana_js_crawl,
        "discovery_rel": "discovery",
        "katana_max_assets": katana_max_assets,
        "katana_max_path_depth": katana_max_path_depth,
        "katana_dirsearch_per_asset": katana_dirsearch_per_asset,
    }

    # 初始化资产队列：默认以 target 为首轮 seed；编排器可在 dirsearch 后下发二阶段仅爬 extra_seeds。
    asset_queue: deque[str] = deque()
    visited_assets: set[str] = set()

    first_seed_fp = _normalize_asset_url_for_visit(target)
    skip_primary = _truthy(params.get("katana_skip_initial_target"))
    if not skip_primary:
        if _asset_seed_allowed_for_queue(
            target,
            max_path_depth=katana_max_path_depth,
            banned_exts=banned_exts,
            html_like_exts=html_like_exts,
        ):
            asset_queue.append(target)
            visited_assets.add(first_seed_fp)
            processed_assets = 0
        else:
            asset_queue.append(target)
            visited_assets.add(first_seed_fp)
    else:
        visited_assets.add(first_seed_fp)
        processed_assets = 0

    _raw_extra = params.get("katana_extra_seeds") or params.get("extra_katana_seeds")
    if isinstance(_raw_extra, str) and _raw_extra.strip().startswith("["):
        try:
            _raw_extra = json.loads(_raw_extra)
        except Exception:
            _raw_extra = [_raw_extra]
    if isinstance(_raw_extra, list):
        for u in _raw_extra:
            su = str(u).strip() if u is not None else ""
            if not su.startswith(("http://", "https://")):
                continue
            if not _asset_seed_allowed_for_queue(
                su,
                max_path_depth=katana_max_path_depth,
                banned_exts=banned_exts,
                html_like_exts=html_like_exts,
            ):
                continue
            fp = _normalize_asset_url_for_visit(su)
            if not fp or fp in visited_assets:
                continue
            visited_assets.add(fp)
            asset_queue.append(su)

    if not asset_queue:
        emit_result(
            {
                "status": "FAILED",
                "parsed_artifacts": {
                    "error": "no katana seeds (empty queue after skip_initial / extra_seeds filter)",
                    "target_url": target,
                },
                "raw_stdout": "",
                "raw_stderr": "",
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
        )
        return 1

    katana_urls_set: set[str] = set()
    if katana_out_final.exists():
        try:
            for line in katana_out_final.read_text(encoding="utf-8", errors="replace").splitlines():
                u = line.strip()
                if u.startswith(("http://", "https://")):
                    katana_urls_set.add(u)
        except Exception:
            pass
    merged_dirsearch_results: list[dict[str, Any]] = []

    def _time_left_seconds() -> float:
        elapsed = time.perf_counter() - start
        return max(0.0, float(total_timeout) - elapsed)

    def _build_katana_cmd(seed: str, out_path: Path) -> list[str]:
        cmd = [
            "katana",
            "-u",
            seed,
            "-silent",
            "-d",
            katana_depth,
            "-c",
            katana_conc,
            "-ct",
            crawl_duration,
            "-o",
            str(out_path),
        ]
        # 始终 JSONL 写入 -o，便于 katana_io 解析；`-f qurl` 仅过滤输出列，不能修复“页面无链可跟”
        cmd.append("-j")
        if exclude_ext and exclude_ext.lower() not in ("0", "false", "none", "off"):
            cmd.extend(["-ef", exclude_ext])
        if katana_form_extraction:
            cmd.extend(["-fx"])
        # XHR 抽取可补充 SPA/API 导航线索
        cmd.extend(["-xhr-extraction"])
        if katana_known_files and katana_known_files.lower() not in ("0", "false", "none", "off"):
            for kf in _parse_katana_known_files(katana_known_files):
                cmd.extend(["-kf", kf])
        if auth_header:
            cmd.extend(["-H", auth_header])
        # 强制默认启用 -jc：即使上下文未显式标注 SPA，也尝试执行 JS 导航。
        if katana_js_crawl:
            cmd.append("-jc")
        # 同源范围：默认 fqdn + 种子 origin 正则，限制产物落在授权主机（如 host.docker.internal:8080）
        fs = str(params.get("katana_field_scope") or "fqdn").strip()
        if fs and fs.lower() not in ("0", "false", "none", "off", "no"):
            cmd.extend(["-fs", fs])
        if _truthy(params.get("katana_strict_crawl_scope", True)):
            cs_rx = str(params.get("katana_crawl_scope") or "").strip()
            if not cs_rx:
                cs_rx = _seed_strict_crawl_scope_regex(seed) or ""
            if cs_rx:
                cmd.extend(["-cs", cs_rx])
        for rx in _DEFAULT_KATANA_CRAWL_OUT_SCOPE:
            cmd.extend(["-crawl-out-scope", rx])
        for pat in deny_extra[:8]:
            p = (pat or "").strip()
            if p:
                cmd.extend(["-crawl-out-scope", p])
        return cmd

    def _run_katana_round(seed: str, round_idx: int) -> None:
        nonlocal partial, katana_rounds
        if katana_rounds >= katana_max_assets:
            return
        left = _time_left_seconds()
        if left <= 0:
            return
        round_out = dd / f"katana_urls_round_{round_idx}.txt"
        katana_cmd = _build_katana_cmd(seed, round_out)

        # 每个 round 给予相同比例预算，避免跑飞
        per_seed_timeout = max(10, int(budget_k * katana_katana_round_budget_ratio))
        per_seed_timeout = int(min(per_seed_timeout, max(10, left)))

        rc_k, out_k, err_k = _run(katana_cmd, timeout=per_seed_timeout)
        diag.setdefault("katana_rounds", []).append(
            {
                "round_idx": round_idx,
                "seed": seed,
                "katana_timeout": per_seed_timeout,
                "rc": rc_k,
            }
        )
        katana_rounds += 1

        if rc_k != 0:
            partial = True
            if err_k:
                diag["katana_round_stderr_sample"] = (err_k or "")[:2000]
            if out_k:
                diag["katana_round_stdout_sample"] = (out_k or "")[:2000]

        # 即使 katana 非 0，若输出文件存在也尽量解析
        if round_out.exists():
            phases_completed_local = "katana"
            katana_urls = katana_urls_from_file(round_out, fallback_base=seed)
            for u in katana_urls:
                if u:
                    katana_urls_set.add(u)
            if _asset_seed_allowed_for_queue(
                seed,
                max_path_depth=katana_max_path_depth,
                banned_exts=banned_exts,
                html_like_exts=html_like_exts,
            ):
                katana_urls_set.add(_normalize_asset_url_for_visit(seed).split("?", 1)[0])
            if phases_completed_local not in phases_completed:
                phases_completed.append(phases_completed_local)
        else:
            partial = True

    def _load_dirsearch_results(json_path: Path) -> list[dict[str, Any]]:
        if not json_path.exists():
            return []
        try:
            data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return []
        if isinstance(data, dict):
            rs = data.get("results")
            if isinstance(rs, list):
                out: list[dict[str, Any]] = []
                for item in rs:
                    if isinstance(item, dict):
                        out.append(item)
                return out
        return []

    def _run_dirsearch_round(seed: str, round_idx: int) -> None:
        nonlocal partial
        nonlocal merged_dirsearch_results
        if skip_dirsearch or not wl:
            return
        if len(merged_dirsearch_results) >= katana_dirsearch_max_assets:
            return
        left = _time_left_seconds()
        if left <= 0:
            return

        round_json = dd / f"dirsearch_round_{round_idx}.json"
        ds_cmd = [
            "dirsearch",
            "-u",
            seed,
            "-w",
            wl,
            "-t",
            str(dir_threads),
            "-e",
            str(params.get("extensions") or default_ext),
            "-O",
            "json",
            "-o",
            str(round_json),
            "-q",
        ]
        if dir_wl_cats:
            ds_cmd.extend(["--wordlist-categories", dir_wl_cats])
        if dir_include_status:
            ds_cmd.extend(["-i", dir_include_status])
        if auth_header:
            ds_cmd.extend(["--header", auth_header])

        per_seed_timeout = max(10, int(budget_d * dirsearch_round_budget_ratio))
        per_seed_timeout = int(min(per_seed_timeout, max(10, left)))
        rc_d, _, err_d = _run(ds_cmd, timeout=per_seed_timeout)
        diag.setdefault("dirsearch_rounds", []).append(
            {"round_idx": round_idx, "seed": seed, "dirsearch_timeout": per_seed_timeout, "rc": rc_d}
        )
        if rc_d != 0:
            partial = True
            if err_d:
                diag["dirsearch_round_stderr_sample"] = (err_d or "")[:2000]
        results = _load_dirsearch_results(round_json)
        if results:
            if len(merged_dirsearch_results) < katana_dirsearch_max_assets:
                merged_dirsearch_results.extend(results[: katana_dirsearch_max_assets - len(merged_dirsearch_results)])
            if "dirsearch" not in phases_completed:
                phases_completed.append("dirsearch")

    # 主循环：Katana seed 资产队列（dirsearch 只在首轮或按参数触发）
    dirsearch_done_for_first = False
    round_idx = 0
    while asset_queue and processed_assets < katana_max_assets and _time_left_seconds() > 0:
        asset = asset_queue.popleft()
        asset_fp = _normalize_asset_url_for_visit(asset)
        if asset_fp in visited_assets and asset_fp != first_seed_fp:
            # visited_assets 不区分“已出队未处理”，这里只做防重逻辑；主逻辑以 processed_assets 控制
            pass
        processed_assets += 1

        cur_round = round_idx
        _run_katana_round(asset, round_idx=cur_round)
        round_idx += 1

        # 首轮仅爬到根 URL 时：常见情况是“/ 返回壳页、真实导航在 /showcase.jsp 等同源页”（与 katana_io 过滤 .jsp 无关）
        if processed_assets == 1:
            ks_raw = params.get("katana_sparse_probe")
            if ks_raw is None:
                sparse_on = True
            else:
                sparse_on = _truthy(ks_raw)
            probe_path_list: list[str] = []
            if sparse_on:
                pl = params.get("katana_sparse_probe_paths")
                if isinstance(pl, list):
                    probe_path_list = [str(x).strip() for x in pl if str(x).strip()]
                elif isinstance(pl, str) and pl.strip():
                    probe_path_list = [x.strip() for x in pl.split(",") if x.strip()]
                else:
                    probe_path_list = _load_default_sparse_probe_paths()
            round0 = dd / "katana_urls_round_0.txt"
            if sparse_on and probe_path_list:
                uniq = _sparse_round_unique_urls(round0, asset)
                if len(uniq) <= 1:
                    enq: list[str] = []
                    # 本轮已占用 1 次 processed_assets，剩余可再跑的 Katana 轮次上限
                    probe_budget = max(0, katana_max_assets - processed_assets)
                    for u in _origin_probe_urls(asset, probe_path_list):
                        if len(enq) >= probe_budget:
                            break
                        if not _asset_seed_allowed_for_queue(
                            u,
                            max_path_depth=katana_max_path_depth,
                            banned_exts=banned_exts,
                            html_like_exts=html_like_exts,
                        ):
                            continue
                        fp = _normalize_asset_url_for_visit(u)
                        if not fp or fp in visited_assets:
                            continue
                        visited_assets.add(fp)
                        asset_queue.append(u)
                        enq.append(u)
                    if enq:
                        diag["katana_sparse_probes_enqueued"] = enq

        # dirsearch：只在首轮默认开启；若启用 per_asset，则对每个资产都跑（并用 merged_max_results 收敛）
        if not skip_dirsearch and wl and not dirsearch_done_for_first:
            _run_dirsearch_round(asset, round_idx=round_idx)
            dirsearch_done_for_first = True
            if len(merged_dirsearch_results) < katana_dirsearch_max_assets:
                round_json = dd / f"dirsearch_round_{round_idx}.json"
                seed_assets = dirsearch_asset_seeds_from_json(
                    round_json,
                    allowed_status=(200, 403),
                    max_assets=katana_dirsearch_asset_batch_limit,
                )
                for s in seed_assets[:katana_dirsearch_max_assets]:
                    if _asset_seed_allowed_for_queue(
                        s,
                        max_path_depth=katana_max_path_depth,
                        banned_exts=banned_exts,
                        html_like_exts=html_like_exts,
                    ):
                        fp = _normalize_asset_url_for_visit(s)
                        if fp and fp not in visited_assets:
                            visited_assets.add(fp)
                            asset_queue.append(s)
                            enqueued_assets += 1
            continue

        if katana_dirsearch_per_asset and not skip_dirsearch and wl:
            _run_dirsearch_round(asset, round_idx=round_idx)
            round_json = dd / f"dirsearch_round_{round_idx}.json"
            seed_assets = dirsearch_asset_seeds_from_json(
                round_json,
                allowed_status=(200, 403),
                max_assets=katana_dirsearch_asset_batch_limit,
            )
            for s in seed_assets[:katana_dirsearch_max_assets]:
                if _asset_seed_allowed_for_queue(
                    s,
                    max_path_depth=katana_max_path_depth,
                    banned_exts=banned_exts,
                    html_like_exts=html_like_exts,
                ):
                    fp = _normalize_asset_url_for_visit(s)
                    if fp and fp not in visited_assets:
                        visited_assets.add(fp)
                        asset_queue.append(s)
                        enqueued_assets += 1

    # 写出最终 katana_urls.txt 与 dirsearch.json（供 dispatcher prepare 读取）
    # katana_urls.txt：每行一个 URL（dispatcher/ETL 会做进一步 canonicalize + 去重）
    # 爬虫参数无法完全阻止页面内外链；在落盘前按任务主机白名单剔除第三方域名。
    scope_hosts_wr = scope_hosts_for_payload(target, payload)
    if scope_hosts_wr:
        _pre_k = len(katana_urls_set)
        katana_urls_set = {
            u for u in katana_urls_set if in_scope(u, scope_hosts_wr, allow_subdomains=False)
        }
        diag["katana_urls_dropped_scope"] = _pre_k - len(katana_urls_set)
        diag["write_scope_hosts"] = sorted(scope_hosts_wr)
        if merged_dirsearch_results:
            merged_dirsearch_results = [
                r
                for r in merged_dirsearch_results
                if isinstance(r, dict)
                and (
                    not (r.get("url") or r.get("URL"))
                    or in_scope(
                        str(r.get("url") or r.get("URL") or ""),
                        scope_hosts_wr,
                        allow_subdomains=False,
                    )
                )
            ]

    k_urls = sorted(katana_urls_set)
    katana_text = "\n".join(k_urls) + ("\n" if k_urls else "")
    clustered_urls = _cluster_urls_for_targets(
        k_urls,
        max_items=int(params.get("clustered_target_limit") or 20),
        fallback_target=target,
    )
    clustered_text = "\n".join(clustered_urls) + ("\n" if clustered_urls else "")
    katana_written_shared = False
    katana_written_task = False
    clustered_written_shared = False
    clustered_written_task = False
    try:
        katana_out_final.write_text(katana_text, encoding="utf-8")
        katana_written_shared = True
    except Exception:
        pass
    try:
        clustered_targets_final.write_text(clustered_text, encoding="utf-8")
        clustered_written_shared = True
    except Exception:
        pass
    # 无条件再写一份 task 私有副本，避免 shared_dir 映射问题导致“元数据有、文件无”
    try:
        task_discovery_dir.mkdir(parents=True, exist_ok=True)
        task_katana_out_final.write_text(katana_text, encoding="utf-8")
        katana_written_task = True
    except Exception:
        pass
    try:
        task_discovery_dir.mkdir(parents=True, exist_ok=True)
        task_clustered_targets_final.write_text(clustered_text, encoding="utf-8")
        clustered_written_task = True
    except Exception:
        pass

    # 仅当内置 dirsearch 被启用时，才写出 dirsearch.json，避免覆盖外部 dirsearch 的产物
    dirsearch_written_shared = False
    dirsearch_written_task = False
    if not skip_dirsearch:
        ds_text = json.dumps({"results": merged_dirsearch_results}, ensure_ascii=False)
        try:
            dirsearch_json_final.write_text(ds_text, encoding="utf-8")
            dirsearch_written_shared = True
        except Exception:
            pass
        try:
            task_discovery_dir.mkdir(parents=True, exist_ok=True)
            task_dirsearch_json_final.write_text(ds_text, encoding="utf-8")
            dirsearch_written_task = True
        except Exception:
            pass

    d_urls = dirsearch_urls_from_json(dirsearch_json_final) if not skip_dirsearch else []
    diag["katana_url_count"] = len(k_urls)
    diag["dirsearch_url_count"] = len(d_urls)
    diag["enqueued_assets_count"] = enqueued_assets
    diag["processed_assets_count"] = processed_assets
    diag["katana_written_shared"] = katana_written_shared
    diag["katana_written_task"] = katana_written_task
    diag["clustered_targets_count"] = len(clustered_urls)
    diag["clustered_written_shared"] = clustered_written_shared
    diag["clustered_written_task"] = clustered_written_task
    diag["task_discovery_dir"] = _to_ws_rel(task_discovery_dir, ws)
    diag["task_katana_urls_rel"] = _to_ws_rel(task_katana_out_final, ws)
    diag["task_clustered_targets_rel"] = _to_ws_rel(task_clustered_targets_final, ws)
    if not skip_dirsearch:
        diag["dirsearch_written_shared"] = dirsearch_written_shared
        diag["dirsearch_written_task"] = dirsearch_written_task
        diag["task_dirsearch_json_rel"] = _to_ws_rel(task_dirsearch_json_final, ws)

    pre_wsref = str(context.get("executor_artifact_ref") or "").strip()

    out = {
        # 与 partial_results 配合：Katana/dirsearch 非零退出仍可能已有可用输出文件，统一标 SUCCESS 由 partial_results 说明
        "status": "SUCCESS",
        "parsed_artifacts": {
            "run_id": run_id,
            "task_id": task_id,
            "target_url": target,
            "run_root": _to_ws_rel(rr, ws),
            "output_discovery_dir": _to_ws_rel(dd, ws),
            "relative_paths": {
                # 输出文件直接落在 output_discovery_dir 下
                "katana_urls": "katana_urls.txt",
                "clustered_targets": "clustered_targets.txt",
                "dirsearch_json": "dirsearch.json",
            },
            "phases_completed": phases_completed,
            "partial_results": partial,
            "url_counts": {"katana": len(k_urls), "dirsearch": len(d_urls), "total_raw": len(k_urls) + len(d_urls)},
            "workspace_artifact_ref": pre_wsref or _to_ws_rel(rr, ws),
            "diagnostics": diag,
        },
        "raw_stdout": "",
        "raw_stderr": "",
        "duration_ms": int((time.perf_counter() - start) * 1000),
    }
    emit_result(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
