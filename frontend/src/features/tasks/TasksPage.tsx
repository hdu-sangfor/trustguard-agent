import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import { ORBIT_TASKS_UPDATED_EVENT, SENTINEL_ORBIT_TASKS_KEY, readStoredOrbitTasks, type StoredOrbitTask } from "@/shared/constants/orbitTasksStorage";
import { createTask as apiCreateTask, runTask as apiRunTask, stopTask as apiStopTask, resumeTask as apiResumeTask, tickTask as apiTickTask, listTasks as apiListTasks, deleteTask as apiDeleteTask, getTask, getTaskObservation, getTaskTrace, getTaskTodos, getTaskExecutions, getExecutionRecord, getSliSnapshot, getMqStatus, getV1Overview, getTracePlan, getTraceCompile, toFrontendStatus, TRUSTGUARD_PHASES, type ApiTrace, type ApiSliSnapshot, type ApiMqStatus, type ApiExecutionRecord, type ApiTodo, type ApiV1Overview, type ApiObservation, type ApiTask } from "@/shared/lib/api";
import { elapsedForStatus, formatElapsed } from "@/shared/lib/time";
import { readTaskViewMode, writeTaskViewMode } from "@/shared/lib/preferences";
import { toast } from "sonner";
import "@/shared/styles/console-pages.css";

const STORAGE_KEY = "sentinel_session_v1";
const DELETED_TASKS_KEY = "sentinel_deleted_tasks_v1";

function loadDeletedIds(): Set<string> {
  try {
    const raw = localStorage.getItem(DELETED_TASKS_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw) as unknown;
    if (!Array.isArray(arr)) return new Set();
    return new Set(arr.filter((x): x is string => typeof x === "string"));
  } catch {
    return new Set();
  }
}

function saveDeletedIds(ids: Set<string>): void {
  try {
    localStorage.setItem(DELETED_TASKS_KEY, JSON.stringify([...ids]));
  } catch {
    /* ignore */
  }
}

type Session = {
  username: string;
  created: number;
  completed: number;
  running: number;
  failed: number;
};

type OrbitTask = {
  id: string;
  name: string;
  desc: string;
  url: string;
  log?: string;
  createdAt: number;
  updatedAt?: number;
  status: "not_started" | "running" | "paused" | "failed" | "finished";
  currentPhase?: string;
};

type Vec3 = { x: number; y: number; z: number };

const EMPTY_VERTICES: Vec3[] = [];
const EMPTY_ORBIT_EDGES: { a: number; b: number }[] = [];

const defaultSession: Session = {
  username: "访客",
  created: 0,
  completed: 0,
  running: 0,
  failed: 0,
};

function loadOrbitTasksFromStorage(): OrbitTask[] {
  try {
    const arr = readStoredOrbitTasks();
    return arr.map((t) => {
      const st = String(t.status ?? "");
      const status: OrbitTask["status"] =
          st === "running" || st === "paused" || st === "failed" || st === "finished" || st === "not_started"
              ? (st as OrbitTask["status"])
              : "not_started";
      return {
        id: String(t.id),
        name: String(t.name ?? ""),
        desc: String(t.desc ?? ""),
        url: String(t.url ?? ""),
        log: typeof t.log === "string" ? t.log : "",
        createdAt: Number(t.createdAt) || Date.now(),
        updatedAt: typeof t.updatedAt === "number" ? t.updatedAt : undefined,
        status,
        currentPhase: typeof t.currentPhase === "string" ? t.currentPhase : undefined,
      };
    });
  } catch {
    return [];
  }
}

function readSession(): Session {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultSession;
    const p = JSON.parse(raw) as Partial<Session>;
    return {
      username: typeof p.username === "string" ? p.username : defaultSession.username,
      created: Number(p.created) || defaultSession.created,
      completed: Number(p.completed) || defaultSession.completed,
      running: Number(p.running) || defaultSession.running,
      failed: Number(p.failed) || defaultSession.failed,
    };
  } catch {
    return defaultSession;
  }
}

function taskStatusColor(status: OrbitTask["status"]) {
  switch (status) {
    case "finished":
      return { stroke: "rgba(34,197,94,0.78)", glow: "rgba(34, 197, 94, 0.58)" };
    case "running":
      return { stroke: "rgba(34,211,238,0.7)", glow: "rgba(34, 211, 238, 0.55)" };
    case "paused":
      return { stroke: "rgba(250,204,21,0.7)", glow: "rgba(250, 204, 21, 0.5)" };
    case "failed":
      return { stroke: "rgba(248,113,113,0.8)", glow: "rgba(248, 113, 113, 0.55)" };
    default:
      return { stroke: "rgba(148,163,184,0.55)", glow: "rgba(148, 163, 184, 0.35)" };
  }
}

function taskWithStatus(task: OrbitTask, status: OrbitTask["status"], extra: Partial<OrbitTask> = {}): OrbitTask {
  const now = Date.now();
  const shouldStamp =
    status === "running" ||
    status === "paused" ||
    status === "finished" ||
    status === "failed" ||
    task.status !== status;
  return {
    ...task,
    ...extra,
    status,
    updatedAt: extra.updatedAt ?? (shouldStamp ? now : task.updatedAt),
  };
}

function taskFromApi(at: ApiTask, local?: OrbitTask): OrbitTask {
  const nextStatus = toFrontendStatus(at.status);
  const apiUpdatedAt = at.updatedAt ? new Date(at.updatedAt).getTime() || undefined : undefined;
  const updatedAt = apiUpdatedAt
    ?? ((nextStatus === "finished" || nextStatus === "failed") ? (local?.updatedAt ?? Date.now()) : local?.updatedAt);
  return {
    id: at.taskId,
    name: at.name ?? local?.name ?? "未命名任务",
    desc: at.description ?? local?.desc ?? "",
    url: at.target ?? local?.url ?? "",
    log: local?.log ?? "",
    createdAt: new Date(at.createdAt).getTime() || local?.createdAt || Date.now(),
    updatedAt,
    status: nextStatus,
    currentPhase: at.currentPhase ?? local?.currentPhase,
  };
}

function getTaskStatusText(status: OrbitTask["status"]) {
  switch (status) {
    case "not_started":
      return "未开始";
    case "running":
      return "运行中";
    case "paused":
      return "已暂停";
    case "failed":
      return "失败";
    case "finished":
      return "已完成";
    default:
      return "未知";
  }
}

const OrbitTaskNode = ({
                         task,
                         x,
                         y,
                         scale,
                         onClick,
                         zLayer,
                       }: {
  task: OrbitTask;
  x: number;
  y: number;
  scale: number;
  zLayer: number;
  onClick: () => void;
}) => {
  const [hovered, setHovered] = useState(false);
  const c = taskStatusColor(task.status);
  const size = hovered ? 120 : 64;
  return (
      <button
          type="button"
          onMouseEnter={() => {
            setHovered(true);
          }}
          onMouseLeave={() => {
            setHovered(false);
          }}
          onFocus={() => {
            setHovered(true);
          }}
          onBlur={() => {
            setHovered(false);
          }}
          onClick={(e) => {
            e.stopPropagation();
            onClick();
          }}
          style={{
            position: "absolute",
            left: x,
            top: y,
            transform: `translate(-50%, -50%) scale(${scale})`,
            width: size,
            height: size,
            borderRadius: "50%",
            border: `1px solid ${c.stroke}`,
            background: "var(--tg-panel-bg)",
            color: "var(--tg-text)",
            boxShadow: hovered ? `0 0 26px ${c.glow}` : `0 0 10px ${c.glow}`,
            opacity: 1,
            transition: "width 0.15s ease, height 0.15s ease, box-shadow 0.15s ease",
            padding: hovered ? 8 : 6,
            overflow: "hidden",
            cursor: "pointer",
            textAlign: "center",
            fontSize: hovered ? 10 : 11,
            lineHeight: 1.35,
            zIndex: zLayer,
          }}
          title={task.name}
      >
        {hovered ? (
            <>
              <div style={{ fontWeight: 700, color: "var(--tg-accent)", marginBottom: 2 }}>{task.name}</div>
              {task.currentPhase && (
                <div style={{
                  display: "inline-block", padding: "1px 6px", marginBottom: 3, borderRadius: 3,
                  background: "rgba(34,211,238,0.18)", border: "1px solid rgba(34,211,238,0.4)",
                  color: "#67e8f9", fontSize: 9, fontWeight: 700, letterSpacing: "0.06em",
                }}>{task.currentPhase}</div>
              )}
              <div style={{ opacity: 0.9 }}>{task.desc || "-"}</div>
              <div style={{ opacity: 0.82, marginTop: 2 }}>{task.url}</div>
            </>
        ) : (
            <>
              <span style={{ color: "var(--tg-text)", fontWeight: 600 }}>{task.name}</span>
              {task.currentPhase && (
                <div style={{
                  marginTop: 3, fontSize: 8, fontWeight: 700, letterSpacing: "0.05em",
                  color: task.status === "running" ? "#67e8f9" : "#94a3b8",
                  opacity: 0.85,
                }}>{task.currentPhase}</div>
              )}
            </>
        )}
      </button>
  );
};

function formatTrace(data: ApiTrace): string {
  const parts: string[] = [];
  const execs = Array.isArray(data.executions) ? data.executions : [];
  if (execs.length > 0) {
    parts.push(`执行轨迹 (${execs.length} 条):`);
    execs.slice(0, 15).forEach((e, i) => {
      const dur = typeof e.duration_ms === 'number' ? ` ${(e.duration_ms / 1000).toFixed(1)}s` : '';
      parts.push(`[${i + 1}] ${String(e.phase ?? '?')} › ${String(e.skill_id ?? '?')} › ${String(e.status ?? '?')}${dur}`);
      const r = typeof e.reasoning === 'string' ? e.reasoning.trim() : '';
      if (r) parts.push(`    ${r.slice(0, 110)}${r.length > 110 ? '…' : ''}`);
    });
    if (execs.length > 15) parts.push(`    … 还有 ${execs.length - 15} 条`);
  } else {
    parts.push('暂无执行记录');
  }
  const planItems = Array.isArray((data.plan as { items?: unknown[] } | undefined)?.items)
    ? (data.plan as { items: unknown[] }).items
    : [];
  if (planItems.length > 0) {
    parts.push('');
    parts.push(`计划项 (${planItems.length}):`);
    planItems.slice(0, 8).forEach((item, i) => {
      const it = item as Record<string, unknown>;
      parts.push(`[${i + 1}] ${String(it.phase ?? '?')} › ${String(it.skill_id ?? it.name ?? '?')} › ${String(it.status ?? '?')}`);
    });
    if (planItems.length > 8) parts.push(`    … 还有 ${planItems.length - 8} 条`);
  }
  return parts.join('\n') || JSON.stringify(data, null, 2).slice(0, 600);
}

