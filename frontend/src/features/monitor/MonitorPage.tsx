/**
 * MonitorPage — 实时监控大屏
 * 每 5 秒轮询 /api/v1/admin/monitor/snapshot，展示：
 *   - 任务统计指标卡
 *   - 活跃任务 + 阶段流水线可视化
 *   - 实时全局事件流
 *   - 平台并发能力指示
 * 离线时从 localStorage 读取演示数据。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import { getMonitorSnapshot, TRUSTGUARD_PHASES, type ApiActiveTask, type ApiGlobalEvent, type ApiMonitorSnapshot, type ApiTaskStats } from "@/shared/lib/api";
import { readStoredOrbitTasks } from "@/shared/constants/orbitTasksStorage";
import { formatLocalDateTime, formatLocalTime, parseTimestampMs } from "@/shared/lib/time";

// ─── Phase config ──────────────────────────────────────────────────────────────
const PHASE_LABELS: Record<string, string> = {
  RECON:        "情报收集",
  THREAT_MODEL: "威胁建模",
  VULN_SCAN:    "漏洞扫描",
  EXPLOIT:      "漏洞利用",
  REPORT:       "报告生成",
  DONE:         "已完成",
};

const PHASE_COLORS: Record<string, string> = {
  RECON:        "#38bdf8",
  THREAT_MODEL: "#818cf8",
  VULN_SCAN:    "#fb923c",
  EXPLOIT:      "#f87171",
  REPORT:       "#34d399",
  DONE:         "#4ade80",
};

const phaseIndex = (phase: string | null | undefined): number => {
  if (!phase) return -1;
  const p = phase.toUpperCase();
  const idx = (TRUSTGUARD_PHASES as readonly string[]).indexOf(p);
  return idx >= 0 ? idx : -1;
};

// ─── Demo data builder ─────────────────────────────────────────────────────────
function buildDemoSnapshot(): ApiMonitorSnapshot {
  const tasks = readStoredOrbitTasks();
  const running = tasks.filter(t => t.status === "running" || t.status === "paused");
  const done    = tasks.filter(t => t.status === "finished").length;
  const failed  = tasks.filter(t => t.status === "failed").length;

  const activeTasks: ApiActiveTask[] = running.map(t => ({
    taskId:       t.id,
    name:         t.name,
    target:       t.url,
    status:       t.status === "running" ? "RUNNING" : "PAUSED",
    currentPhase: t.currentPhase ?? null,
    createdAt:    t.createdAt ? new Date(t.createdAt).toISOString() : null,
    updatedAt:    t.updatedAt ? new Date(t.updatedAt).toISOString() : null,
  }));

  const taskStats: ApiTaskStats = {
    total:   tasks.length,
    running: running.filter(t => t.status === "running").length,
    paused:  running.filter(t => t.status === "paused").length,
    done,
    failed,
    pending: 0,
  };

  const demoEvents: ApiGlobalEvent[] = running.slice(0, 5).map((t, i) => ({
    taskId:      t.id,
    eventType:   ["PHASE_TRANSITION", "SKILL_COMPLETED", "SKILL_STARTED", "ORCHESTRATOR_TICK"][i % 4],
    ts:          new Date(Date.now() - i * 12000).toISOString(),
    sourceModule: i % 2 === 0 ? "ORCHESTRATOR" : "EXECUTOR",
  }));

  return {
    taskStats,
    activeTasks,
    recentTasks: tasks.slice(0, 10).map(t => ({
      taskId:       t.id,
      name:         t.name,
      status:       t.status === "running" ? "RUNNING"
                  : t.status === "paused"  ? "PAUSED"
                  : t.status === "finished" ? "DONE"
                  : t.status === "failed"   ? "FAILED" : "PENDING",
      currentPhase: t.currentPhase ?? null,
      updatedAt:    t.updatedAt ? new Date(t.updatedAt).toISOString() : null,
    })),
    recentEvents: demoEvents,
    mqStatus:     { mode: "demo", available: false },
    snapshotAt:   new Date().toISOString(),
  };
}

// ─── Sub-components ────────────────────────────────────────────────────────────

/** Single stat metric card */
function StatCard({ label, value, color, dim }: { label: string; value: number | string; color: string; dim?: boolean }) {
  return (
    <div style={{
      flex: "1 1 130px",
      minWidth: 120,
      background: dim ? "var(--tg-panel-muted)" : "var(--tg-panel-bg)",
      border: `1px solid ${color}40`,
      borderRadius: 10,
      padding: "18px 20px",
      textAlign: "center",
      boxShadow: dim ? "none" : "var(--tg-shadow)",
      transition: "box-shadow 0.3s",
    }}>
      <div style={{ fontSize: 34, fontWeight: 900, color, fontFamily: "monospace", lineHeight: 1.1, textShadow: `0 0 12px ${color}` }}>
        {value}
      </div>
      <div style={{ fontSize: 11, color: "var(--tg-text-muted)", marginTop: 6, letterSpacing: "0.06em", textTransform: "uppercase" }}>
        {label}
      </div>
    </div>
  );
}

