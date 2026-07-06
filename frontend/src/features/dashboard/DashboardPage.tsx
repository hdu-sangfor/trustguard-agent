/**
 * DashboardPage — 运营管理中心
 * 路由: /dashboard
 * 统一管理视图：任务统计 + 活跃任务 + 最近事件流 + 快捷操作。
 * 后端: GET /api/v1/admin/dashboard/summary (DB-only, <200ms)
 * 自动每 15 秒刷新。
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import {
  getDashboardSummary, getSystemHealth,
  bulkStopRunningTasks, cleanupFinishedTasks,
  createTask, runTask, listTasks,
  type ApiDashboardSummary, type ApiDashboardActiveTask, type ApiGlobalEvent,
  type ApiTask,
} from "@/shared/lib/api";
import { readStoredOrbitTasks } from "@/shared/constants/orbitTasksStorage";

// ── Phase colours ─────────────────────────────────────────────────────────────
const PHASE_COLORS: Record<string, string> = {
  RECON: "#38bdf8", THREAT_MODEL: "#818cf8", VULN_SCAN: "#fb923c",
  EXPLOIT: "#f87171", REPORT: "#34d399", DONE: "#4ade80",
};
const PHASE_LABELS: Record<string, string> = {
  RECON: "侦察", THREAT_MODEL: "建模", VULN_SCAN: "扫描",
  EXPLOIT: "利用", REPORT: "报告", DONE: "完成",
};

// ── Event type colours ────────────────────────────────────────────────────────
const EVENT_COLORS: Record<string, string> = {
  SKILL_COMPLETED:   "#34d399",
  SKILL_STARTED:     "#38bdf8",
  ORCHESTRATOR_TICK: "#64748b",
  PHASE_TRANSITION:  "#818cf8",
  SKILL_FAILED:      "#f87171",
  TASK_COMPLETED:    "#4ade80",
  TASK_CREATED:      "#a78bfa",
  TASK_FAILED:       "#f87171",
};
function evColor(type: string) { return EVENT_COLORS[type] ?? "#64748b"; }
function evLabel(type: string) {
  const m: Record<string, string> = {
    SKILL_COMPLETED: "技能完成", SKILL_STARTED: "技能启动",
    ORCHESTRATOR_TICK: "编排推进", PHASE_TRANSITION: "阶段切换",
    SKILL_FAILED: "技能失败", TASK_COMPLETED: "任务完成",
    TASK_CREATED: "任务创建", TASK_FAILED: "任务失败",
  };
  return m[type] ?? type;
}

// ── Demo data fallback ────────────────────────────────────────────────────────
function buildDemoSummary(): ApiDashboardSummary {
  const local = readStoredOrbitTasks();
  const running = local.filter((t) => t.status === "running").length;
  const done = local.filter((t) => t.status === "finished").length;
  const failed = local.filter((t) => t.status === "failed").length;
  const paused = local.filter((t) => t.status === "paused").length;
  const total = local.length;

  const activeTasks: ApiDashboardActiveTask[] = local
    .filter((t) => t.status === "running" || t.status === "paused")
    .slice(0, 5)
    .map((t) => ({
      taskId: t.id,
      name: t.name,
      target: t.url,
      status: t.status === "running" ? "RUNNING" : "PAUSED",
      currentPhase: t.currentPhase ?? "VULN_SCAN",
      updatedAt: t.updatedAt ? new Date(t.updatedAt).toISOString() : null,
    }));

  const demoEvents: ApiGlobalEvent[] = [
    { taskId: "task-demo1", eventType: "SKILL_COMPLETED", sourceModule: "executor",   ts: new Date(Date.now() - 12000).toISOString() },
    { taskId: "task-demo2", eventType: "PHASE_TRANSITION", sourceModule: "orchestrator", ts: new Date(Date.now() - 34000).toISOString() },
    { taskId: "task-demo3", eventType: "SKILL_STARTED",    sourceModule: "executor",   ts: new Date(Date.now() - 55000).toISOString() },
    { taskId: "task-demo1", eventType: "ORCHESTRATOR_TICK", sourceModule: "orchestrator", ts: new Date(Date.now() - 80000).toISOString() },
    { taskId: "task-demo2", eventType: "SKILL_COMPLETED",  sourceModule: "executor",   ts: new Date(Date.now() - 120000).toISOString() },
    { taskId: "task-demo3", eventType: "TASK_COMPLETED",   sourceModule: "orchestrator", ts: new Date(Date.now() - 240000).toISOString() },
  ];

  return {
    task_stats: {
      total: Math.max(total, 11),
      running: Math.max(running, 3),
      paused: Math.max(paused, 1),
      done: Math.max(done, 5),
      failed: Math.max(failed, 1),
      pending: Math.max(total - running - done - failed - paused, 1),
    },
    recent_events: demoEvents,
    active_tasks: activeTasks.length > 0 ? activeTasks : [
      { taskId: "task-demo1", name: "Web 常规渗透测试", target: "http://192.168.1.100/dvwa/", status: "RUNNING", currentPhase: "EXPLOIT", updatedAt: new Date().toISOString() },
      { taskId: "task-demo2", name: "Struts2 RCE 检测",  target: "http://192.168.1.102:8080/", status: "RUNNING", currentPhase: "VULN_SCAN", updatedAt: new Date().toISOString() },
      { taskId: "task-demo3", name: "Flask SSTI 漏洞",   target: "http://192.168.1.103:5000/", status: "PAUSED",  currentPhase: "REPORT", updatedAt: new Date().toISOString() },
    ],
    recent_completed: [
      { taskId: "task-c1", name: "API 接口安全测试",   target: "http://192.168.1.101:8080/api/v1/", updatedAt: new Date(Date.now() - 3600000).toISOString() },
      { taskId: "task-c2", name: "内网主机综合渗透",   target: "192.168.10.5", updatedAt: new Date(Date.now() - 7200000).toISOString() },
      { taskId: "task-c3", name: "ThinkPHP RCE 检测",  target: "http://192.168.1.104/", updatedAt: new Date(Date.now() - 14400000).toISOString() },
    ],
    generated_at: new Date().toISOString(),
  };
}

// ── Sub-components ────────────────────────────────────────────────────────────
function KpiCard({ label, value, color, sub }: { label: string; value: number; color: string; sub?: string }) {
  return (
    <div style={{
      background: `${color}08`, border: `1px solid ${color}22`,
      borderRadius: 10, padding: "16px 18px",
      display: "flex", flexDirection: "column", gap: 3,
    }}>
      <div style={{ fontSize: 10, fontFamily: "monospace", color: "rgba(148,163,184,0.5)", letterSpacing: "0.08em", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 30, fontWeight: 900, color, fontFamily: "monospace", lineHeight: 1.1 }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: "rgba(148,163,184,0.35)", fontFamily: "monospace" }}>{sub}</div>}
    </div>
  );
}

function PhasePip({ phase }: { phase: string }) {
  const color = PHASE_COLORS[phase] ?? "#64748b";
  const label = PHASE_LABELS[phase] ?? phase;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "2px 8px", borderRadius: 4,
      background: `${color}18`, border: `1px solid ${color}40`,
      fontSize: 10, fontFamily: "monospace", color,
    }}>
      <span style={{ width: 5, height: 5, borderRadius: "50%", background: color, boxShadow: `0 0 4px ${color}` }} />
      {label}
    </span>
  );
}

function ActiveTaskRow({ task, navigate }: { task: ApiDashboardActiveTask; navigate: (p: string) => void }) {
  const isRunning = task.status === "RUNNING";
  const stColor = isRunning ? "#22d3ee" : "#fbbf24";
  return (
    <div
      onClick={() => navigate(`/trace/${task.taskId}`)}
      style={{
        display: "grid", gridTemplateColumns: "1fr 160px 90px 70px",
        gap: 10, padding: "10px 14px",
        borderBottom: "1px solid rgba(51,65,85,0.2)",
        cursor: "pointer", transition: "background 0.15s",
        alignItems: "center",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(34,211,238,0.03)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "")}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, color: "#e2e8f0", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{task.name}</div>
        <div style={{ fontSize: 11, color: "rgba(148,163,184,0.5)", fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{task.target}</div>
      </div>
      <PhasePip phase={task.currentPhase} />
      <span style={{
        display: "inline-flex", alignItems: "center", gap: 4,
        fontSize: 11, fontFamily: "monospace", color: stColor,
      }}>
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: stColor, boxShadow: isRunning ? `0 0 5px ${stColor}` : undefined }} />
        {isRunning ? "运行中" : "已暂停"}
      </span>
      <span style={{ fontSize: 10, color: "rgba(148,163,184,0.3)", fontFamily: "monospace" }}>
        {task.updatedAt ? new Date(task.updatedAt).toLocaleTimeString("zh-CN", { hour12: false }) : "—"}
      </span>
    </div>
  );
}

function EventRow({ ev, i }: { ev: ApiGlobalEvent; i: number }) {
  const color = evColor(ev.eventType ?? "");
  const ts = ev.ts
    ? new Date(ev.ts).toLocaleTimeString("zh-CN", { hour12: false })
    : "—";
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "7px 14px",
      background: i % 2 === 0 ? "transparent" : "rgba(15,23,42,0.2)",
    }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: color, boxShadow: `0 0 4px ${color}`, flexShrink: 0 }} />
      <span style={{ fontSize: 11, fontFamily: "monospace", color, flex: "0 0 80px" }}>
        {evLabel(ev.eventType ?? "")}
      </span>
      <span style={{ fontSize: 10, color: "rgba(148,163,184,0.35)", fontFamily: "monospace", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {ev.taskId ? String(ev.taskId).slice(-10) : "—"}
        {ev.sourceModule ? ` · ${ev.sourceModule}` : ""}
      </span>
      <span style={{ fontSize: 10, color: "rgba(100,116,139,0.5)", fontFamily: "monospace", flexShrink: 0 }}>{ts}</span>
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────
export default function DashboardPage() {
  const navigate = useNavigate();
  const { loggedIn } = useAppSession();

  const [data, setData] = useState<ApiDashboardSummary | null>(null);
  const [backendOk, setBackendOk] = useState<boolean | null>(null);
  const [isDemo, setIsDemo] = useState(false);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [opBusy, setOpBusy] = useState<string | null>(null);   // which op is running
  const [confirmOp, setConfirmOp] = useState<string | null>(null); // pending confirm

  // Quick create form
  const [quickName, setQuickName] = useState("");
  const [quickTarget, setQuickTarget] = useState("");
  const [quickAutoStart, setQuickAutoStart] = useState(true);
  const [quickLoading, setQuickLoading] = useState(false);

  // Pending tasks
  const [pendingTasks, setPendingTasks] = useState<ApiTask[]>([]);
  const [startingTask, setStartingTask] = useState<string | null>(null);

  useEffect(() => {
    if (!loggedIn) {
      toast.error("请先登录");
      localStorage.setItem("sentinel_login_redirect", "/dashboard");
      navigate("/login");
    }
  }, [loggedIn, navigate]);

  const load = useCallback(async () => {
    try {
      const [summary, health, tasks] = await Promise.allSettled([
        getDashboardSummary(),
        getSystemHealth(),
        listTasks(),
      ]);
      if (summary.status === "fulfilled") {
        setData(summary.value);
        setIsDemo(false);
      } else {
        setData(buildDemoSummary());
        setIsDemo(true);
      }
      if (health.status === "fulfilled") {
        setBackendOk(health.value.status === "ok");
      } else {
        setBackendOk(false);
      }
      if (tasks.status === "fulfilled") {
        setPendingTasks(tasks.value.filter((t) => t.status === "PENDING"));
      }
    } catch {
      setData(buildDemoSummary());
      setIsDemo(true);
      setBackendOk(false);
    } finally {
      setLoading(false);
      setLastRefresh(new Date());
    }
  }, []);

  useEffect(() => {
    if (!loggedIn) return;
    void load();
    const iv = window.setInterval(() => { void load(); }, 15000);
    return () => window.clearInterval(iv);
  }, [loggedIn, load]);

  const handleBulkStop = async () => {
    setConfirmOp(null);
    setOpBusy("stop");
    try {
      const res = await bulkStopRunningTasks();
      toast.success(`已停止 ${res.stopped} 个运行中任务`, { duration: 4000 });
      setTimeout(() => { void load(); }, 1200);
    } catch {
      toast.error("停止操作失败（演示模式下不可用）");
    } finally {
      setOpBusy(null);
    }
  };

  const handleCleanup = async () => {
    setConfirmOp(null);
    setOpBusy("cleanup");
    try {
      const res = await cleanupFinishedTasks();
      toast.success(`已清理 ${res.deleted} 条已完成/失败记录`, { duration: 4000 });
      setTimeout(() => { void load(); }, 1200);
    } catch {
      toast.error("清理操作失败（演示模式下不可用）");
    } finally {
      setOpBusy(null);
    }
  };

  const handleQuickCreate = async () => {
    if (!quickName.trim() || !quickTarget.trim()) {
      toast.error("请填写任务名称和目标地址");
      return;
    }
    setQuickLoading(true);
    try {
      const task = await createTask({ name: quickName.trim(), description: "", target: quickTarget.trim() });
      if (quickAutoStart) {
        await runTask(task.taskId);
        toast.success(`任务已创建并启动：${task.name}`, { duration: 3000 });
      } else {
        toast.success(`任务已创建：${task.name}`, { duration: 3000 });
      }
      setQuickName("");
      setQuickTarget("");
      setTimeout(() => { void load(); }, 1000);
    } catch (err) {
      toast.error(`创建失败：${err instanceof Error ? err.message : "请检查后端连接"}`);
    } finally {
      setQuickLoading(false);
    }
  };

  const handleStartTask = async (taskId: string) => {
    setStartingTask(taskId);
    try {
      await runTask(taskId);
      toast.success("任务已启动", { duration: 2000 });
      setTimeout(() => { void load(); }, 1000);
    } catch {
      toast.error("启动失败（演示模式或后端离线）");
    } finally {
      setStartingTask(null);
    }
  };

  if (!loggedIn) return null;

  const stats = data?.task_stats;
  const completionRate = stats && stats.total > 0
    ? Math.round(((stats.done ?? 0) / stats.total) * 100)
    : 0;

  // Quick action definitions
  const QUICK_ACTIONS = [
    { label: "+ 新建任务",   path: "/tasks",   color: "#22d3ee", bg: "rgba(34,211,238,0.1)",  border: "rgba(34,211,238,0.4)" },
    { label: "⚡ 批量调度",  path: "/batch",   color: "#818cf8", bg: "rgba(129,140,248,0.1)", border: "rgba(129,140,248,0.4)" },
    { label: "⚠ 漏洞库",    path: "/vulns",   color: "#ef4444", bg: "rgba(239,68,68,0.08)",  border: "rgba(239,68,68,0.35)" },
    { label: "📋 报告中心",  path: "/reports", color: "#fb923c", bg: "rgba(251,146,60,0.08)", border: "rgba(251,146,60,0.35)" },
    { label: "📡 监控大屏",  path: "/monitor", color: "#34d399", bg: "rgba(52,211,153,0.08)", border: "rgba(52,211,153,0.35)" },
    { label: "📊 统计分析",  path: "/stats",   color: "#f87171", bg: "rgba(248,113,113,0.08)", border: "rgba(248,113,113,0.35)" },
  ];

  return (
    <div style={{ minHeight: "100vh", background: "#020a12", paddingTop: 80, paddingBottom: 60 }}>
      <Header />

      <div style={{ maxWidth: 1280, margin: "0 auto", padding: "0 24px" }}>

        {/* ── Page header ── */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24, flexWrap: "wrap", gap: 12 }}>
          <div>
            <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800, color: "#e2e8f0", fontFamily: "monospace", letterSpacing: "0.06em" }}>
              运营管理中心
              <span style={{ marginLeft: 12, fontSize: 11, color: "rgba(148,163,184,0.4)", fontWeight: 400 }}>Operations Dashboard</span>
            </h1>
            <div style={{ fontSize: 11, fontFamily: "monospace", display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ color: backendOk ? "rgba(52,211,153,0.8)" : isDemo ? "rgba(251,191,36,0.7)" : "rgba(148,163,184,0.4)" }}>
                {backendOk ? "● 实时数据" : isDemo ? "○ 演示模式" : "● 连接中…"}
              </span>
              {lastRefresh && (
                <span style={{ color: "rgba(100,116,139,0.5)" }}>
                  {lastRefresh.toLocaleTimeString("zh-CN", { hour12: false })} · 15s自动刷新
                </span>
              )}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              onClick={() => { setLoading(true); void load(); }}
              style={{
                padding: "6px 14px", borderRadius: 6,
                background: "rgba(34,211,238,0.08)", border: "1px solid rgba(34,211,238,0.3)",
                color: "#22d3ee", fontSize: 11, cursor: "pointer", fontFamily: "monospace",
              }}
            >↻ 刷新</button>
            <button
              type="button"
              onClick={() => navigate("/admin")}
              style={{
                padding: "6px 14px", borderRadius: 6,
                background: "rgba(15,23,42,0.5)", border: "1px solid rgba(51,65,85,0.4)",
                color: "#94a3b8", fontSize: 11, cursor: "pointer", fontFamily: "monospace",
              }}
            >平台管理 →</button>
          </div>
        </div>

        {/* ── KPI row ── */}
        {stats && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 12, marginBottom: 20 }}>
            <KpiCard label="总任务" value={stats.total ?? 0} color="#38bdf8" sub={`完成率 ${completionRate}%`} />
            <KpiCard label="运行中" value={stats.running ?? 0} color="#22d3ee" />
            <KpiCard label="已完成" value={stats.done ?? 0} color="#34d399" />
            <KpiCard label="已暂停" value={stats.paused ?? 0} color="#fbbf24" />
            <KpiCard label="失败" value={stats.failed ?? 0} color="#f87171" />
            <KpiCard label="待启动" value={stats.pending ?? 0} color="#64748b" />
          </div>
        )}

        {/* ── Admin ops bar ── */}
        <div style={{
          marginBottom: 18,
          padding: "12px 16px",
          background: "rgba(15,23,42,0.5)", border: "1px solid rgba(51,65,85,0.35)",
          borderRadius: 10, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        }}>
          <span style={{ fontSize: 10, fontFamily: "monospace", color: "rgba(100,116,139,0.6)", letterSpacing: "0.07em", textTransform: "uppercase" as const, flexShrink: 0 }}>
            管控操作
          </span>
          {/* Bulk stop */}
          {confirmOp === "stop" ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, fontFamily: "monospace" }}>
              <span style={{ color: "#fbbf24" }}>确认停止所有运行中任务？</span>
              <button type="button" onClick={() => { void handleBulkStop(); }}
                style={{ padding: "3px 10px", borderRadius: 4, background: "rgba(239,68,68,0.2)", border: "1px solid rgba(239,68,68,0.6)", color: "#f87171", fontSize: 11, cursor: "pointer" }}>
                确认</button>
              <button type="button" onClick={() => setConfirmOp(null)}
                style={{ padding: "3px 10px", borderRadius: 4, background: "transparent", border: "1px solid #475569", color: "#94a3b8", fontSize: 11, cursor: "pointer" }}>
                取消</button>
            </span>
          ) : (
            <button type="button"
              onClick={() => setConfirmOp("stop")}
              disabled={opBusy !== null}
              style={{
                padding: "5px 14px", borderRadius: 6,
                background: "rgba(248,113,113,0.08)", border: "1px solid rgba(248,113,113,0.3)",
                color: opBusy === "stop" ? "#64748b" : "#f87171",
                fontSize: 11, cursor: opBusy ? "not-allowed" : "pointer", fontFamily: "monospace",
              }}>
              {opBusy === "stop" ? "停止中…" : `⏹ 停止运行中任务${stats?.running ? ` (${stats.running})` : ""}`}
            </button>
          )}
          {/* Cleanup */}
          {confirmOp === "cleanup" ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, fontFamily: "monospace" }}>
              <span style={{ color: "#fbbf24" }}>确认清理已完成/失败记录？（不可恢复）</span>
              <button type="button" onClick={() => { void handleCleanup(); }}
                style={{ padding: "3px 10px", borderRadius: 4, background: "rgba(239,68,68,0.2)", border: "1px solid rgba(239,68,68,0.6)", color: "#f87171", fontSize: 11, cursor: "pointer" }}>
                确认</button>
              <button type="button" onClick={() => setConfirmOp(null)}
                style={{ padding: "3px 10px", borderRadius: 4, background: "transparent", border: "1px solid #475569", color: "#94a3b8", fontSize: 11, cursor: "pointer" }}>
                取消</button>
            </span>
          ) : (
            <button type="button"
              onClick={() => setConfirmOp("cleanup")}
              disabled={opBusy !== null}
              style={{
                padding: "5px 14px", borderRadius: 6,
                background: "rgba(100,116,139,0.08)", border: "1px solid rgba(100,116,139,0.3)",
                color: opBusy === "cleanup" ? "#64748b" : "#94a3b8",
                fontSize: 11, cursor: opBusy ? "not-allowed" : "pointer", fontFamily: "monospace",
              }}>
              {opBusy === "cleanup" ? "清理中…" : `🗑 清理完成/失败记录${stats ? ` (${(stats.done ?? 0) + (stats.failed ?? 0)})` : ""}`}
            </button>
          )}
          <button type="button" onClick={() => navigate("/audit")}
            style={{ padding: "5px 14px", borderRadius: 6, background: "transparent", border: "1px solid rgba(129,140,248,0.25)", color: "#818cf8", fontSize: 11, cursor: "pointer", fontFamily: "monospace" }}>
            审计日志 →
          </button>
          <button type="button" onClick={() => navigate("/admin")}
            style={{ marginLeft: "auto", padding: "5px 14px", borderRadius: 6, background: "transparent", border: "1px solid rgba(129,140,248,0.3)", color: "#a78bfa", fontSize: 11, cursor: "pointer", fontFamily: "monospace" }}>
            平台管理 →
          </button>
        </div>

        {/* ── Main grid ── */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 18 }}>

          {/* Left: Active tasks + Recent completed */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

            {/* Active tasks */}
            <div style={{
              background: "rgba(15,23,42,0.6)", border: "1px solid rgba(51,65,85,0.5)",
              borderRadius: 10, overflow: "hidden",
            }}>
              <div style={{
                padding: "12px 16px", borderBottom: "1px solid rgba(51,65,85,0.4)",
                display: "flex", justifyContent: "space-between", alignItems: "center",
              }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: "rgba(226,232,240,0.85)", fontFamily: "monospace", letterSpacing: "0.05em" }}>
                  活跃任务
                  {data && data.active_tasks.length > 0 && (
                    <span style={{ marginLeft: 8, fontSize: 10, color: "#22d3ee", fontFamily: "monospace" }}>
                      {data.active_tasks.length} 个
                    </span>
                  )}
                </span>
                <button type="button" onClick={() => navigate("/tasks")}
                  style={{ background: "none", border: "none", color: "#38bdf8", cursor: "pointer", fontSize: 11, fontFamily: "monospace", padding: 0 }}>
                  全部任务 →
                </button>
              </div>

              {loading ? (
                <div style={{ padding: "24px", textAlign: "center", color: "rgba(148,163,184,0.3)", fontFamily: "monospace", fontSize: 12 }}>加载中…</div>
              ) : data && data.active_tasks.length > 0 ? (
                <>
                  {/* Column headers */}
                  <div style={{
                    display: "grid", gridTemplateColumns: "1fr 160px 90px 70px",
                    gap: 10, padding: "6px 14px",
                    background: "rgba(15,23,42,0.4)",
                    borderBottom: "1px solid rgba(51,65,85,0.2)",
                  }}>
                    {["任务 / 目标", "当前阶段", "状态", "更新"].map((h) => (
                      <span key={h} style={{ fontSize: 10, fontFamily: "monospace", color: "rgba(100,116,139,0.7)", letterSpacing: "0.06em" }}>{h}</span>
                    ))}
                  </div>
                  {data.active_tasks.map((t) => (
                    <ActiveTaskRow key={t.taskId} task={t} navigate={navigate} />
                  ))}
                </>
              ) : (
                <div style={{ padding: "24px 16px", textAlign: "center" }}>
                  <div style={{ color: "rgba(148,163,184,0.4)", fontFamily: "monospace", fontSize: 12, marginBottom: 10 }}>
                    无活跃任务
                  </div>
                  <button type="button" onClick={() => navigate("/tasks")}
                    style={{
                      padding: "7px 16px", borderRadius: 6,
                      background: "rgba(34,211,238,0.08)", border: "1px solid rgba(34,211,238,0.3)",
                      color: "#22d3ee", fontSize: 12, cursor: "pointer", fontFamily: "monospace",
                    }}>
                    + 创建任务
                  </button>
                </div>
              )}
            </div>

            {/* Pending tasks */}
            {pendingTasks.length > 0 && (
              <div style={{
                background: "rgba(15,23,42,0.6)", border: "1px solid rgba(100,116,139,0.3)",
                borderRadius: 10, overflow: "hidden",
              }}>
                <div style={{ padding: "10px 16px", borderBottom: "1px solid rgba(51,65,85,0.3)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "rgba(226,232,240,0.85)", fontFamily: "monospace" }}>
                    待启动任务
                    <span style={{ marginLeft: 8, fontSize: 10, color: "#64748b", fontFamily: "monospace" }}>{pendingTasks.length} 个</span>
                  </span>
                  <button type="button" onClick={() => navigate("/tasks")}
                    style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer", fontSize: 11, fontFamily: "monospace", padding: 0 }}>
                    全部 →
                  </button>
                </div>
                {pendingTasks.slice(0, 6).map((t, i) => (
                  <div key={t.taskId} style={{
                    display: "flex", alignItems: "center", gap: 10,
                    padding: "9px 14px",
                    borderBottom: i < Math.min(pendingTasks.length, 6) - 1 ? "1px solid rgba(51,65,85,0.15)" : undefined,
                  }}>
                    <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#64748b", flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, color: "#e2e8f0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.name}</div>
                      <div style={{ fontSize: 10, color: "rgba(148,163,184,0.4)", fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.target}</div>
                    </div>
                    <button type="button" onClick={() => { void handleStartTask(t.taskId); }}
                      disabled={startingTask === t.taskId}
                      style={{
                        padding: "4px 12px", borderRadius: 5, flexShrink: 0,
                        background: startingTask === t.taskId ? "rgba(51,65,85,0.4)" : "rgba(34,211,238,0.08)",
                        border: `1px solid ${startingTask === t.taskId ? "rgba(51,65,85,0.4)" : "rgba(34,211,238,0.3)"}`,
                        color: startingTask === t.taskId ? "#64748b" : "#22d3ee",
                        fontSize: 11, cursor: startingTask === t.taskId ? "not-allowed" : "pointer",
                        fontFamily: "monospace",
                      }}>
                      {startingTask === t.taskId ? "启动中…" : "▶ 启动"}
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Recent completed */}
            {data && data.recent_completed.length > 0 && (
              <div style={{
                background: "rgba(15,23,42,0.6)", border: "1px solid rgba(52,211,153,0.2)",
                borderRadius: 10, overflow: "hidden",
              }}>
                <div style={{
                  padding: "10px 16px", borderBottom: "1px solid rgba(52,211,153,0.15)",
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "rgba(226,232,240,0.85)", fontFamily: "monospace" }}>
                    最近完成
                  </span>
                  <button type="button" onClick={() => navigate("/reports")}
                    style={{ background: "none", border: "none", color: "#34d399", cursor: "pointer", fontSize: 11, fontFamily: "monospace", padding: 0 }}>
                    报告中心 →
                  </button>
                </div>
                {data.recent_completed.map((t, i) => (
                  <div
                    key={t.taskId}
                    onClick={() => navigate(`/reports`)}
                    style={{
                      display: "flex", alignItems: "center", gap: 10,
                      padding: "9px 14px",
                      borderBottom: i < data.recent_completed.length - 1 ? "1px solid rgba(51,65,85,0.15)" : undefined,
                      cursor: "pointer",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(52,211,153,0.03)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "")}
                  >
                    <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#34d399", boxShadow: "0 0 4px #34d399", flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, color: "#e2e8f0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.name}</div>
                      <div style={{ fontSize: 10, color: "rgba(148,163,184,0.4)", fontFamily: "monospace" }}>{t.target}</div>
                    </div>
                    <div style={{ fontSize: 10, color: "rgba(100,116,139,0.5)", fontFamily: "monospace", flexShrink: 0 }}>
                      {t.updatedAt ? new Date(t.updatedAt).toLocaleDateString("zh-CN") : "—"}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Quick create form */}
            <div style={{
              background: "rgba(15,23,42,0.5)", border: "1px solid rgba(34,211,238,0.2)",
              borderRadius: 10, padding: "14px 16px",
            }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#22d3ee", fontFamily: "monospace", letterSpacing: "0.07em", marginBottom: 12 }}>
                ⚡ 快速创建任务
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <input
                  value={quickName}
                  onChange={(e) => setQuickName(e.target.value)}
                  placeholder="任务名称（如：Web 渗透测试）"
                  style={{
                    width: "100%", padding: "8px 10px", borderRadius: 6,
                    background: "rgba(15,23,42,0.8)", border: "1px solid rgba(51,65,85,0.5)",
                    color: "#e2e8f0", fontSize: 12, fontFamily: "monospace", outline: "none",
                    boxSizing: "border-box" as const,
                  }}
                  onKeyDown={(e) => { if (e.key === "Enter") { void handleQuickCreate(); } }}
                />
                <input
                  value={quickTarget}
                  onChange={(e) => setQuickTarget(e.target.value)}
                  placeholder="目标地址（IP / URL）"
                  style={{
                    width: "100%", padding: "8px 10px", borderRadius: 6,
                    background: "rgba(15,23,42,0.8)", border: "1px solid rgba(51,65,85,0.5)",
                    color: "#e2e8f0", fontSize: 12, fontFamily: "monospace", outline: "none",
                    boxSizing: "border-box" as const,
                  }}
                  onKeyDown={(e) => { if (e.key === "Enter") { void handleQuickCreate(); } }}
                />
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 11, fontFamily: "monospace", color: "rgba(148,163,184,0.6)" }}>
                    <input type="checkbox" checked={quickAutoStart} onChange={(e) => setQuickAutoStart(e.target.checked)}
                      style={{ accentColor: "#22d3ee" }} />
                    创建后立即启动
                  </label>
                  <button type="button" onClick={() => { void handleQuickCreate(); }}
                    disabled={quickLoading || !quickName.trim() || !quickTarget.trim()}
                    style={{
                      padding: "7px 16px", borderRadius: 6,
                      background: quickLoading || !quickName.trim() || !quickTarget.trim()
                        ? "rgba(51,65,85,0.4)" : "rgba(34,211,238,0.15)",
                      border: `1px solid ${quickLoading || !quickName.trim() || !quickTarget.trim() ? "rgba(51,65,85,0.4)" : "rgba(34,211,238,0.5)"}`,
                      color: quickLoading || !quickName.trim() || !quickTarget.trim() ? "#64748b" : "#22d3ee",
                      fontSize: 12, cursor: quickLoading ? "not-allowed" : "pointer",
                      fontFamily: "monospace", fontWeight: 600,
                    }}>
                    {quickLoading ? "创建中…" : quickAutoStart ? "创建并启动" : "创建任务"}
                  </button>
                </div>
              </div>
            </div>

            {/* Quick nav grid */}
            <div style={{
              background: "rgba(15,23,42,0.5)", border: "1px solid rgba(51,65,85,0.4)",
              borderRadius: 10, padding: "12px 14px",
            }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: "rgba(148,163,184,0.4)", fontFamily: "monospace", letterSpacing: "0.07em", marginBottom: 10 }}>
                快捷导航
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 7 }}>
                {QUICK_ACTIONS.map(({ label, path, color, bg, border }) => (
                  <button
                    key={path}
                    type="button"
                    onClick={() => navigate(path)}
                    style={{
                      padding: "8px 6px", borderRadius: 6,
                      background: bg, border: `1px solid ${border}`,
                      color, fontSize: 11, cursor: "pointer",
                      fontFamily: "monospace", fontWeight: 600,
                      transition: "all 0.15s",
                    }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.filter = "brightness(1.2)"; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.filter = ""; }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Right: Event feed + Platform status */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

            {/* Real-time event feed */}
            <div style={{
              background: "rgba(15,23,42,0.6)", border: "1px solid rgba(51,65,85,0.5)",
              borderRadius: 10, overflow: "hidden",
            }}>
              <div style={{ padding: "12px 16px", borderBottom: "1px solid rgba(51,65,85,0.4)" }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: "rgba(226,232,240,0.85)", fontFamily: "monospace", letterSpacing: "0.05em" }}>
                  实时事件流
                </span>
              </div>
              <div style={{ maxHeight: 320, overflowY: "auto" }}>
                {(data?.recent_events ?? []).map((ev, i) => (
                  <EventRow key={i} ev={ev} i={i} />
                ))}
                {(!data || data.recent_events.length === 0) && (
                  <div style={{ padding: "20px 14px", fontSize: 12, color: "rgba(148,163,184,0.3)", fontFamily: "monospace", textAlign: "center" }}>
                    暂无事件
                  </div>
                )}
              </div>
            </div>

            {/* Platform compliance */}
            <div style={{
              background: "rgba(15,23,42,0.5)", border: "1px solid rgba(34,197,94,0.15)",
              borderRadius: 10, padding: "14px 16px",
            }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: "#86efac", fontFamily: "monospace", letterSpacing: "0.07em", marginBottom: 10 }}>
                ◎ 竞赛能力指标
              </div>
              {[
                ["安全技能数量", "40+ 工具", "#34d399"],
                ["单目标耗时", "≤ 15 分钟", "#22d3ee"],
                ["并发测试能力", "≥ 3 目标", "#818cf8"],
                ["报告自动生成", "MD + HTML", "#fb923c"],
                ["漏洞修复建议", "CVE + 修复方案", "#f87171"],
              ].map(([k, v, c]) => (
                <div key={k} style={{ display: "flex", justifyContent: "space-between", fontSize: 11, fontFamily: "monospace", padding: "3px 0", borderBottom: "1px solid rgba(51,65,85,0.1)" }}>
                  <span style={{ color: "rgba(148,163,184,0.5)" }}>{k}</span>
                  <span style={{ color: c as string, fontWeight: 700 }}>✓ {v}</span>
                </div>
              ))}
            </div>

            {/* Backend status */}
            <div style={{
              background: "rgba(15,23,42,0.4)", border: `1px solid ${backendOk ? "rgba(52,211,153,0.2)" : "rgba(251,191,36,0.2)"}`,
              borderRadius: 8, padding: "12px 14px",
            }}>
              <div style={{ fontSize: 10, color: "rgba(148,163,184,0.4)", fontFamily: "monospace", marginBottom: 8 }}>服务连接</div>
              {[
                ["Gateway Backend :18080", backendOk],
                ["Orchestrator :18081",    backendOk],
                ["Executor :18102",         backendOk],
              ].map(([label, ok]) => {
                const c = ok ? "#34d399" : isDemo ? "#fbbf24" : "#64748b";
                return (
                  <div key={String(label)} style={{ display: "flex", justifyContent: "space-between", fontSize: 11, fontFamily: "monospace", padding: "2px 0" }}>
                    <span style={{ color: "rgba(148,163,184,0.4)" }}>{String(label)}</span>
                    <span style={{ color: c }}>
                      <span style={{ width: 5, height: 5, borderRadius: "50%", background: c, display: "inline-block", marginRight: 5 }} />
                      {ok ? "在线" : isDemo ? "演示" : "离线"}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* ── Navigation strip ── */}
        <div style={{
          marginTop: 22, padding: "10px 16px",
          background: "rgba(15,23,42,0.4)", border: "1px solid rgba(51,65,85,0.3)",
          borderRadius: 8, display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center",
          fontSize: 11, fontFamily: "monospace",
        }}>
          <span style={{ color: "#475569" }}>导航：</span>
          {[
            { label: "任务管理",  path: "/tasks",   color: "#38bdf8" },
            { label: "批量调度",  path: "/batch",   color: "#818cf8" },
            { label: "漏洞库",    path: "/vulns",   color: "#ef4444" },
            { label: "报告中心",  path: "/reports", color: "#fb923c" },
            { label: "监控大屏",  path: "/monitor", color: "#34d399" },
            { label: "统计分析",  path: "/stats",   color: "#f87171" },
            { label: "审计日志",  path: "/audit",   color: "#818cf8" },
            { label: "平台管理",  path: "/admin",   color: "#a78bfa" },
            { label: "系统状态",  path: "/system",  color: "#64748b" },
          ].map(({ label, path, color }) => (
            <button key={path} type="button" onClick={() => navigate(path)}
              style={{ background: "none", border: "none", color, cursor: "pointer", fontFamily: "monospace", fontSize: 11, padding: 0 }}>
              {label} →
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