const TasksPage = () => {
  const { loggedIn } = useAppSession();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // 强制隐藏滚动条，填满全屏
  useEffect(() => {
    const style = document.createElement('style');
    style.textContent = `
      html, body {
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        width: 100vw !important;
        height: 100vh !important;
      }
      html::-webkit-scrollbar, body::-webkit-scrollbar {
        display: none !important;
      }
      html {
        scrollbar-width: none !important;
      }
    `;
    document.head.appendChild(style);
    document.body.classList.add("no-page-scroll", "no-scrollbar");

    return () => {
      document.head.removeChild(style);
      document.body.classList.remove("no-page-scroll", "no-scrollbar");
    };
  }, []);

  useEffect(() => {
    if (!loggedIn) {
      toast.error("请先登录");
      localStorage.setItem("sentinel_login_redirect", "/tasks");
      navigate("/login", { replace: true });
    }
  }, [loggedIn, navigate]);

  const [listView, setListView] = useState(() => readTaskViewMode() !== "orbit");
  const [listSearch, setListSearch] = useState("");
  const [listStatusFilter, setListStatusFilter] = useState<string>("all");
  const [listSort, setListSort] = useState<{ col: string; dir: "asc" | "desc" }>({ col: "status", dir: "asc" });
  const [tasksRefreshing, setTasksRefreshing] = useState(false);
  const [lastTaskSyncAt, setLastTaskSyncAt] = useState<number | null>(null);
  const [detailRefreshing, setDetailRefreshing] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [open, setOpen] = useState(false);
  const [taskName, setTaskName] = useState("");
  const [taskDesc, setTaskDesc] = useState("");
  const [taskUrl, setTaskUrl] = useState("");
  const [maxTicks, setMaxTicks] = useState(100);
  const [maxRunDurationSeconds, setMaxRunDurationSeconds] = useState(900);
  const [observationData, setObservationData] = useState<ApiObservation | null>(null);
  const [observationLoading, setObservationLoading] = useState(false);
  const [todosData, setTodosData] = useState<ApiTodo[] | null>(null);
  const [todosLoading, setTodosLoading] = useState(false);
  const [traceText, setTraceText] = useState<string | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [tracePlanData, setTracePlanData] = useState<Record<string, unknown> | null>(null);
  const [tracePlanLoading, setTracePlanLoading] = useState(false);
  const [traceCompileData, setTraceCompileData] = useState<Record<string, unknown> | null>(null);
  const [traceCompileLoading, setTraceCompileLoading] = useState(false);
  const [executionsData, setExecutionsData] = useState<ApiExecutionRecord[] | null>(null);
  const [executionsLoading, setExecutionsLoading] = useState(false);
  const [executionsOffset, setExecutionsOffset] = useState(0);
  const [expandedExecId, setExpandedExecId] = useState<string | null>(null);
  const [execDetailCache, setExecDetailCache] = useState<Record<string, ApiExecutionRecord>>({});
  const [execDetailLoading, setExecDetailLoading] = useState<string | null>(null);
  const [sliData, setSliData] = useState<ApiSliSnapshot | null>(null);
  const [mqData, setMqData] = useState<ApiMqStatus | null>(null);
  const [v1Data, setV1Data] = useState<ApiV1Overview | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [formErrors, setFormErrors] = useState<{ name?: boolean; url?: boolean }>({});
  const [orbitTasks, setOrbitTasks] = useState<OrbitTask[]>(() => loadOrbitTasksFromStorage());
  const taskNameInputRef = useRef<HTMLInputElement | null>(null);
  const executionsDataRef = useRef<ApiExecutionRecord[] | null>(null);
  const demoSimTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>[]>>(new Map());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [nowTick, setNowTick] = useState(() => Date.now());
  const [deletedIds, setDeletedIds] = useState<Set<string>>(() => loadDeletedIds());
  const [centerExpanded, setCenterExpanded] = useState(false);

  const setTaskView = (next: boolean | ((prev: boolean) => boolean)) => {
    setListView((prev) => {
      const resolved = typeof next === "function" ? next(prev) : next;
      writeTaskViewMode(resolved ? "list" : "orbit");
      return resolved;
    });
  };

  const session = readSession();
  const center = 260;
  const orbitRadius = 210;

  const taskStatusCounts = useMemo(() => {
    const c = { not_started: 0, running: 0, paused: 0, failed: 0, finished: 0 };
    for (const t of orbitTasks) {
      if (t.status === "not_started") c.not_started += 1;
      else if (t.status === "running") c.running += 1;
      else if (t.status === "paused") c.paused += 1;
      else if (t.status === "failed") c.failed += 1;
      else if (t.status === "finished") c.finished += 1;
    }
    return c;
  }, [orbitTasks]);

  useEffect(() => {
    const t = window.setInterval(() => setNowTick(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    if (!hovered) { setCenterExpanded(false); return; }
    // Lazy-fetch SLI + MQ + V1 overview when user hovers the center bubble
    getSliSnapshot(false).then((s) => setSliData(s)).catch(() => {});
    getMqStatus().then((m) => setMqData(m)).catch(() => {});
    getV1Overview().then((v) => setV1Data(v)).catch(() => {});
  }, [hovered]);

  useEffect(() => {
    try {
      localStorage.setItem(SENTINEL_ORBIT_TASKS_KEY, JSON.stringify(orbitTasks));
      window.dispatchEvent(new Event(ORBIT_TASKS_UPDATED_EVENT));
    } catch {
      /* ignore quota */
    }
  }, [orbitTasks]);

  // Auto-switch to list view when orbit gets too crowded
  useEffect(() => {
    if (orbitTasks.length > 8 && !listView && readTaskViewMode() == null) setTaskView(true);
  }, [orbitTasks.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // Deep-link: ?taskId=xxx opens the task detail modal
  useEffect(() => {
    const tid = searchParams.get("taskId");
    if (!tid || !loggedIn) return;
    const task = orbitTasks.find((t) => t.id === tid);
    if (!task) return;
    setEditingId(task.id);
    setTaskName(task.name);
    setTaskDesc(task.desc);
    setTaskUrl(task.url);
    setFormErrors({});
    setObservationData(null);
    setTraceText(null);
    setExecutionsData(null);
    setExecutionsOffset(0);
    setTodosData(null);
    setTracePlanData(null);
    setTraceCompileData(null);
    setExpandedExecId(null);
    setConfirmDeleteId(null);

    setOpen(true);
    // Clear the param so refresh/close doesn't re-trigger
    setSearchParams((prev) => { prev.delete("taskId"); return prev; }, { replace: true });
  }, [searchParams, orbitTasks, loggedIn]); // eslint-disable-line react-hooks/exhaustive-deps

  // ✅ 替换为中文问候
  const greeting = useMemo(() => {
    const h = new Date().getHours();
    if (h < 12) return "早上好";
    if (h < 18) return "下午好";
    return "晚上好";
  }, []);

  const baseVertices = useMemo<Vec3[]>(() => {
    const n = orbitTasks.length;
    if (n <= 0) return EMPTY_VERTICES;
    if (n <= 3) {
      return Array.from({ length: n }, (_, i) => {
        const a = (i / n) * Math.PI * 2;
        return { x: Math.cos(a), y: Math.sin(a), z: 0 };
      });
    }
    const out: Vec3[] = [];
    const ga = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < n; i++) {
      const y = 1 - (i / Math.max(1, n - 1)) * 2;
      const r = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = ga * i;
      out.push({ x: Math.cos(theta) * r, y, z: Math.sin(theta) * r });
    }
    return out;
  }, [orbitTasks.length]);

  const orbitLayout = useMemo(() => {
    const angle = -0.42;
    const radius = orbitRadius;
    const cy = Math.cos(angle);
    const sy = Math.sin(angle);
    return orbitTasks.map((task, i) => {
      const v = baseVertices[i] ?? { x: 0, y: 0, z: 0 };
      const x1 = v.x * cy + v.z * sy;
      const z2 = -v.x * sy + v.z * cy;
      const y2 = v.y;
      const scale = 0.72 + (z2 + 1) * 0.22;
      const isBack = z2 <= 0;
      const zLayer = isBack ? 12 + Math.round((z2 + 1) * 10) : 42 + Math.round(z2 * 12);
      return {
        task,
        x: x1 * radius,
        y: y2 * radius,
        z: z2,
        scale,
        baseIndex: i,
        isBack,
        zLayer,
      };
    });
  }, [orbitTasks, baseVertices, orbitRadius]);

  const orbitBack = useMemo(() => orbitLayout.filter((o) => o.isBack), [orbitLayout]);
  const orbitFront = useMemo(() => orbitLayout.filter((o) => !o.isBack), [orbitLayout]);

  const orbitEdges = useMemo(() => {
    const n = baseVertices.length;
    if (n <= 1) return EMPTY_ORBIT_EDGES;
    if (n <= 3) {
      const edges: { a: number; b: number }[] = [];
      for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
          edges.push({ a: i, b: j });
        }
      }
      return edges;
    }
    const edgeSet = new Set<string>();
    const DEGREE = 3;
    const verts = baseVertices;
    for (let i = 0; i < n; i++) {
      const vi = verts[i];
      const dists: { j: number; d: number }[] = [];
      for (let j = 0; j < n; j++) {
        if (j === i) continue;
        const vj = verts[j];
        const dx = vi.x - vj.x;
        const dy = vi.y - vj.y;
        const dz = vi.z - vj.z;
        dists.push({ j, d: dx * dx + dy * dy + dz * dz });
      }
      dists.sort((a, b) => a.d - b.d);
      for (let k = 0; k < Math.min(DEGREE, dists.length); k++) {
        const j = dists[k].j;
        const a = Math.min(i, j);
        const b = Math.max(i, j);
        edgeSet.add(`${a}-${b}`);
      }
    }
    return Array.from(edgeSet).map((key) => {
      const [a, b] = key.split("-").map(Number);
      return { a, b };
    });
  }, [baseVertices]);

  const hasRunningTask = orbitTasks.some((t) => t.status === "running");
  const prevApiStatusRef = useRef<Map<string, string>>(new Map());
  const pollInitializedRef = useRef(false);
  const pollFailCountRef = useRef(0);

  const syncTasksFromBackend = useCallback(async (manual = false) => {
    if (manual) setTasksRefreshing(true);
    try {
      const apiTasks = await apiListTasks();
      if (pollFailCountRef.current >= 3) {
        toast.dismiss("poll-fail-warn");
        toast.success("后端连接已恢复", { id: "poll-recover", duration: 3000 });
      }
      pollFailCountRef.current = 0;

      if (pollInitializedRef.current) {
        const prev = prevApiStatusRef.current;
        for (const at of apiTasks) {
          const newSt = toFrontendStatus(at.status);
          const oldSt = prev.get(at.taskId);
          if (oldSt && oldSt !== newSt) {
            const n = at.name ?? at.taskId;
            if (newSt === "finished" && oldSt === "running") {
              toast.success(`✓ 任务完成：${n}`, { duration: 8000, action: { label: "查看报告", onClick: () => navigate("/reports") } });
            } else if (newSt === "failed" && oldSt === "running") {
              toast.error(`✗ 任务失败：${n}`, { duration: 8000 });
            }
          }
        }
      }

      for (const at of apiTasks) prevApiStatusRef.current.set(at.taskId, toFrontendStatus(at.status));
      pollInitializedRef.current = true;
      setOrbitTasks((prev) => {
        const localById = new Map(prev.map((t) => [t.id, t]));
        const merged = apiTasks
          .filter((at) => !deletedIds.has(at.taskId))
          .map((at) => taskFromApi(at, localById.get(at.taskId)));
        const backendIds = new Set(apiTasks.map((t) => t.taskId));
        const hasBacked = apiTasks.length > 0;
        const localOnly = prev.filter((t) => {
          if (backendIds.has(t.id)) return false;
          if (hasBacked && /^\d+$/.test(t.id)) return false;
          if (deletedIds.has(t.id)) return false;
          if (hasBacked && t.id.startsWith("task-")) return false;
          return true;
        });
        return [...merged, ...localOnly];
      });
      setLastTaskSyncAt(Date.now());
      if (manual) toast.success("任务列表已刷新", { id: "tasks-refresh-ok", duration: 2000 });
    } catch {
      pollFailCountRef.current += 1;
      if (manual) {
        toast.error("刷新失败，显示缓存数据");
      } else if (pollFailCountRef.current === 3) {
        toast.warning("后端连接中断，显示缓存数据", { id: "poll-fail-warn", duration: Infinity });
      }
    } finally {
      if (manual) setTasksRefreshing(false);
    }
  }, [deletedIds, navigate]);

  // Sync task list from backend on mount and periodically.
  // Poll at 2s when any task is RUNNING, 8s otherwise.
  useEffect(() => {
    const pollMs = hasRunningTask ? 2000 : 8000;
    syncTasksFromBackend(false);
    const iv = window.setInterval(() => { void syncTasksFromBackend(false); }, pollMs);
    return () => window.clearInterval(iv);
  }, [hasRunningTask, syncTasksFromBackend]);

  // Auto-focus task name when modal opens (creating new task)
  useEffect(() => {
    if (open && !editingId) {
      const t = window.setTimeout(() => taskNameInputRef.current?.focus(), 60);
      return () => window.clearTimeout(t);
    }
  }, [open, editingId]);

  // Clear all task-specific panel state when switching to a different task
  useEffect(() => {
    setExecDetailCache({});
    setExecDetailLoading(null);

    setObservationData(null);
    setTraceText(null);
    setTracePlanData(null);
    setTraceCompileData(null);
    setExecutionsData(null);
    setExecutionsOffset(0);
    setTodosData(null);
    setExpandedExecId(null);
    setConfirmDeleteId(null);
  }, [editingId]);

  // Keep executionsDataRef in sync for the auto-refresh interval below
  useEffect(() => { executionsDataRef.current = executionsData; }, [executionsData]);

  // Auto-refresh executions / observation / todos panels every 5 s while viewing a running task
  const observationDataRef = useRef<ApiObservation | null>(null);
  const todosDataRef = useRef<ApiTodo[] | null>(null);
  useEffect(() => { observationDataRef.current = observationData; }, [observationData]);
  useEffect(() => { todosDataRef.current = todosData; }, [todosData]);

  useEffect(() => {
    if (!editingId || !hasRunningTask) return;
    const iv = window.setInterval(() => {
      const currentStatus = orbitTasks.find((t) => t.id === editingId)?.status;
      if (currentStatus !== 'running') return;
      // Refresh executions panel
      if (executionsDataRef.current !== null) {
        getTaskExecutions(editingId, 20, executionsOffset)
          .then((d) => { setExecutionsData(d); setExecDetailCache({}); })
          .catch(() => {});
      }
      // Refresh observation panel
      if (observationDataRef.current !== null) {
        getTaskObservation(editingId).then((obs) => {
          setObservationData(obs);
        }).catch(() => {});
      }
      // Refresh todos panel
      if (todosDataRef.current !== null) {
        getTaskTodos(editingId).then(setTodosData).catch(() => {});
      }
    }, 5000);
    return () => window.clearInterval(iv);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editingId, executionsOffset, hasRunningTask]);

  // When the viewed task transitions to finished/failed, auto-refresh executions & toast
  const editingTaskStatus = editingId ? orbitTasks.find((t) => t.id === editingId)?.status : undefined;
  const prevEditingStatusRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    const prev = prevEditingStatusRef.current;
    prevEditingStatusRef.current = editingTaskStatus;
    if (!editingId || !prev || prev === editingTaskStatus) return;
    if ((editingTaskStatus === "finished" || editingTaskStatus === "failed") && prev === "running") {
      // Task just completed — fetch final execution records
      getTaskExecutions(editingId, 20, 0)
        .then((d) => { setExecutionsData(d); setExecDetailCache({}); setExecutionsOffset(0); })
        .catch(() => {});
    }
  }, [editingTaskStatus, editingId]);

  // Close modal on Escape key
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        setFormErrors({});
        setEditingId(null);
        setObservationData(null);
        setTraceText(null);
        setTracePlanData(null);
        setTraceCompileData(null);
        setExecutionsData(null);
        setExecutionsOffset(0);
        setTodosData(null);
        setExpandedExecId(null);

        setConfirmDeleteId(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const readValidatedTaskForm = () => {
    const name = taskName.trim();
    const desc = taskDesc.trim();
    const url = taskUrl.trim();
    const errs: { name?: boolean; url?: boolean } = {};
    if (!name) { errs.name = true; }
    if (!url) { errs.url = true; }
    else {
      try {
        const parsed = new URL(url);
        if (parsed.protocol !== "http:" && parsed.protocol !== "https:") errs.url = true;
      } catch { errs.url = true; }
    }
    if (errs.name || errs.url) {
      setFormErrors(errs);
      if (errs.name) toast.error("任务名称不能为空");
      else if (errs.url) toast.error("靶机 URL 格式无效，须以 http:// 或 https:// 开头且包含主机名");
      return;
    }
    setFormErrors({});
    return { name, desc, url };
  };

  const commitTask = () => {
    const validated = readValidatedTaskForm();
    if (!validated) return;
    const { name, desc, url } = validated;

    if (editingId) {
      setOrbitTasks((prev) => prev.map((t) => (t.id === editingId ? { ...t, name, desc, url } : t)));
      setTaskName("");
      setTaskDesc("");
      setTaskUrl("");
      setOpen(false);
      setEditingId(null);
      toast.success("修改已保存");
      return;
    }

    // Optimistically add to local state while API call is in-flight
    const tempId = `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    const newTask: OrbitTask = {
      id: tempId,
      name,
      desc,
      url,
      log: `[${new Date().toLocaleString()}] [TASK] 新任务已创建：${name}`,
      createdAt: Date.now(),
      status: "not_started",
    };
    setOrbitTasks((prev) => [...prev, newTask]);
    setTaskName("");
    setTaskDesc("");
    setTaskUrl("");
    setOpen(false);
    setEditingId(null);

    // Submit to backend; replace tempId with real taskId on success
    apiCreateTask({ name, description: desc, target: url })
      .then((apiTask) => {
        setOrbitTasks((prev) =>
          prev.map((t) =>
            t.id === tempId
              ? taskFromApi(apiTask, t)
              : t,
          ),
        );
        toast.success(`任务已创建：${name}`);
      })
      .catch((e: Error) => {
        toast.error(`后端创建失败（已保存本地）: ${e.message}`);
      });
  };

  const commitAndRunTask = () => {
    const validated = readValidatedTaskForm();
    if (!validated) return;
    const { name, desc, url } = validated;

    const tempId = `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    const newTask: OrbitTask = { id: tempId, name, desc, url, log: `[${new Date().toLocaleString()}] [TASK] 新任务已创建：${name}`, createdAt: Date.now(), status: "not_started" };
    setOrbitTasks((prev) => [...prev, newTask]);
    setTaskName(""); setTaskDesc(""); setTaskUrl("");
    setOpen(false); setEditingId(null);

    apiCreateTask({ name, description: desc, target: url })
      .then((apiTask) => {
        const realId = apiTask.taskId;
        setOrbitTasks((prev) => prev.map((t) => t.id === tempId ? taskWithStatus(taskFromApi(apiTask, t), "running", { id: realId }) : t));
        return apiRunTask(realId, maxTicks, maxRunDurationSeconds);
      })
      .then(() => toast.success(`任务已创建并启动：${name}`))
      .catch((e: Error) => toast.error(`创建并执行失败: ${e.message}`));
  };

  // 演示模式：为数字 ID 的种子任务模拟渗透测试阶段进度
  const simulateDemoTask = (id: string) => {
    // Cancel any existing simulation for this task
    const existing = demoSimTimersRef.current.get(id) ?? [];
    existing.forEach((t) => clearTimeout(t));
    const timers: ReturnType<typeof setTimeout>[] = [];
    const phases = [...TRUSTGUARD_PHASES];
    // Each phase takes 3–6 seconds for demo effect
    const delays = [0, 3500, 7000, 12000, 17000, 22000];
    phases.forEach((phase, i) => {
      const t = setTimeout(() => {
        setOrbitTasks((prev) => prev.map((task) =>
          task.id === id ? taskWithStatus(task, "running", { currentPhase: phase }) : task
        ));
      }, delays[i] ?? (i * 4000));
      timers.push(t);
    });
    // Mark finished after last phase
    const finishTimer = setTimeout(() => {
      setOrbitTasks((prev) => prev.map((task) =>
        task.id === id ? taskWithStatus(task, "finished", { currentPhase: phases[phases.length - 1] }) : task
      ));
      demoSimTimersRef.current.delete(id);
      toast.success("演示任务已完成（离线演示模式）");
    }, (delays[phases.length - 1] ?? 22000) + 3000);
    timers.push(finishTimer);
    demoSimTimersRef.current.set(id, timers);
  };

  const handleRefreshCurrentTask = async () => {
    if (!editingId) return;
    const taskId = editingId;
    setDetailRefreshing(true);
    try {
      const [apiTask] = await Promise.all([
        getTask(taskId),
        observationData !== null
          ? getTaskObservation(taskId).then(setObservationData).catch((e: Error) => toast.error(`观测刷新失败: ${e.message}`))
          : Promise.resolve(),
        todosData !== null
          ? getTaskTodos(taskId).then(setTodosData).catch((e: Error) => toast.error(`计划刷新失败: ${e.message}`))
          : Promise.resolve(),
        traceText !== null
          ? getTaskTrace(taskId).then((trace) => setTraceText(formatTrace(trace))).catch((e: Error) => toast.error(`轨迹刷新失败: ${e.message}`))
          : Promise.resolve(),
        tracePlanData !== null
          ? getTracePlan(taskId).then(setTracePlanData).catch((e: Error) => toast.error(`策略计划刷新失败: ${e.message}`))
          : Promise.resolve(),
        traceCompileData !== null
          ? getTraceCompile(taskId).then(setTraceCompileData).catch((e: Error) => toast.error(`编译段刷新失败: ${e.message}`))
          : Promise.resolve(),
        executionsData !== null
          ? getTaskExecutions(taskId, 20, executionsOffset).then((d) => { setExecutionsData(d); setExecDetailCache({}); }).catch((e: Error) => toast.error(`执行记录刷新失败: ${e.message}`))
          : Promise.resolve(),
      ]);
      setOrbitTasks((prev) => prev.map((t) => t.id === taskId ? taskFromApi(apiTask, t) : t));
      toast.success("任务详情已刷新", { id: "task-detail-refresh-ok", duration: 2000 });
    } catch (e: unknown) {
      toast.error(`任务刷新失败: ${(e as Error).message}`);
    } finally {
      setDetailRefreshing(false);
    }
  };

  if (!loggedIn) return null;

  return (
      <div
          className="tg-console-page tg-console-page--tasks"
          style={{
            width: "100vw",
            height: "100vh",
            margin: 0,
            padding: "60px 0 0",
            boxSizing: "border-box",
            background: "var(--tg-page-gradient)",
            overflow: "hidden",
            position: "relative",
          }}
      >
        <Header />
        {/* View toggle */}
        <div style={{ position: "fixed", top: 68, right: 20, zIndex: 200, display: "flex", gap: 8 }}>
          <button
            type="button"
            disabled={tasksRefreshing}
            onClick={() => { void syncTasksFromBackend(true); }}
            style={{
              padding: "5px 13px", borderRadius: 8, fontSize: 12, fontWeight: 700,
              border: "1px solid var(--tg-panel-border)", background: "var(--tg-panel-bg)",
              color: tasksRefreshing ? "#64748b" : "#a5f3fc", cursor: tasksRefreshing ? "wait" : "pointer", letterSpacing: "0.04em",
              backdropFilter: "blur(6px)",
            }}
          >
            {tasksRefreshing ? "刷新中…" : "刷新"}
          </button>
          {!listView && (
            <button
              type="button"
              onClick={() => {
                setEditingId(null);
                setTaskName(""); setTaskDesc(""); setTaskUrl("");
                setObservationData(null); setTraceText(null);
                setExecutionsData(null); setExecutionsOffset(0);
                setTodosData(null); setTracePlanData(null); setTraceCompileData(null); setExpandedExecId(null);
                setConfirmDeleteId(null);
                setOpen(true);
              }}
              style={{
                padding: "5px 13px", borderRadius: 8, fontSize: 12, fontWeight: 800,
                border: "1px solid var(--tg-panel-border)", background: "var(--tg-panel-bg)",
                color: "#a5f3fc", cursor: "pointer", letterSpacing: "0.04em",
                backdropFilter: "blur(6px)",
              }}
            >+ 新建任务</button>
          )}
          <button
            type="button"
            onClick={() => { setTaskView((v) => !v); setListSearch(""); }}
            style={{
              padding: "5px 13px", borderRadius: 8, fontSize: 12, fontWeight: 700,
              border: "1px solid var(--tg-panel-border)", background: "var(--tg-panel-bg)",
              color: "#7dd3fc", cursor: "pointer", letterSpacing: "0.04em",
              backdropFilter: "blur(6px)",
            }}
          >
            {listView ? "轨道视图" : "列表视图"}
          </button>
        </div>

        <main
            className="tasks-console-main"
            style={{
              width: "100%",
              height: "calc(100vh - 60px)",
              display: "flex",
              alignItems: listView ? "flex-start" : "center",
              justifyContent: "center",
              padding: listView ? "24px 32px 0" : 0,
              overflow: listView ? "auto" : "hidden",
              boxSizing: "border-box",
            }}
        >
          {listView ? (
            /* ── LIST VIEW ─────────────────────────────────────────────── */
            <div className="tasks-list-shell" style={{ width: "100%", maxWidth: 960, paddingTop: 20 }}>
              {/* Capability badge strip */}
              <div className="tasks-badge-strip" style={{ display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
                {([
                  { label: "30 技能容器", col: "#67e8f9", border: "rgba(34,211,238,0.28)" },
                  { label: "6 阶段链式", col: "#c4b5fd", border: "rgba(167,139,250,0.28)" },
                  { label: "多目标并发", col: "#86efac", border: "rgba(34,197,94,0.28)" },
                  { label: "Vulhub ✓", col: "#fde047", border: "rgba(251,191,36,0.28)" },
                  { label: "Bugku PAR ✓", col: "#fda4af", border: "rgba(251,113,133,0.28)" },
                  { label: "GPT · Claude · DeepSeek", col: "#94a3b8", border: "rgba(71,85,105,0.28)" },
                ] as { label: string; col: string; border: string }[]).map((b) => (
                  <span key={b.label} style={{
                    padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 600,
                    border: `1px solid ${b.border}`, color: b.col,
                    background: "var(--tg-panel-muted)", fontFamily: "monospace", letterSpacing: "0.02em",
                  }}>{b.label}</span>
                ))}
              </div>
              <div className="tasks-list-toolbar" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10, gap: 10, flexWrap: "wrap" }}>
                <div style={{ color: "var(--tg-text-muted)", fontSize: 11, fontFamily: "monospace", whiteSpace: "nowrap" }}>
                  {orbitTasks.length} 个任务
                  {taskStatusCounts.running > 0 && (
                    <span style={{
                      color: "#34d399", marginLeft: 6,
                      ...(taskStatusCounts.running >= 3 ? {
                        background: "rgba(34,211,153,0.12)", border: "1px solid rgba(34,211,153,0.3)",
                        borderRadius: 3, padding: "0 5px", fontWeight: 700,
                      } : {}),
                    }}>● {taskStatusCounts.running} 运行{taskStatusCounts.running >= 3 ? " (并发)" : ""}</span>
                  )}
                  {taskStatusCounts.paused > 0 && <span style={{ color: "#facc15", marginLeft: 6 }}>● {taskStatusCounts.paused} 暂停</span>}
                  {taskStatusCounts.finished > 0 && <span style={{ color: "#22c55e", marginLeft: 6 }}>✓ {taskStatusCounts.finished} 完成</span>}
                  {taskStatusCounts.failed > 0 && <span style={{ color: "#f87171", marginLeft: 6 }}>✗ {taskStatusCounts.failed} 失败</span>}
                  {lastTaskSyncAt && (
                    <span style={{ color: "#475569", marginLeft: 8 }}>
                      最近刷新 {new Date(lastTaskSyncAt).toLocaleTimeString("zh-CN", { hour12: false })}
                    </span>
                  )}
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center", flex: "1 1 200px", justifyContent: "flex-end", flexWrap: "wrap" }}>
                  {/* Batch: start all launchable */}
                  {(taskStatusCounts.not_started + taskStatusCounts.paused) > 0 && (
                    <button
                      type="button"
                      onClick={() => {
                        const launchable = orbitTasks.filter((t) => t.status === "not_started" || t.status === "paused");
                        if (launchable.length === 0) return;
                        setOrbitTasks((prev) => prev.map((t) =>
                          t.status === "not_started" || t.status === "paused" ? taskWithStatus(t, "running") : t
                        ));
                        launchable.forEach((t) => {
                          if (/^\d+$/.test(t.id)) { simulateDemoTask(t.id); return; }
                          const fn = t.status === "paused" ? apiResumeTask : apiRunTask;
                          fn(t.id, maxTicks, maxRunDurationSeconds).catch(() => {});
                        });
                        toast.success(`已并发启动 ${launchable.length} 个任务`);
                      }}
                      style={{
                        padding: "5px 12px", borderRadius: 7, fontSize: 11, fontWeight: 700,
                        border: "1px solid rgba(34,197,94,0.5)", background: "var(--tg-panel-bg)",
                        color: "#86efac", cursor: "pointer", whiteSpace: "nowrap",
                      }}
                    >▶ 全部启动</button>
                  )}
                  {/* Batch: stop all running */}
                  {taskStatusCounts.running > 0 && (
                    <button
                      type="button"
                      onClick={() => {
                        const running = orbitTasks.filter((t) => t.status === "running");
                        if (running.length === 0) return;
                        setOrbitTasks((prev) => prev.map((t) =>
                          t.status === "running" ? taskWithStatus(t, "paused") : t
                        ));
                        running.forEach((t) => { apiStopTask(t.id).catch(() => {}); });
                        toast.success(`已暂停 ${running.length} 个任务`);
                      }}
                      style={{
                        padding: "5px 12px", borderRadius: 7, fontSize: 11, fontWeight: 700,
                        border: "1px solid rgba(248,113,113,0.45)", background: "var(--tg-panel-bg)",
                        color: "#fca5a5", cursor: "pointer", whiteSpace: "nowrap",
                      }}
                    >⏹ 全部停止</button>
                  )}
                  <select
                    value={listStatusFilter}
                    onChange={(e) => setListStatusFilter(e.target.value)}
                    style={{
                      padding: "5px 8px", borderRadius: 7, fontSize: 11,
                      border: "1px solid var(--tg-panel-border)", background: "var(--tg-input-bg)",
                      color: listStatusFilter === "all" ? "#64748b" : "#a5f3fc",
                      fontFamily: "monospace", outline: "none", cursor: "pointer",
                    }}
                  >
                    <option value="all">全部</option>
                    <option value="running">运行中</option>
                    <option value="paused">已暂停</option>
                    <option value="not_started">未开始</option>
                    <option value="finished">已完成</option>
                    <option value="failed">已失败</option>
                  </select>
                  <input
                    type="text"
                    value={listSearch}
                    onChange={(e) => setListSearch(e.target.value)}
                    placeholder="搜索任务名 / URL / 描述…"
                    style={{
                      padding: "5px 10px", borderRadius: 7, fontSize: 11,
                      border: "1px solid var(--tg-panel-border)", background: "var(--tg-input-bg)",
                      color: "var(--tg-text)", fontFamily: "monospace", width: 160, outline: "none",
                    }}
                  />
                  {listSearch && (
                    <button type="button" onClick={() => setListSearch("")}
                      style={{ background: "none", border: "none", color: "#475569", cursor: "pointer", fontSize: 12, padding: 0 }}>✕</button>
                  )}
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setEditingId(null);
                      setTaskName(""); setTaskDesc(""); setTaskUrl("");
                      setFormErrors({});
                      setObservationData(null); setTraceText(null);
                      setExecutionsData(null); setExecutionsOffset(0);
                      setTodosData(null); setTracePlanData(null); setTraceCompileData(null); setExpandedExecId(null);
                      setConfirmDeleteId(null);
                      setOpen(true);
                    }}
                    style={{
                      padding: "6px 14px", borderRadius: 7, fontSize: 12, fontWeight: 800,
                      border: "1px solid var(--tg-panel-border)", background: "var(--tg-panel-bg)",
                      color: "#a5f3fc", cursor: "pointer", whiteSpace: "nowrap",
                    }}
                  >+ 新建任务</button>
                </div>
              </div>
              <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "monospace", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid rgba(34,211,238,0.2)" }}>
                    {[
                      { label: "状态", col: "status" },
                      { label: "任务名", col: "name" },
                      { label: "目标", col: "url" },
                      { label: "阶段", col: "phase" },
                      { label: "运行时间", col: "createdAt" },
                      { label: "操作", col: "" },
                    ].map(({ label, col }) => (
                      <th
                        key={label}
                        onClick={col ? () => setListSort((s) => ({ col, dir: s.col === col && s.dir === "asc" ? "desc" : "asc" })) : undefined}
                        style={{
                          padding: "6px 10px", textAlign: "left", whiteSpace: "nowrap",
                          color: listSort.col === col ? "#a5f3fc" : "#64748b",
                          fontWeight: 600,
                          cursor: col ? "pointer" : "default",
                          userSelect: "none",
                        }}
                      >
                        {label}{col && listSort.col === col ? (listSort.dir === "asc" ? " ↑" : " ↓") : ""}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {orbitTasks.length === 0 && (
                    <tr>
                      <td colSpan={6} style={{ padding: "32px 10px", textAlign: "center", color: "var(--tg-text-faint)", fontFamily: "monospace", fontSize: 12 }}>
                        暂无任务 · 点击"+ 新建任务"创建第一个
                      </td>
                    </tr>
                  )}
                  {orbitTasks.length > 0 && (listSearch.trim() || listStatusFilter !== "all") && !orbitTasks.some((t) => {
                    const kw = listSearch.toLowerCase();
                    const matchText = !listSearch.trim() || t.name.toLowerCase().includes(kw) || t.url.toLowerCase().includes(kw) || t.desc.toLowerCase().includes(kw);
                    const matchStatus = listStatusFilter === "all" || t.status === listStatusFilter;
                    return matchText && matchStatus;
                  }) && (
                    <tr>
                      <td colSpan={6} style={{ padding: "20px 10px", textAlign: "center", color: "var(--tg-text-faint)", fontFamily: "monospace", fontSize: 12 }}>
                        无匹配任务 · 清空筛选重试
                      </td>
                    </tr>
                  )}
                  {[...orbitTasks]
                    .filter((t) => {
                      const kw = listSearch.toLowerCase().trim();
                      const matchText = !kw || t.name.toLowerCase().includes(kw) || t.url.toLowerCase().includes(kw) || t.desc.toLowerCase().includes(kw);
                      const matchStatus = listStatusFilter === "all" || t.status === listStatusFilter;
                      return matchText && matchStatus;
                    })
                    .sort((a, b) => {
                      const { col, dir } = listSort;
                      const mul = dir === "asc" ? 1 : -1;
                      if (col === "status") {
                        const order = { running: 0, paused: 1, not_started: 2, failed: 3, finished: 4 };
                        const diff = ((order[a.status] ?? 5) - (order[b.status] ?? 5)) * mul;
                        if (diff !== 0) return diff;
                        return b.createdAt - a.createdAt;
                      }
                      if (col === "name") return a.name.localeCompare(b.name, "zh-CN") * mul;
                      if (col === "url") return a.url.localeCompare(b.url) * mul;
                      if (col === "phase") return (a.currentPhase ?? "").localeCompare(b.currentPhase ?? "") * mul;
                      if (col === "createdAt") return (b.createdAt - a.createdAt) * mul;
                      return 0;
                    }).map((task) => {
                    const isRunning = task.status === "running";
                    const isPaused = task.status === "paused";
                    const isTerminal = task.status === "finished" || task.status === "failed";
                    const elapsed = elapsedForStatus({
                      createdAt: task.createdAt,
                      updatedAt: task.updatedAt,
                      status: task.status,
                      now: nowTick,
                    });
                    const dotColor = isRunning ? "#34d399" : task.status === "finished" ? "#22c55e" : isPaused ? "#facc15" : task.status === "failed" ? "#f87171" : "#475569";
                    const statusLabel = getTaskStatusText(task.status);
                    return (
                      <tr
                        key={task.id}
                        style={{ borderBottom: "1px solid rgba(71,85,105,0.2)", transition: "background 0.15s" }}
                        onMouseEnter={(e) => { (e.currentTarget as HTMLTableRowElement).style.background = "var(--tg-hover-bg)"; }}
                        onMouseLeave={(e) => { (e.currentTarget as HTMLTableRowElement).style.background = ""; }}
                      >
                        {/* Status */}
                        <td style={{ padding: "8px 10px", whiteSpace: "nowrap" }}>
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                            <span style={{ width: 7, height: 7, borderRadius: "50%", background: dotColor, boxShadow: isRunning ? `0 0 5px ${dotColor}` : "none", flexShrink: 0 }} />
                            <span style={{ color: dotColor, fontWeight: 600 }}>{statusLabel}</span>
                          </span>
                        </td>
                        {/* Name */}
                        <td style={{ padding: "8px 10px", maxWidth: 180 }}>
                          <button
                            type="button"
                            onClick={() => { setEditingId(task.id); setTaskName(task.name); setTaskDesc(task.desc); setTaskUrl(task.url); setFormErrors({}); setObservationData(null); setTraceText(null); setExecutionsData(null); setExecutionsOffset(0); setTodosData(null); setTracePlanData(null); setTraceCompileData(null); setExpandedExecId(null); setConfirmDeleteId(null);  setOpen(true); }}
                            style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: "#a5f3fc", fontWeight: 700, fontSize: 12, textAlign: "left", maxWidth: 175, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}
                            title={task.name}
                          >{task.name}</button>
                        </td>
                        {/* Target */}
                        <td style={{ padding: "8px 10px", maxWidth: 200 }}>
                          <span style={{ color: "var(--tg-text-muted)", display: "block", maxWidth: 195, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={task.url}>{task.url}</span>
                        </td>
                        {/* Phase — mini progress bar */}
                        <td style={{ padding: "8px 10px", minWidth: 110 }}>
                          {task.currentPhase || task.status === "finished" ? (() => {
                            const cIdx = TRUSTGUARD_PHASES.indexOf(task.currentPhase as typeof TRUSTGUARD_PHASES[number]);
                            return (
                              <div title={task.currentPhase ?? "DONE"}>
                                <div style={{ display: "flex", gap: 2, marginBottom: 2 }}>
                                  {TRUSTGUARD_PHASES.map((ph, idx) => {
                                    const done = task.status === "finished" || (cIdx >= 0 && idx < cIdx);
                                    const cur = task.status !== "finished" && idx === cIdx;
                                    const fail = task.status === "failed" && idx === cIdx;
                                    return (
                                      <div key={ph} style={{
                                        flex: 1, height: 3, borderRadius: 1,
                                        background: fail ? "rgba(248,113,113,0.8)" : done ? "rgba(34,197,94,0.7)" : cur ? "rgba(34,211,238,0.85)" : "rgba(71,85,105,0.3)",
                                        boxShadow: cur ? "0 0 5px rgba(34,211,238,0.5)" : "none",
                                      }} />
                                    );
                                  })}
                                </div>
                                <span style={{ fontSize: 9, color: task.status === "failed" ? "#fca5a5" : task.status === "finished" ? "#86efac" : "#67e8f9", fontWeight: 700, fontFamily: "monospace" }}>
                                  {task.status === "finished" ? "DONE" : task.currentPhase}
                                </span>
                              </div>
                            );
                          })() : <span style={{ color: "#334155" }}>—</span>}
                        </td>
                        {/* Elapsed */}
                        <td style={{ padding: "8px 10px", whiteSpace: "nowrap",
                          color: isRunning ? "#34d399" : task.status === "finished" ? "#22c55e" : task.status === "failed" ? "#f87171" : "#64748b" }}>
                          {elapsed || "—"}
                        </td>
                        {/* Actions */}
                        <td style={{ padding: "8px 10px", whiteSpace: "nowrap" }}>
                          <span style={{ display: "inline-flex", gap: 6 }}>
                            <button
                              type="button"
                              onClick={() => {
                                const id = task.id;
                                if (isRunning) {
                                  setOrbitTasks((prev) => prev.map((t) => t.id === id ? taskWithStatus(t, "paused") : t));
                                  apiStopTask(id).then(() => toast.success("任务已暂停")).catch((e: Error) => toast.error(`暂停失败: ${e.message}`));
                                } else if (isPaused) {
                                  setOrbitTasks((prev) => prev.map((t) => t.id === id ? taskWithStatus(t, "running") : t));
                                  apiResumeTask(id, maxTicks, maxRunDurationSeconds).then(() => toast.success("任务已续跑")).catch((e: Error) => toast.error(`续跑失败: ${e.message}`));
                                } else {
                                  setOrbitTasks((prev) => prev.map((t) => t.id === id ? taskWithStatus(t, "running") : t));
                                  if (/^\d+$/.test(id)) { simulateDemoTask(id); }
                                  else apiRunTask(id, maxTicks, maxRunDurationSeconds).then(() => toast.success("任务已启动")).catch((e: Error) => toast.error(`执行失败: ${e.message}`));
                                }
                              }}
                              style={{
                                padding: "4px 10px", borderRadius: 5, fontSize: 11, fontWeight: 700,
                                border: `1px solid ${isRunning ? "rgba(248,113,113,0.5)" : "rgba(34,211,238,0.4)"}`,
                                background: "rgba(2,8,20,0.6)",
                                color: isRunning ? "#fca5a5" : isPaused ? "#fde047" : "#a5f3fc",
                                cursor: "pointer",
                              }}
                            >{isRunning ? "暂停" : isPaused ? "续跑" : "执行"}</button>
                            <button
                              type="button"
                              onClick={() => {
                                navigate(`/logs?taskId=${task.id}`);
                              }}
                              style={{
                                padding: "4px 8px", borderRadius: 5, fontSize: 11, fontWeight: 700,
                                border: "1px solid rgba(34,211,238,0.3)",
                                background: "rgba(2,8,20,0.6)", color: "#67e8f9",
                                cursor: "pointer",
                              }}
                            >日志</button>
                            <button
                              type="button"
                              onClick={() => { navigate(`/trace/${task.id}`); }}
                              style={{
                                padding: "4px 8px", borderRadius: 5, fontSize: 11, fontWeight: 700,
                                border: "1px solid rgba(129,140,248,0.35)",
                                background: "rgba(2,8,20,0.6)", color: "#a5b4fc",
                                cursor: "pointer",
                              }}
                            >轨迹</button>
                            {confirmDeleteId === task.id ? (
                              <span style={{ display: "inline-flex", gap: 4 }}>
                                <button
                                  type="button"
                                  onClick={() => {
                                    const id = task.id;
                                    setConfirmDeleteId(null);
                                    setOrbitTasks((prev) => prev.filter((t) => t.id !== id));
                                    setDeletedIds((prev) => { const next = new Set(prev); next.add(id); saveDeletedIds(next); return next; });
                                    // Best-effort backend delete (silent on failure — task already hidden locally)
                                    apiDeleteTask(id).catch(() => {/* local-only tasks have no backend row */});
                                    toast.success("任务已删除");
                                  }}
                                  style={{
                                    padding: "4px 8px", borderRadius: 5, fontSize: 11, fontWeight: 700,
                                    border: "1px solid rgba(248,113,113,0.7)",
                                    background: "rgba(248,113,113,0.18)", color: "#fca5a5",
                                    cursor: "pointer",
                                  }}
                                >确认</button>
                                <button
                                  type="button"
                                  onClick={() => setConfirmDeleteId(null)}
                                  style={{
                                    padding: "4px 8px", borderRadius: 5, fontSize: 11,
                                    border: "1px solid rgba(71,85,105,0.5)",
                                    background: "rgba(2,8,20,0.6)", color: "#64748b",
                                    cursor: "pointer",
                                  }}
                                >取消</button>
                              </span>
                            ) : (
                              <button
                                type="button"
                                onClick={() => setConfirmDeleteId(task.id)}
                                style={{
                                  padding: "4px 8px", borderRadius: 5, fontSize: 11, fontWeight: 700,
                                  border: "1px solid rgba(248,113,113,0.35)",
                                  background: "rgba(2,8,20,0.6)", color: "rgba(248,113,113,0.75)",
                                  cursor: "pointer",
                                }}
                              >删除</button>
                            )}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
          <div style={{ position: "relative", width: 520, height: 520, textAlign: "center" }}>
            {orbitTasks.length === 0 && (
              <div style={{
                position: "absolute", inset: 0, display: "flex", flexDirection: "column",
                alignItems: "center", justifyContent: "center", gap: 8, pointerEvents: "none",
                zIndex: 2,
              }}>
                <div style={{ color: "rgba(148,163,184,0.35)", fontSize: 12, fontFamily: "monospace", letterSpacing: "0.06em" }}>
                  暂无任务
                </div>
                <div style={{ color: "rgba(100,116,139,0.3)", fontSize: 10, fontFamily: "monospace" }}>
                  点击右上角 + 新建任务
                </div>
              </div>
            )}
            {orbitLayout.length > 2 && (
                <svg
                    width={520}
                    height={520}
                    style={{
                      position: "absolute",
                      inset: 0,
                      pointerEvents: "none",
                      zIndex: 6,
                      opacity: 1,
                    }}
                >
                  {orbitEdges.map(({ a, b }) => {
                    const p = orbitLayout[a];
                    const q = orbitLayout[b];
                    if (!p || !q) return null;
                    return (
                        <line
                            key={`${p.task.id}_${q.task.id}`}
                            x1={center + p.x}
                            y1={center + p.y}
                            x2={center + q.x}
                            y2={center + q.y}
                            stroke="rgba(34,211,238,0.3)"
                            strokeWidth={1}
                        />
                    );
                  })}
                </svg>
            )}
            {orbitBack.map(({ task, x, y, scale, zLayer }) => (
                <OrbitTaskNode
                    key={task.id}
                    task={task}
                    x={center + x}
                    y={center + y}
                    scale={scale}
                    zLayer={zLayer}
                    onClick={() => {
                      setEditingId(task.id);
                      setTaskName(task.name);
                      setTaskDesc(task.desc);
                      setTaskUrl(task.url);
                      setFormErrors({});
                      setObservationData(null);
                      setTraceText(null);
                      setExecutionsData(null);
                      setExecutionsOffset(0);
                      setTodosData(null);
                      setTracePlanData(null);
                      setTraceCompileData(null);
                      setExpandedExecId(null);
                      setConfirmDeleteId(null);

                      setOpen(true);
                    }}
                />
            ))}

            <div
                style={{
                  position: "absolute",
                  left: center,
                  top: center,
                  transform: "translate(-50%, -50%)",
                  width: 132,
                  height: 132,
                  borderRadius: "50%",
                  pointerEvents: "none",
                  zIndex: 28,
                  background:
                      "radial-gradient(circle at 30% 30%, rgba(99,102,241,0.35), transparent 58%), radial-gradient(circle at 70% 70%, rgba(0,247,255,0.22), transparent 62%), rgba(2, 6, 23, 0.15)",
                  border: "1px solid rgba(99,102,241,0.28)",
                  boxShadow: "0 0 22px rgba(99, 102, 241, 0.18)",
                }}
            />

            <button
                type="button"
                aria-label="任务与登录概览"
                onMouseEnter={() => setHovered(true)}
                onMouseLeave={() => {
                  setCenterExpanded(false);
                  window.setTimeout(() => setHovered(false), 220);
                }}
                onFocus={() => setHovered(true)}
                onBlur={() => {
                  setCenterExpanded(false);
                  window.setTimeout(() => setHovered(false), 220);
                }}
                onTransitionEnd={(e) => {
                  if (e.propertyName !== "width" && e.propertyName !== "height") return;
                  if (hovered) setCenterExpanded(true);
                }}
                style={{
                  position: "absolute",
                  left: center,
                  top: center,
                  transform: "translate(-50%, -50%)",
                  width: hovered ? 212 : 118,
                  height: hovered ? 212 : 118,
                  borderRadius: "50%",
                  border: "2px solid rgba(0, 247, 255, 0.55)",
                  background:
                      "radial-gradient(circle at 35% 30%, rgba(0,247,255,0.35), transparent 55%), radial-gradient(circle at 70% 70%, rgba(99,102,241,0.4), transparent 50%), rgba(15,23,42,0.92)",
                  boxShadow: hovered
                      ? "0 0 60px rgba(0, 247, 255, 0.45), inset 0 0 40px rgba(0, 247, 255, 0.12)"
                      : "0 0 28px rgba(0, 247, 255, 0.25)",
                  cursor: "pointer",
                  opacity: 1,
                  transition: "width 0.15s ease, height 0.15s ease, box-shadow 0.15s ease",
                  padding: hovered ? "12px 14px 40px" : 0,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  overflow: "hidden",
                  boxSizing: "border-box",
                  zIndex: hovered ? 5000 : 32,
                }}
            >
              {hovered && !centerExpanded && (
                  <div
                      style={{
                        position: "absolute",
                        inset: 0,
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "center",
                        justifyContent: "center",
                        gap: 8,
                        color: "#a5f3fc",
                        pointerEvents: "none",
                      }}
                  >
                    <div style={{ fontSize: "0.86rem", fontWeight: 700, letterSpacing: "0.04em" }}>{session.username}</div>
                    <div style={{ fontSize: "0.68rem", opacity: 0.85, fontVariantNumeric: "tabular-nums" }}>
                      {new Date(nowTick).toLocaleTimeString()}
                    </div>
                  </div>
              )}
              <div
                  style={{
                    opacity: hovered && centerExpanded ? 1 : 0,
                    transform: hovered && centerExpanded ? "scale(1)" : "scale(0.92)",
                    transition: "opacity 0.25s ease, transform 0.25s ease",
                    width: "100%",
                    maxHeight: "100%",
                    overflow: "auto",
                    color: "rgba(226, 232, 240, 0.96)",
                    fontSize: "0.77rem",
                    lineHeight: 1.45,
                    textAlign: "center",
                    pointerEvents: hovered && centerExpanded ? "auto" : "none",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "center",
                    alignItems: "center",
                  }}
              >
                <div style={{ color: "#7dd3fc", fontWeight: 700, marginBottom: 8, letterSpacing: "0.04em", fontSize: "0.8rem" }}>
                  {greeting}, {session.username}
                </div>
                <div>
                  <strong>未启动任务</strong>: {taskStatusCounts.not_started}
                </div>
                <div>
                  <strong>运行中任务</strong>: {taskStatusCounts.running}
                </div>
                <div>
                  <strong>暂停中任务</strong>: {taskStatusCounts.paused}
                </div>
                <div>
                  <strong>失败任务</strong>: {taskStatusCounts.failed}
                </div>
                <div>
                  <strong>完成任务</strong>: {taskStatusCounts.finished}
                </div>
                {sliData && (
                  <>
                    <div style={{ borderTop: "1px solid rgba(34,211,238,0.15)", margin: "6px 0 4px", width: "100%" }} />
                    {typeof sliData.active_tasks === 'number' && (
                      <div><strong>活跃任务</strong>: {sliData.active_tasks}</div>
                    )}
                    {typeof sliData.total_ticks === 'number' && (
                      <div><strong>总 Tick</strong>: {sliData.total_ticks}</div>
                    )}
                    {typeof sliData.tick_error_rate === 'number' && (
                      <div style={{ color: sliData.tick_error_rate > 0.1 ? "rgba(248,113,113,0.85)" : "rgba(34,211,153,0.85)" }}>
                        <strong>错误率</strong>: {(sliData.tick_error_rate * 100).toFixed(1)}%
                      </div>
                    )}
                  </>
                )}
                {mqData && (typeof mqData.mode === 'string' || typeof mqData.messages_ready === 'number') && (
                  <>
                    <div style={{ borderTop: "1px solid rgba(167,139,250,0.15)", margin: "6px 0 4px", width: "100%" }} />
                    {typeof mqData.mode === 'string' && (
                      <div style={{ color: mqData.mode === 'mq' ? "rgba(167,139,250,0.85)" : "rgba(148,163,184,0.75)" }}>
                        <strong>MQ</strong>: {mqData.mode}
                      </div>
                    )}
                    {typeof mqData.messages_ready === 'number' && (
                      <div style={{ color: mqData.messages_ready > 0 ? "rgba(250,204,21,0.85)" : "rgba(148,163,184,0.65)" }}>
                        <strong>待处理</strong>: {mqData.messages_ready}
                      </div>
                    )}
                    {typeof mqData.consumers === 'number' && (
                      <div><strong>消费者</strong>: {mqData.consumers}</div>
                    )}
                  </>
                )}
                {v1Data && v1Data.v1_scheduling && (
                  <>
                    <div style={{ borderTop: "1px solid rgba(99,102,241,0.15)", margin: "6px 0 4px", width: "100%" }} />
                    {typeof v1Data.v1_scheduling.mode === 'string' && (
                      <div style={{ color: "rgba(165,180,252,0.85)" }}>
                        <strong>V1</strong>: {v1Data.v1_scheduling.mode}
                      </div>
                    )}
                    {v1Data.v1_kb && typeof v1Data.v1_kb.enabled === 'boolean' && (
                      <div style={{ color: v1Data.v1_kb.enabled ? "rgba(34,211,153,0.85)" : "rgba(148,163,184,0.65)" }}>
                        <strong>KB</strong>: {v1Data.v1_kb.enabled ? "启用" : "关闭"}
                      </div>
                    )}
                  </>
                )}
              </div>
              {hovered && (
                  <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditingId(null);
                        setTaskName(""); setTaskDesc(""); setTaskUrl("");
                        setObservationData(null); setTraceText(null);
                        setExecutionsData(null); setExecutionsOffset(0);
                        setTodosData(null); setTracePlanData(null); setTraceCompileData(null); setExpandedExecId(null);
                        setConfirmDeleteId(null);
                        setOpen(true);
                      }}
                      style={{
                        position: "absolute",
                        left: "50%",
                        bottom: 10,
                        transform: "translateX(-50%)",
                        width: 56,
                        minWidth: 56,
                        maxWidth: 56,
                        boxSizing: "border-box",
                        border: "1px solid rgba(34, 211, 238, 0.55)",
                        background: "transparent",
                        color: "#a5f3fc",
                        borderRadius: 8,
                        padding: "6px 2px",
                        cursor: "pointer",
                        fontWeight: 700,
                        fontSize: "0.65rem",
                        lineHeight: 1.2,
                        zIndex: 2,
                        transition: "bottom 0.35s ease, padding 0.35s ease, font-size 0.35s ease, opacity 0.35s ease",
                        opacity: centerExpanded ? 1 : 0,
                        pointerEvents: centerExpanded ? "auto" : "none",
                      }}
                  >
                    创建任务
                  </button>
              )}
              {!hovered && (
                  <div
                      style={{
                        position: "absolute",
                        left: "50%",
                        top: "50%",
                        transform: "translate(-50%, -54%)",
                        pointerEvents: "none",
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "center",
                        gap: 4,
                        maxWidth: "90%",
                        zIndex: 1,
                      }}
                  >
                <span
                    style={{
                      color: "#a5f3fc",
                      fontSize: "0.78rem",
                      letterSpacing: "0.04em",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      WebkitFontSmoothing: "antialiased",
                      textRendering: "geometricPrecision",
                    }}
                >
                  {session.username}
                </span>
                    <span style={{ color: "rgba(165, 243, 252, 0.82)", fontSize: "0.65rem", fontVariantNumeric: "tabular-nums" }}>
                  {new Date(nowTick).toLocaleTimeString()}
                </span>
                  </div>
              )}
            </button>

            {orbitFront.map(({ task, x, y, scale, zLayer }) => (
                <OrbitTaskNode
                    key={`front-${task.id}`}
                    task={task}
                    x={center + x}
                    y={center + y}
                    scale={scale}
                    zLayer={zLayer}
                    onClick={() => {
                      setEditingId(task.id);
                      setTaskName(task.name);
                      setTaskDesc(task.desc);
                      setTaskUrl(task.url);
                      setFormErrors({});
                      setObservationData(null);
                      setTraceText(null);
                      setExecutionsData(null);
                      setExecutionsOffset(0);
                      setTodosData(null);
                      setTracePlanData(null);
                      setTraceCompileData(null);
                      setExpandedExecId(null);
                      setConfirmDeleteId(null);

                      setOpen(true);
                    }}
                />
            ))}
          </div>
          )} {/* end listView ternary */}
        </main>

        {open && (
            <div
                style={{
                  position: "fixed",
                  inset: 0,
                  background: "var(--tg-modal-backdrop)",
                  zIndex: 12000,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  padding: 16,
                }}
                onClick={() => { setOpen(false); setFormErrors({}); setObservationData(null); setTraceText(null); setExecutionsData(null); setExecutionsOffset(0); setTodosData(null); setTracePlanData(null); setTraceCompileData(null); setExpandedExecId(null);  setConfirmDeleteId(null); }}
            >
              <div
                  style={{
                    width: "100%",
                    maxWidth: editingId ? 680 : 580,
                    maxHeight: "92vh",
                    overflowY: "auto",
                    background: "var(--tg-panel-bg)",
                    border: "1px solid var(--tg-panel-border)",
                    borderRadius: 14,
                    padding: 20,
                    color: "var(--tg-text)",
                    boxShadow: "var(--tg-shadow)",
                  }}
                  onClick={(e) => e.stopPropagation()}
              >
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 14 }}>
                  <h3 style={{ marginTop: 0, marginBottom: 0, color: "#7dd3fc", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", paddingRight: 12 }}>
                    {editingId ? (orbitTasks.find((t) => t.id === editingId)?.name || "编辑任务") : "创建任务"}
                  </h3>
                  <button
                    type="button"
                    onClick={() => { setOpen(false); setFormErrors({}); setObservationData(null); setTraceText(null); setExecutionsData(null); setExecutionsOffset(0); setTodosData(null); setTracePlanData(null); setTraceCompileData(null); setExpandedExecId(null);  setConfirmDeleteId(null); }}
                    style={{ flexShrink: 0, background: "none", border: "none", color: "#475569", cursor: "pointer", fontSize: 20, lineHeight: 1, padding: "0 2px", marginTop: 2 }}
                    title="关闭 (Esc)"
                  >×</button>
                </div>
                {editingId && (
                    <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: 8, marginBottom: 12, fontSize: 12, opacity: 0.9 }}>
                      <div>
                        <strong style={{ color: "#a5f3fc" }}>状态</strong>:{" "}
                        {getTaskStatusText(orbitTasks.find((t) => t.id === editingId)?.status ?? "not_started")}
                      </div>
                      {(() => {
                        const phase = orbitTasks.find((t) => t.id === editingId)?.currentPhase;
                        return phase ? (
                          <div>
                            <strong style={{ color: "#a5f3fc" }}>阶段</strong>:{" "}
                            <span style={{
                              display: "inline-block", padding: "1px 8px", borderRadius: 4,
                              background: "rgba(34,211,238,0.15)", border: "1px solid rgba(34,211,238,0.4)",
                              color: "#67e8f9", fontSize: 11, fontWeight: 700, letterSpacing: "0.05em",
                            }}>{phase}</span>
                          </div>
                        ) : null;
                      })()}
                      <div>
                        <strong style={{ color: "#a5f3fc" }}>创建时间</strong>:{" "}
                        {new Date(orbitTasks.find((t) => t.id === editingId)?.createdAt ?? Date.now()).toLocaleString()}
                      </div>
                      {(() => {
                        const upd = orbitTasks.find((t) => t.id === editingId)?.updatedAt;
                        return upd ? (
                          <div>
                            <strong style={{ color: "#a5f3fc" }}>最后更新</strong>:{" "}
                            {new Date(upd).toLocaleString()}
                          </div>
                        ) : null;
                      })()}
                      <div style={{ width: "100%", marginTop: 2 }}>
                        <button
                          type="button"
                          title="点击复制任务ID"
                          onClick={() => {
                            navigator.clipboard?.writeText(editingId).then(() => toast.success("已复制任务ID")).catch(() => {});
                          }}
                          style={{
                            background: "none", border: "none", padding: 0, cursor: "pointer",
                            fontFamily: "monospace", fontSize: 10, color: "#334155",
                            letterSpacing: "0.03em",
                          }}
                        >
                          ID: {editingId}
                        </button>
                      </div>
                    </div>
                )}
                {/* Phase progress bar */}
                {editingId && (() => {
                  const task = orbitTasks.find((t) => t.id === editingId);
                  if (!task) return null;
                  const phase = task.currentPhase ?? '';
                  const isFinished = task.status === "finished";
                  const isFailed = task.status === "failed";
                  const currentIdx = TRUSTGUARD_PHASES.indexOf(phase as typeof TRUSTGUARD_PHASES[number]);
                  return (
                    <div style={{ marginBottom: 14 }}>
                      <div style={{ fontSize: 11, color: "#64748b", marginBottom: 5, letterSpacing: "0.04em" }}>执行阶段</div>
                      <div style={{ display: "flex", gap: 3, alignItems: "center" }}>
                        {TRUSTGUARD_PHASES.map((ph, idx) => {
                          const isCurrent = !isFinished && idx === currentIdx;
                          const isDone = isFinished || (currentIdx >= 0 && idx < currentIdx);
                          const isError = isFailed && idx === currentIdx;
                          const barBg = isError
                            ? "rgba(248,113,113,0.8)"
                            : isDone
                              ? "rgba(34,197,94,0.75)"
                              : isCurrent
                                ? "rgba(34,211,238,0.85)"
                                : "rgba(71,85,105,0.4)";
                          const textColor = isError ? "#fca5a5" : isCurrent ? "#67e8f9" : isDone ? "#86efac" : "#475569";
                          return (
                            <div key={ph} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
                              <div style={{
                                width: "100%", height: 4, borderRadius: 2,
                                background: barBg,
                                boxShadow: isCurrent ? "0 0 8px rgba(34,211,238,0.6)" : isError ? "0 0 8px rgba(248,113,113,0.5)" : "none",
                                transition: "background 0.3s",
                              }} />
                              <span style={{
                                fontSize: 8,
                                color: textColor,
                                fontWeight: (isCurrent || isDone || isError) ? 700 : 400,
                                letterSpacing: "0.03em",
                                whiteSpace: "nowrap",
                              }}>{ph}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })()}
                {/* Elapsed time row */}
                {editingId && (() => {
                  const task = orbitTasks.find((t) => t.id === editingId);
                  if (!task || (task.status !== "running" && task.status !== "paused")) return null;
                  const elapsed = elapsedForStatus({
                    createdAt: task.createdAt,
                    updatedAt: task.updatedAt,
                    status: task.status,
                    now: nowTick,
                  });
                  const color = task.status === "running" ? "#34d399" : "#facc15";
                  return (
                    <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#64748b" }}>
                      <span>运行时长</span>
                      <span style={{ fontFamily: "monospace", color, fontWeight: 700, fontSize: 13 }}>{elapsed}</span>
                      <span style={{ color: "#334155", fontSize: 11 }}>起始 {new Date(task.createdAt).toLocaleString("zh-CN")}</span>
                    </div>
                  );
                })()}
                <label htmlFor="task-name-input" style={{ display: "block", fontSize: 13, marginBottom: 6 }}>任务名称</label>
                <input
                    ref={taskNameInputRef}
                    id="task-name-input"
                    value={taskName}
                    onChange={(e) => { setTaskName(e.target.value); if (formErrors.name) setFormErrors((p) => ({ ...p, name: false })); }}
                    onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.form?.requestSubmit?.(); }}
                    style={{ width: "100%", boxSizing: "border-box", marginBottom: 12, padding: 10, borderRadius: 8, border: formErrors.name ? "1px solid #f87171" : "1px solid var(--tg-panel-border)", background: "var(--tg-input-bg)", color: "var(--tg-text)" }}
                />
                <label htmlFor="task-desc-input" style={{ display: "block", fontSize: 13, marginBottom: 6 }}>任务描述</label>
                <textarea
                    id="task-desc-input"
                    value={taskDesc}
                    onChange={(e) => setTaskDesc(e.target.value)}
                    rows={5}
                    style={{ width: "100%", boxSizing: "border-box", marginBottom: 12, padding: 10, borderRadius: 8, border: "1px solid var(--tg-panel-border)", background: "var(--tg-input-bg)", color: "var(--tg-text)", resize: "vertical" }}
                />
                <label htmlFor="task-url-input" style={{ display: "block", fontSize: 13, marginBottom: 6 }}>靶机URL</label>
                <input
                    id="task-url-input"
                    value={taskUrl}
                    onChange={(e) => { setTaskUrl(e.target.value); if (formErrors.url) setFormErrors((p) => ({ ...p, url: false })); }}
                    onKeyDown={(e) => { if (e.key === "Enter") commitTask(); }}
                    placeholder="https://..."
                    style={{ width: "100%", boxSizing: "border-box", marginBottom: editingId ? 16 : 8, padding: 10, borderRadius: 8, border: formErrors.url ? "1px solid #f87171" : "1px solid var(--tg-panel-border)", background: "var(--tg-input-bg)", color: "var(--tg-text)" }}
                />
                {/* Quick presets — only show on creation */}
	                {!editingId && (
	                  <div style={{ marginBottom: 16 }}>
	                    <div style={{ fontSize: 10, color: "var(--tg-text-muted)", marginBottom: 5, letterSpacing: "0.04em", fontFamily: "monospace" }}>快速预设靶场</div>
	                    <div style={{
	                      marginBottom: 8,
	                      padding: "8px 10px",
	                      borderRadius: 6,
	                      border: "1px solid rgba(251,191,36,0.28)",
	                      background: "rgba(251,191,36,0.08)",
	                      color: "#fde68a",
	                      fontSize: 11,
	                      lineHeight: 1.55,
	                    }}>
	                      靶机可能需要自己通过在宿主机上手动建立；快速预设靶场只会填充任务信息，不会帮你拉起或启动靶机。
	                    </div>
	                    <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                      {([
                        { label: "Struts2 S2-045", name: "Struts2 S2-045 RCE 渗透测试", desc: "测试 Apache Struts2 S2-045 远程代码执行漏洞（CVE-2017-5638），验证 OGNL 注入利用链。", url: "http://host.docker.internal:8080/" },
                        { label: "Struts2 S2-057", name: "Struts2 S2-057 RCE 渗透测试", desc: "测试 Apache Struts2 S2-057 远程代码执行漏洞（CVE-2018-11776），验证命名空间注入利用。", url: "http://host.docker.internal:8080/" },
                        { label: "ThinkPHP RCE", name: "ThinkPHP 5.0.23 RCE", desc: "对 ThinkPHP 5.0.23 框架进行远程代码执行漏洞自动化渗透测试。", url: "http://host.docker.internal:8080/" },
                        { label: "Shiro 反序列化", name: "Shiro CVE-2016-4437 反序列化", desc: "测试 Apache Shiro RememberMe 反序列化 RCE 漏洞（CVE-2016-4437），爆破 AES 密钥。", url: "http://host.docker.internal:8080/" },
                        { label: "Flask SSTI", name: "Flask SSTI 模板注入", desc: "对 Flask 应用进行服务端模板注入（SSTI）漏洞验证，利用 Jinja2 模板引擎执行任意代码。", url: "http://host.docker.internal:8080/" },
                        { label: "FastJSON 1.2.24", name: "FastJSON 1.2.24 反序列化 RCE", desc: "测试 Fastjson 1.2.24 反序列化远程代码执行漏洞，利用 JNDI 注入获取服务器权限。", url: "http://host.docker.internal:8080/" },
                        { label: "WebLogic RCE", name: "WebLogic CVE-2023-21839 RCE", desc: "测试 Oracle WebLogic Server CVE-2023-21839 远程代码执行漏洞（T3/IIOP 协议）。", url: "http://host.docker.internal:8080/" },
                        { label: "Tomcat PUT", name: "Tomcat CVE-2017-12615 文件上传", desc: "测试 Apache Tomcat CVE-2017-12615 PUT 方法任意文件上传漏洞，上传 JSP Webshell。", url: "http://host.docker.internal:8080/" },
                      ] as { label: string; name: string; desc: string; url: string }[]).map((p) => (
                        <button
                          key={p.label}
                          type="button"
                          onClick={() => { setTaskName(p.name); setTaskDesc(p.desc); setTaskUrl(p.url); }}
                          style={{
                            padding: "3px 9px", borderRadius: 5, fontSize: 10, fontWeight: 600,
                            border: "1px solid rgba(71,85,105,0.5)", background: "var(--tg-input-bg)",
                            color: "var(--tg-text-muted)", cursor: "pointer", fontFamily: "monospace",
                            transition: "color 0.15s, border-color 0.15s",
                          }}
                          onMouseEnter={(e) => { e.currentTarget.style.color = "#a5f3fc"; e.currentTarget.style.borderColor = "rgba(34,211,238,0.5)"; }}
                          onMouseLeave={(e) => { e.currentTarget.style.color = "#64748b"; e.currentTarget.style.borderColor = "rgba(71,85,105,0.5)"; }}
                        >{p.label}</button>
                      ))}
                    </div>
                  </div>
                )}
                {/* Observation panel — structured */}
                {observationData && (
                  <div style={{
                    marginBottom: 12, borderRadius: 8,
                    background: "var(--tg-panel-muted)", border: "1px solid rgba(99,102,241,0.25)",
                    overflow: "hidden",
                  }}>
                    {/* Header row */}
                    <div style={{
                      padding: "7px 12px", borderBottom: "1px solid rgba(99,102,241,0.15)",
                      display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center",
                      background: "rgba(99,102,241,0.05)",
                    }}>
                      <span style={{ color: "#a5b4fc", fontWeight: 800, fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase" }}>观测快照</span>
                      <span style={{ color: "#64748b", fontFamily: "monospace", fontSize: 11 }}>
                        阶段: <span style={{
                          color: { RECON: "#64748b", THREAT_MODEL: "#a78bfa", VULN_SCAN: "#fb923c", EXPLOIT: "#f87171", REPORT: "#34d399", DONE: "#22d3ee" }[observationData.current_phase ?? ""] ?? "#94a3b8",
                          fontWeight: 700,
                        }}>{observationData.current_phase ?? "—"}</span>
                      </span>
                      <span style={{ color: "#64748b", fontFamily: "monospace", fontSize: 11 }}>
                        状态: <span style={{ color: observationData.status === "RUNNING" ? "#22d3ee" : observationData.status === "DONE" ? "#34d399" : observationData.status === "FAILED" ? "#f87171" : "#fbbf24", fontWeight: 700 }}>{observationData.status ?? "—"}</span>
                      </span>
                      <span style={{ color: "#475569", fontFamily: "monospace", fontSize: 10 }}>目标: <span style={{ color: "#64748b" }}>{observationData.target ?? "—"}</span></span>
                      {observationData.generated_at && (
                        <span style={{ color: "#334155", fontFamily: "monospace", fontSize: 10, marginLeft: "auto" }}>
                          {(() => { try { return new Date(observationData.generated_at).toLocaleTimeString("zh-CN"); } catch { return observationData.generated_at.slice(0, 19); } })()}
                        </span>
                      )}
                    </div>
                    {/* Context key-values */}
                    {Object.keys(observationData.context ?? {}).filter(k => !k.startsWith("_") && !k.endsWith("_raw")).length > 0 && (
                      <div style={{ padding: "6px 12px", borderBottom: "1px solid rgba(51,65,85,0.2)" }}>
                        <div style={{ color: "#475569", fontSize: 10, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 4 }}>上下文</div>
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: "2px 16px" }}>
                          {Object.entries(observationData.context ?? {})
                            .filter(([k]) => !k.startsWith("_") && !k.endsWith("_raw"))
                            .slice(0, 14)
                            .map(([k, v]) => {
                              const val = typeof v === "string" ? v : JSON.stringify(v);
                              return (
                                <div key={k} style={{ display: "flex", gap: 6, alignItems: "baseline", fontSize: 11, fontFamily: "monospace" }}>
                                  <span style={{ color: "#475569", flexShrink: 0 }}>{k}:</span>
                                  <span style={{ color: "#94a3b8", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={val}>{val.slice(0, 80)}{val.length > 80 ? "…" : ""}</span>
                                </div>
                              );
                            })}
                        </div>
                      </div>
                    )}
                    {/* Artifacts summary */}
                    {(observationData.artifacts_summary ?? []).length > 0 && (
                      <div style={{ padding: "6px 12px", maxHeight: 120, overflowY: "auto" }}>
                        <div style={{ color: "#475569", fontSize: 10, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 4 }}>Artifacts</div>
                        {(observationData.artifacts_summary ?? []).map((a, i) => (
                          <div key={i} style={{ display: "flex", gap: 8, fontSize: 11, fontFamily: "monospace", padding: "2px 0", borderBottom: i < (observationData.artifacts_summary?.length ?? 0) - 1 ? "1px solid rgba(51,65,85,0.12)" : "none" }}>
                            <span style={{ color: "#6366f1", fontWeight: 700, flexShrink: 0 }}>[{a.skill_id}]</span>
                            <span style={{ color: "#64748b" }}>{String(a.summary ?? "").slice(0, 140)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                {/* Trace panel */}
                {traceText && (
                  <div style={{
                    marginBottom: 12, padding: 10, borderRadius: 8,
                    background: "var(--tg-panel-muted)", border: "1px solid rgba(167,139,250,0.3)",
                    fontSize: 11, color: "#c4b5fd", fontFamily: "monospace",
                    maxHeight: 220, overflowY: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all",
                  }}>
                    <div style={{ color: "#a78bfa", fontWeight: 700, marginBottom: 4, fontSize: 10, letterSpacing: "0.04em" }}>▸ 执行轨迹</div>
                    {traceText}
                  </div>
                )}
                {/* Trace Plan panel */}
                {tracePlanData && (
                  <div style={{
                    marginBottom: 12, padding: 10, borderRadius: 8,
                    background: "var(--tg-panel-muted)", border: "1px solid rgba(251,146,60,0.3)",
                    fontSize: 11, color: "#fdba74", fontFamily: "monospace",
                    maxHeight: 200, overflowY: "auto",
                  }}>
                    <div style={{ color: "#fb923c", fontWeight: 700, marginBottom: 6, fontSize: 10, letterSpacing: "0.04em" }}>▸ 策略计划 (LLM Plan)</div>
                    {(() => {
                      const plan = tracePlanData;
                      const items = Array.isArray((plan as { items?: unknown[] }).items) ? (plan as { items: unknown[] }).items : [];
                      if (items.length > 0) {
                        return (
                          <div>
                            {items.map((item, i) => {
                              const it = item as Record<string, unknown>;
                              const phase = String(it.phase ?? it.stage ?? '-');
                              const skill = String(it.skill_id ?? it.name ?? it.action ?? '-');
                              const status = String(it.status ?? '');
                              const desc = typeof it.description === 'string' ? it.description.slice(0, 120) : '';
                              return (
                                <div key={i} style={{ marginBottom: 3 }}>
                                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                                    <span style={{ color: '#fb923c', opacity: 0.5, minWidth: 18 }}>[{i + 1}]</span>
                                    <span style={{ color: '#fde68a', minWidth: 80 }}>{phase}</span>
                                    <span style={{ color: '#fdba74' }}>{skill}</span>
                                    {status && <span style={{ color: '#f97316', opacity: 0.6, marginLeft: 'auto', whiteSpace: 'nowrap' }}>{status}</span>}
                                  </div>
                                  {desc && <div style={{ color: '#fde68a', opacity: 0.5, fontSize: 10, paddingLeft: 24 }}>{desc}</div>}
                                </div>
                              );
                            })}
                          </div>
                        );
                      }
                      return (
                        <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                          {JSON.stringify(plan, null, 2).slice(0, 1200)}
                        </pre>
                      );
                    })()}
                  </div>
                )}
                {/* Compile trace panel */}
                {traceCompileData && (
                  <div style={{
                    marginBottom: 12, padding: 10, borderRadius: 8,
                    background: "var(--tg-panel-muted)", border: "1px solid rgba(20,184,166,0.3)",
                    fontSize: 11, color: "#5eead4", fontFamily: "monospace",
                    maxHeight: 200, overflowY: "auto",
                  }}>
                    <div style={{ color: "#2dd4bf", fontWeight: 700, marginBottom: 6, fontSize: 10, letterSpacing: "0.04em" }}>▸ 编译段 (Compile Trace)</div>
                    <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all', color: '#94a3b8' }}>
                      {JSON.stringify(traceCompileData, null, 2).slice(0, 1500)}
                    </pre>
                  </div>
                )}
                {/* Execution records panel */}
                {executionsData && (
                  <div style={{
                    marginBottom: 12, borderRadius: 8,
                    background: "var(--tg-panel-muted)", border: "1px solid rgba(52,211,153,0.25)",
                    fontSize: 11, fontFamily: "monospace",
                  }}>
                    <div style={{
                      padding: "6px 10px", borderBottom: "1px solid rgba(52,211,153,0.15)",
                      display: "flex", justifyContent: "space-between", alignItems: "center",
                    }}>
                      <span style={{ color: "#34d399", fontWeight: 700, fontSize: 10, letterSpacing: "0.04em" }}>
                        ▸ 执行记录 (offset {executionsOffset})
                      </span>
                      <span style={{ display: "flex", gap: 6 }}>
                        <button
                          type="button"
                          disabled={executionsOffset === 0}
                          onClick={() => {
                            const newOffset = Math.max(0, executionsOffset - 20);
                            setExecutionsOffset(newOffset);
                            setExpandedExecId(null);
                            setExecutionsLoading(true);
                            getTaskExecutions(editingId!, 20, newOffset)
                              .then((d) => setExecutionsData(d))
                              .catch(() => {})
                              .finally(() => setExecutionsLoading(false));
                          }}
                          style={{
                            padding: "2px 8px", borderRadius: 4, fontSize: 10,
                            border: "1px solid rgba(52,211,153,0.3)", background: "transparent",
                            color: executionsOffset === 0 ? "#1e293b" : "#34d399",
                            cursor: executionsOffset === 0 ? "default" : "pointer",
                          }}
                        >‹ 上页</button>
                        <button
                          type="button"
                          disabled={executionsData.length < 20}
                          onClick={() => {
                            const newOffset = executionsOffset + 20;
                            setExecutionsOffset(newOffset);
                            setExpandedExecId(null);
                            setExecutionsLoading(true);
                            getTaskExecutions(editingId!, 20, newOffset)
                              .then((d) => setExecutionsData(d))
                              .catch(() => {})
                              .finally(() => setExecutionsLoading(false));
                          }}
                          style={{
                            padding: "2px 8px", borderRadius: 4, fontSize: 10,
                            border: "1px solid rgba(52,211,153,0.3)", background: "transparent",
                            color: executionsData.length < 20 ? "#1e293b" : "#34d399",
                            cursor: executionsData.length < 20 ? "default" : "pointer",
                          }}
                        >下页 ›</button>
                      </span>
                    </div>
                    {executionsLoading ? (
                      <div style={{ padding: "10px", color: "#475569", textAlign: "center" }}>加载中…</div>
                    ) : executionsData.length === 0 ? (
                      <div style={{ padding: "10px", color: "#475569", textAlign: "center" }}>暂无记录</div>
                    ) : (
                      <div style={{ maxHeight: 160, overflowY: "auto" }}>
                        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10 }}>
                          <thead>
                            <tr style={{ borderBottom: "1px solid rgba(52,211,153,0.12)" }}>
                              {["#", "阶段", "技能", "状态", "耗时", "Worker", ""].map((h) => (
                                <th key={h} style={{ padding: "4px 6px", textAlign: "left", color: "#475569", fontWeight: 600, whiteSpace: "nowrap" }}>{h}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {executionsData.map((rec, i) => {
                              const dur = typeof rec.duration_ms === "number" ? `${(rec.duration_ms / 1000).toFixed(1)}s` : "—";
                              const statusColor = rec.status === "success" || rec.status === "SUCCESS" ? "#34d399"
                                : rec.status === "failed" || rec.status === "FAILED" ? "#f87171"
                                : "#94a3b8";
                              const recKey = rec.request_id ?? String(i);
                              const isExpanded = expandedExecId === recKey;
                              const detail = rec.request_id ? execDetailCache[rec.request_id] : undefined;
                              const isDetailLoading = execDetailLoading === recKey;
                              return (
                                <Fragment key={recKey}>
                                  <tr
                                    onClick={() => {
                                      if (isExpanded) { setExpandedExecId(null); return; }
                                      setExpandedExecId(recKey);
                                      if (rec.request_id && !execDetailCache[rec.request_id]) {
                                        setExecDetailLoading(recKey);
                                        getExecutionRecord(rec.request_id)
                                          .then((d) => setExecDetailCache((prev) => ({ ...prev, [rec.request_id!]: d })))
                                          .catch(() => {})
                                          .finally(() => setExecDetailLoading((cur) => cur === recKey ? null : cur));
                                      }
                                    }}
                                    style={{ borderBottom: "1px solid rgba(71,85,105,0.15)", cursor: "pointer" }}
                                  >
                                    <td style={{ padding: "3px 6px", color: "#334155" }}>{executionsOffset + i + 1}</td>
                                    <td style={{ padding: "3px 6px", color: "#67e8f9" }}>{String(rec.phase ?? "—")}</td>
                                    <td style={{ padding: "3px 6px", color: "#a5f3fc", maxWidth: 100, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={String(rec.skill_id ?? "")}>{String(rec.skill_id ?? "—")}</td>
                                    <td style={{ padding: "3px 6px", color: statusColor, fontWeight: 700 }}>{String(rec.status ?? "—")}</td>
                                    <td style={{ padding: "3px 6px", color: "#64748b" }}>{dur}</td>
                                    <td style={{ padding: "3px 6px", color: "#475569", maxWidth: 80, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={String(rec.worker_id ?? "")}>{String(rec.worker_id ?? "—")}</td>
                                    <td style={{ padding: "3px 4px", color: isExpanded ? "#a5f3fc" : "#334155", fontSize: 8, transition: "color 0.15s" }}>{isExpanded ? "▾" : "▸"}</td>
                                  </tr>
                                  {isExpanded && (
                                    <tr>
                                      <td colSpan={7} style={{
                                        padding: "6px 10px", background: "var(--tg-input-bg)",
                                        borderBottom: "1px solid rgba(71,85,105,0.15)",
                                      }}>
                                        {isDetailLoading && (
                                          <div style={{ fontSize: 10, color: "#475569", fontFamily: "monospace", marginBottom: 3 }}>加载详情…</div>
                                        )}
                                        {(() => {
                                          const d = detail ?? rec;
                                          const reasoning = typeof d.reasoning === 'string' ? d.reasoning.trim() : '';
                                          const rawOut = typeof (d as Record<string, unknown>).raw_stdout === 'string' ? (d as Record<string, unknown>).raw_stdout as string : '';
                                          const rawErr = typeof (d as Record<string, unknown>).raw_stderr === 'string' ? (d as Record<string, unknown>).raw_stderr as string : '';
                                          return (
                                            <>
                                              {reasoning && (
                                                <div style={{ marginBottom: 3, fontSize: 10, color: "#94a3b8", fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                                                  <span style={{ color: "#64748b", fontWeight: 700 }}>推理：</span>{reasoning}
                                                </div>
                                              )}
                                              {rawOut && (
                                                <div style={{ marginBottom: 3, fontSize: 10, color: "#86efac", fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 200, overflowY: "auto" }}>
                                                  <span style={{ color: "#4ade80", fontWeight: 700 }}>输出：</span>{rawOut.slice(0, 3000)}{rawOut.length > 3000 ? '…' : ''}
                                                </div>
                                              )}
                                              {rawErr && (
                                                <div style={{ marginBottom: 3, fontSize: 10, color: "#fca5a5", fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 160, overflowY: "auto" }}>
                                                  <span style={{ color: "#f87171", fontWeight: 700 }}>错误：</span>{rawErr.slice(0, 2000)}{rawErr.length > 2000 ? '…' : ''}
                                                </div>
                                              )}
                                            </>
                                          );
                                        })()}
                                        {rec.request_id && (
                                          <div style={{ fontSize: 10, color: "#475569", fontFamily: "monospace", marginTop: 2 }}>
                                            <span style={{ color: "#64748b", fontWeight: 700 }}>请求ID：</span>
                                            <button type="button" onClick={(e) => { e.stopPropagation(); navigator.clipboard?.writeText(String(rec.request_id)).catch(() => {}); toast.success("已复制请求ID"); }}
                                              style={{ background: "none", border: "none", cursor: "pointer", color: "#475569", fontSize: 10, fontFamily: "monospace", padding: 0 }}
                                            >{String(rec.request_id)}</button>
                                          </div>
                                        )}
                                        {(detail?.created_at ?? rec.created_at) && (
                                          <div style={{ fontSize: 10, color: "#475569", fontFamily: "monospace", marginTop: 2 }}>
                                            <span style={{ color: "#64748b", fontWeight: 700 }}>时间：</span>{String(detail?.created_at ?? rec.created_at)}
                                          </div>
                                        )}
                                      </td>
                                    </tr>
                                  )}
                                </Fragment>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}

                {/* Todos panel */}
                {todosData && (
                  <div style={{
                    marginBottom: 12, borderRadius: 8,
                    background: "var(--tg-panel-muted)", border: "1px solid rgba(251,191,36,0.25)",
                    fontSize: 11, fontFamily: "monospace",
                  }}>
                    <div style={{
                      padding: "6px 10px", borderBottom: "1px solid rgba(251,191,36,0.15)",
                      display: "flex", justifyContent: "space-between", alignItems: "center",
                    }}>
                      <span style={{ color: "#fbbf24", fontWeight: 700, fontSize: 10, letterSpacing: "0.04em" }}>
                        ▸ 测试计划 ({todosData.length} 项)
                      </span>
                    </div>
                    {todosLoading ? (
                      <div style={{ padding: "10px", color: "#475569", textAlign: "center" }}>加载中…</div>
                    ) : todosData.length === 0 ? (
                      <div style={{ padding: "10px", color: "#475569", textAlign: "center" }}>暂无计划</div>
                    ) : (
                      <div style={{ maxHeight: 160, overflowY: "auto" }}>
                        {todosData.map((todo, i) => {
                          const statusColor = todo.status === 'DONE' ? "#22c55e"
                            : todo.status === 'IN_PROGRESS' ? "#34d399"
                            : todo.status === 'FAILED' ? "#f87171"
                            : todo.status === 'SKIPPED' ? "#94a3b8"
                            : "#475569";
                          return (
                            <div key={todo.todoId ?? i} style={{
                              padding: "5px 10px",
                              borderBottom: i < todosData.length - 1 ? "1px solid rgba(71,85,105,0.15)" : "none",
                              display: "flex", alignItems: "center", gap: 6,
                            }}>
                              <span style={{
                                flexShrink: 0, padding: "1px 5px", borderRadius: 3,
                                background: "rgba(34,211,238,0.1)", border: "1px solid rgba(34,211,238,0.2)",
                                color: "#67e8f9", fontSize: 9, fontWeight: 700,
                              }}>{todo.phase ?? "?"}</span>
                              <span style={{
                                flexShrink: 0, padding: "1px 5px", borderRadius: 3,
                                background: "var(--tg-input-bg)", border: `1px solid ${statusColor}55`,
                                color: statusColor, fontSize: 9, fontWeight: 700,
                              }}>{todo.status ?? "?"}</span>
                              <span style={{ color: "#94a3b8", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }} title={todo.description ?? ""}>{todo.name}</span>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}

                <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                  {editingId && (
                      <>
                        {/* run controls */}
                        {(() => {
                          const st = orbitTasks.find((t) => t.id === editingId)?.status ?? "not_started";
                          const notRunning = st !== "running";
                          return notRunning ? (
                            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                              <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
                                <span style={{ fontSize: 11, color: "#64748b" }}>最大轮次</span>
                                <input
                                  type="number"
                                  min={1} max={500}
                                  value={maxTicks}
                                  onChange={(e) => setMaxTicks(Math.max(1, Math.min(500, Number(e.target.value) || 100)))}
                                  style={{
                                    width: 56, padding: "5px 6px", borderRadius: 6,
                                    border: "1px solid var(--tg-panel-border)", background: "var(--tg-input-bg)",
                                    color: "var(--tg-text)", fontSize: 12, textAlign: "center",
                                  }}
                                />
                              </label>
                              <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
                                <span style={{ fontSize: 11, color: "#64748b" }}>单次时长</span>
                                <input
                                  type="number"
                                  min={60} max={7200} step={60}
                                  value={maxRunDurationSeconds}
                                  onChange={(e) => setMaxRunDurationSeconds(Math.max(60, Math.min(7200, Number(e.target.value) || 900)))}
                                  style={{
                                    width: 72, padding: "5px 6px", borderRadius: 6,
                                    border: "1px solid var(--tg-panel-border)", background: "var(--tg-input-bg)",
                                    color: "var(--tg-text)", fontSize: 12, textAlign: "center",
                                  }}
                                />
                                <span style={{ fontSize: 11, color: "#64748b" }}>秒</span>
                              </label>
                            </div>
                          ) : null;
                        })()}

                        {/* 查看日志按钮 */}
                        <button
                          type="button"
                          onClick={() => {
                            setOpen(false);
                            setEditingId(null);
                            navigate(`/logs?taskId=${editingId}`);
                          }}
                          style={{
                            padding: "8px 14px", borderRadius: 8,
                            border: "1px solid rgba(34,211,238,0.4)",
                            background: "var(--tg-input-bg)", color: "var(--tg-accent)",
                            cursor: "pointer", fontWeight: 700, fontSize: 12,
                          }}
                        >
                          查看日志
                        </button>

	                        {/* ── Data query row ── */}
	                        <div style={{ width: "100%", display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
	                          <span style={{ color: "#334155", fontSize: 10, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase" as const, fontFamily: "monospace", flexShrink: 0 }}>数据查询</span>
	                          <div style={{ flex: 1, height: 1, background: "rgba(51,65,85,0.25)" }} />
	                        </div>

	                        <button
	                          type="button"
	                          disabled={detailRefreshing}
	                          onClick={() => { void handleRefreshCurrentTask(); }}
	                          style={{
	                            padding: "5px 10px", borderRadius: 6,
	                            border: "1px solid rgba(148,163,184,0.45)",
	                            background: "var(--tg-input-bg)",
	                            color: detailRefreshing ? "#64748b" : "#cbd5e1",
	                            cursor: detailRefreshing ? "wait" : "pointer", fontWeight: 700, fontSize: 11,
	                          }}
	                        >
	                          {detailRefreshing ? "刷新中…" : "刷新当前"}
	                        </button>

	                        {/* 观测按钮 — toggle */}
                        <button
                            type="button"
                            disabled={observationLoading}
                            onClick={async () => {
                              if (observationData !== null) { setObservationData(null); return; }
                              setObservationLoading(true);
                              setTraceText(null);
                              setExecutionsData(null);
                              setTodosData(null);
                              setTracePlanData(null);
                              setTraceCompileData(null);
                              setObservationData(null);
                              try {
                                const obs = await getTaskObservation(editingId!);
                                setObservationData(obs);
                              } catch (e: unknown) {
                                toast.error(`观测失败: ${(e as Error).message}`);
                              } finally {
                                setObservationLoading(false);
                              }
                            }}
                            style={{
                              padding: "5px 10px", borderRadius: 6,
                              border: `1px solid ${observationData !== null ? "rgba(99,102,241,0.85)" : "rgba(99,102,241,0.5)"}`,
                              background: observationData !== null ? "rgba(99,102,241,0.18)" : "var(--tg-input-bg)",
                              color: "#a5b4fc",
                              cursor: observationLoading ? "wait" : "pointer", fontWeight: 700, fontSize: 11,
                            }}
                        >
                          {observationLoading ? "查询中…" : "观测"}
                        </button>

                        {/* 计划按钮 — toggle */}
                        <button
                          type="button"
                          disabled={todosLoading}
                          onClick={async () => {
                            if (todosData !== null) { setTodosData(null); return; }
                            setTodosLoading(true);
                            setTodosData(null);
                            setObservationData(null);
                            setTraceText(null);
                            setExecutionsData(null);
                            setTracePlanData(null);
                            setTraceCompileData(null);
                            try {
                              const todos = await getTaskTodos(editingId!);
                              setTodosData(todos);
                            } catch (e: unknown) {
                              toast.error(`计划加载失败: ${(e as Error).message}`);
                            } finally {
                              setTodosLoading(false);
                            }
                          }}
                          style={{
                            padding: "5px 10px", borderRadius: 6,
                            border: `1px solid ${todosData !== null ? "rgba(251,191,36,0.85)" : "rgba(251,191,36,0.5)"}`,
                            background: todosData !== null ? "rgba(251,191,36,0.12)" : "var(--tg-input-bg)",
                            color: "#fde047",
                            cursor: todosLoading ? "wait" : "pointer", fontWeight: 700, fontSize: 11,
                          }}
                        >
                          {todosLoading ? "加载中…" : "计划"}
                        </button>

                        {/* 轨迹按钮 — toggle */}
                        <button
                          type="button"
                          disabled={traceLoading}
                          onClick={async () => {
                            if (traceText !== null) { setTraceText(null); return; }
                            setTraceLoading(true);
                            setTraceText(null);
                            setObservationData(null);
                            setExecutionsData(null);
                            setTodosData(null);
                            setTracePlanData(null);
                            setTraceCompileData(null);
                            try {
                              const trace = await getTaskTrace(editingId!);
                              setTraceText(formatTrace(trace));
                            } catch (e: unknown) {
                              toast.error(`轨迹加载失败: ${(e as Error).message}`);
                            } finally {
                              setTraceLoading(false);
                            }
                          }}
                          style={{
                            padding: "5px 10px", borderRadius: 6,
                            border: `1px solid ${traceText !== null ? "rgba(167,139,250,0.85)" : "rgba(167,139,250,0.5)"}`,
                            background: traceText !== null ? "rgba(167,139,250,0.18)" : "var(--tg-input-bg)",
                            color: "#c4b5fd",
                            cursor: traceLoading ? "wait" : "pointer", fontWeight: 700, fontSize: 11,
                          }}
                        >
                          {traceLoading ? "加载中…" : "轨迹"}
                        </button>

                        {/* 策略计划按钮 — toggle */}
                        <button
                          type="button"
                          disabled={tracePlanLoading}
                          onClick={async () => {
                            if (tracePlanData !== null) { setTracePlanData(null); return; }
                            setTracePlanLoading(true);
                            setTracePlanData(null);
                            setTraceText(null);
                            setObservationData(null);
                            setExecutionsData(null);
                            setTodosData(null);
                            try {
                              const plan = await getTracePlan(editingId!);
                              setTracePlanData(plan);
                            } catch (e: unknown) {
                              toast.error(`策略计划加载失败: ${(e as Error).message}`);
                            } finally {
                              setTracePlanLoading(false);
                            }
                          }}
                          style={{
                            padding: "5px 10px", borderRadius: 6,
                            border: `1px solid ${tracePlanData !== null ? "rgba(251,146,60,0.85)" : "rgba(251,146,60,0.5)"}`,
                            background: tracePlanData !== null ? "rgba(251,146,60,0.18)" : "var(--tg-input-bg)",
                            color: "#fdba74",
                            cursor: tracePlanLoading ? "wait" : "pointer", fontWeight: 700, fontSize: 11,
                          }}
                        >
                          {tracePlanLoading ? "加载中…" : "策略计划"}
                        </button>

                        {/* 编译段按钮 — toggle */}
                        <button
                          type="button"
                          disabled={traceCompileLoading}
                          onClick={async () => {
                            if (traceCompileData !== null) { setTraceCompileData(null); return; }
                            setTraceCompileLoading(true);
                            setTraceCompileData(null);
                            setTraceText(null);
                            setObservationData(null);
                            setExecutionsData(null);
                            setTodosData(null);
                            setTracePlanData(null);
                            try {
                              const compile = await getTraceCompile(editingId!);
                              setTraceCompileData(compile);
                            } catch (e: unknown) {
                              toast.error(`编译段加载失败: ${(e as Error).message}`);
                            } finally {
                              setTraceCompileLoading(false);
                            }
                          }}
                          style={{
                            padding: "5px 10px", borderRadius: 6,
                            border: `1px solid ${traceCompileData !== null ? "rgba(20,184,166,0.85)" : "rgba(20,184,166,0.5)"}`,
                            background: traceCompileData !== null ? "rgba(20,184,166,0.15)" : "var(--tg-input-bg)",
                            color: "#5eead4",
                            cursor: traceCompileLoading ? "wait" : "pointer", fontWeight: 700, fontSize: 11,
                          }}
                        >
                          {traceCompileLoading ? "加载中…" : "编译段"}
                        </button>

                        {/* 执行记录按钮 — toggle */}
                        <button
                          type="button"
                          disabled={executionsLoading}
                          onClick={async () => {
                            if (executionsData !== null) { setExecutionsData(null); return; }
                            setExecutionsLoading(true);
                            setExecutionsOffset(0);
                            setExecutionsData(null);
                            setTraceText(null);
                            setObservationData(null);
                            setTodosData(null);
                            setTracePlanData(null);
                            setTraceCompileData(null);
                            try {
                              const recs = await getTaskExecutions(editingId!, 20, 0);
                              setExecutionsData(recs);
                            } catch (e: unknown) {
                              toast.error(`执行记录加载失败: ${(e as Error).message}`);
                            } finally {
                              setExecutionsLoading(false);
                            }
                          }}
                          style={{
                            padding: "5px 10px", borderRadius: 6,
                            border: `1px solid ${executionsData !== null ? "rgba(52,211,153,0.85)" : "rgba(52,211,153,0.5)"}`,
                            background: executionsData !== null ? "rgba(52,211,153,0.15)" : "var(--tg-input-bg)",
                            color: "#6ee7b7",
                            cursor: executionsLoading ? "wait" : "pointer", fontWeight: 700, fontSize: 11,
                          }}
                        >
                          {executionsLoading ? "加载中…" : "执行记录"}
                        </button>

                        {/* ── Action row separator ── */}
                        <div style={{ width: "100%", display: "flex", alignItems: "center", gap: 8, marginBottom: 2, marginTop: 4 }}>
                          <span style={{ color: "#334155", fontSize: 10, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase" as const, fontFamily: "monospace", flexShrink: 0 }}>操作</span>
                          <div style={{ flex: 1, height: 1, background: "rgba(51,65,85,0.25)" }} />
                        </div>

                        {/* ── Action buttons row ── */}
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, width: "100%" }}>
                          {(() => {
                            const btnBase: React.CSSProperties = {
                              flex: 1, minWidth: 0, padding: "8px 6px", borderRadius: 8,
                              border: "1px solid rgba(148,163,184,0.3)",
                              background: "var(--tg-input-bg)", color: "var(--tg-text-muted)",
                              cursor: "pointer", fontWeight: 700, fontSize: 11, textAlign: "center" as const,
                              whiteSpace: "nowrap" as const,
                            };
                            const st = orbitTasks.find((t) => t.id === editingId)?.status ?? "not_started";
                            const isRunning = st === "running";
                            const isPaused = st === "paused";
                            return (
                              <>
                                {/* 单步 */}
                                {st !== "running" && (
                                  <button
                                    type="button"
                                    style={btnBase}
                                    onClick={() => {
                                      const id = editingId!;
                                      apiTickTask(id)
                                        .then(() => toast.success("单步执行完成"))
                                        .catch((e: Error) => toast.error(`单步失败: ${e.message}`));
                                    }}
                                  >单步</button>
                                )}
                                {/* 报告中心 */}
                                <button
                                  type="button"
                                  style={{ ...btnBase, border: "1px solid rgba(52,211,153,0.5)", background: "rgba(52,211,153,0.08)", color: "#34d399" }}
                                  onClick={() => { setOpen(false); navigate("/reports"); }}
                                >报告中心 →</button>
                                {/* 执行/暂停/续跑 */}
                                <button
                                  type="button"
                                  style={{ ...btnBase, border: "1px solid rgba(34,211,238,0.4)", color: isPaused ? "#fde047" : "#a5f3fc" }}
                                  onClick={() => {
                                    const id = editingId!;
                                    if (isRunning) {
                                      setOrbitTasks((prev) => prev.map((t) => t.id === id ? taskWithStatus(t, "paused") : t));
                                      apiStopTask(id).catch((e: Error) => toast.error(`暂停失败: ${e.message}`));
                                    } else if (isPaused) {
                                      setOrbitTasks((prev) => prev.map((t) => t.id === id ? taskWithStatus(t, "running") : t));
                                      apiResumeTask(id, maxTicks, maxRunDurationSeconds).catch((e: Error) => toast.error(`续跑失败: ${e.message}`));
                                    } else {
                                      setOrbitTasks((prev) => prev.map((t) => t.id === id ? taskWithStatus(t, "running") : t));
                                      if (/^\d+$/.test(id)) { simulateDemoTask(id); }
                                      else apiRunTask(id, maxTicks, maxRunDurationSeconds).catch((e: Error) => toast.error(`执行失败: ${e.message}`));
                                    }
                                  }}
                                >{isRunning ? "暂停" : isPaused ? "续跑" : "执行"}</button>
                                {/* 删除 */}
                                {confirmDeleteId === editingId ? (
                                  <span style={{ display: "inline-flex", gap: 4, flex: 1 }}>
                                    <button
                                      type="button"
                                      style={{ ...btnBase, border: "1px solid rgba(248,113,113,0.8)", background: "rgba(248,113,113,0.18)", color: "#fca5a5" }}
                                      onClick={() => {
                                        const id = editingId!;
                                        setConfirmDeleteId(null);
                                        setOrbitTasks((prev) => prev.filter((t) => t.id !== id));
                                        setDeletedIds((prev) => { const next = new Set(prev); next.add(id); saveDeletedIds(next); return next; });
                                        apiDeleteTask(id).catch(() => {});
                                        setOpen(false); setEditingId(null);
                                        setTaskName(""); setTaskDesc(""); setTaskUrl("");
                                        setFormErrors({});
                                        toast.success("任务已删除");
                                      }}
                                    >确认删除</button>
                                    <button
                                      type="button"
                                      style={{ ...btnBase, border: "1px solid #475569", background: "transparent", color: "#94a3b8" }}
                                      onClick={() => setConfirmDeleteId(null)}
                                    >取消</button>
                                  </span>
                                ) : (
                                  <button
                                    type="button"
                                    style={{ ...btnBase, border: "1px solid rgba(248,113,113,0.55)", color: "rgba(248,113,113,0.95)", fontWeight: 900 }}
                                    onClick={() => setConfirmDeleteId(editingId)}
                                  >删除</button>
                                )}
                                {/* 取消（关闭弹窗） */}
                                <button
                                  type="button"
                                  style={{ ...btnBase, border: "1px solid var(--tg-panel-border)", color: "var(--tg-text-muted)" }}
                                  onClick={() => {
                                    setOpen(false); setEditingId(null);
                                    setObservationData(null); setTraceText(null);
                                    setExecutionsData(null); setExecutionsOffset(0);
                                    setTodosData(null); setTracePlanData(null); setTraceCompileData(null);
                                    setExpandedExecId(null); setConfirmDeleteId(null);
                                  }}
                                >取消</button>
                                {/* 保存修改 / 确认创建 */}
                                <button
                                  type="button"
                                  onClick={editingId ? commitTask : commitAndRunTask}
                                  style={{ ...btnBase, border: editingId ? "1px solid rgba(34,211,238,0.4)" : "1px solid rgba(34,197,94,0.6)", background: editingId ? "var(--tg-input-bg)" : "rgba(34,197,94,0.1)", color: editingId ? "#a5f3fc" : "#86efac", fontWeight: 800 }}
                                >{editingId ? "保存修改" : "创建并执行"}</button>
                              </>
                            );
                          })()}
                        </div>
                      </>
                  )}
                </div>
              </div>
            </div>
        )}
      </div>
  );
};

export default TasksPage;