/** Phase pipeline for a single task */
function PhasePipeline({ currentPhase }: { currentPhase: string | null | undefined }) {
  const cur = phaseIndex(currentPhase);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "nowrap", overflow: "hidden" }}>
      {(TRUSTGUARD_PHASES as readonly string[]).map((ph, i) => {
        const isActive  = i === cur;
        const isDone    = i < cur;
        const color     = PHASE_COLORS[ph] ?? "#64748b";
        const opacity   = isActive ? 1 : isDone ? 0.55 : 0.2;
        return (
          <div key={ph} style={{ display: "flex", alignItems: "center", gap: 2, flex: 1, minWidth: 0 }}>
            <div
              title={PHASE_LABELS[ph] ?? ph}
              style={{
                flex: 1,
                height: 6,
                borderRadius: 3,
                background: color,
                opacity,
                boxShadow: isActive ? `0 0 8px ${color}` : "none",
                transition: "opacity 0.2s, box-shadow 0.2s",
              }}
            />
            {i < (TRUSTGUARD_PHASES as readonly string[]).length - 1 && (
              <div style={{ width: 2, height: 6, background: "rgba(148,163,184,0.12)", flexShrink: 0 }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Active task card */
function ActiveTaskCard({ task, idx }: { task: ApiActiveTask; idx: number }) {
  const navigate = useNavigate();
  const isRunning = task.status === "RUNNING";
  const phaseLabel = task.currentPhase ? (PHASE_LABELS[task.currentPhase] ?? task.currentPhase) : "等待中";
  const phaseColor = task.currentPhase ? (PHASE_COLORS[task.currentPhase] ?? "#64748b") : "#64748b";

  const elapsed = (() => {
    if (!task.createdAt) return null;
    const createdAt = parseTimestampMs(task.createdAt);
    if (createdAt == null) return null;
    const secs = Math.floor((Date.now() - createdAt) / 1000);
    if (secs < 60) return `${secs}s`;
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `${mins}m${secs % 60}s`;
    return `${Math.floor(mins / 60)}h${mins % 60}m`;
  })();

  return (
    <div
      onClick={() => navigate(`/trace/${task.taskId}`)}
      style={{
        background: "var(--tg-panel-bg)",
        border: isRunning ? "1px solid rgba(34,211,238,0.35)" : "1px solid rgba(251,191,36,0.28)",
        borderRadius: 10,
        padding: "14px 16px",
        cursor: "pointer",
        transition: "border-color 0.25s, box-shadow 0.25s",
        boxShadow: isRunning ? "0 0 14px rgba(34,211,238,0.08)" : "none",
        position: "relative",
        overflow: "hidden",
      }}
      onMouseEnter={e => (e.currentTarget.style.boxShadow = "0 0 22px rgba(34,211,238,0.16)")}
      onMouseLeave={e => (e.currentTarget.style.boxShadow = isRunning ? "0 0 14px rgba(34,211,238,0.08)" : "none")}
    >
      {isRunning && (
        <div style={{
          position: "absolute", top: 0, left: 0, right: 0, height: 2,
          background: "#22d3ee",
        }} />
      )}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
        <div style={{ flex: 1, minWidth: 0, marginRight: 10 }}>
          <div style={{
            fontSize: 13, fontWeight: 700, color: "var(--tg-text)",
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          }}>
            #{idx + 1} {task.name}
          </div>
          <div style={{
            fontSize: 11, color: "var(--tg-text-muted)", marginTop: 2,
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            fontFamily: "monospace",
          }}>
            {task.target}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 3, flexShrink: 0 }}>
          <span style={{
            fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 4,
            background: isRunning ? "rgba(34,211,238,0.15)" : "rgba(251,191,36,0.15)",
            color: isRunning ? "#22d3ee" : "#fbbf24",
            border: isRunning ? "1px solid rgba(34,211,238,0.4)" : "1px solid rgba(251,191,36,0.4)",
            letterSpacing: "0.05em",
          }}>
            {isRunning ? "RUNNING" : "PAUSED"}
          </span>
          {elapsed && (
            <span style={{ fontSize: 10, color: "rgba(148,163,184,0.5)", fontFamily: "monospace" }}>
              {elapsed}
            </span>
          )}
        </div>
      </div>

      {/* Phase pipeline */}
      <PhasePipeline currentPhase={task.currentPhase} />

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 6 }}>
        <div style={{ fontSize: 11, color: phaseColor, fontFamily: "monospace", fontWeight: 600 }}>
          {task.currentPhase ?? "—"} · {phaseLabel}
        </div>
        <div style={{ fontSize: 10, color: "rgba(148,163,184,0.4)", fontFamily: "monospace" }}>
          {task.taskId?.slice(-8) ?? "local"}
        </div>
      </div>
    </div>
  );
}

/** Event type → color */
const EVENT_COLORS: Record<string, string> = {
  PHASE_TRANSITION:   "#818cf8",
  SKILL_STARTED:      "#38bdf8",
  SKILL_COMPLETED:    "#34d399",
  SKILL_FAILED:       "#f87171",
  ORCHESTRATOR_TICK:  "#94a3b8",
  TASK_CREATED:       "#a78bfa",
  TASK_COMPLETED:     "#4ade80",
  TASK_FAILED:        "#f87171",
  TASK_PAUSED:        "#fbbf24",
};

function eventColor(type: string): string {
  const key = Object.keys(EVENT_COLORS).find(k => type?.toUpperCase().includes(k));
  return key ? EVENT_COLORS[key] : "#64748b";
}

/** Concurrent execution indicator */
function ConcurrencyMeter({ running }: { running: number }) {
  const CAPACITY = 5;
  const slots = Array.from({ length: CAPACITY }, (_, i) => i < running);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ display: "flex", gap: 4 }}>
        {slots.map((active, i) => (
          <div
            key={i}
            style={{
              width: 20, height: 20, borderRadius: 5,
              background: active ? "rgba(34,211,238,0.25)" : "rgba(30,41,59,0.6)",
              border: active ? "1px solid rgba(34,211,238,0.7)" : "1px solid rgba(71,85,105,0.4)",
              boxShadow: active ? "0 0 8px rgba(34,211,238,0.4)" : "none",
              transition: "all 0.2s",
            }}
          />
        ))}
      </div>
      <span style={{ fontSize: 12, color: "rgba(148,163,184,0.7)", fontFamily: "monospace" }}>
        {running}/{CAPACITY} 并发槽
      </span>
    </div>
  );
}

