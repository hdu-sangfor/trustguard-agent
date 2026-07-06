import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Toaster, toast } from "sonner";
import NotFound from "@/features/not-found/NotFound";
import ErrorBoundary from "@/shared/components/ErrorBoundary";
import { applyThemeMode, PREFERENCES_CHANGED_EVENT, readThemeMode, type ThemeMode } from "@/shared/lib/preferences";

const PageLoader = () => (
  <div style={{
    width: '100vw', height: '100vh',
    background: 'var(--tg-page-gradient)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
  }}>
    <span style={{ color: 'var(--tg-accent)', opacity: 0.72, fontSize: 11, fontFamily: 'monospace', letterSpacing: '0.15em' }}>
      LOADING…
    </span>
  </div>
);

// 当 CD 重新构建 dist 后，浏览器缓存的 index.html 仍引用已删除的旧 chunk hash，
// 动态 import 会抛 "Failed to fetch dynamically imported module"。捕获后强制硬刷新拉最新 index.html。
const RELOAD_FLAG_KEY = "sentinel_chunk_reload_attempt";
function reloadOnChunkError<T>(factory: () => Promise<T>): () => Promise<T> {
  return async () => {
    try {
      return await factory();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      const isChunkMiss = /Failed to fetch dynamically imported module|Loading chunk \d+ failed|Importing a module script failed/i.test(msg);
      if (isChunkMiss) {
        // 防止无限刷新：仅当最近 60s 内未尝试过 reload 时才触发
        try {
          const last = Number(sessionStorage.getItem(RELOAD_FLAG_KEY) ?? 0);
          if (Date.now() - last > 60_000) {
            sessionStorage.setItem(RELOAD_FLAG_KEY, String(Date.now()));
            window.location.reload();
            // Return a pending promise so React Suspense keeps spinner until reload fires
            return new Promise<T>(() => { /* intentional never-resolve */ });
          }
        } catch { /* sessionStorage blocked — fall through */ }
      }
      throw err;
    }
  };
}

// Lazy-load heavy pages to reduce initial bundle
const Index = lazy(reloadOnChunkError(() => import("@/features/home/IndexPage")));
const FeaturesPage = lazy(reloadOnChunkError(() => import("@/features/features-overview/FeaturesPage")));
const TasksPage = lazy(reloadOnChunkError(() => import("@/features/tasks/TasksPage")));
const LoginPage = lazy(reloadOnChunkError(() => import("@/features/login/LoginPage")));
const LogsPage = lazy(reloadOnChunkError(() => import("@/features/logs/LogsPage")));
const AdminPage = lazy(reloadOnChunkError(() => import("@/features/admin/AdminPage")));
const ReportsPage = lazy(reloadOnChunkError(() => import("@/features/reports/ReportsPage")));
const SystemPage = lazy(reloadOnChunkError(() => import("@/features/system/SystemPage")));
const MonitorPage = lazy(reloadOnChunkError(() => import("@/features/monitor/MonitorPage")));
const SkillsPage = lazy(reloadOnChunkError(() => import("@/features/skills/SkillsPage")));
const ProfilePage = lazy(reloadOnChunkError(() => import("@/features/profile/ProfilePage")));
const TaskTracePage = lazy(reloadOnChunkError(() => import("@/features/trace/TaskTracePage")));
const ConfigPage = lazy(reloadOnChunkError(() => import("@/features/config/ConfigPage")));
const StatsPage = lazy(reloadOnChunkError(() => import("@/features/stats/StatsPage")));
const VulnsPage = lazy(reloadOnChunkError(() => import("@/features/vulns/VulnsPage")));
const BatchPage = lazy(reloadOnChunkError(() => import("@/features/batch/BatchPage")));
const DashboardPage = lazy(reloadOnChunkError(() => import("@/features/dashboard/DashboardPage")));
const AuditPage = lazy(reloadOnChunkError(() => import("@/features/audit/AuditPage")));
import { AppSessionProvider } from "@/shared/context/AppSessionContext";
import { SENTINEL_ORBIT_TASKS_KEY, ORBIT_TASKS_UPDATED_EVENT, type StoredOrbitTask } from "@/shared/constants/orbitTasksStorage";

const queryClient = new QueryClient();


/**
 * Polls localStorage for task status changes.
 * When a task transitions to "finished" (DONE) or "failed", fires a toast.
 * Runs only when the user is on the platform (logged in).
 */
