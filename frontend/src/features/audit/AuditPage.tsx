/**
 * AuditPage — 平台审计日志
 * 路由: /audit
 * 展示平台操作审计流水：登录认证、用户管理、任务操作。
 * 后端: GET /api/v1/admin/audit/events?limit=200
 * 离线时使用内置演示事件集（覆盖全部 12 种事件类型）。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import { getAuditEvents, type ApiAuditEvent } from "@/shared/lib/api";

// ── Event type metadata ───────────────────────────────────────────────────────

type Category = "all" | "auth" | "user" | "task";

const AUTH_TYPES  = new Set(["LOGIN_SUCCESS", "LOGIN_FAILED", "LOGOUT", "REGISTER"]);
const USER_TYPES  = new Set(["USER_CREATED", "USER_UPDATED", "USER_DELETED", "PASSWORD_CHANGED"]);
const TASK_TYPES  = new Set(["TASK_CREATED", "TASK_DELETED", "TASK_STARTED", "TASK_STOPPED"]);

function categoryOf(type: string): "auth" | "user" | "task" | "other" {
  if (AUTH_TYPES.has(type))  return "auth";
  if (USER_TYPES.has(type))  return "user";
  if (TASK_TYPES.has(type))  return "task";
  return "other";
}

const TYPE_LABELS: Record<string, string> = {
  LOGIN_SUCCESS:    "登录成功",
  LOGIN_FAILED:     "登录失败",
  LOGOUT:           "退出登录",
  REGISTER:         "注册账户",
  USER_CREATED:     "创建用户",
  USER_UPDATED:     "修改用户",
  USER_DELETED:     "删除用户",
  PASSWORD_CHANGED: "修改密码",
  TASK_CREATED:     "创建任务",
  TASK_DELETED:     "删除任务",
  TASK_STARTED:     "启动任务",
  TASK_STOPPED:     "停止任务",
};

interface TypeStyle { color: string; bg: string; border: string }

function typeStyle(type: string): TypeStyle {
  const cat = categoryOf(type);
  if (type === "LOGIN_FAILED") return { color: "#f87171", bg: "rgba(248,113,113,0.12)", border: "rgba(248,113,113,0.4)" };
  if (type === "USER_DELETED") return { color: "#fb923c", bg: "rgba(251,146,60,0.12)",  border: "rgba(251,146,60,0.4)" };
  if (cat === "auth")          return { color: "#34d399", bg: "rgba(52,211,153,0.10)",   border: "rgba(52,211,153,0.35)" };
  if (cat === "user")          return { color: "#818cf8", bg: "rgba(129,140,248,0.10)",  border: "rgba(129,140,248,0.35)" };
  if (cat === "task")          return { color: "#22d3ee", bg: "rgba(34,211,238,0.10)",   border: "rgba(34,211,238,0.35)" };
  return { color: "#64748b", bg: "rgba(100,116,139,0.10)", border: "rgba(100,116,139,0.3)" };
}

// ── Demo data ─────────────────────────────────────────────────────────────────

const now = Date.now();
const DEMO_EVENTS: ApiAuditEvent[] = [
  { type: "LOGIN_SUCCESS",    actor: "admin",    target: "user-admin-001",   detail: "管理员登录成功，IP: 192.168.1.10",                      timestamp: new Date(now - 180000).toISOString() },
  { type: "TASK_CREATED",     actor: "admin",    target: "task-7c3e9a2f",   detail: "创建任务 [Struts2 RCE 检测]，目标: 192.168.1.102:8080", timestamp: new Date(now - 165000).toISOString() },
  { type: "TASK_STARTED",     actor: "admin",    target: "task-7c3e9a2f",   detail: "任务 [Struts2 RCE 检测] 已启动，maxTicks: 30",          timestamp: new Date(now - 160000).toISOString() },
  { type: "TASK_CREATED",     actor: "operator", target: "task-8b1d4f50",   detail: "创建任务 [Flask SSTI 漏洞]，目标: 192.168.1.103:5000",  timestamp: new Date(now - 145000).toISOString() },
  { type: "TASK_STARTED",     actor: "operator", target: "task-8b1d4f50",   detail: "任务 [Flask SSTI 漏洞] 已启动",                         timestamp: new Date(now - 140000).toISOString() },
  { type: "USER_CREATED",     actor: "admin",    target: "user-operator-02", detail: "新建操作员账号 operator02，角色: OPERATOR",              timestamp: new Date(now - 120000).toISOString() },
  { type: "LOGIN_FAILED",     actor: "unknown",  target: "",                 detail: "登录失败，用户名: admin，IP: 10.0.0.55（密码错误）",     timestamp: new Date(now - 105000).toISOString() },
  { type: "LOGIN_FAILED",     actor: "unknown",  target: "",                 detail: "登录失败，用户名: root，IP: 10.0.0.55（用户不存在）",    timestamp: new Date(now - 103000).toISOString() },
  { type: "LOGIN_SUCCESS",    actor: "operator", target: "user-op-001",      detail: "操作员登录成功，IP: 192.168.1.15",                       timestamp: new Date(now - 90000).toISOString() },
  { type: "TASK_STOPPED",     actor: "admin",    target: "task-8b1d4f50",   detail: "手动停止任务 [Flask SSTI 漏洞]",                         timestamp: new Date(now - 75000).toISOString() },
  { type: "PASSWORD_CHANGED", actor: "operator", target: "user-op-001",      detail: "操作员修改自身密码",                                     timestamp: new Date(now - 60000).toISOString() },
  { type: "USER_UPDATED",     actor: "admin",    target: "user-operator-02", detail: "更新用户 operator02 显示名称",                           timestamp: new Date(now - 50000).toISOString() },
  { type: "TASK_CREATED",     actor: "operator", target: "task-3d8e1b7c",   detail: "创建任务 [Web 常规渗透测试]，目标: 192.168.1.100/dvwa/", timestamp: new Date(now - 40000).toISOString() },
  { type: "TASK_STARTED",     actor: "operator", target: "task-3d8e1b7c",   detail: "任务 [Web 常规渗透测试] 已启动",                         timestamp: new Date(now - 38000).toISOString() },
  { type: "LOGOUT",           actor: "operator", target: "user-op-001",      detail: "用户主动退出登录",                                       timestamp: new Date(now - 25000).toISOString() },
  { type: "REGISTER",         actor: "admin",    target: "user-viewer-03",   detail: "注册只读账号 viewer03，角色: VIEWER",                    timestamp: new Date(now - 15000).toISOString() },
  { type: "TASK_DELETED",     actor: "admin",    target: "task-old-001",     detail: "批量清理：删除已完成任务 task-old-001",                   timestamp: new Date(now - 8000).toISOString() },
  { type: "USER_DELETED",     actor: "admin",    target: "user-viewer-00",   detail: "删除账号 viewer00（离职）",                              timestamp: new Date(now - 3000).toISOString() },
  { type: "LOGIN_SUCCESS",    actor: "admin",    target: "user-admin-001",   detail: "管理员刷新会话，IP: 192.168.1.10",                       timestamp: new Date(now - 500).toISOString() },
];

// ── Sub-components ────────────────────────────────────────────────────────────

function KpiCard({ label, value, color, sub }: { label: string; value: number; color: string; sub?: string }) {
  return (
    <div style={{
      background: `${color}08`, border: `1px solid ${color}22`,
      borderRadius: 10, padding: "14px 16px",
      display: "flex", flexDirection: "column", gap: 3,
    }}>
      <div style={{ fontSize: 10, fontFamily: "monospace", color: "rgba(148,163,184,0.5)", letterSpacing: "0.08em", textTransform: "uppercase" as const }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 900, color, fontFamily: "monospace", lineHeight: 1.1 }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: "rgba(148,163,184,0.35)", fontFamily: "monospace" }}>{sub}</div>}
    </div>
  );
}

function TypeBadge({ type }: { type: string }) {
  const s = typeStyle(type);
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "2px 8px", borderRadius: 4,
      background: s.bg, border: `1px solid ${s.border}`,
      color: s.color, fontSize: 10, fontFamily: "monospace", fontWeight: 700,
      whiteSpace: "nowrap" as const,
    }}>
      {TYPE_LABELS[type] ?? type}
    </span>
  );
}

function EventRow({ ev, idx }: { ev: ApiAuditEvent; idx: number }) {
  const ts = (() => {
    try { return new Date(ev.timestamp).toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "medium" }); }
    catch { return ev.timestamp; }
  })();
  return (
    <tr style={{ background: idx % 2 === 0 ? "transparent" : "rgba(15,23,42,0.2)" }}>
      <td style={{ padding: "8px 14px", fontSize: 11, fontFamily: "monospace", color: "rgba(100,116,139,0.7)", whiteSpace: "nowrap" }}>{ts}</td>
      <td style={{ padding: "8px 14px" }}><TypeBadge type={ev.type} /></td>
      <td style={{ padding: "8px 14px", fontSize: 12, fontFamily: "monospace", color: "#94a3b8" }}>{ev.actor || "—"}</td>
      <td style={{ padding: "8px 14px", fontSize: 11, fontFamily: "monospace", color: "rgba(148,163,184,0.45)", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {ev.target || "—"}
      </td>
      <td style={{ padding: "8px 14px", fontSize: 12, color: "rgba(226,232,240,0.7)", maxWidth: 340, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {ev.detail || "—"}
      </td>
    </tr>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AuditPage() {
  const navigate = useNavigate();
  const { loggedIn } = useAppSession();

  const [events, setEvents]         = useState<ApiAuditEvent[]>([]);
  const [loading, setLoading]       = useState(true);
  const [isDemo, setIsDemo]         = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [category, setCategory]     = useState<Category>("all");
  const [search, setSearch]         = useState("");

  useEffect(() => {
    if (!loggedIn) {
      toast.error("请先登录");
      localStorage.setItem("sentinel_login_redirect", "/audit");
      navigate("/login");
    }
  }, [loggedIn, navigate]);

  const fetchEvents = useCallback(async () => {
    try {
      const data = await getAuditEvents(200);
      setEvents(data);
      setIsDemo(false);
    } catch {
      setEvents(DEMO_EVENTS);
      setIsDemo(true);
    } finally {
      setLoading(false);
      setLastRefresh(new Date());
    }
  }, []);

  useEffect(() => {
    if (!loggedIn) return;
    void fetchEvents();
    const iv = window.setInterval(() => { void fetchEvents(); }, 30000);
    return () => window.clearInterval(iv);
  }, [loggedIn, fetchEvents]);

  // Derived filtered list
  const filtered = useMemo(() => {
    let base = events;
    if (category !== "all") {
      const allowed = category === "auth" ? AUTH_TYPES : category === "user" ? USER_TYPES : TASK_TYPES;
      base = base.filter((e) => allowed.has(e.type));
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      base = base.filter((e) =>
        e.actor?.toLowerCase().includes(q) ||
        e.detail?.toLowerCase().includes(q) ||
        e.target?.toLowerCase().includes(q) ||
        (TYPE_LABELS[e.type] ?? e.type).includes(q)
      );
    }
    return base;
  }, [events, category, search]);

  // KPI counts
  const kpi = useMemo(() => {
    const auth  = events.filter((e) => AUTH_TYPES.has(e.type)).length;
    const user  = events.filter((e) => USER_TYPES.has(e.type)).length;
    const task  = events.filter((e) => TASK_TYPES.has(e.type)).length;
    const fail  = events.filter((e) => e.type === "LOGIN_FAILED").length;
    return { auth, user, task, fail, total: events.length };
  }, [events]);

  if (!loggedIn) return null;

  return (
    <div style={{ minHeight: "100vh", background: "#020a12", paddingTop: 80, paddingBottom: 60 }}>
      <Header />

      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "0 24px" }}>

        {/* ── Title row ── */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 22, flexWrap: "wrap", gap: 10 }}>
          <div>
            <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800, color: "#e2e8f0", fontFamily: "monospace", letterSpacing: "0.06em" }}>
              审计日志
              <span style={{ marginLeft: 12, fontSize: 11, color: "rgba(148,163,184,0.35)", fontWeight: 400 }}>Audit Log</span>
            </h1>
            <div style={{ fontSize: 11, fontFamily: "monospace", color: isDemo ? "rgba(251,191,36,0.7)" : "rgba(52,211,153,0.7)" }}>
              {isDemo ? "○ 演示模式" : "● 实时审计"}
              {lastRefresh && (
                <span style={{ color: "rgba(100,116,139,0.5)", marginLeft: 10 }}>
                  {lastRefresh.toLocaleTimeString("zh-CN", { hour12: false })} · 30s自动刷新
                </span>
              )}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" onClick={() => { setLoading(true); void fetchEvents(); }}
              style={{ padding: "6px 14px", borderRadius: 6, background: "rgba(34,211,238,0.08)", border: "1px solid rgba(34,211,238,0.3)", color: "#22d3ee", fontSize: 11, cursor: "pointer", fontFamily: "monospace" }}>
              {loading ? "刷新中…" : "↻ 刷新"}
            </button>
            <button type="button" onClick={() => navigate("/dashboard")}
              style={{ padding: "6px 14px", borderRadius: 6, background: "rgba(15,23,42,0.5)", border: "1px solid rgba(51,65,85,0.4)", color: "#94a3b8", fontSize: 11, cursor: "pointer", fontFamily: "monospace" }}>
              ← 管理中心
            </button>
            <button type="button" onClick={() => navigate("/admin")}
              style={{ padding: "6px 14px", borderRadius: 6, background: "transparent", border: "1px solid rgba(129,140,248,0.3)", color: "#818cf8", fontSize: 11, cursor: "pointer", fontFamily: "monospace" }}>
              平台管理 →
            </button>
          </div>
        </div>

        {/* ── Demo banner ── */}
        {isDemo && (
          <div style={{
            marginBottom: 16, padding: "10px 16px",
            background: "rgba(251,191,36,0.06)", border: "1px solid rgba(251,191,36,0.25)", borderRadius: 8,
            fontSize: 11, fontFamily: "monospace", color: "rgba(251,191,36,0.75)",
          }}>
            ⚠ 后端未连接 — 展示演示审计事件（涵盖全部操作类型）。连接后台后将自动切换为实时数据。
          </div>
        )}

        {/* ── KPI cards ── */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12, marginBottom: 18 }}>
          <KpiCard label="总事件"    value={kpi.total} color="#38bdf8" sub={`最近 200 条`} />
          <KpiCard label="登录认证"  value={kpi.auth}  color="#34d399" sub={`含 ${kpi.fail} 次失败`} />
          <KpiCard label="用户管理"  value={kpi.user}  color="#818cf8" />
          <KpiCard label="任务操作"  value={kpi.task}  color="#22d3ee" />
          <KpiCard label="登录失败"  value={kpi.fail}  color="#f87171" sub={kpi.fail > 2 ? "⚠ 注意异常" : "正常"} />
        </div>

        {/* ── Filter bar ── */}
        <div style={{
          marginBottom: 14, padding: "10px 14px",
          background: "rgba(15,23,42,0.6)", border: "1px solid rgba(51,65,85,0.4)",
          borderRadius: 8, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        }}>
          <span style={{ fontSize: 10, color: "rgba(100,116,139,0.6)", fontFamily: "monospace", letterSpacing: "0.07em", flexShrink: 0 }}>筛选</span>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value as Category)}
            style={{
              padding: "5px 10px", borderRadius: 5,
              background: "rgba(15,23,42,0.9)", border: "1px solid rgba(51,65,85,0.5)",
              color: "#e2e8f0", fontSize: 12, fontFamily: "monospace", cursor: "pointer",
            }}
          >
            <option value="all">全部类型</option>
            <option value="auth">登录认证</option>
            <option value="user">用户管理</option>
            <option value="task">任务操作</option>
          </select>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索操作者、目标或详情…"
            style={{
              flex: 1, minWidth: 200, padding: "5px 10px", borderRadius: 5,
              background: "rgba(15,23,42,0.9)", border: "1px solid rgba(51,65,85,0.5)",
              color: "#e2e8f0", fontSize: 12, fontFamily: "monospace", outline: "none",
            }}
          />
          {(category !== "all" || search) && (
            <button type="button"
              onClick={() => { setCategory("all"); setSearch(""); }}
              style={{ padding: "5px 10px", borderRadius: 5, background: "transparent", border: "1px solid #475569", color: "#94a3b8", fontSize: 11, cursor: "pointer", fontFamily: "monospace" }}>
              ✕ 清除
            </button>
          )}
          <span style={{ fontSize: 10, color: "rgba(100,116,139,0.45)", fontFamily: "monospace", marginLeft: "auto", flexShrink: 0 }}>
            {filtered.length} / {events.length} 条
          </span>
        </div>

        {/* ── Events table ── */}
        <div style={{
          background: "rgba(15,23,42,0.6)", border: "1px solid rgba(51,65,85,0.5)",
          borderRadius: 10, overflow: "hidden",
        }}>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ background: "rgba(15,23,42,0.6)", borderBottom: "1px solid rgba(51,65,85,0.4)" }}>
                  {["时间", "操作类型", "操作者", "目标资源", "详情"].map((h) => (
                    <th key={h} style={{ padding: "8px 14px", textAlign: "left", fontSize: 10, fontFamily: "monospace", color: "rgba(100,116,139,0.7)", letterSpacing: "0.07em", fontWeight: 700, whiteSpace: "nowrap" }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={5} style={{ padding: "28px", textAlign: "center", color: "rgba(148,163,184,0.3)", fontFamily: "monospace", fontSize: 12 }}>加载中…</td></tr>
                ) : filtered.length === 0 ? (
                  <tr><td colSpan={5} style={{ padding: "28px", textAlign: "center", color: "rgba(148,163,184,0.25)", fontFamily: "monospace", fontSize: 12 }}>
                    {search || category !== "all" ? "无匹配事件" : "暂无审计记录"}
                  </td></tr>
                ) : (
                  filtered.map((ev, i) => <EventRow key={i} ev={ev} idx={i} />)
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* ── Navigation strip ── */}
        <div style={{
          marginTop: 20, padding: "10px 16px",
          background: "rgba(15,23,42,0.4)", border: "1px solid rgba(51,65,85,0.3)",
          borderRadius: 8, display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center",
          fontSize: 11, fontFamily: "monospace",
        }}>
          <span style={{ color: "#475569" }}>导航：</span>
          {[
            { label: "管理中心",  path: "/dashboard", color: "#22d3ee" },
            { label: "平台管理",  path: "/admin",     color: "#818cf8" },
            { label: "用户管理",  path: "/admin",     color: "#a78bfa" },
            { label: "系统状态",  path: "/system",    color: "#64748b" },
            { label: "平台配置",  path: "/config",    color: "#fbbf24" },
            { label: "统计分析",  path: "/stats",     color: "#f87171" },
          ].map(({ label, path, color }) => (
            <button key={label + path} type="button" onClick={() => navigate(path)}
              style={{ background: "none", border: "none", color, cursor: "pointer", fontFamily: "monospace", fontSize: 11, padding: 0 }}>
              {label} →
            </button>
          ))}
        </div>

      </div>
    </div>
  );
}