// ─── Main page ─────────────────────────────────────────────────────────────────

export default function MonitorPage() {
  const navigate = useNavigate();
  const { loggedIn } = useAppSession();
  const [snapshot, setSnapshot] = useState<ApiMonitorSnapshot | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [lastRefresh, setLastRefresh] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<"time" | "status" | "phase">("time");
  const [filterText, setFilterText] = useState<string>("");
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchSnapshot = useCallback(async () => {
    try {
      const data = await getMonitorSnapshot(30);
      setSnapshot(data);
      setOnline(true);
      setError(null);
    } catch {
      setOnline(false);
      setError(null);
      setSnapshot(buildDemoSnapshot());
    }
    setLastRefresh(formatLocalTime(Date.now()));
  }, []);

  // Auth guard
  useEffect(() => {
    if (!loggedIn) {
      toast.error("请先登录");
      localStorage.setItem("sentinel_login_redirect", "/monitor");
      navigate("/login");
    }
  }, [loggedIn, navigate]);

  useEffect(() => {
    void fetchSnapshot();
    timerRef.current = setInterval(() => { void fetchSnapshot(); }, 5000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [fetchSnapshot]);

  const stats: ApiTaskStats = snapshot?.taskStats ?? {};
  const activeTasks: ApiActiveTask[] = snapshot?.activeTasks ?? [];
  const recentEvents: ApiGlobalEvent[] = snapshot?.recentEvents ?? [];
  const runningCount = Number(stats.running ?? 0);
  const pausedCount  = Number(stats.paused ?? 0);
  const doneCount    = Number(stats.done ?? 0);
  const failedCount  = Number(stats.failed ?? 0);
  const totalCount   = Number(stats.total ?? 0);

  // Filter and sort recent tasks
  const filteredAndSorted = [...(snapshot?.recentTasks ?? [])]
    .filter(t =>
      filterText === "" ||
      t.name?.toLowerCase().includes(filterText.toLowerCase()) ||
      t.taskId?.toLowerCase().includes(filterText.toLowerCase())
    )
    .sort((a, b) => {
      if (sortBy === "time") {
        return (parseTimestampMs(b.updatedAt) ?? 0) - (parseTimestampMs(a.updatedAt) ?? 0);
      } else if (sortBy === "status") {
        const statusOrder: Record<string, number> = { RUNNING: 0, PAUSED: 1, DONE: 2, FAILED: 3, PENDING: 4 };
        return (statusOrder[a.status] ?? 5) - (statusOrder[b.status] ?? 5);
      } else {
        // sort by phase (null phases first)
        const phaseA = phaseIndex(a.currentPhase);
        const phaseB = phaseIndex(b.currentPhase);
        return phaseB - phaseA;
      }
    });
  const sortedRecentTasks = filteredAndSorted;

  return (
    <div style={{ minHeight: "100vh", background: "var(--tg-page-gradient)", paddingTop: 60 }}>
      <Header />

      <div style={{ maxWidth: 1400, margin: "0 auto", padding: "24px 20px" }}>

        {/* ── Page header ── */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 28 }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
              <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#22d3ee", boxShadow: "0 0 6px rgba(34,211,238,0.35)" }} />
              <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: "var(--tg-text)", fontFamily: "monospace", letterSpacing: "0.1em" }}>
                实时监控大屏
              </h1>
            </div>
            <div style={{ fontSize: 12, color: "var(--tg-text-muted)", fontFamily: "monospace" }}>
              {online === true ? "● 后端实时数据" : online === false ? "○ 演示模式 · 本地数据" : "正在连接…"}
              {lastRefresh && <span style={{ marginLeft: 12 }}>上次刷新 {lastRefresh}（本地时间）</span>}
            </div>
          </div>

          <div style={{ display: "flex", gap: 10 }}>
            <button
              type="button"
              onClick={() => { void fetchSnapshot(); }}
              style={{
                background: "rgba(34,211,238,0.1)", border: "1px solid rgba(34,211,238,0.4)",
                color: "#22d3ee", borderRadius: 7, padding: "6px 14px",
                fontSize: 12, cursor: "pointer", fontFamily: "monospace",
              }}
            >
              ⟳ 刷新
            </button>
            <button
              type="button"
              onClick={() => navigate("/tasks")}
              style={{
                background: "rgba(15,23,42,0.6)", border: "1px solid rgba(71,85,105,0.5)",
                color: "var(--tg-text-muted)", borderRadius: 7, padding: "6px 14px",
                fontSize: 12, cursor: "pointer", fontFamily: "monospace",
              }}
            >
              → 任务管理
            </button>
          </div>
        </div>

        {error && (
          <div style={{
            padding: "10px 16px", background: "rgba(248,113,113,0.1)", border: "1px solid rgba(248,113,113,0.3)",
            borderRadius: 8, color: "#fca5a5", fontSize: 12, marginBottom: 20,
          }}>{error}</div>
        )}

        {/* ── Stat cards ── */}
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 28 }}>
          <StatCard label="总任务" value={totalCount} color="#64748b" dim={totalCount === 0} />
          <StatCard label="运行中" value={runningCount} color="#22d3ee" dim={runningCount === 0} />
          <StatCard label="已暂停" value={pausedCount} color="#fbbf24" dim={pausedCount === 0} />
          <StatCard label="已完成" value={doneCount} color="#34d399" dim={doneCount === 0} />
          <StatCard label="已失败" value={failedCount} color="#f87171" dim={failedCount === 0} />
          {/* Concurrent capacity indicator */}
          <div style={{
            flex: "1 1 200px", minWidth: 180,
            background: "var(--tg-panel-bg)", border: "1px solid var(--tg-panel-border)",
            borderRadius: 10, padding: "18px 20px",
            display: "flex", flexDirection: "column", justifyContent: "center", gap: 10,
          }}>
            <div style={{ fontSize: 11, color: "rgba(148,163,184,0.8)", textTransform: "uppercase", letterSpacing: "0.06em" }}>并发执行</div>
            <ConcurrencyMeter running={runningCount} />
          </div>
        </div>

        {/* ── Main body: active tasks + event feed ── */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 380px", gap: 20, alignItems: "start" }}>

          {/* Active tasks */}
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: "rgba(148,163,184,0.9)", marginBottom: 12, letterSpacing: "0.08em", textTransform: "uppercase" }}>
              活跃任务
              <span style={{ fontSize: 11, color: "rgba(148,163,184,0.45)", marginLeft: 8, fontWeight: 400 }}>
                {activeTasks.length > 0 ? `${activeTasks.length} 个任务运行中` : "暂无活跃任务"}
              </span>
            </div>

            {activeTasks.length === 0 ? (
              <div style={{
                padding: "48px 20px", textAlign: "center",
                background: "var(--tg-panel-muted)", border: "1px dashed var(--tg-panel-border)",
                borderRadius: 12, color: "var(--tg-text-faint)", fontSize: 13,
              }}>
                <div style={{ fontSize: 32, marginBottom: 10 }}>⬡</div>
                无活跃任务<br />
                <span style={{ fontSize: 11, marginTop: 6, display: "block" }}>
                  前往<button type="button" onClick={() => navigate("/tasks")} style={{ background: "none", border: "none", color: "#22d3ee", cursor: "pointer", padding: "0 4px", fontSize: 11 }}>任务管理</button>创建新任务
                </span>
              </div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 12 }}>
                {activeTasks.map((t, i) => (
                  <ActiveTaskCard key={t.taskId} task={t} idx={i} />
                ))}
              </div>
            )}

            {/* Phase legend */}
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px", marginTop: 20 }}>
              {(TRUSTGUARD_PHASES as readonly string[]).map(ph => (
                <div key={ph} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  <div style={{ width: 10, height: 10, borderRadius: 3, background: PHASE_COLORS[ph] ?? "#64748b" }} />
                  <span style={{ fontSize: 11, color: "rgba(148,163,184,0.6)", fontFamily: "monospace" }}>
                    {ph} · {PHASE_LABELS[ph] ?? ph}
                  </span>
                </div>
              ))}
            </div>

            {/* Recent tasks table */}
            {(snapshot?.recentTasks?.length ?? 0) > 0 && (
              <div style={{ marginTop: 28 }}>
                <div style={{ display: "flex", gap: 10, marginBottom: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "rgba(148,163,184,0.9)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 6 }}>
                      最近任务 {filteredAndSorted.length !== (snapshot?.recentTasks?.length ?? 0) && <span style={{ fontSize: 10, color: "rgba(148,163,184,0.5)" }}>({filteredAndSorted.length})</span>}
                    </div>
                    <input
                      type="text"
                      placeholder="搜索任务名、ID 或目标…"
                      value={filterText}
                      onChange={(e) => setFilterText(e.target.value)}
                      style={{
                        fontSize: 11, padding: "6px 10px", borderRadius: 4, fontFamily: "monospace",
                        border: "1px solid var(--tg-panel-border)", background: "var(--tg-input-bg)",
                        color: "var(--tg-text)", minWidth: 200,
                      }}
                    />
                  </div>
                  <div style={{ display: "flex", gap: 6, fontSize: 11 }}>
                    {(["time", "status", "phase"] as const).map(sort => (
                      <button
                        key={sort}
                        type="button"
                        onClick={() => setSortBy(sort)}
                        style={{
                          padding: "4px 10px", borderRadius: 4,
                          border: sortBy === sort ? "1px solid #22d3ee" : "1px solid rgba(71,85,105,0.3)",
                          background: sortBy === sort ? "rgba(34,211,238,0.15)" : "transparent",
                          color: sortBy === sort ? "#22d3ee" : "rgba(148,163,184,0.6)",
                          cursor: "pointer", fontFamily: "monospace",
                        }}
                      >
                        {sort === "time" ? "时间" : sort === "status" ? "状态" : "阶段"}
                      </button>
                    ))}
                  </div>
                </div>
                <div style={{ background: "var(--tg-table-bg)", border: "1px solid var(--tg-panel-border)", borderRadius: 10, overflow: "hidden" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid rgba(71,85,105,0.3)" }}>
                        {["任务名", "状态", "当前阶段", "更新时间"].map(h => (
                          <th key={h} style={{ padding: "10px 14px", textAlign: "left", fontWeight: 600, color: "rgba(148,163,184,0.7)", letterSpacing: "0.04em", fontSize: 11 }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sortedRecentTasks.map((t, i) => {
                        const statusColor: Record<string, string> = { RUNNING: "#22d3ee", PAUSED: "#fbbf24", DONE: "#34d399", FAILED: "#f87171", PENDING: "#94a3b8" };
                        const sc = statusColor[t.status] ?? "#64748b";
                        return (
                          <tr
                            key={t.taskId}
                            style={{ borderBottom: i < sortedRecentTasks.length - 1 ? "1px solid rgba(71,85,105,0.15)" : "none", cursor: "pointer" }}
                            onClick={() => navigate(`/trace/${t.taskId}`)}
                            onMouseEnter={e => (e.currentTarget.style.background = "rgba(34,211,238,0.04)")}
                            onMouseLeave={e => (e.currentTarget.style.background = "")}
                          >
                            <td style={{ padding: "9px 14px", color: "var(--tg-text)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.name}</td>
                            <td style={{ padding: "9px 14px" }}>
                              <span style={{ color: sc, background: `${sc}18`, border: `1px solid ${sc}40`, borderRadius: 4, padding: "2px 7px", fontFamily: "monospace", fontSize: 10, fontWeight: 700 }}>{t.status}</span>
                            </td>
                            <td style={{ padding: "9px 14px", color: t.currentPhase ? (PHASE_COLORS[t.currentPhase] ?? "#94a3b8") : "#64748b", fontFamily: "monospace" }}>
                              {t.currentPhase ?? "—"}
                            </td>
                            <td style={{ padding: "9px 14px", color: "rgba(148,163,184,0.5)", fontFamily: "monospace" }}>{formatLocalDateTime(t.updatedAt)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>

          {/* Event feed */}
          <div style={{ position: "sticky", top: 80 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "rgba(148,163,184,0.9)", marginBottom: 12, letterSpacing: "0.08em", textTransform: "uppercase" }}>
              实时事件流
              <span style={{ fontSize: 11, color: "rgba(34,211,238,0.6)", marginLeft: 8 }}>●</span>
            </div>
            <div style={{
              background: "var(--tg-panel-bg)", border: "1px solid var(--tg-panel-border)",
              borderRadius: 12, overflow: "hidden",
            }}>
              {recentEvents.length === 0 ? (
                <div style={{ padding: "40px 20px", textAlign: "center", color: "rgba(148,163,184,0.3)", fontSize: 12 }}>
                  暂无事件
                </div>
              ) : (
                <div style={{ maxHeight: 520, overflowY: "auto" }}>
                  {recentEvents.map((ev, i) => {
                    const color = eventColor(ev.eventType ?? "");
                    return (
                      <div
                        key={i}
                        style={{
                          display: "flex", alignItems: "flex-start", gap: 10,
                          padding: "9px 14px",
                          borderBottom: i < recentEvents.length - 1 ? "1px solid rgba(71,85,105,0.12)" : "none",
                          transition: "background 0.15s",
                        }}
                        onMouseEnter={e => (e.currentTarget.style.background = "rgba(34,211,238,0.03)")}
                        onMouseLeave={e => (e.currentTarget.style.background = "")}
                      >
                        <div style={{ width: 7, height: 7, borderRadius: "50%", background: color, flexShrink: 0, marginTop: 4, boxShadow: `0 0 5px ${color}` }} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 11, fontFamily: "monospace", color, fontWeight: 600, marginBottom: 1 }}>
                            {ev.eventType ?? "EVENT"}
                          </div>
                          <div style={{ fontSize: 10, color: "rgba(148,163,184,0.5)", fontFamily: "monospace" }}>
                            {ev.taskId && <span style={{ marginRight: 6, color: "rgba(148,163,184,0.35)" }}>{String(ev.taskId).slice(-8)}</span>}
                            {ev.sourceModule && <span style={{ marginRight: 6, color: "rgba(148,163,184,0.4)" }}>{ev.sourceModule}</span>}
                          </div>
                        </div>
                        <div style={{ fontSize: 10, color: "rgba(148,163,184,0.3)", fontFamily: "monospace", flexShrink: 0 }}>
                          {formatLocalTime(ev.ts)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Platform capability summary */}
            <div style={{
              marginTop: 16, padding: "14px 16px",
              background: "var(--tg-panel-bg)", border: "1px solid var(--tg-panel-border)",
              borderRadius: 10, fontSize: 11, fontFamily: "monospace", color: "rgba(148,163,184,0.6)",
            }}>
              <div style={{ fontWeight: 700, color: "rgba(148,163,184,0.8)", marginBottom: 8, fontSize: 11, letterSpacing: "0.05em" }}>平台能力</div>
              {[
                ["渗透阶段", "6 阶段 PTES 全流程"],
                ["安全技能", "40+ 工具自动编排"],
                ["并发能力", "≥ 3 目标并行"],
                ["报告输出", "结构化 CVE 报告"],
              ].map(([k, v]) => (
                <div key={k} style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ color: "rgba(148,163,184,0.45)" }}>{k}</span>
                  <span style={{ color: "#22d3ee" }}>{v}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Quick nav */}
        <div style={{
          marginTop: 16, padding: "10px 16px",
          background: "var(--tg-panel-muted)", border: "1px solid var(--tg-panel-border)",
          borderRadius: 8, display: "flex", gap: 12, flexWrap: "wrap" as const, alignItems: "center",
          fontSize: 11, fontFamily: "monospace",
        }}>
          <span style={{ color: "#475569" }}>导航：</span>
          {[
            { label: "任务管理", path: "/tasks",   color: "#38bdf8" },
            { label: "漏洞库",   path: "/vulns",   color: "#ef4444" },
            { label: "统计分析", path: "/stats",   color: "#f87171" },
            { label: "报告中心", path: "/reports",   color: "#fb923c" },
            { label: "管理中心", path: "/dashboard", color: "#22d3ee" },
            { label: "平台管理", path: "/admin",     color: "#a78bfa" },
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
