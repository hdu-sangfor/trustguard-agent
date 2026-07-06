import { useCallback, useEffect, useRef, useState } from 'react';
import {
  readStoredOrbitTasks,
  ORBIT_TASKS_UPDATED_EVENT,
  SENTINEL_ORBIT_TASKS_KEY,
  PENDING_LOG_TASK_KEY,
  type StoredOrbitTask,
} from '../orbitTasksStorage';
import { getTaskEvents, getTaskTodos, listTasks, toFrontendStatus, formatEventsAsLog, type ApiEvent, type ApiTodo } from '@/shared/lib/api';

/** Safely format a timestamp — returns HH:MM:SS or raw string on failure */
function safeTime(ts: unknown): string {
  if (!ts) return '--:--:--';
  const d = new Date(ts as string);
  if (isNaN(d.getTime())) {
    // Try epoch millis
    const n = Number(ts);
    if (!isNaN(n) && n > 1e12) return new Date(n).toLocaleTimeString('zh-CN');
    // Return raw truncated
    return String(ts).slice(0, 8);
  }
  return d.toLocaleTimeString('zh-CN');
}

/** Parse Java-style toString maps like {key=value, key2=value2} into a Record */
function parseJavaMap(raw: string): Record<string, string> | null {
  const s = raw.trim();
  if (!s.startsWith('{') || !s.endsWith('}')) return null;
  const inner = s.slice(1, -1);
  const result: Record<string, string> = {};
  // Split on ", " but respect nested braces
  let depth = 0, start = 0;
  for (let i = 0; i <= inner.length; i++) {
    const ch = inner[i];
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    else if ((ch === ',' && depth === 0) || i === inner.length) {
      const part = inner.slice(start, i).trim();
      const eq = part.indexOf('=');
      if (eq > 0) {
        result[part.slice(0, eq).trim()] = part.slice(eq + 1).trim();
      }
      start = i + 1;
    }
  }
  return Object.keys(result).length > 0 ? result : null;
}

const PH = {
  text: 'var(--tg-text)',
  screenBg: 'var(--tg-terminal-bg)',
};

const LOG_TONE = {
  accent: 'var(--tg-accent)',
  muted: 'var(--tg-text-muted)',
  faint: 'var(--tg-text-faint)',
  panel: 'var(--tg-panel-bg)',
  panelMuted: 'var(--tg-panel-muted)',
  border: 'var(--tg-panel-border)',
  success: 'var(--tg-success)',
  successSoft: 'var(--tg-success-soft)',
  warning: 'var(--tg-warning)',
  warningSoft: 'var(--tg-warning-soft)',
  danger: 'var(--tg-danger)',
  dangerSoft: 'var(--tg-danger-soft)',
};

const LOG_STATUS_STYLES: Record<string, { bg: string; border: string; text: string }> = {
  DONE:        { bg: LOG_TONE.successSoft, border: LOG_TONE.border, text: LOG_TONE.success },
  IN_PROGRESS: { bg: 'var(--tg-accent-soft)', border: LOG_TONE.border, text: LOG_TONE.accent },
  FAILED:      { bg: LOG_TONE.dangerSoft, border: LOG_TONE.border, text: LOG_TONE.danger },
  SKIPPED:     { bg: LOG_TONE.panelMuted, border: LOG_TONE.border, text: LOG_TONE.muted },
  PENDING:     { bg: LOG_TONE.warningSoft, border: LOG_TONE.border, text: LOG_TONE.warning },
};

function TabDashDivider() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [dashCount, setDashCount] = useState(48);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      const approxCharPx = 6.4;
      setDashCount(Math.max(12, Math.floor(w / approxCharPx)));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className="relative z-10 w-full min-w-0 text-center font-mono text-[10px] leading-tight pb-1 pt-0.5 select-none shrink-0 overflow-hidden whitespace-pre"
      style={{ color: LOG_TONE.faint }}
    >
      {'-'.repeat(dashCount)}
    </div>
  );
}

