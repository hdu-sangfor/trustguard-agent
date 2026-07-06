import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import { getVulnsSummary, type ApiVulnEntry, type ApiVulnsTaskRow } from "@/shared/lib/api";
import { readStoredOrbitTasks } from "@/shared/constants/orbitTasksStorage";

// ── Severity helpers ──────────────────────────────────────────────────────────
const SEV_ORDER = ["critical", "high", "medium", "low", "info", "unknown"] as const;
type Sev = typeof SEV_ORDER[number];

const SEV_META: Record<Sev, { label: string; color: string; bg: string; border: string }> = {
  critical: { label: "严重", color: "#ef4444", bg: "rgba(239,68,68,0.12)",    border: "rgba(239,68,68,0.4)" },
  high:     { label: "高危", color: "#f97316", bg: "rgba(249,115,22,0.12)",   border: "rgba(249,115,22,0.4)" },
  medium:   { label: "中危", color: "#eab308", bg: "rgba(234,179,8,0.12)",    border: "rgba(234,179,8,0.4)" },
  low:      { label: "低危", color: "#22d3ee", bg: "rgba(34,211,238,0.10)",   border: "rgba(34,211,238,0.35)" },
  info:     { label: "信息", color: "#94a3b8", bg: "rgba(148,163,184,0.10)",  border: "rgba(148,163,184,0.3)" },
  unknown:  { label: "未知", color: "#64748b", bg: "rgba(100,116,139,0.10)",  border: "rgba(100,116,139,0.3)" },
};

function normSev(raw: string | undefined): Sev {
  const s = (raw ?? "").toLowerCase();
  if (s.includes("critical") || s.includes("严重")) return "critical";
  if (s.includes("high") || s.includes("高"))      return "high";
  if (s.includes("medium") || s.includes("中"))    return "medium";
  if (s.includes("low") || s.includes("低"))       return "low";
  if (s.includes("info") || s.includes("信息"))    return "info";
  return "unknown";
}

function SevBadge({ sev }: { sev: Sev }) {
  const m = SEV_META[sev];
  return (
    <span style={{
      display: "inline-block", padding: "1px 7px", borderRadius: 3,
      fontSize: 11, fontWeight: 700, fontFamily: "monospace",
      color: m.color, background: m.bg, border: `1px solid ${m.border}`,
    }}>
      {m.label}
    </span>
  );
}

// ── Demo fallback ─────────────────────────────────────────────────────────────
const DEMO_VULNS: ApiVulnsTaskRow[] = [
  {
    task_id: "demo-1", task_name: "Web 常规渗透测试", target: "http://192.168.1.100/dvwa/",
    vuln_count: 3,
    vulnerabilities: [
      { name: "SQL 注入（联合查询）", cve: "CWE-89", severity: "high", description: "参数 id 存在联合查询注入，可获取数据库名及表结构", affected_service: "Apache/2.4 + PHP 7.2", remediation: "使用参数化查询（Prepared Statement）" },
      { name: "弱口令", cve: "CWE-521", severity: "medium", description: "管理后台 admin/admin 可登录", affected_service: "登录接口 /login.php", remediation: "强制复杂密码策略 + 帐号锁定" },
      { name: "目录遍历", cve: "CWE-548", severity: "low", description: "发现 /phpinfo.php 暴露服务器配置", affected_service: "Web 根目录", remediation: "删除或限制访问调试文件" },
    ],
  },
  {
    task_id: "demo-2", task_name: "内网主机综合渗透（Struts2）", target: "http://192.168.1.102:8080/",
    vuln_count: 2,
    vulnerabilities: [
      { name: "Apache Struts2 S2-045 RCE", cve: "CVE-2017-5638", severity: "critical", description: "Content-Type 头注入 OGNL 表达式，执行 id 命令返回 uid=0(root)", affected_service: "Struts2 2.3.5", remediation: "升级至 Struts2 ≥ 2.5.30；在反向代理层过滤异常 Content-Type" },
      { name: "Java 反序列化", cve: "CVE-2015-4852", severity: "high", description: "T3 协议反序列化 gadget 可执行任意命令", affected_service: "WebLogic 12c", remediation: "应用 Oracle CPU 补丁；限制 T3 外网访问" },
    ],
  },
  {
    task_id: "demo-3", task_name: "API 接口安全测试（Spring Boot）", target: "http://192.168.1.101:8080/api/v1/",
    vuln_count: 2,
    vulnerabilities: [
      { name: "未授权访问敏感接口", cve: "CWE-284", severity: "high", description: "/api/v1/admin/user 无需鉴权，泄露 12 条用户记录", affected_service: "Spring Boot 2.7", remediation: "所有管理接口强制 JWT 鉴权；最小权限原则" },
      { name: "数字型注入", cve: "CWE-89", severity: "high", description: "/userinfo?id=1 存在数字型注入", affected_service: "MySQL via MyBatis", remediation: "使用 MyBatis #{} 占位符替换 ${} 字符串拼接" },
    ],
  },
  {
    task_id: "demo-4", task_name: "Flask SSTI 检测", target: "http://192.168.1.103:5000/",
    vuln_count: 2,
    vulnerabilities: [
      { name: "服务端模板注入 (SSTI)", cve: "CWE-94", severity: "critical", description: "Jinja2 render_template_string 未转义，可读取 /etc/passwd", affected_service: "Flask 2.0 + Jinja2", remediation: "避免将用户输入传入模板引擎；使用 Markup.escape()" },
      { name: "调试模式开启", cve: "CVE-2019-1000025", severity: "medium", description: "Werkzeug debugger PIN 可被暴力破解", affected_service: "Flask DEV server", remediation: "生产环境关闭 DEBUG=True；使用 gunicorn 部署" },
    ],
  },
];

