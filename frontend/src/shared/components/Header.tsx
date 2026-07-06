import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Menu, Moon, Sun, X } from "lucide-react";
import { toast } from "sonner";
import { useAppSession } from "@/shared/context/AppSessionContext";
import { ORBIT_TASKS_UPDATED_EVENT, SENTINEL_ORBIT_TASKS_KEY, readStoredOrbitTasks, type StoredOrbitTask } from "@/shared/constants/orbitTasksStorage";
import { listTasks, toFrontendStatus } from "@/shared/lib/api";
import { applyThemeMode, readThemeMode, writeThemeMode, type ThemeMode } from "@/shared/lib/preferences";
import { displayNameOrUsername } from "@/shared/lib/text";
import mainLogoSrc from "@/shared/assets/main.jpg";

interface HeaderProps {
  currentPhase?: number;
}

function countRunningTasks(): number {
  return readStoredOrbitTasks().filter((t) => t.status === "running").length;
}

const SESSION_UPDATED_EVENT = "sentinel_session_updated";

function readSessionDisplayName(): string | null {
  try {
    const raw = localStorage.getItem("sentinel_session_v1");
    if (!raw) return null;
    const p = JSON.parse(raw) as Record<string, unknown>;
    const name = displayNameOrUsername(p);
    return name || null;
  } catch { return null; }
}

