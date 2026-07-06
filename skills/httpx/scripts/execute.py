from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse


def _normalize_url(raw: str) -> str:
    u = (raw or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = f"http://{u}"
    return u


def _norm_host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except Exception:
        return ""
    if host in ("localhost", "127.0.0.1", "::1"):
        return "host.docker.internal"
    return host


def _same_host(url_a: str, url_b: str) -> bool:
    ha = _norm_host(url_a)
    hb = _norm_host(url_b)
    return bool(ha and hb and ha == hb)


def _parse_first_json_line(raw: str) -> dict:
    for line in (raw or "").splitlines():
        text = line.strip()
        if not text or not text.startswith("{"):
            continue
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


# Known admin paths for common Java EE / middleware platforms.
# Probed as secondary fallback when root returns 404 with no tech_stack.
# Each entry: (path, tech_tag) — tech_tag is appended to tech_stack when match confirmed.
_ADMIN_FINGERPRINT_PROBES = [
    ("/console/login/LoginForm.jsp", "weblogic"),  # Oracle WebLogic admin console
    ("/console/j_security_check", "weblogic"),     # WebLogic security check (alt path)
]

# Body-content fingerprints for JSON/API services that return 200 but no tech_stack.
# Each entry: (path, tech_tag, required_keywords_any_case)
# All keywords must appear in the first 4 KB of the response body.
_BODY_FINGERPRINT_PROBES = [
    ("/",            "elasticsearch", ["cluster_name", "lucene_version"]),
    ("/_cat/health", "elasticsearch", ["elasticsearch"]),
    ("/solr/admin/info/system?wt=json", "solr", ["solr-spec-version"]),
    ("/api/json",    "jenkins",       ["_class", "assignedLabels"]),
    ("/nacos/v1/console/server/state", "nacos", ["standalone_mode"]),
    ("/",            "xxl-job",       ["HttpMethod not support", "invalid request"]),
    ("/ws/v1/cluster/info", "hadoop",  ["clusterInfo", "resourceManagerVersion"]),
]

# Header-based fingerprints — send a probe request with specific headers and check the response.
# Each entry: (path, probe_headers, tech_tag, required_response_header_keywords)
# All keywords must appear (case-insensitive) in any Set-Cookie or response header value.
_HEADER_FINGERPRINT_PROBES = [
    # Apache Shiro: rememberMe cookie triggers deleteMe response when deserialization fails
    ("/", {"Cookie": "rememberMe=4AvVhmFLUs0KTA3Kprsdag=="}, "shiro", ["rememberme=deleteme"]),
]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from following redirects so we can inspect intermediate headers."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _probe_header_fingerprint(base_url: str, timeout: int) -> dict:
    """Detect frameworks by sending probe requests and checking response headers.
    Does NOT follow redirects so intermediate 3xx headers are visible.
    Returns {tech_tag, probe_url} on match, empty dict otherwise."""
    try:
        parsed = urlparse(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return {}
    opener = urllib.request.build_opener(_NoRedirect)
    for path, probe_headers, tech_tag, header_keywords in _HEADER_FINGERPRINT_PROBES:
        probe_url = origin + path
        try:
            req = urllib.request.Request(probe_url, headers=probe_headers)
            try:
                resp = opener.open(req, timeout=min(5, timeout))
                resp_headers_raw = str(resp.headers).lower()
            except urllib.error.HTTPError as exc:
                resp_headers_raw = str(exc.headers).lower()
            if all(kw.lower() in resp_headers_raw for kw in header_keywords):
                return {"tech_tag": tech_tag, "probe_url": probe_url}
        except Exception:
            pass
    return {}


def _probe_body_fingerprint(base_url: str, timeout: int) -> dict:
    """Detect API services by response body keywords when httpx tech-detect finds nothing.
    Returns {tech_tag, probe_url} on match, empty dict otherwise."""
    try:
        parsed = urlparse(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return {}
    for path, tech_tag, keywords in _BODY_FINGERPRINT_PROBES:
        probe_url = origin + path
        try:
            req = urllib.request.urlopen(probe_url, timeout=min(5, timeout))
            body = req.read(4096).decode("utf-8", errors="replace").lower()
            if all(kw.lower() in body for kw in keywords):
                return {"tech_tag": tech_tag, "probe_url": probe_url}
        except Exception:
            pass
    return {}


def _probe_secondary_admin(base_url: str, timeout: int) -> dict:
    """Try known admin paths when root returns 404 with no tech_stack.
    Returns a dict with tech_tag, title, and probe_url if a match is found."""
    try:
        parsed = urlparse(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return {}
    for path, tech_tag in _ADMIN_FINGERPRINT_PROBES:
        probe_url = origin + path
        try:
            cmd = [
                "httpx", "-u", probe_url,
                "-td", "-sc", "-title", "-json", "-silent",
                "-t", "5", "-timeout", str(min(15, timeout)),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=min(20, timeout + 5))
            result = _parse_first_json_line(proc.stdout or "")
            if result.get("status_code") == 200:
                title = str(result.get("title") or "").lower()
                if tech_tag in title or tech_tag == "weblogic" and ("weblogic" in title or "oracle" in title and "server" in title):
                    return {
                        "tech_tag": tech_tag,
                        "title": str(result.get("title") or ""),
                        "probe_url": probe_url,
                        "tech": result.get("tech") or [],
                    }
        except Exception:
            pass
    return {}


def main() -> int:
    start = time.perf_counter()
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

    target_url = _normalize_url(str(params.get("url") or payload.get("target") or ""))
    if not target_url:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "params.url or target required"},
            "raw_stdout": "",
            "raw_stderr": "missing target url",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1

    threads = int(params.get("threads") or 30)
    rate_limit = int(params.get("rate_limit") or 100)
    max_redirects = int(params.get("max_redirects") or 2)
    timeout_seconds = int(params.get("timeout") or 20)

    cmd = [
        "httpx",
        "-u",
        target_url,
        "-td",
        "-sc",
        "-title",
        "-server",
        "-fr",
        "-maxr",
        str(max_redirects),
        "-json",
        "-silent",
        "-t",
        str(threads),
        "-rl",
        str(rate_limit),
    ]

    run_timeout = max(8, timeout_seconds)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=run_timeout)
    except subprocess.TimeoutExpired:
        out = {
            "status": "TIMEOUT",
            "parsed_artifacts": {
                "input_url": target_url,
                "error": f"httpx timeout after {run_timeout}s",
                "command": " ".join(cmd),
            },
            "raw_stdout": "",
            "raw_stderr": f"timeout after {run_timeout}s",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    except FileNotFoundError:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "httpx executable not found"},
            "raw_stdout": "",
            "raw_stderr": "httpx not found in PATH",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1

    parsed_line = _parse_first_json_line(proc.stdout or "")
    input_url = str(parsed_line.get("url") or target_url).strip() or target_url
    final_url = str(parsed_line.get("final_url") or input_url).strip() or input_url
    out_of_scope_redirect = not _same_host(input_url, final_url)

    tech_stack = parsed_line.get("tech") if isinstance(parsed_line.get("tech"), list) else []
    title = str(parsed_line.get("title") or "").strip()
    status_code = parsed_line.get("status_code")

    # Enrich tech_stack from CPE data (httpx may identify frameworks via CPE but not add to tech[])
    # e.g. Solr returns cpe:[{product:"solr",vendor:"apache",...}] but tech=["AngularJS",...]
    _CPE_PRODUCT_MAP = {"solr": "solr", "jenkins": "jenkins", "elasticsearch": "elasticsearch"}
    cpe_list = parsed_line.get("cpe") if isinstance(parsed_line.get("cpe"), list) else []
    for cpe_entry in cpe_list:
        if isinstance(cpe_entry, dict):
            product = str(cpe_entry.get("product") or "").lower()
            if product in _CPE_PRODUCT_MAP:
                tag = _CPE_PRODUCT_MAP[product]
                if tag not in tech_stack:
                    tech_stack = [tag] + list(tech_stack)
        elif isinstance(cpe_entry, str):
            # CPE string format: cpe:2.3:a:vendor:product:...
            parts = cpe_entry.lower().split(":")
            if len(parts) >= 5:
                product = parts[4]
                if product in _CPE_PRODUCT_MAP:
                    tag = _CPE_PRODUCT_MAP[product]
                    if tag not in tech_stack:
                        tech_stack = [tag] + list(tech_stack)

    # Secondary admin-path probe when root has no tech_stack (e.g. WebLogic returns 404 for /)
    secondary_probe_result: dict = {}
    body_probe_result: dict = {}
    if not tech_stack and status_code in (404, None):
        secondary_probe_result = _probe_secondary_admin(target_url, run_timeout)
        if secondary_probe_result:
            tag = secondary_probe_result.get("tech_tag", "")
            if tag and tag not in tech_stack:
                tech_stack = [tag] + list(tech_stack)
            if not title and secondary_probe_result.get("title"):
                title = secondary_probe_result["title"]

    # Body-based fingerprint for JSON API services.
    # Run always: services like Hadoop YARN populate tech_stack with generic server info
    # (Jetty, jQuery) but the application-level service is only detectable via its API path.
    # Probe regardless of root status code: some services (e.g. Nacos) return 404 at /
    # but expose fingerprint-rich endpoints at sub-paths (/nacos/v1/console/server/state).
    body_probe_result = _probe_body_fingerprint(target_url, run_timeout)
    if body_probe_result:
        tag = body_probe_result.get("tech_tag", "")
        if tag and tag not in tech_stack:
            tech_stack = [tag] + list(tech_stack)

    # Header-based fingerprint for frameworks that respond to specific cookie/header probes
    # (e.g. Apache Shiro returns rememberMe=deleteMe when given a bad rememberMe cookie)
    header_probe_result: dict = {}
    if not tech_stack or not any(t in ("shiro",) for t in tech_stack):
        header_probe_result = _probe_header_fingerprint(target_url, run_timeout)
        if header_probe_result:
            tag = header_probe_result.get("tech_tag", "")
            if tag and tag not in tech_stack:
                tech_stack = [tag] + list(tech_stack)

    parsed_artifacts = {
        "input_url": input_url,
        "final_url": final_url,
        "status_code": status_code,
        "title": title,
        "webserver": str(parsed_line.get("webserver") or "").strip(),
        "tech_stack": tech_stack,
        "redirect_warning": out_of_scope_redirect,
        "out_of_scope_redirect": out_of_scope_redirect,
        "threads": threads,
        "rate_limit": rate_limit,
        "max_redirects": max_redirects,
        "command": " ".join(cmd),
        "raw_preview": (proc.stdout or "")[:2000],
    }
    if secondary_probe_result:
        parsed_artifacts["secondary_probe"] = secondary_probe_result
    if body_probe_result:
        parsed_artifacts["body_probe"] = body_probe_result
    if header_probe_result:
        parsed_artifacts["header_probe"] = header_probe_result
    if proc.stderr:
        parsed_artifacts["stderr_preview"] = proc.stderr[:500]

    status = "SUCCESS" if proc.returncode == 0 and parsed_line else "FAILED"
    out = {
        "status": status,
        "parsed_artifacts": parsed_artifacts,
        "raw_stdout": proc.stdout or "",
        "raw_stderr": proc.stderr or "",
        "duration_ms": int((time.perf_counter() - start) * 1000),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