const TaskCompletionWatcher = () => {
  const seenRef = useRef<Map<string, string>>(new Map());

  useEffect(() => {
    const poll = () => {
      try {
        const raw = localStorage.getItem(SENTINEL_ORBIT_TASKS_KEY);
        if (!raw) return;
        const tasks = JSON.parse(raw) as StoredOrbitTask[];
        for (const t of tasks) {
          const prev = seenRef.current.get(t.id);
          const curr = t.status;
          if (prev !== undefined && prev !== curr) {
            if (curr === "finished") {
              toast.success(`任务完成：${t.name.slice(0, 28)}`, {
                description: "渗透测试已完成 · 查看报告中心获取详情",
                duration: 6000,
              });
            } else if (curr === "failed") {
              toast.error(`任务失败：${t.name.slice(0, 28)}`, {
                description: "请检查日志页面排查问题",
                duration: 5000,
              });
            }
          }
          seenRef.current.set(t.id, curr);
        }
      } catch { /* ignore */ }
    };

    // Initialize seen map without firing notifications
    try {
      const raw = localStorage.getItem(SENTINEL_ORBIT_TASKS_KEY);
      if (raw) {
        const tasks = JSON.parse(raw) as StoredOrbitTask[];
        tasks.forEach(t => seenRef.current.set(t.id, t.status));
      }
    } catch { /* ignore */ }

    const iv = window.setInterval(poll, 5000);
    window.addEventListener(ORBIT_TASKS_UPDATED_EVENT, poll);
    return () => {
      window.clearInterval(iv);
      window.removeEventListener(ORBIT_TASKS_UPDATED_EVENT, poll);
    };
  }, []);

  return null;
};

const GlobalStyles = ({ onThemeChange }: { onThemeChange: (theme: ThemeMode) => void }) => {
  useEffect(() => {
    const apply = () => {
      const theme = readThemeMode();
      applyThemeMode(theme);
      onThemeChange(theme);
      document.body.style.margin = "0";
      document.body.style.padding = "0";
      document.body.style.overflow = "auto";
      document.body.style.background = "var(--tg-page-bg)";
      document.body.style.fontFamily = "'Inter', 'Courier New', monospace";
      document.documentElement.style.scrollBehavior = "auto";
    };

    const syncFromStorage = (event: StorageEvent) => {
      if (!event.key || event.key.includes("theme")) apply();
    };

    apply();
    window.addEventListener(PREFERENCES_CHANGED_EVENT, apply);
    window.addEventListener("storage", syncFromStorage);
    return () => {
      window.removeEventListener(PREFERENCES_CHANGED_EVENT, apply);
      window.removeEventListener("storage", syncFromStorage);
    };
  }, [onThemeChange]);
  return null;
};

const App = () => {
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => readThemeMode());

  return (
    <QueryClientProvider client={queryClient}>
      <GlobalStyles onThemeChange={setThemeMode} />
      <TaskCompletionWatcher />
      <Toaster
        position="top-right"
        theme={themeMode}
        toastOptions={{
          style: {
            background: "var(--tg-panel-bg)",
            border: "1px solid var(--tg-panel-border)",
            color: "var(--tg-text)",
            boxShadow: "var(--tg-shadow)",
            fontFamily: "'Inter','Courier New',monospace",
          },
        }}
      />
      <AppSessionProvider>
        <BrowserRouter>
          <ErrorBoundary>
            <Suspense fallback={<PageLoader />}>
              <Routes>
                <Route path="/" element={<Index />} />
                <Route path="/features" element={<FeaturesPage />} />
                <Route path="/tasks" element={<TasksPage />} />
                <Route path="/login" element={<LoginPage />} />
                <Route path="/logs" element={<LogsPage />} />
                <Route path="/admin" element={<AdminPage />} />
                <Route path="/reports" element={<ReportsPage />} />
                <Route path="/system" element={<SystemPage />} />
                <Route path="/monitor" element={<MonitorPage />} />
                <Route path="/skills" element={<SkillsPage />} />
                <Route path="/profile" element={<ProfilePage />} />
                <Route path="/trace/:taskId" element={<TaskTracePage />} />
                <Route path="/config" element={<ConfigPage />} />
                <Route path="/stats" element={<StatsPage />} />
                <Route path="/vulns" element={<VulnsPage />} />
                <Route path="/batch" element={<BatchPage />} />
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="/audit" element={<AuditPage />} />
                <Route path="/testbench" element={<Navigate to="/tasks" replace />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </BrowserRouter>
      </AppSessionProvider>
    </QueryClientProvider>
  );
};

export default App;