const Header = ({ currentPhase = 0 }: HeaderProps) => {
  void currentPhase;
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { loggedIn, logout } = useAppSession();
  const [runningCount, setRunningCount] = useState(() => countRunningTasks());
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const [confirmLogout, setConfirmLogout] = useState(false);
  const [sessionDisplayName, setSessionDisplayName] = useState<string | null>(() => readSessionDisplayName());
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => readThemeMode());
  const [navMenuOpen, setNavMenuOpen] = useState(false);

  useEffect(() => {
    applyThemeMode(themeMode);
  }, [themeMode]);

  useEffect(() => {
    setNavMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    const refresh = () => setRunningCount(countRunningTasks());
    window.addEventListener("storage", refresh);
    window.addEventListener(ORBIT_TASKS_UPDATED_EVENT, refresh);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener(ORBIT_TASKS_UPDATED_EVENT, refresh);
    };
  }, []);

  useEffect(() => {
    const checkHealth = () => {
      fetch('/health', { method: 'GET', signal: AbortSignal.timeout(4000) })
        .then((r) => setBackendOnline(r.ok))
        .catch(() => setBackendOnline(false));
    };
    checkHealth();
    const iv = window.setInterval(checkHealth, 30000);
    return () => window.clearInterval(iv);
  }, []);

  // Sync visible user name whenever login/session data changes.
  useEffect(() => {
    const refresh = () => setSessionDisplayName(loggedIn ? readSessionDisplayName() : null);
    refresh();
    window.addEventListener("storage", refresh);
    window.addEventListener(SESSION_UPDATED_EVENT, refresh);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener(SESSION_UPDATED_EVENT, refresh);
    };
  }, [loggedIn]);

  // When user logs in, fetch real backend tasks and drop numeric-ID demo seeds.
  // This ensures Header badge, CRTerminal tabs, and TasksPage all show real data.
  useEffect(() => {
    if (!loggedIn) return;
    listTasks()
      .then((apiTasks) => {
        if (apiTasks.length === 0) return; // backend empty or unreachable — keep seeds
        const existing = readStoredOrbitTasks();
        const existingIds = new Set(existing.map((t) => t.id));
        const merged: StoredOrbitTask[] = [
          // Keep real (non-numeric-ID) existing tasks
          ...existing.filter((t) => !/^\d+$/.test(t.id)),
          // Add backend tasks not yet in localStorage
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
            })),
        ];
        try {
          localStorage.setItem(SENTINEL_ORBIT_TASKS_KEY, JSON.stringify(merged));
          window.dispatchEvent(new Event(ORBIT_TASKS_UPDATED_EVENT));
          setRunningCount(merged.filter((t) => t.status === 'running').length);
        } catch { /* quota */ }
      })
      .catch(() => { /* backend unavailable — keep seeds */ });
  }, [loggedIn]);

  const goHome = () => {
    navigate("/");
    window.scrollTo({ top: 0 });
  };

  const requireLogin = (path: "/logs" | "/tasks" | "/admin" | "/reports" | "/system" | "/monitor" | "/config" | "/stats" | "/vulns" | "/batch" | "/dashboard" | "/audit") => {
    if (!loggedIn) {
      toast.error("请先登录");
      localStorage.setItem("sentinel_login_redirect", path);
      navigate("/login");
      return;
    }
    navigate(path);
  };

  const navLinks = [
    { label: "首页",     onClick: goHome,                           badge: 0,          path: "/" },
    { label: "技术特点", onClick: () => navigate("/features"),      badge: 0,          path: "/features" },
    { label: "运行日志", onClick: () => requireLogin("/logs"),      badge: 0,          path: "/logs" },
    { label: "任务管理", onClick: () => requireLogin("/tasks"),     badge: runningCount, path: "/tasks" },
    { label: "报告中心", onClick: () => requireLogin("/reports"),   badge: 0,          path: "/reports" },
    { label: "技能库",   onClick: () => navigate("/skills"),        badge: 0,          path: "/skills" },
    { label: "监控大屏", onClick: () => requireLogin("/monitor"),   badge: 0,          path: "/monitor" },
    { label: "统计分析", onClick: () => requireLogin("/stats"),     badge: 0,          path: "/stats" },
    { label: "漏洞库",   onClick: () => requireLogin("/vulns"),     badge: 0,          path: "/vulns" },
    { label: "批量调度", onClick: () => requireLogin("/batch"),     badge: 0,          path: "/batch" },
    { label: "管理中心", onClick: () => requireLogin("/dashboard"), badge: 0,          path: "/dashboard" },
    { label: "审计日志", onClick: () => requireLogin("/audit"),    badge: 0,          path: "/audit" },
    { label: "平台管理", onClick: () => requireLogin("/admin"),     badge: 0,          path: "/admin" },
    { label: "系统状态", onClick: () => requireLogin("/system"),    badge: 0,          path: "/system" },
  ];

  const runNavAction = (action: () => void) => {
    setNavMenuOpen(false);
    action();
  };

  return (
      <header
          data-cmp="Header"
          className="nav-glass"
          style={{
              position: "fixed",
              top: 0,
              left: 0,
              right: 0,
              zIndex: 50,
              height: "60px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "0 28px",
              width: "100%",
              gap: 18,
          }}
      >
          <div
              style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  cursor: "pointer",
                  flexShrink: 0,
              }}
              onClick={goHome}
              onKeyDown={(e) => e.key === "Enter" && goHome()}
              role="button"
              tabIndex={0}
          >
              <img
                  src={mainLogoSrc}
                  alt="Logo"
                  style={{
                      width: "50px",
                      height: "50px",
                      objectFit: "cover", // 防止图片变形
                      borderRadius: "4px" // 可选，轻微圆角更好看
                  }}
              />
              <span
                  style={{
                      fontFamily: "'Courier New', monospace",
                      fontSize: "1rem",
                      fontWeight: 700,
                      letterSpacing: "0.18em",
                      color: "var(--neon-blue)",
                      textShadow: themeMode === "dark" ? "0 0 10px var(--neon-blue)" : "none",
                  }}
              >
        TRUSTGUARD AGENT
    </span>
          </div>

          <nav style={{display: "flex", alignItems: "center", gap: "10px", overflowX: "auto", flexShrink: 1, minWidth: 0, paddingBottom: 2}}>
              {navLinks.map((link) => {
                const isActive = link.path === "/" ? pathname === "/" : pathname.startsWith(link.path);
                return (
                  <button
                      key={link.label}
                      type="button"
                      className="nav-link"
                      onClick={() => runNavAction(link.onClick)}
                      style={{
                        background: "none", border: "none", padding: 0, cursor: "pointer", position: "relative",
                        color: isActive ? "var(--neon-blue)" : undefined,
                        textShadow: isActive && themeMode === "dark" ? "0 0 8px var(--neon-blue)" : undefined,
                      }}
                  >
                      {link.label}
                      {link.badge > 0 && (
                          <span style={{
                              position: "absolute",
                              top: -8,
                              right: -14,
                              minWidth: 16,
                              height: 16,
                              borderRadius: 8,
                              background: "#00f7ff",
                              color: "#020a12",
                              fontSize: 10,
                              fontWeight: 900,
                              lineHeight: "16px",
                              textAlign: "center",
                              padding: "0 3px",
                              boxShadow: "0 0 6px #00f7ff",
                          }}>
                              {link.badge}
                          </span>
                      )}
                  </button>
                );
              })}
          </nav>

          <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0, position: "relative" }}>
              {/* Backend health indicator */}
              {backendOnline !== null && (
                  <span
                      title={backendOnline ? "后端连接正常 · 数据实时同步" : "后端未连接 · 当前展示演示数据"}
                      style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 5,
                          fontSize: 11,
                          fontFamily: "monospace",
                          color: backendOnline ? "rgba(52,211,153,0.9)" : "rgba(251,191,36,0.9)",
                          userSelect: "none",
                          padding: backendOnline ? undefined : "1px 6px",
                          borderRadius: backendOnline ? undefined : 3,
                          border: backendOnline ? undefined : "1px solid rgba(251,191,36,0.35)",
                          background: backendOnline ? undefined : "rgba(251,191,36,0.06)",
                      }}
                  >
                      <span style={{
                          width: 7, height: 7, borderRadius: "50%",
                          background: backendOnline ? "#34d399" : "#fbbf24",
                          boxShadow: backendOnline ? "0 0 5px rgba(52,211,153,0.7)" : "0 0 5px rgba(251,191,36,0.7)",
                      }} />
                      {backendOnline ? "API" : "演示模式"}
                  </span>
              )}
              <button
                type="button"
                className="nav-menu-btn"
                onClick={() => setNavMenuOpen((open) => !open)}
                title="打开导航菜单"
                aria-label="打开导航菜单"
                aria-expanded={navMenuOpen}
                style={{
                  width: 32,
                  height: 32,
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  borderRadius: 6,
                  border: "1px solid var(--tg-panel-border)",
                  background: "var(--tg-input-bg)",
                  color: "var(--neon-blue)",
                  cursor: "pointer",
                  flexShrink: 0,
                }}
              >
                {navMenuOpen ? <X size={17} /> : <Menu size={17} />}
              </button>
              {navMenuOpen && (
                <div
                  role="menu"
                  aria-label="导航菜单"
                  style={{
                    position: "absolute",
                    right: 0,
                    top: 42,
                    width: 220,
                    maxHeight: "calc(100vh - 86px)",
                    overflowY: "auto",
                    padding: 8,
                    borderRadius: 8,
                    border: "1px solid var(--tg-panel-border)",
                    background: "var(--tg-panel-bg)",
                    boxShadow: "var(--tg-shadow)",
                    zIndex: 80,
                  }}
                >
                  {navLinks.map((link) => {
                    const isActive = link.path === "/" ? pathname === "/" : pathname.startsWith(link.path);
                    return (
                      <button
                        key={`menu-${link.label}`}
                        type="button"
                        role="menuitem"
                        onClick={() => runNavAction(link.onClick)}
                        style={{
                          width: "100%",
                          minHeight: 34,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          gap: 10,
                          border: "none",
                          borderRadius: 6,
                          background: isActive ? "var(--tg-accent-soft)" : "transparent",
                          color: isActive ? "var(--tg-accent)" : "var(--tg-text)",
                          cursor: "pointer",
                          padding: "7px 9px",
                          fontSize: 13,
                          fontWeight: isActive ? 800 : 600,
                          textAlign: "left",
                        }}
                      >
                        <span>{link.label}</span>
                        {link.badge > 0 && (
                          <span
                            style={{
                              minWidth: 18,
                              height: 18,
                              borderRadius: 9,
                              background: "var(--tg-accent)",
                              color: "#020a12",
                              fontSize: 10,
                              fontWeight: 900,
                              lineHeight: "18px",
                              textAlign: "center",
                              padding: "0 4px",
                            }}
                          >
                            {link.badge}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
              <button
                type="button"
                onClick={() => {
                  const next = themeMode === "dark" ? "light" : "dark";
                  setThemeMode(next);
                  writeThemeMode(next);
                }}
                title={themeMode === "dark" ? "切换浅色模式" : "切换深色模式"}
                aria-label={themeMode === "dark" ? "切换浅色模式" : "切换深色模式"}
                style={{
                  width: 28,
                  height: 28,
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  borderRadius: 6,
                  border: "1px solid var(--tg-panel-border)",
                  background: "var(--tg-input-bg)",
                  color: "var(--neon-blue)",
                  cursor: "pointer",
                  flexShrink: 0,
                }}
              >
                {themeMode === "dark" ? <Sun size={15} /> : <Moon size={15} />}
              </button>
              {confirmLogout ? (
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <button
                    type="button"
                    onClick={() => { setConfirmLogout(false); logout(); navigate("/"); }}
                    style={{
                      background: "rgba(248,113,113,0.18)", border: "1px solid rgba(248,113,113,0.7)",
                      color: "#fca5a5", borderRadius: 6, padding: "4px 10px",
                      fontSize: 12, cursor: "pointer", fontWeight: 900,
                    }}
                  >确认</button>
                  <button
                    type="button"
                    onClick={() => setConfirmLogout(false)}
                    style={{
                      background: "transparent", border: "1px solid var(--tg-panel-border)",
                      color: "var(--tg-text-muted)", borderRadius: 6, padding: "4px 10px",
                      fontSize: 12, cursor: "pointer",
                    }}
                  >取消</button>
                </span>
              ) : (
                <>
                  {loggedIn && sessionDisplayName && (
                    <button
                      type="button"
                      onClick={() => navigate("/profile")}
                      style={{
                        background: "none", border: "none", padding: "0 4px",
                        cursor: "pointer", color: "var(--tg-text-muted)",
                        fontFamily: "monospace", fontSize: 11,
                      }}
                      title="个人中心"
                    >
                      {sessionDisplayName.slice(0, 12)}
                    </button>
                  )}
                  <button
                      type="button"
                      className="nav-login-btn"
                      onClick={() => {
                          if (loggedIn) { setConfirmLogout(true); return; }
                          navigate("/login");
                      }}
                  >
                      {loggedIn ? "退出" : "登录"}
                  </button>
                </>
              )}
          </div>
      </header>
  );
};

export default Header;