// Combine DEMO_VULNS with stored tasks to fake more entries
function buildDemoRows(): ApiVulnsTaskRow[] {
  const stored = readStoredOrbitTasks().filter((t) => t.status === "finished" || t.status === "running");
  const extras: ApiVulnsTaskRow[] = stored
    .filter((t) => !DEMO_VULNS.some((d) => d.task_name === t.name))
    .slice(0, 3)
    .map((t) => ({
      task_id: t.id,
      task_name: t.name,
      target: t.url,
      vuln_count: 1,
      vulnerabilities: [
        { name: "信息泄露", cve: "CWE-200", severity: "info", description: "扫描发现敏感路径或版本信息暴露", affected_service: t.url, remediation: "移除服务版本响应头；禁用目录列举" },
      ],
    }));
  return [...DEMO_VULNS, ...extras];
}

// ── Main component ────────────────────────────────────────────────────────────
export default function VulnsPage() {
  const navigate = useNavigate();
  const { loggedIn } = useAppSession();

  const [rows, setRows] = useState<ApiVulnsTaskRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);
  const [filterSev, setFilterSev] = useState<Sev | "all">("all");
  const [filterTask, setFilterTask] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [expandedTask, setExpandedTask] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getVulnsSummary(30);
      if (data.by_task && data.by_task.length > 0) {
        setRows(data.by_task);
        setIsDemo(false);
      } else {
        setRows(buildDemoRows());
        setIsDemo(true);
      }
    } catch {
      setRows(buildDemoRows());
      setIsDemo(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!loggedIn) { navigate("/login"); return; }
    void load();
  }, [loggedIn, load, navigate]);

  // Flatten all vulns for aggregate stats
  const allVulns: Array<ApiVulnEntry & { _taskId: string; _taskName: string; _target: string }> = rows.flatMap(
    (row) => row.vulnerabilities.map((v) => ({ ...v, _taskId: row.task_id, _taskName: row.task_name, _target: row.target }))
  );

  const sevCounts = SEV_ORDER.reduce<Record<string, number>>((acc, s) => {
    acc[s] = allVulns.filter((v) => normSev(v.severity) === s).length;
    return acc;
  }, {});

  // Filter
  const filtered = allVulns.filter((v) => {
    if (filterSev !== "all" && normSev(v.severity) !== filterSev) return false;
    if (filterTask !== "all" && v._taskId !== filterTask) return false;
    if (search) {
      const q = search.toLowerCase();
      return (
        (v.name ?? "").toLowerCase().includes(q) ||
        (v.cve ?? "").toLowerCase().includes(q) ||
        (v.description ?? "").toLowerCase().includes(q) ||
        v._taskName.toLowerCase().includes(q)
      );
    }
    return true;
  });

  return (
    <div style={{ minHeight: "100vh", background: "#020a12", paddingTop: 80, paddingBottom: 60 }}>
      <Header />

      <div style={{ maxWidth: 1300, margin: "0 auto", padding: "0 24px" }}>
        {/* Title */}
        <div style={{ marginBottom: 28 }}>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: "#e2e8f0", fontFamily: "monospace", letterSpacing: "0.05em" }}>
            漏洞库 <span style={{ fontSize: 13, color: "rgba(148,163,184,0.7)", fontWeight: 400 }}>Vulnerability Inventory</span>
          </h1>
          {isDemo && (
            <span style={{
              marginTop: 6, display: "inline-block",
              padding: "2px 10px", borderRadius: 4,
              fontSize: 11, fontFamily: "monospace",
              background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.3)",
              color: "rgba(251,191,36,0.9)",
            }}>
              演示数据 — 后端不可达或无已完成任务
            </span>
          )}
        </div>

        {/* Stats row */}
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 24 }}>
          {SEV_ORDER.slice(0, 5).map((sev) => {
            const m = SEV_META[sev];
            const cnt = sevCounts[sev] ?? 0;
            return (
              <div
                key={sev}
                onClick={() => setFilterSev(filterSev === sev ? "all" : sev)}
                style={{
                  flex: "1 1 120px", minWidth: 110,
                  padding: "14px 18px",
                  background: filterSev === sev ? m.bg : "rgba(15,23,42,0.6)",
                  border: `1px solid ${filterSev === sev ? m.border : "rgba(51,65,85,0.5)"}`,
                  borderRadius: 8, cursor: "pointer",
                  transition: "all 0.2s",
                }}
              >
                <div style={{ fontSize: 24, fontWeight: 800, color: m.color, fontFamily: "monospace" }}>{cnt}</div>
                <div style={{ fontSize: 12, color: "rgba(148,163,184,0.8)", marginTop: 2 }}>{m.label}</div>
              </div>
            );
          })}
          <div style={{
            flex: "1 1 120px", minWidth: 110,
            padding: "14px 18px",
            background: "rgba(15,23,42,0.6)",
            border: "1px solid rgba(51,65,85,0.5)",
            borderRadius: 8,
          }}>
            <div style={{ fontSize: 24, fontWeight: 800, color: "#22d3ee", fontFamily: "monospace" }}>{allVulns.length}</div>
            <div style={{ fontSize: 12, color: "rgba(148,163,184,0.8)", marginTop: 2 }}>总计</div>
          </div>
        </div>

        {/* Filters */}
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 20, alignItems: "center" }}>
          <input
            type="text"
            placeholder="搜索漏洞名称 / CVE / 描述…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              flex: "1 1 240px", padding: "7px 14px",
              background: "rgba(15,23,42,0.7)", border: "1px solid rgba(51,65,85,0.6)",
              borderRadius: 6, color: "#e2e8f0", fontSize: 13, fontFamily: "monospace",
              outline: "none",
            }}
          />
          <select
            value={filterTask}
            onChange={(e) => setFilterTask(e.target.value)}
            style={{
              padding: "7px 12px",
              background: "rgba(15,23,42,0.7)", border: "1px solid rgba(51,65,85,0.6)",
              borderRadius: 6, color: "#e2e8f0", fontSize: 13, fontFamily: "monospace",
              outline: "none",
            }}
          >
            <option value="all">全部任务</option>
            {rows.map((r) => (
              <option key={r.task_id} value={r.task_id}>{r.task_name.slice(0, 30)}</option>
            ))}
          </select>
          {(filterSev !== "all" || filterTask !== "all" || search) && (
            <button
              type="button"
              onClick={() => { setFilterSev("all"); setFilterTask("all"); setSearch(""); }}
              style={{
                padding: "7px 14px",
                background: "rgba(51,65,85,0.4)", border: "1px solid rgba(71,85,105,0.5)",
                borderRadius: 6, color: "#94a3b8", fontSize: 12, cursor: "pointer",
                fontFamily: "monospace",
              }}
            >
              清除筛选
            </button>
          )}
          <span style={{ marginLeft: "auto", fontSize: 12, color: "rgba(148,163,184,0.6)", fontFamily: "monospace" }}>
            {filtered.length} / {allVulns.length} 条
          </span>
        </div>

        {/* Loading */}
        {loading && (
          <div style={{ textAlign: "center", color: "rgba(34,211,238,0.5)", fontFamily: "monospace", padding: 40 }}>
            LOADING…
          </div>
        )}

        {/* Error */}
        {error && !loading && (
          <div style={{
            padding: 16, borderRadius: 8,
            background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.3)",
            color: "#fca5a5", fontFamily: "monospace", fontSize: 13,
          }}>
            {error}
          </div>
        )}

        {/* Vuln table */}
        {!loading && filtered.length > 0 && (
          <div style={{
            background: "rgba(15,23,42,0.6)", border: "1px solid rgba(51,65,85,0.5)",
            borderRadius: 10, overflow: "hidden",
          }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid rgba(51,65,85,0.5)", background: "rgba(15,23,42,0.8)" }}>
                  {["严重度", "漏洞名称 / CVE", "受影响服务", "所属任务", "修复建议"].map((h) => (
                    <th key={h} style={{
                      padding: "10px 14px", textAlign: "left",
                      fontSize: 11, fontWeight: 700, fontFamily: "monospace",
                      color: "rgba(148,163,184,0.7)", letterSpacing: "0.06em",
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((v, i) => {
                  const sev = normSev(v.severity);
                  const m = SEV_META[sev];
                  return (
                    <tr
                      key={`${v._taskId}-${i}`}
                      style={{
                        borderBottom: "1px solid rgba(51,65,85,0.3)",
                        background: i % 2 === 0 ? "transparent" : "rgba(15,23,42,0.25)",
                        transition: "background 0.15s",
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = `${m.bg}`)}
                      onMouseLeave={(e) => (e.currentTarget.style.background = i % 2 === 0 ? "transparent" : "rgba(15,23,42,0.25)")}
                    >
                      <td style={{ padding: "10px 14px", whiteSpace: "nowrap" }}>
                        <SevBadge sev={sev} />
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <div style={{ fontSize: 13, color: "#e2e8f0", fontWeight: 600 }}>{v.name ?? "未知漏洞"}</div>
                        {v.cve && (
                          <div style={{ fontSize: 11, color: "rgba(148,163,184,0.6)", fontFamily: "monospace", marginTop: 2 }}>
                            {v.cve}
                          </div>
                        )}
                        {v.description && (
                          <div style={{ fontSize: 11, color: "rgba(148,163,184,0.55)", marginTop: 3, lineHeight: 1.5 }}>
                            {v.description.slice(0, 120)}{v.description.length > 120 ? "…" : ""}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: "10px 14px", fontSize: 12, color: "rgba(148,163,184,0.75)", fontFamily: "monospace" }}>
                        {v.affected_service ?? v._target ?? "—"}
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <div style={{ fontSize: 12, color: "rgba(34,211,238,0.8)", fontFamily: "monospace" }}>
                          {v._taskName.slice(0, 24)}{v._taskName.length > 24 ? "…" : ""}
                        </div>
                        <div style={{ fontSize: 10, color: "rgba(100,116,139,0.8)", marginTop: 2 }}>
                          {v._target.slice(0, 32)}{v._target.length > 32 ? "…" : ""}
                        </div>
                      </td>
                      <td style={{ padding: "10px 14px", fontSize: 12, color: "rgba(148,163,184,0.7)", lineHeight: 1.5, maxWidth: 260 }}>
                        {v.remediation ? v.remediation.slice(0, 100) + (v.remediation.length > 100 ? "…" : "") : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Empty state */}
        {!loading && filtered.length === 0 && allVulns.length > 0 && (
          <div style={{ textAlign: "center", color: "rgba(148,163,184,0.5)", fontFamily: "monospace", padding: 40 }}>
            无匹配漏洞记录
          </div>
        )}

        {/* Per-task breakdown */}
        <div style={{ marginTop: 36 }}>
          <h2 style={{ fontSize: 15, fontWeight: 700, color: "#e2e8f0", fontFamily: "monospace", marginBottom: 16, letterSpacing: "0.04em" }}>
            按任务分类
          </h2>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {rows.map((row) => {
              const isOpen = expandedTask === row.task_id;
              const rowSevCounts = SEV_ORDER.reduce<Record<string, number>>((acc, s) => {
                acc[s] = row.vulnerabilities.filter((v) => normSev(v.severity) === s).length;
                return acc;
              }, {});
              return (
                <div
                  key={row.task_id}
                  style={{
                    background: "rgba(15,23,42,0.6)", border: "1px solid rgba(51,65,85,0.5)",
                    borderRadius: 8, overflow: "hidden",
                  }}
                >
                  {/* Task header */}
                  <div
                    onClick={() => setExpandedTask(isOpen ? null : row.task_id)}
                    style={{
                      display: "flex", alignItems: "center", gap: 12,
                      padding: "12px 18px", cursor: "pointer",
                      background: isOpen ? "rgba(34,211,238,0.04)" : undefined,
                    }}
                  >
                    <span style={{ fontSize: 13, color: isOpen ? "#22d3ee" : "#e2e8f0", fontWeight: 600, flex: 1 }}>
                      {row.task_name}
                    </span>
                    <span style={{ fontSize: 11, color: "rgba(100,116,139,0.8)", fontFamily: "monospace" }}>
                      {row.target.slice(0, 36)}
                    </span>
                    <div style={{ display: "flex", gap: 6 }}>
                      {SEV_ORDER.slice(0, 4).map((s) =>
                        (rowSevCounts[s] ?? 0) > 0 ? (
                          <span key={s} style={{
                            padding: "1px 6px", borderRadius: 3, fontSize: 11,
                            color: SEV_META[s].color, background: SEV_META[s].bg,
                            border: `1px solid ${SEV_META[s].border}`, fontFamily: "monospace",
                          }}>
                            {SEV_META[s].label} ×{rowSevCounts[s]}
                          </span>
                        ) : null
                      )}
                    </div>
                    <span style={{ fontSize: 13, color: "rgba(148,163,184,0.5)", marginLeft: 8 }}>
                      {isOpen ? "▲" : "▼"}
                    </span>
                  </div>

                  {/* Expanded vuln list */}
                  {isOpen && row.vulnerabilities.length > 0 && (
                    <div style={{ borderTop: "1px solid rgba(51,65,85,0.4)" }}>
                      {row.vulnerabilities.map((v, i) => {
                        const sev = normSev(v.severity);
                        const m = SEV_META[sev];
                        return (
                          <div
                            key={i}
                            style={{
                              padding: "12px 18px",
                              borderBottom: i < row.vulnerabilities.length - 1 ? "1px solid rgba(51,65,85,0.2)" : undefined,
                              display: "flex", gap: 16, alignItems: "flex-start",
                            }}
                          >
                            <SevBadge sev={sev} />
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ fontSize: 13, color: "#e2e8f0", fontWeight: 600 }}>
                                {v.name ?? "未知漏洞"}
                                {v.cve && (
                                  <span style={{ marginLeft: 8, fontSize: 11, color: m.color, fontFamily: "monospace" }}>
                                    {v.cve}
                                  </span>
                                )}
                              </div>
                              {v.description && (
                                <div style={{ fontSize: 12, color: "rgba(148,163,184,0.7)", marginTop: 4, lineHeight: 1.6 }}>
                                  {v.description}
                                </div>
                              )}
                              {v.remediation && (
                                <div style={{
                                  marginTop: 6, padding: "5px 10px",
                                  background: "rgba(34,197,94,0.06)", border: "1px solid rgba(34,197,94,0.2)",
                                  borderRadius: 4, fontSize: 12, color: "rgba(134,239,172,0.85)",
                                }}>
                                  修复建议：{v.remediation}
                                </div>
                              )}
                            </div>
                            {v.affected_service && (
                              <div style={{ fontSize: 11, color: "rgba(100,116,139,0.8)", fontFamily: "monospace", whiteSpace: "nowrap", maxWidth: 180 }}>
                                {v.affected_service}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                  {isOpen && row.vulnerabilities.length === 0 && (
                    <div style={{ padding: "12px 18px", fontSize: 12, color: "rgba(100,116,139,0.7)", fontFamily: "monospace" }}>
                      该任务暂无漏洞记录
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Quick nav */}
        <div style={{
          marginTop: 28, padding: "10px 16px",
          background: "rgba(15,23,42,0.5)", border: "1px solid rgba(51,65,85,0.3)",
          borderRadius: 8, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center",
          fontSize: 11, fontFamily: "monospace",
        }}>
          <span style={{ color: "#475569" }}>导航：</span>
          {[
            { label: "统计分析", path: "/stats",     color: "#f87171" },
            { label: "报告中心", path: "/reports",   color: "#fb923c" },
            { label: "管理中心", path: "/dashboard", color: "#22d3ee" },
            { label: "任务管理", path: "/tasks",     color: "#38bdf8" },
            { label: "平台管理", path: "/admin",     color: "#a78bfa" },
          ].map(({ label, path, color }) => (
            <button key={path} type="button" onClick={() => navigate(path)}
              style={{ background: "none", border: "none", color, cursor: "pointer", fontFamily: "monospace", fontSize: 11, padding: 0 }}>
              {label} →
            </button>
          ))}
        </div>

        {/* Refresh footer */}
        <div style={{ marginTop: 16, display: "flex", justifyContent: "flex-end" }}>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            style={{
              padding: "8px 20px",
              background: "rgba(34,211,238,0.08)", border: "1px solid rgba(34,211,238,0.3)",
              borderRadius: 6, color: "#22d3ee", fontSize: 12, cursor: "pointer",
              fontFamily: "monospace", letterSpacing: "0.05em",
              opacity: loading ? 0.5 : 1,
            }}
          >
            {loading ? "加载中…" : "刷新"}
          </button>
        </div>
      </div>
    </div>
  );
}
