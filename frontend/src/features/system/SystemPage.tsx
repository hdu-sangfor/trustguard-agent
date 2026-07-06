import { useEffect, useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import {
  getSystemInfoFull, getSystemHealth, getSliSnapshot, getMqStatus, getTaskStats, listUsers,
  type ApiSystemInfo, type ApiHealthStatus, type ApiSliSnapshot, type ApiMqStatus, type ApiTaskStats,
  type ApiUser,
} from "@/shared/lib/api";

// ─── helpers ────────────────────────────────────────────────────────────────

function uptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function dot(color: string) {
  return (
    <span style={{
      display: "inline-block", width: 8, height: 8, borderRadius: "50%",
      background: color, boxShadow: `0 0 6px ${color}`,
      marginRight: 6, flexShrink: 0,
    }} />
  );
}

// ─── Card ────────────────────────────────────────────────────────────────────

function Card({ title, children, accent = "rgba(34,211,238,0.35)" }: {
  title: string; children: React.ReactNode; accent?: string;
}) {
  return (
    <div style={{
      background: "rgba(2,6,23,0.75)", borderRadius: 10,
      border: `1px solid ${accent}`,
      padding: "16px 20px",
    }}>
      <div style={{
        color: "#94a3b8", fontFamily: "monospace", fontSize: 10,
        fontWeight: 800, letterSpacing: "0.1em", textTransform: "uppercase",
        marginBottom: 12,
      }}>{title}</div>
      {children}
    </div>
  );
}

// ─── Main ────────────────────────────────────────────────────────────────────

const SystemPage = () => {
  const { loggedIn } = useAppSession();
  const navigate = useNavigate();

  const [sysInfo, setSysInfo]   = useState<ApiSystemInfo | null>(null);
  const [health, setHealth]     = useState<ApiHealthStatus | null>(null);
  const [sli, setSli]           = useState<ApiSliSnapshot | null>(null);
  const [mq, setMq]             = useState<ApiMqStatus | null>(null);
  const [taskStats, setTaskStats] = useState<ApiTaskStats | null>(null);
  const [users, setUsers]       = useState<ApiUser[]>([]);
  const [loading, setLoading]   = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  useEffect(() => {
    if (!loggedIn) {
      toast.error("请先登录");
      localStorage.setItem("sentinel_login_redirect", "/system");
      navigate("/login", { replace: true });
    }
  }, [loggedIn, navigate]);

  const refresh = useCallback(async () => {
    await Promise.resolve(); // defer setState to avoid cascading-render lint warning
    setLoading(true);
    const results = await Promise.allSettled([
      getSystemInfoFull(),
      getSystemHealth(),
      getSliSnapshot(true),
      getMqStatus(),
      getTaskStats(),
      listUsers(),
    ]);
    if (results[0].status === "fulfilled") setSysInfo(results[0].value);
    if (results[1].status === "fulfilled") setHealth(results[1].value);
    if (results[2].status === "fulfilled") setSli(results[2].value as ApiSliSnapshot);
    if (results[3].status === "fulfilled") setMq(results[3].value as ApiMqStatus);
    if (results[4].status === "fulfilled") setTaskStats(results[4].value as ApiTaskStats);
    if (results[5].status === "fulfilled") setUsers(results[5].value as ApiUser[]);
    setLastRefresh(new Date());
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!loggedIn) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
    const iv = window.setInterval(() => { void refresh(); }, 15000);
    return () => window.clearInterval(iv);
  }, [loggedIn, refresh]);

  if (!loggedIn) return null;

  // Derive service statuses from Gateway's aggregated health map.
  const mqReady = typeof mq?.messages_ready === "number" ? mq.messages_ready : null;
  const mqConsumers = typeof mq?.consumers === "number" ? mq.consumers : null;
  const services = sysInfo?.services ?? {};
  const serviceSpecs = [
    { key: "gateway", name: "Gateway Backend", role: "REST API 网关", port: 18080 },
    { key: "orchestrator", name: "Orchestrator", role: "LLM 状态机", port: 18081 },
    { key: "executor", name: "Executor", role: "技能执行器", port: 18102 },
    { key: "evidence", name: "Evidence", role: "可观测性存储", port: 18103 },
    { key: "mq", name: "MQ Agent", role: "异步任务队列", port: 5672 },
  ];
  const serviceRows: { name: string; role: string; port: number; ok: boolean | null; detail?: string }[] = serviceSpecs.map((spec) => {
    const svc = services[spec.key];
    const status = (svc?.status ?? "").toUpperCase();
    const ok = status === "UP" ? true : status === "DOWN" ? false : null;
    let detail = ok === true ? "✓ 已连接" : ok === false ? "离线" : "未知";
    if (spec.key === "mq" && mqReady !== null) detail = `堆积 ${String(mqReady)} 消息 · ${String(mqConsumers ?? 0)} 消费者`;
    else if (svc?.detail) detail = ok === true ? `✓ ${svc.detail}` : svc.detail;
    return {
      name: spec.name,
      role: spec.role,
      port: svc?.port ?? spec.port,
      ok,
      detail,
    };
  });

  const totalTasks  = typeof taskStats?.total === "number" ? taskStats.total : 0;
  const runningTasks = typeof taskStats?.running === "number" ? taskStats.running : 0;
  const doneTasks   = typeof taskStats?.done === "number" ? taskStats.done : 0;
  const failedTasks = typeof taskStats?.failed === "number" ? taskStats.failed : 0;

  const infoRows: [string, string][] = sysInfo ? [
    ["平台版本", `${sysInfo.platform} v${sysInfo.version}`],
    ["发行版", sysInfo.edition],
    ["API 版本", sysInfo.apiVersion],
    ["运行时", sysInfo.runtime],
    ["操作系统", sysInfo.os],
    ["启动时间", new Date(sysInfo.startTime).toLocaleString("zh-CN")],
    ["运行时长", uptime(sysInfo.uptimeSeconds)],
  ] : [
    ["平台版本", "TrustGuard Agent v1.0.0"],
    ["发行版", "Competition Edition — 服务外包大赛"],
    ["API 版本", "v1"],
    ["状态", "后端离线 — 展示静态配置信息"],
  ];

  const capRows: [string, string][] = sysInfo?.capabilities ? [
    ["技能容器", sysInfo.capabilities.skillContainers],
    ["执行阶段", `${String(sysInfo.capabilities.phases)} 个 (${sysInfo.capabilities.phaseList.join(" → ")})`],
    ["并发目标", sysInfo.capabilities.concurrentTargets],
    ["调度模式", sysInfo.capabilities.dispatchModes.join(" / ")],
    ["LLM 提供商", sysInfo.capabilities.llmProviders.join(" · ")],
  ] : [
    ["技能容器", "33+"],
    ["执行阶段", "6 个 (RECON → THREAT_MODEL → VULN_SCAN → EXPLOIT → REPORT → DONE)"],
    ["并发目标", "≥3"],
    ["调度模式", "http / mq"],
    ["LLM 提供商", "openai_compat · anthropic · gemini · local"],
  ];

  return (
    <div style={{ minHeight: "100vh", background: "#020a12", paddingTop: 60 }}>
      <Header />
      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "32px 24px 60px" }}>

        {/* Page title */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24, flexWrap: "wrap", gap: 12 }}>
          <div>
            <h1 style={{ margin: 0, fontFamily: "monospace", fontSize: "1.5rem", fontWeight: 900, color: "#22d3ee", letterSpacing: "0.06em" }}>
              系统状态
            </h1>
            <p style={{ margin: "4px 0 0", color: "#475569", fontFamily: "monospace", fontSize: 11 }}>
              SYSTEM DASHBOARD · 平台健康监控与配置概览
            </p>
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            {lastRefresh && (
              <span style={{ color: "#334155", fontFamily: "monospace", fontSize: 10 }}>
                {lastRefresh.toLocaleTimeString("zh-CN")} 刷新
              </span>
            )}
            <button
              type="button"
              disabled={loading}
              onClick={() => { void refresh(); }}
              style={{
                padding: "6px 16px", borderRadius: 6, border: "1px solid rgba(34,211,238,0.35)",
                background: "rgba(34,211,238,0.07)", color: loading ? "#334155" : "#22d3ee",
                fontFamily: "monospace", fontSize: 11, cursor: loading ? "default" : "pointer",
              }}
            >{loading ? "刷新中…" : "↻ 刷新"}</button>
          </div>
        </div>

        {/* ── Top KPI row ─────────────────────────────────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 12, marginBottom: 20 }}>
          {[
            { label: "累计任务", value: totalTasks, color: "#22d3ee" },
            { label: "运行中",   value: runningTasks, color: "#fbbf24" },
            { label: "已完成",   value: doneTasks,   color: "#34d399" },
            { label: "失败",     value: failedTasks,  color: "#f87171" },
            { label: "平台用户", value: users.length, color: "#a78bfa" },
            { label: "活跃用户", value: users.filter((u) => u.status === "ACTIVE").length, color: "#38bdf8" },
          ].map((kpi) => (
            <div key={kpi.label} style={{
              background: "rgba(2,6,23,0.75)", borderRadius: 8,
              border: `1px solid ${kpi.color}22`,
              padding: "14px 16px", textAlign: "center",
            }}>
              <div style={{ color: kpi.color, fontFamily: "monospace", fontWeight: 900, fontSize: "1.6rem", lineHeight: 1 }}>
                {kpi.value}
              </div>
              <div style={{ color: "#475569", fontFamily: "monospace", fontSize: 10, marginTop: 5 }}>
                {kpi.label}
              </div>
            </div>
          ))}
        </div>

        {/* ── Main grid ────────────────────────────────────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>

          {/* Service Health */}
          <Card title="服务健康状态" accent="rgba(34,211,238,0.3)">
            <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "monospace", fontSize: 11 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid rgba(51,65,85,0.4)" }}>
                  {["服务", "端口", "职责", "状态"].map((h) => (
                    <th key={h} style={{ padding: "4px 8px", textAlign: "left", color: "#334155", fontWeight: 600, fontSize: 10 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {serviceRows.map((s) => {
                  const statusColor = s.ok === true ? "#34d399" : s.ok === false ? "#f87171" : "#64748b";
                  return (
                    <tr key={s.name} style={{ borderBottom: "1px solid rgba(51,65,85,0.1)" }}>
                      <td style={{ padding: "7px 8px", color: "#e2e8f0", fontWeight: 600 }}>{s.name}</td>
                      <td style={{ padding: "7px 8px", color: "#475569" }}>{s.port}</td>
                      <td style={{ padding: "7px 8px", color: "#64748b" }}>{s.role}</td>
                      <td style={{ padding: "7px 8px" }}>
                        <span style={{ display: "inline-flex", alignItems: "center", color: statusColor }}>
                          {dot(statusColor)}{s.detail}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>

          {/* Platform Info */}
          <Card title="平台信息" accent="rgba(167,139,250,0.3)">
            <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "monospace", fontSize: 11 }}>
              <tbody>
                {infoRows.map(([k, v]) => (
                  <tr key={k} style={{ borderBottom: "1px solid rgba(51,65,85,0.08)" }}>
                    <td style={{ padding: "6px 8px", color: "#475569", width: "35%" }}>{k}</td>
                    <td style={{ padding: "6px 8px", color: "#94a3b8" }}>{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          {/* Capability Matrix */}
          <Card title="平台能力矩阵" accent="rgba(251,191,36,0.3)">
            <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "monospace", fontSize: 11 }}>
              <tbody>
                {capRows.map(([k, v]) => (
                  <tr key={k} style={{ borderBottom: "1px solid rgba(51,65,85,0.08)" }}>
                    <td style={{ padding: "6px 8px", color: "#fbbf24", width: "35%", fontWeight: 600 }}>{k}</td>
                    <td style={{ padding: "6px 8px", color: "#94a3b8" }}>{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {/* Competition compliance */}
            <div style={{ marginTop: 14, padding: "8px 12px", borderRadius: 6, background: "rgba(34,197,94,0.06)", border: "1px solid rgba(34,197,94,0.2)" }}>
              <div style={{ color: "#86efac", fontSize: 10, fontFamily: "monospace", fontWeight: 800, marginBottom: 5 }}>◎ 竞赛指标符合度</div>
              {[
                ["工具数量 ≥30", "33+ Skill 容器 + 50+ 底层工具"],
                ["单目标时间 ≤15min", "Tick 驱动并发，分钟级完成"],
                ["并发测试 ≥3", "多 WorkSpace 独立隔离"],
                ["详细报告+修复建议", "结构化 Markdown + CVE 修复方案"],
              ].map(([req, val]) => (
                <div key={req} style={{ display: "flex", justifyContent: "space-between", fontSize: 10, fontFamily: "monospace", padding: "2px 0" }}>
                  <span style={{ color: "#64748b" }}>{req}</span>
                  <span style={{ color: "#34d399" }}>✓ {val}</span>
                </div>
              ))}
            </div>
          </Card>

          {/* User Management Summary */}
          <Card title="用户账号概览" accent="rgba(99,102,241,0.3)">
            {users.length === 0 ? (
              <div style={{ color: "#334155", fontFamily: "monospace", fontSize: 11 }}>后端离线 — 用户数据不可用</div>
            ) : (
              <>
                <div style={{ display: "flex", gap: 16, marginBottom: 12 }}>
                  {[
                    { label: "总账号", value: users.length, color: "#a5b4fc" },
                    { label: "管理员", value: users.filter((u) => u.role === "ADMIN").length, color: "#f87171" },
                    { label: "运维员", value: users.filter((u) => u.role === "OPERATOR").length, color: "#fbbf24" },
                    { label: "只读", value: users.filter((u) => u.role === "VIEWER").length, color: "#64748b" },
                  ].map((s) => (
                    <div key={s.label} style={{ textAlign: "center" }}>
                      <div style={{ color: s.color, fontFamily: "monospace", fontWeight: 900, fontSize: "1.2rem" }}>{s.value}</div>
                      <div style={{ color: "#334155", fontFamily: "monospace", fontSize: 9 }}>{s.label}</div>
                    </div>
                  ))}
                </div>
                <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "monospace", fontSize: 10 }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid rgba(51,65,85,0.3)" }}>
                      {["用户名", "角色", "状态", "最近登录"].map((h) => (
                        <th key={h} style={{ padding: "3px 6px", textAlign: "left", color: "#334155", fontWeight: 600 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {users.slice(0, 6).map((u) => {
                      const roleColor = u.role === "ADMIN" ? "#f87171" : u.role === "OPERATOR" ? "#fbbf24" : "#64748b";
                      const stColor = u.status === "ACTIVE" ? "#34d399" : "#f87171";
                      return (
                        <tr key={u.userId} style={{ borderBottom: "1px solid rgba(51,65,85,0.08)" }}>
                          <td style={{ padding: "5px 6px", color: "#e2e8f0" }}>{u.username}</td>
                          <td style={{ padding: "5px 6px", color: roleColor, fontWeight: 700 }}>{u.role}</td>
                          <td style={{ padding: "5px 6px", color: stColor }}>{u.status}</td>
                          <td style={{ padding: "5px 6px", color: "#334155" }}>
                            {u.lastLoginAt ? new Date(u.lastLoginAt).toLocaleDateString("zh-CN") : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </>
            )}
          </Card>
        </div>

        {/* ── Quick Navigation ─────────────────────────────────────────── */}
        <div style={{ marginTop: 16, padding: "14px 18px", borderRadius: 10, background: "rgba(2,6,23,0.6)", border: "1px solid rgba(51,65,85,0.4)" }}>
          <div style={{ color: "#475569", fontFamily: "monospace", fontSize: 10, marginBottom: 10 }}>快速操作</div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {[
              { label: "管理中心",  path: "/dashboard", color: "#22d3ee" },
              { label: "监控大屏",  path: "/monitor",  color: "#34d399" },
              { label: "统计分析",  path: "/stats",    color: "#f87171" },
              { label: "漏洞库",    path: "/vulns",    color: "#ef4444" },
              { label: "平台管理",  path: "/admin",    color: "#a78bfa" },
              { label: "配置中心",  path: "/config",   color: "#818cf8" },
              { label: "批量调度",  path: "/batch",    color: "#818cf8" },
              { label: "任务管理",  path: "/tasks",    color: "#38bdf8" },
              { label: "技能库",    path: "/skills",   color: "#fbbf24" },
              { label: "报告中心",  path: "/reports",  color: "#fb923c" },
              { label: "运行日志",  path: "/logs",     color: "#64748b" },
            ].map((link) => (
              <button
                key={link.path}
                type="button"
                onClick={() => navigate(link.path)}
                style={{
                  padding: "6px 16px", borderRadius: 6,
                  border: `1px solid ${link.color}40`,
                  background: `${link.color}0a`,
                  color: link.color, fontFamily: "monospace", fontSize: 11,
                  fontWeight: 700, cursor: "pointer", letterSpacing: "0.04em",
                }}
              >{link.label}</button>
            ))}
          </div>
        </div>

        {/* ── Auto-refresh note ─────────────────────────────────────────── */}
        <div style={{ marginTop: 12, textAlign: "center", color: "#1e293b", fontFamily: "monospace", fontSize: 10 }}>
          数据每 15 秒自动刷新 · 系统状态页
        </div>
      </div>
    </div>
  );
};

export default SystemPage;