export function CRTerminal() {
  const [tasks, setTasks] = useState<StoredOrbitTask[]>(() => readStoredOrbitTasks());
  const [selectedId, setSelectedId] = useState<string | null>(() => readStoredOrbitTasks()[0]?.id ?? null);

  const selectedTask = tasks.find((t) => t.id === selectedId) ?? null;
  const selectedRunning = selectedTask?.status === 'running';
  const tabScrollRef = useRef<HTMLDivElement | null>(null);
  const [tabOverflow, setTabOverflow] = useState({ left: false, right: false });

  // Real-time events fetched from backend for the selected task
  const [apiEvents, setApiEvents] = useState<ApiEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const apiLogFetchRef = useRef<string | null>(null);
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());
  const backendFailCountRef = useRef(0);

  // Event filter
  const filterInputRef = useRef<HTMLInputElement | null>(null);
  const [filterText, setFilterText] = useState('');
  const filteredEvents = filterText.trim()
    ? apiEvents.filter((e) => {
        const kw = filterText.toLowerCase();
        const p = e.payload ?? {};
        return (
          (e.eventType ?? '').toLowerCase().includes(kw) ||
          (typeof p.skill_id === 'string' && p.skill_id.toLowerCase().includes(kw)) ||
          (typeof p.phase === 'string' && p.phase.toLowerCase().includes(kw)) ||
          (typeof p.message === 'string' && p.message.toLowerCase().includes(kw)) ||
          (typeof p.reasoning === 'string' && p.reasoning.toLowerCase().includes(kw)) ||
          (typeof p.summary === 'string' && p.summary.toLowerCase().includes(kw))
        );
      })
    : apiEvents;

  // Auto-scroll state
  const logBodyRef = useRef<HTMLDivElement | null>(null);
  const [userScrolledUp, setUserScrolledUp] = useState(false);
  const lastEventCountRef = useRef(0);

  // Todo list for the selected task
  const [todos, setTodos] = useState<ApiTodo[]>([]);
  const todoFetchRef = useRef<string | null>(null);

  useEffect(() => {
    if (!selectedId) { setTodos([]); return; }
    todoFetchRef.current = selectedId;
    const fetchTodos = () => {
      if (todoFetchRef.current !== selectedId) return;
      getTaskTodos(selectedId)
        .then((list) => { if (todoFetchRef.current === selectedId) setTodos(list); })
        .catch(() => setTodos([]));
    };
    fetchTodos();
    const pollTodo = () => {
      const backoff = Math.min(backendFailCountRef.current * 3000, 26000);
      return window.setTimeout(() => {
        fetchTodos();
        todoIv = pollTodo();
      }, 5000 + backoff);
    };
    let todoIv = pollTodo();
    return () => { window.clearTimeout(todoIv); todoFetchRef.current = null; };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) { setApiEvents([]); setEventsLoading(false); return; }
    apiLogFetchRef.current = selectedId;
    setEventsLoading(true);

    const basePollMs = selectedRunning ? 2000 : 4000;
    const fetchEvents = () => {
      if (apiLogFetchRef.current !== selectedId) return;
      getTaskEvents(selectedId)
        .then((evts) => {
          backendFailCountRef.current = 0;
          if (apiLogFetchRef.current === selectedId) {
            setApiEvents(evts);
            setEventsLoading(false);
          }
        })
        .catch(() => {
          backendFailCountRef.current = Math.min(backendFailCountRef.current + 1, 10);
          if (apiLogFetchRef.current === selectedId) setEventsLoading(false);
        });
    };

    fetchEvents();
    // Back off polling when backend is unreachable (up to 30s)
    const poll = () => {
      const backoff = Math.min(backendFailCountRef.current * 3000, 26000);
      return window.setTimeout(() => {
        fetchEvents();
        ivRef = poll();
      }, basePollMs + backoff);
    };
    let ivRef = poll();
    return () => {
      window.clearTimeout(ivRef);
      apiLogFetchRef.current = null;
    };
  }, [selectedId, selectedRunning]);

  // Auto-scroll to bottom when new events arrive (unless user scrolled up)
  useEffect(() => {
    const el = logBodyRef.current;
    if (!el) return;
    const newCount = apiEvents.length;
    if (newCount > lastEventCountRef.current && !userScrolledUp) {
      el.scrollTop = el.scrollHeight;
    }
    lastEventCountRef.current = newCount;
  }, [apiEvents, userScrolledUp]);

  // Reset scroll + filter + expanded state when task changes
  useEffect(() => {
    setUserScrolledUp(false);
    setFilterText('');
    setExpandedKeys(new Set());
    lastEventCountRef.current = 0;
    const el = logBodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [selectedId]);

  const handleLogScroll = useCallback(() => {
    const el = logBodyRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setUserScrolledUp(distFromBottom > 60);
  }, []);

  // Ctrl+F focuses the filter input; ArrowLeft/Right cycles tabs
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        const fi = filterInputRef.current;
        if (!fi) return;
        e.preventDefault();
        fi.focus();
        fi.select();
        return;
      }
      // Skip if focus is inside an input/textarea
      const tag = (document.activeElement as HTMLElement | null)?.tagName ?? '';
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        setSelectedId((cur) => {
          if (tasks.length === 0) return cur;
          const idx = tasks.findIndex((t) => t.id === cur);
          const next = e.key === 'ArrowLeft'
            ? (idx <= 0 ? tasks.length - 1 : idx - 1)
            : (idx < 0 || idx >= tasks.length - 1 ? 0 : idx + 1);
          const newId = tasks[next]?.id ?? cur;
          // Scroll the new tab into view
          window.requestAnimationFrame(() => {
            const el = tabScrollRef.current;
            if (!el) return;
            const btn = el.querySelector<HTMLButtonElement>(`[data-task-id="${newId}"]`);
            btn?.scrollIntoView({ block: 'nearest', inline: 'center' });
          });
          return newId;
        });
        e.preventDefault();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [tasks]);

  const exportEvents = useCallback(() => {
    if (!apiEvents.length) return;
    const t = selectedTask;
    const header = [
      `# 事件日志导出 — ${t?.name ?? selectedId ?? '未知任务'}`,
      `# 导出时间: ${new Date().toLocaleString('zh-CN')}`,
      `# 事件数量: ${apiEvents.length}`,
      '',
    ];
    const lines = [...header, ...formatEventsAsLog(apiEvents)];
    const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `events_${(t?.name ?? selectedId ?? 'task').replace(/[\s/\\]/g, '_')}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }, [apiEvents, selectedTask, selectedId]);

  const refreshTasks = useCallback(() => {
    const list = readStoredOrbitTasks();
    setTasks(list);
    setSelectedId((cur) => (cur && list.some((t) => t.id === cur) ? cur : list[0]?.id ?? null));
  }, []);

  useEffect(() => {
    refreshTasks();
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || e.key === SENTINEL_ORBIT_TASKS_KEY) refreshTasks();
    };
    window.addEventListener('storage', onStorage);
    window.addEventListener(ORBIT_TASKS_UPDATED_EVENT, refreshTasks);
    const iv = window.setInterval(refreshTasks, 1200);
    return () => {
      window.removeEventListener('storage', onStorage);
      window.removeEventListener(ORBIT_TASKS_UPDATED_EVENT, refreshTasks);
      window.clearInterval(iv);
    };
  }, [refreshTasks]);

  // On mount: always sync task list from backend, then handle URL/pending deep-link.
  useEffect(() => {
    const urlTaskId = new URLSearchParams(window.location.search).get('taskId');
    const pending = urlTaskId ?? localStorage.getItem(PENDING_LOG_TASK_KEY);
    if (pending && !urlTaskId) localStorage.removeItem(PENDING_LOG_TASK_KEY);

    // Always fetch from backend on mount to hydrate real tasks into localStorage
    listTasks()
      .then((apiTasks) => {
        if (apiTasks.length === 0) return;
        const existing = readStoredOrbitTasks();
        const existingIds = new Set(existing.map((t) => t.id));
        // Drop demo seeds (numeric-only IDs) when we have real backend tasks
        const merged = [
          ...existing.filter((t) => !/^\d+$/.test(t.id)),
          ...apiTasks
            .filter((at) => !existingIds.has(at.taskId))
            .map((at) => ({
              id: at.taskId,
              name: at.name ?? '未命名任务',
              desc: at.description ?? '',
              url: at.target ?? '',
              log: '',
              createdAt: new Date(at.createdAt).getTime() || Date.now(),
              updatedAt: at.updatedAt ? new Date(at.updatedAt).getTime() || undefined : undefined,
              status: toFrontendStatus(at.status),
              currentPhase: at.currentPhase,
            } satisfies StoredOrbitTask)),
        ];
        try {
          localStorage.setItem(SENTINEL_ORBIT_TASKS_KEY, JSON.stringify(merged));
          window.dispatchEvent(new Event(ORBIT_TASKS_UPDATED_EVENT));
        } catch { /* quota */ }
        // Refresh local state
        const refreshed = readStoredOrbitTasks();
        setTasks(refreshed);
        // Select deep-link target or first real task
        const target = pending ?? refreshed[0]?.id ?? null;
        if (target) setSelectedId(target);
      })
      .catch(() => { /* backend unavailable — keep localStorage tasks */ });

    if (pending) setSelectedId(pending);

    // Scroll the tab into view after the tab row renders
    const t = window.setTimeout(() => {
      const el = tabScrollRef.current;
      if (!el || !pending) return;
      const btn = el.querySelector<HTMLButtonElement>(`[data-task-id="${pending}"]`);
      btn?.scrollIntoView({ block: 'nearest', inline: 'center' });
    }, 200);
    return () => window.clearTimeout(t);
  }, []);

  const updateTabArrows = useCallback(() => {
    const el = tabScrollRef.current;
    if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    if (max <= 2) {
      setTabOverflow({ left: false, right: false });
      return;
    }
    setTabOverflow({
      left: el.scrollLeft > 4,
      right: el.scrollLeft < max - 4,
    });
  }, []);

  useEffect(() => {
    updateTabArrows();
    const el = tabScrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => updateTabArrows());
    ro.observe(el);
    return () => ro.disconnect();
  }, [tasks, updateTabArrows]);

  const onTabWheel = useCallback(
    (e: React.WheelEvent) => {
      const el = tabScrollRef.current;
      if (!el) return;
      if (el.scrollWidth <= el.clientWidth + 2) return;
      e.preventDefault();
      el.scrollLeft += e.deltaY;
      requestAnimationFrame(updateTabArrows);
    },
    [updateTabArrows]
  );

  const crtCss = `
    .crt-shell {
      background: var(--tg-panel-bg) !important;
      border: 1px solid var(--tg-panel-border) !important;
      border-radius: 8px !important;
      box-shadow: var(--tg-shadow) !important;
    }
    .crt-screen-bezel,
    .crt-readable,
    .crt-container,
    .crt-log-body {
      background: var(--tg-terminal-bg) !important;
      color: var(--tg-text) !important;
      box-shadow: none !important;
    }
    .crt-screen-bezel {
      border: 1px solid var(--tg-panel-border) !important;
      border-radius: 6px !important;
    }
    .crt-readable {
      --log-accent: var(--tg-accent);
      --log-muted: var(--tg-text-muted);
      --log-success: var(--tg-success);
      --log-warning: var(--tg-warning);
      --log-danger: var(--tg-danger);
    }
    .scanline-effect { display: none !important; }
    .crt-curvature {
      box-shadow: none;
    }
    .crt-vignette {
      display: none !important;
    }
    .crt-container::before {
      display: none !important;
    }
    .crt-container::after {
      display: none !important;
    }
    .crt-text-chromatic {
      text-shadow: none !important;
      animation: none !important;
    }
    .crt-glitch-layer {
      animation: none !important;
      transform: none !important;
      filter: none !important;
      clip-path: none !important;
    }
    .crt-log-tabs { scrollbar-width: thin; scrollbar-color: var(--tg-panel-border) transparent; }
    .crt-log-tabs::-webkit-scrollbar { height: 6px; }
    .crt-log-tabs::-webkit-scrollbar-thumb { background: var(--tg-panel-border); border-radius: 999px; }
    .crt-log-body { scrollbar-width: thin; scrollbar-color: var(--tg-panel-border) var(--tg-panel-muted); }
    .crt-log-body::-webkit-scrollbar { width: 8px; }
    .crt-log-body::-webkit-scrollbar-track { background: var(--tg-panel-muted); border-radius: 999px; }
    .crt-log-body::-webkit-scrollbar-thumb { background: var(--tg-panel-border); border-radius: 999px; }
    .crt-readable .crt-container::before,
    .crt-readable .crt-container::after,
    .crt-readable .crt-tab-scanlines,
    .crt-readable .scanline-effect,
    .crt-readable .crt-vignette {
      display: none !important;
    }
    .crt-readable .crt-glitch-layer,
    .crt-readable .crt-text-chromatic {
      animation: none !important;
      transform: none !important;
      filter: none !important;
      clip-path: none !important;
      text-shadow: none !important;
    }
    .crt-readable .crt-curvature {
      box-shadow: none;
    }
    .crt-log-line {
      border-left: 2px solid var(--tg-panel-border);
      background: var(--tg-panel-muted);
      border-radius: 5px;
      margin: 2px 0;
    }
    .crt-readable .crt-log-line {
      border-left-color: var(--tg-panel-border);
      background: var(--tg-panel-muted);
    }
    .crt-readable [class*="text-sky-"],
    .crt-readable [class*="text-cyan-"],
    .crt-readable [class*="text-indigo-"],
    .crt-readable [class*="text-violet-"],
    .crt-readable [class*="text-purple-"] {
      color: var(--log-accent) !important;
    }
    .crt-readable [class*="text-slate-"] {
      color: var(--log-muted) !important;
    }
    .crt-readable [class*="text-green-"],
    .crt-readable [class*="text-emerald-"] {
      color: var(--log-success) !important;
    }
    .crt-readable [class*="text-yellow-"],
    .crt-readable [class*="text-amber-"],
    .crt-readable [class*="text-orange-"] {
      color: var(--log-warning) !important;
    }
    .crt-readable [class*="text-red-"],
    .crt-readable [class*="text-rose-"] {
      color: var(--log-danger) !important;
    }
    .crt-readable [class*="bg-sky-"],
    .crt-readable [class*="bg-cyan-"],
    .crt-readable [class*="bg-indigo-"],
    .crt-readable [class*="bg-violet-"],
    .crt-readable [class*="bg-black"] {
      background: var(--tg-panel-muted) !important;
    }
    .crt-readable [class*="border-sky-"],
    .crt-readable [class*="border-cyan-"],
    .crt-readable [class*="border-indigo-"],
    .crt-readable [class*="border-violet-"],
    .crt-readable [class*="border-green-"],
    .crt-readable [class*="border-emerald-"],
    .crt-readable [class*="border-red-"] {
      border-color: var(--tg-panel-border) !important;
    }
    .crt-readable button {
      color: var(--tg-text) !important;
    }
    .crt-readable .log-muted-text {
      color: var(--tg-text-muted) !important;
    }
    .crt-readable .log-faint-text {
      color: var(--tg-text-faint) !important;
    }
    .crt-readable .log-accent-text {
      color: var(--tg-accent) !important;
    }
    .crt-readable .log-success-text {
      color: var(--tg-success) !important;
    }
    .crt-readable .log-warning-text {
      color: var(--tg-warning) !important;
    }
    .crt-readable .log-danger-text {
      color: var(--tg-danger) !important;
    }
    .crt-readable .log-soft-panel {
      background: var(--tg-panel-muted) !important;
      border-color: var(--tg-panel-border) !important;
    }
    .crt-readable .log-divider {
      background: var(--tg-panel-border) !important;
    }
    .crt-readable .log-hover-row:hover {
      background: var(--tg-hover-bg) !important;
    }
    .crt-tab-strip {
      background: var(--tg-panel-bg);
      border-bottom: 1px solid var(--tg-panel-border);
    }
    .crt-tab-scanlines {
      display: none !important;
    }
  `;

  const tabRow = (
    <>
      <div className="crt-tab-strip flex flex-col shrink-0 min-w-0 min-h-0">
        <div className="relative shrink-0 min-w-0 overflow-hidden rounded-md">
          <div className="relative z-10 flex items-center gap-1 px-1 pt-1 pb-0.5 shrink-0 min-w-0">
        <span
          className="shrink-0 text-xs font-mono w-4 text-center select-none"
          style={{ color: "var(--tg-text-muted)", visibility: tabOverflow.left ? 'visible' : 'hidden' }}
          aria-hidden
        >
          {'\u25C0'}
        </span>
        <div
          ref={tabScrollRef}
          onWheel={onTabWheel}
          onScroll={updateTabArrows}
          className="crt-log-tabs flex flex-1 gap-1.5 overflow-x-auto overflow-y-hidden min-w-0 py-1"
        >
          {tasks.length === 0 ? (
            <span className="text-xs font-mono px-2 py-1 whitespace-nowrap" style={{ color: "var(--tg-text-muted)" }}>
              （在任务页创建任务后显示于此）
            </span>
          ) : (
            tasks.map((t) => {
              const sel = t.id === selectedId;
              const dotColor =
                t.status === 'running'  ? 'var(--log-success)' :
                t.status === 'finished' ? 'var(--log-success)' :
                t.status === 'paused'   ? 'var(--log-warning)' :
                t.status === 'failed'   ? 'var(--log-danger)' :
                                          'var(--tg-text-faint)';
              return (
                <button
                  key={t.id}
                  type="button"
                  data-task-id={t.id}
                  onClick={() => setSelectedId(t.id)}
                  className="shrink-0 px-3 py-1.5 rounded-md text-xs font-mono whitespace-nowrap border flex items-center gap-1.5"
                  style={{
                    borderColor: sel ? 'var(--tg-accent)' : 'var(--tg-panel-border)',
                    background: sel ? 'var(--tg-accent-soft)' : 'var(--tg-panel-bg)',
                    color: sel ? 'var(--tg-accent)' : 'var(--tg-text-muted)',
                    boxShadow: 'none',
                  }}
                  title={`${t.name} · ${t.status}${t.currentPhase ? ' · ' + t.currentPhase : ''}`}
                >
                  <span style={{
                    width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                    background: dotColor,
                    boxShadow: 'none',
                  }} />
                  {t.name}
                  {sel && t.currentPhase && (
                    <span style={{
                      fontSize: 8, fontWeight: 700, letterSpacing: '0.06em',
                      padding: '1px 5px', borderRadius: 3,
                      background: 'var(--tg-panel-muted)',
                      border: '1px solid var(--tg-panel-border)',
                      color: 'var(--tg-accent)',
                      marginLeft: 2,
                    }}>{t.currentPhase}</span>
                  )}
                </button>
              );
            })
          )}
        </div>
        <span
          className="shrink-0 text-xs font-mono w-4 text-center select-none"
          style={{ color: "var(--tg-text-muted)", visibility: tabOverflow.right ? 'visible' : 'hidden' }}
          aria-hidden
        >
          {'\u25B6'}
        </span>
          </div>
        </div>
        <TabDashDivider />
      </div>
    </>
  );

  return (
    <div
      className="absolute inset-0 flex items-center justify-center pointer-events-none p-3 md:p-6"
    >
      <div
        className="crt-shell relative flex flex-col shrink-0 pointer-events-auto p-2 md:p-3 max-w-[100vw] max-h-[100dvh] box-border"
        style={{ width: "min(1180px, 96vw)", height: "calc(100dvh - 112px)" }}
      >
        <div className="crt-screen-bezel crt-readable flex flex-col flex-1 min-h-0 p-1 md:p-2 overflow-hidden">
          {tabRow}

          {/* Todo strip — shows per-phase execution status */}
          {todos.length > 0 && (
            <div className="shrink-0 flex flex-wrap gap-1 px-2 pb-1 min-w-0">
              {todos.map((td) => {
                const c = LOG_STATUS_STYLES[td.status] ?? LOG_STATUS_STYLES.PENDING;
                return (
                  <span
                    key={td.todoId}
                    title={td.description}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 4,
                      padding: '2px 7px', borderRadius: 4, fontSize: 10,
                      fontFamily: 'monospace', fontWeight: 600, letterSpacing: '0.04em',
                      background: c.bg, border: `1px solid ${c.border}`, color: c.text,
                      cursor: 'default',
                    }}
                  >
                    <span style={{ opacity: 0.75, fontSize: 9 }}>{td.phase}</span>
                    <span>{td.name}</span>
                    <span style={{ opacity: 0.65, fontSize: 9 }}>{td.status}</span>
                  </span>
                );
              })}
            </div>
          )}

          <div
            className={`flex-1 min-h-0 relative crt-curvature rounded-[1.5rem] overflow-hidden`}
            style={{ background: PH.screenBg }}
          >
            <div className="absolute inset-0 crt-glitch-layer crt-container">
              <style>{crtCss}</style>
              <div className="absolute inset-0 z-10 flex flex-col min-h-0 p-3 md:p-5 pointer-events-none">
                {/* Offline export button — shown when no API events but local log exists */}
                {apiEvents.length === 0 && !eventsLoading && (() => {
                  const t = selectedId ? tasks.find((x) => x.id === selectedId) : null;
                  const log = t?.log ?? '';
                  if (!log.trim()) return null;
                  return (
                    <div className="shrink-0 flex items-center justify-end gap-2 mb-1.5">
                      <span className="text-[9px] font-mono" style={{ color: LOG_TONE.faint }}>本地日志</span>
                      <button
                        type="button"
                        title="导出本地日志"
                        onClick={() => {
                          const blob = new Blob([log], { type: 'text/plain;charset=utf-8' });
                          const url = URL.createObjectURL(blob);
                          const a = document.createElement('a');
                          a.href = url;
                          a.download = `log_${(t?.name ?? selectedId ?? 'task').replace(/[\s/\\]/g, '_')}.txt`;
                          a.click();
                          URL.revokeObjectURL(url);
                        }}
                        className="text-[9px] font-mono shrink-0 px-1.5 py-0.5 rounded"
                        style={{
                          background: LOG_TONE.panel, border: `1px solid ${LOG_TONE.border}`,
                          color: LOG_TONE.accent, cursor: 'pointer',
                        }}
                      >↓</button>
                    </div>
                  );
                })()}
                {/* Filter bar + event count */}
                <div className="shrink-0 flex items-center gap-2 mb-1.5 pointer-events-auto">
                  {apiEvents.length > 0 && (
                    <>
                    <input
                      ref={filterInputRef}
                      type="text"
                      value={filterText}
                      onChange={(e) => setFilterText(e.target.value)}
                      placeholder="筛选… (Ctrl+F)"
                      className="flex-1 min-w-0 text-[10px] font-mono rounded px-2 py-0.5 outline-none"
                      style={{
                        background: 'var(--tg-input-bg)', border: `1px solid ${LOG_TONE.border}`,
                        color: PH.text, maxWidth: 160,
                      }}
                    />
                    <span className="text-[9px] font-mono shrink-0" style={{ color: LOG_TONE.muted }}>
                      {filterText ? `${filteredEvents.length} / ${apiEvents.length}` : `${apiEvents.length} 条`}
                    </span>
                    {filterText && (
                      <button type="button" onClick={() => setFilterText('')}
                        className="text-[9px] font-mono shrink-0"
                        style={{ background: 'none', border: 'none', color: LOG_TONE.accent, cursor: 'pointer', padding: 0 }}>
                        ✕
                      </button>
                    )}
                    </>
                  )}
                    {/* Follow-log toggle */}
                  {apiEvents.length > 0 && (
                    <>
                    <button
                      type="button"
                      title={userScrolledUp ? "点击跟踪最新日志" : "已跟踪最新日志"}
                      onClick={() => {
                        if (userScrolledUp) {
                          const el = logBodyRef.current;
                          if (el) el.scrollTop = el.scrollHeight;
                          setUserScrolledUp(false);
                        }
                      }}
                      className="text-[9px] font-mono shrink-0 px-1.5 py-0.5 rounded"
                      style={{
                        background: userScrolledUp ? LOG_TONE.warningSoft : LOG_TONE.successSoft,
                        border: `1px solid ${LOG_TONE.border}`,
                        color: userScrolledUp ? LOG_TONE.warning : LOG_TONE.success,
                        cursor: userScrolledUp ? 'pointer' : 'default',
                      }}
                    >
                      {userScrolledUp ? '⏸ 已暂停' : '● 跟踪中'}
                    </button>
                    <button
                      type="button"
                      onClick={exportEvents}
                      title="导出日志"
                      className="text-[9px] font-mono shrink-0 px-1.5 py-0.5 rounded"
                      style={{
                        background: LOG_TONE.panel, border: `1px solid ${LOG_TONE.border}`,
                        color: LOG_TONE.accent, cursor: 'pointer',
                      }}
                    >
                      ↓
                    </button>
                    </>
                  )}
                </div>
                {/* Scroll-to-bottom button */}
                {userScrolledUp && (
                  <div className="absolute bottom-6 right-6 z-50 pointer-events-auto">
                    <button
                      type="button"
                      onClick={() => {
                        const el = logBodyRef.current;
                        if (el) el.scrollTop = el.scrollHeight;
                        setUserScrolledUp(false);
                      }}
                      className="flex items-center gap-1 px-3 py-1 rounded-md text-[10px] font-mono font-bold"
                      style={{
                        background: 'var(--tg-accent-soft)',
                        border: `1px solid ${LOG_TONE.border}`,
                        color: LOG_TONE.accent,
                      }}
                    >
                      ↓ 最新
                    </button>
                  </div>
                )}
                <div
                  ref={logBodyRef}
                  onScroll={handleLogScroll}
                  className="crt-log-body flex-1 min-h-0 w-full overflow-y-auto overflow-x-hidden overscroll-contain font-mono text-xs pointer-events-auto select-text"
                  style={{ color: PH.text }}
                >
                  {apiEvents.length === 0 ? (
                    (() => {
                      if (eventsLoading) {
                        return (
                          <div className="flex items-center gap-2 log-muted-text text-xs font-mono px-1 py-2">
                            <span>▋</span>
                            <span>正在获取日志…</span>
                          </div>
                        );
                      }
                      const t = selectedId ? tasks.find((x) => x.id === selectedId) : null;
                      const fallback = t?.log ?? '';
                      if (!fallback.trim()) {
                        return (
                          <div className="log-muted-text text-xs font-mono px-1 py-2">（暂无日志）</div>
                        );
                      }
                      // Parse offline log lines: [timestamp] [MODULE] message
                      const MODULE_COLORS: Record<string, { label: string; color: string }> = {
                        'ORCHESTRATOR': { label: 'ORCH', color: LOG_TONE.accent },
                        'EXECUTOR':     { label: 'EXEC', color: LOG_TONE.accent },
                        'REPORT':       { label: 'RPT',  color: LOG_TONE.success },
                        'TASK':         { label: 'TASK', color: LOG_TONE.muted },
                        'LLM':          { label: 'LLM',  color: LOG_TONE.accent },
                        'ERROR':        { label: 'ERR',  color: LOG_TONE.danger },
                        'WARN':         { label: 'WARN', color: LOG_TONE.warning },
                      };
                      const lineRe = /^\[([^\]]+)\]\s+\[([^\]]+)\]\s+(.*)/;
                      const logLines = fallback.split('\n');
                      return (
                        <div className="w-full max-w-[96%] mx-auto pb-4">
                          {logLines.map((line, li) => {
                            const m = line.match(lineRe);
                            if (!m) {
                              return line.trim() ? (
                                <div key={li} className="crt-log-line flex gap-2 items-start px-2 py-1">
                                  <span className="break-words whitespace-pre-wrap leading-relaxed">{line}</span>
                                </div>
                              ) : null;
                            }
                            const ts = m[1]!;
                            const mod = m[2]!.toUpperCase().split('-')[0]!;
                            const msg = m[3]!;
                            const mc = MODULE_COLORS[mod];
                            return (
                              <div key={li} className="crt-log-line flex gap-2 items-start px-2 py-1">
                                <span className="shrink-0 log-muted-text text-[10px] w-32 overflow-hidden leading-relaxed">{ts}</span>
                                <span
                                  className="shrink-0 text-[10px] font-bold px-1 rounded leading-relaxed"
                                  style={{
                                    color: mc?.color ?? LOG_TONE.muted,
                                    background: LOG_TONE.panelMuted,
                                    border: `1px solid ${LOG_TONE.border}`,
                                    minWidth: 38,
                                    textAlign: 'center',
                                  }}
                                >{mc?.label ?? mod.slice(0, 5)}</span>
                                <span className="text-[11px] break-words whitespace-pre-wrap leading-relaxed">{msg}</span>
                              </div>
                            );
                          })}
                        </div>
                      );
                    })()
                  ) : filteredEvents.length === 0 ? (
                    <div className="log-muted-text text-[10px] font-mono px-1 py-2">
                      无匹配事件
                    </div>
                  ) : (
                    <div className="w-full max-w-[96%] mx-auto pb-4">
                      {(() => {
                        const nodes: React.ReactNode[] = [];
                        let lastPhase = '';
                        filteredEvents.forEach((e, idx) => {
                          // payload may be a JSON string or Java toString map — parse it
                          let p: Record<string, unknown> = {};
                          if (typeof e.payload === 'string') {
                            try { p = JSON.parse(e.payload); } catch {
                              const jm = parseJavaMap(e.payload);
                              p = jm ?? { raw: e.payload };
                            }
                          } else if (e.payload && typeof e.payload === 'object') {
                            p = e.payload as Record<string, unknown>;
                          }
                          const ts = safeTime(e.timestamp);
                          const type = e.eventType ?? '';
                          const phaseVal = typeof p.phase === 'string' ? p.phase : '';

                          // Phase section divider
                          if (type === 'PHASE_START' && phaseVal && phaseVal !== lastPhase) {
                            lastPhase = phaseVal;
                            nodes.push(
                              <div key={`ph-${idx}`} className="my-2 flex items-center gap-2">
                                <div className="flex-1 h-px log-divider" />
                                <span className="log-accent-text text-[10px] font-bold tracking-widest px-2 py-0.5 rounded border log-soft-panel">
                                  {phaseVal}
                                </span>
                                <div className="flex-1 h-px log-divider" />
                              </div>
                            );
                          }

                          // Skill events — expandable
                          if (type === 'SKILL_COMPLETED' || type === 'SKILL_INVOKED') {
                            const skillId = typeof p.skill_id === 'string' ? p.skill_id : '?';
                            const key = `ev-${idx}`;
                            const expanded = expandedKeys.has(key);
                            const ok = p.success !== false;
                            const summary = typeof p.summary === 'string' ? p.summary : '';
                            const ref = typeof p.artifact_ref === 'string' ? p.artifact_ref : '';
                            nodes.push(
                              <div key={key} className="my-0.5">
                                <button
                                  type="button"
                                  className="crt-log-line w-full text-left flex items-start gap-2 rounded px-2 py-1"
                                  onClick={() => {
                                    setExpandedKeys((prev) => {
                                      const next = new Set(prev);
                                      if (next.has(key)) { next.delete(key); } else { next.add(key); }
                                      return next;
                                    });
                                  }}
                                >
                                  <span className="log-muted-text shrink-0 w-16">{ts}</span>
                                  <span className={`shrink-0 w-3 ${ok ? 'log-success-text' : 'log-danger-text'}`}>{type === 'SKILL_COMPLETED' ? (ok ? '✓' : '✗') : '▶'}</span>
                                  <span className="log-accent-text font-bold break-words">{skillId}</span>
                                  {ref && <span className="log-faint-text text-[9px] ml-1">[{ref.slice(0, 32)}…]</span>}
                                  <span className="ml-auto log-faint-text text-[9px]">{expanded ? '▲' : '▼'}</span>
                                </button>
                                {expanded && summary && (
                                  <div className="ml-7 mt-1 mb-2 p-2 rounded border log-soft-panel text-[11px] whitespace-pre-wrap break-words leading-relaxed">
                                    {summary}
                                  </div>
                                )}
                              </div>
                            );
                          } else if (type === 'DECISION') {
                            const action = typeof p.action_type === 'string' ? p.action_type : '';
                            const skill = typeof p.skill_id === 'string' ? `[${p.skill_id}]` : '';
                            const reason = typeof p.reasoning === 'string' ? p.reasoning.slice(0, 200) : '';
                            const key = `ev-${idx}`;
                            const expanded = expandedKeys.has(key);
                            nodes.push(
                              <div key={key} className="my-0.5">
                                <button
                                  type="button"
                                  className="crt-log-line w-full text-left flex items-start gap-2 rounded px-2 py-1"
                                  onClick={() => {
                                    if (!reason) return;
                                    setExpandedKeys((prev) => {
                                      const next = new Set(prev);
                                      if (next.has(key)) { next.delete(key); } else { next.add(key); }
                                      return next;
                                    });
                                  }}
                                >
                                  <span className="log-muted-text shrink-0 w-16">{ts}</span>
                                  <span className="log-accent-text shrink-0">LLM</span>
                                  <span className="log-accent-text">{action}</span>
                                  {skill && <span className="log-muted-text">{skill}</span>}
                                  {reason && <span className="ml-auto log-faint-text text-[9px]">{expanded ? '▲' : '▼'}</span>}
                                </button>
                                {expanded && reason && (
                                  <div className="ml-7 mt-1 mb-2 p-2 rounded border log-soft-panel text-[11px] whitespace-pre-wrap leading-relaxed">
                                    {reason}
                                  </div>
                                )}
                              </div>
                            );
                          } else if (type === 'PHASE_COMPLETE') {
                            const ph = typeof p.phase === 'string' ? p.phase : phaseVal;
                            nodes.push(
                              <div key={`ev-${idx}`} className="my-1.5 flex items-center gap-2">
                                <div className="flex-1 h-px log-divider" />
                                <span className="log-success-text text-[10px] font-bold tracking-widest px-2 py-0.5 rounded border log-soft-panel">
                                  ✓ {ph} 完成
                                </span>
                                <div className="flex-1 h-px log-divider" />
                              </div>
                            );
                          } else if (type === 'TASK_STARTED' || type === 'TASK_RUN_STARTED') {
                            nodes.push(
                              <div key={`ev-${idx}`} className="my-2 flex items-center gap-2">
                                <div className="flex-1 h-px log-divider" />
                                <span className="log-accent-text text-[10px] font-bold tracking-widest px-2 py-0.5 rounded border log-soft-panel">
                                  ▶ 任务启动 {ts}
                                </span>
                                <div className="flex-1 h-px log-divider" />
                              </div>
                            );
                          } else if (type === 'TASK_COMPLETED' || type === 'TASK_DONE') {
                            nodes.push(
                              <div key={`ev-${idx}`} className="my-2 flex items-center gap-2">
                                <div className="flex-1 h-px log-divider" />
                                <span className="log-success-text text-[10px] font-bold tracking-widest px-2 py-0.5 rounded border log-soft-panel">
                                  ✓ 任务完成 {ts}
                                </span>
                                <div className="flex-1 h-px log-divider" />
                              </div>
                            );
                          } else if (type === 'TASK_FAILED' || type === 'TASK_ERROR') {
                            const msg = typeof p.message === 'string' ? p.message : typeof p.error === 'string' ? p.error : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="my-2">
                                <div className="flex items-center gap-2">
                                  <div className="flex-1 h-px log-divider" />
                                  <span className="log-danger-text text-[10px] font-bold tracking-widest px-2 py-0.5 rounded border log-soft-panel">
                                    ✗ 任务失败 {ts}
                                  </span>
                                  <div className="flex-1 h-px log-divider" />
                                </div>
                                {msg && <div className="mt-1 px-3 py-1.5 rounded border log-soft-panel text-[10px] break-all">{msg}</div>}
                              </div>
                            );
                          } else if (type === 'ERROR' || type === 'EXECUTION_ERROR' || type === 'SKILL_ERROR' || type === 'SKILL_TIMEOUT') {
                            const msg = typeof p.message === 'string' ? p.message : typeof p.error === 'string' ? p.error : JSON.stringify(p).slice(0, 120);
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-start px-1 py-0.5">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-danger-text shrink-0 font-bold">✗</span>
                                <span className="log-danger-text shrink-0">{type}</span>
                                <span className="log-danger-text break-all">{msg}</span>
                              </div>
                            );
                          } else if (type.startsWith('PLAN_ITEM')) {
                            const name = typeof p.name === 'string' ? p.name : typeof p.item_id === 'string' ? p.item_id : '';
                            const status = typeof p.status === 'string' ? p.status : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-start px-1 py-0.5 opacity-60">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-accent-text shrink-0 text-[9px]">PLAN</span>
                                <span className="log-muted-text text-[10px]">{name}</span>
                                {status && <span className="log-faint-text text-[9px] ml-auto shrink-0">{status}</span>}
                              </div>
                            );
                          } else if (type === 'TASK_PAUSED') {
                            const reason = typeof p.reason === 'string' ? p.reason : typeof p.message === 'string' ? p.message : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-start px-1 py-0.5">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-warning-text shrink-0 font-bold">⏸</span>
                                <span className="log-warning-text shrink-0 font-semibold">PAUSED</span>
                                {reason && <span className="log-warning-text break-all">{reason}</span>}
                              </div>
                            );
                          } else if (type === 'TASK_RESUMED') {
                            const reason = typeof p.reason === 'string' ? p.reason : typeof p.message === 'string' ? p.message : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-start px-1 py-0.5">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-success-text shrink-0 font-bold">▶</span>
                                <span className="log-success-text shrink-0 font-semibold">RESUMED</span>
                                {reason && <span className="log-success-text break-all">{reason}</span>}
                              </div>
                            );
                          } else if (type === 'TICK_STARTED') {
                            const tickNum = typeof p.tick === 'number' ? p.tick : typeof p.tick_id === 'string' ? p.tick_id : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-start px-1 py-0.5 opacity-40">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-accent-text shrink-0 text-[9px]">TICK</span>
                                <span className="log-muted-text text-[10px]">{tickNum !== '' ? `#${tickNum}` : ''} started</span>
                              </div>
                            );
                          } else if (type === 'TICK_COMPLETED') {
                            const tickNum = typeof p.tick === 'number' ? p.tick : typeof p.tick_id === 'string' ? p.tick_id : '';
                            const dur = typeof p.duration_ms === 'number' ? `${p.duration_ms}ms` : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-start px-1 py-0.5 opacity-40">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-accent-text shrink-0 text-[9px]">TICK</span>
                                <span className="log-muted-text text-[10px]">{tickNum !== '' ? `#${tickNum}` : ''} done {dur && <span className="log-faint-text">({dur})</span>}</span>
                              </div>
                            );
                          } else if (type === 'PHASE_END') {
                            const phase = typeof p.phase === 'string' ? p.phase : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-center px-1 py-0.5 opacity-70">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-success-text shrink-0">◀</span>
                                <span className="log-success-text shrink-0 font-semibold text-[11px]">阶段结束</span>
                                {phase && <span className="log-muted-text text-[10px] uppercase tracking-wider">{phase}</span>}
                              </div>
                            );
                          } else if (type === 'PLAN_LIST_DECISION') {
                            const skill = typeof p.selected_skill === 'string' ? p.selected_skill : typeof p.skill_id === 'string' ? p.skill_id : '';
                            const reasoning = typeof p.reasoning === 'string' ? p.reasoning.trim().slice(0, 200) : '';
                            const intent = typeof p.intent === 'string' ? p.intent.trim().slice(0, 120) : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex flex-col gap-0.5 px-1 py-1 border-l-2 ml-1 mb-0.5" style={{ borderColor: LOG_TONE.border }}>
                                <div className="flex gap-2 items-center">
                                  <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                  <span className="log-accent-text shrink-0 font-bold text-[10px]">DECISION</span>
                                  {skill && <span className="log-accent-text font-semibold">{skill}</span>}
                                </div>
                                {intent && <div className="pl-16 log-muted-text text-[10px] italic">{intent}</div>}
                                {reasoning && <div className="pl-16 log-faint-text text-[9px] break-all">{reasoning}{reasoning.length >= 200 ? '…' : ''}</div>}
                              </div>
                            );
                          } else if (type === 'FRAMEWORK_TARGET_SET' || type === 'FRAMEWORK_TARGET_UPGRADED') {
                            const fw = typeof p.framework === 'string' ? p.framework : typeof p.target_framework === 'string' ? p.target_framework : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-start px-1 py-0.5 opacity-80">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-warning-text shrink-0 text-[9px] font-bold">FRAMEWORK</span>
                                <span className="log-warning-text font-semibold">{fw || '?'}</span>
                                {type === 'FRAMEWORK_TARGET_UPGRADED' && <span className="log-muted-text text-[9px]">↑ upgraded</span>}
                              </div>
                            );
                          } else if (type === 'MEMORY_FACT_ADDED') {
                            const key = typeof p.key === 'string' ? p.key : '';
                            const val = typeof p.value === 'string' ? p.value.slice(0, 80) : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-start px-1 py-0.5 opacity-60">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-accent-text shrink-0 text-[9px]">MEM+</span>
                                {key && <span className="log-accent-text text-[10px]">{key}</span>}
                                {val && <span className="log-muted-text text-[9px] break-all">= {val}</span>}
                              </div>
                            );
                          } else if (type === 'TODO_STATUS_UPDATED') {
                            const name = typeof p.name === 'string' ? p.name : typeof p.item_id === 'string' ? p.item_id : '';
                            const status = typeof p.status === 'string' ? p.status : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-start px-1 py-0.5 opacity-65">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-accent-text shrink-0 text-[9px]">TODO</span>
                                <span className="log-muted-text text-[10px]">{name}</span>
                                {status && <span className="log-faint-text text-[9px] ml-auto">{status}</span>}
                              </div>
                            );
                          } else if (type === 'PHASE_BUDGET_EXCEEDED' || type === 'COST_BUDGET_EXCEEDED' || type === 'PLAN_ROUND_CAP_EXCEEDED') {
                            const msg = typeof p.message === 'string' ? p.message : type;
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-start px-1 py-0.5">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-warning-text shrink-0 font-bold text-[10px]">⚠</span>
                                <span className="log-warning-text text-[10px] break-all">{msg}</span>
                              </div>
                            );
                          } else if (type === 'LOOP_BREAK') {
                            const reason = typeof p.reason === 'string' ? p.reason : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-start px-1 py-0.5 opacity-70">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-warning-text shrink-0 text-[10px] font-bold">LOOP</span>
                                {reason && <span className="log-warning-text text-[10px]">{reason}</span>}
                              </div>
                            );
                          } else if (type === 'LLM_TRANSIENT_RETRY') {
                            const attempt = typeof p.attempt === 'number' ? p.attempt : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-start px-1 py-0.5 opacity-50">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-warning-text shrink-0 text-[9px]">RETRY</span>
                                {attempt !== '' && <span className="log-muted-text text-[9px]">#{attempt}</span>}
                              </div>
                            );
                          } else if (type === 'REPORT_REQUESTED') {
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-center px-1 py-0.5">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-success-text shrink-0 font-bold">▣</span>
                                <span className="log-success-text font-semibold text-[11px]">报告生成中…</span>
                              </div>
                            );
                          } else if (type === 'EXECUTOR_REQUEST') {
                            const skill = typeof p.skill_id === 'string' ? p.skill_id : '';
                            const target = typeof p.target === 'string' ? p.target.slice(0, 60) : '';
                            nodes.push(
                              <div key={`ev-${idx}`} className="flex gap-2 items-start px-1 py-0.5 opacity-55">
                                <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                <span className="log-accent-text shrink-0 text-[9px]">EXEC→</span>
                                {skill && <span className="log-accent-text text-[10px]">{skill}</span>}
                                {target && <span className="log-muted-text text-[9px] break-all">{target}</span>}
                              </div>
                            );
                          } else {
                            // Generic event line — expandable with full JSON
                            const key = `ev-${idx}`;
                            const expanded = expandedKeys.has(key);
                            let detail = '';
                            if (typeof p.message === 'string') detail = p.message;
                            else if (phaseVal) detail = phaseVal;
                            else {
                              const raw = JSON.stringify(p);
                              detail = raw.length > 80 ? raw.slice(0, 80) + '…' : raw;
                            }
                            const fullJson = JSON.stringify(p, null, 2);
                            const hasMore = fullJson.length > 100;
                            nodes.push(
                              <div key={key} className="my-0.5">
                                <button
                                  type="button"
                                  className="w-full text-left flex gap-2 items-start px-1 py-0.5 rounded log-hover-row"
                                  onClick={() => {
                                    if (!hasMore) return;
                                    setExpandedKeys((prev) => {
                                      const next = new Set(prev);
                                      if (next.has(key)) { next.delete(key); } else { next.add(key); }
                                      return next;
                                    });
                                  }}
                                >
                                  <span className="shrink-0 w-14 log-muted-text">{ts}</span>
                                  <span className="log-accent-text shrink-0">{type}</span>
                                  <span className="break-all flex-1">{detail}</span>
                                  {hasMore && <span className="ml-auto log-faint-text text-[9px] shrink-0">{expanded ? '▲' : '▼'}</span>}
                                </button>
                                {expanded && (
                                  <div className="ml-7 mt-1 mb-2 p-2 rounded border log-soft-panel text-[10px] font-mono overflow-x-auto max-h-60 overflow-y-auto">
                                    {Object.entries(p).map(([k, v]) => (
                                      <div key={k} className="flex gap-2 py-0.5">
                                        <span className="log-accent-text shrink-0">{k}:</span>
                                        <span className="break-all whitespace-pre-wrap">{typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v ?? '')}</span>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            );
                          }
                        });
                        return nodes;
                      })()}
                    </div>
                  )}
                </div>
              </div>
              <div className="absolute inset-0 pointer-events-none z-30 crt-vignette" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
