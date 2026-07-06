import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import { listTasks, toFrontendStatus, backendLogin, backendRegister, storeAuthToken, clearAuthToken } from "@/shared/lib/api";

const STORAGE_KEY = "sentinel_session_v1";
const buttonHoverStyle = (button: HTMLButtonElement, hovered: boolean) => {
    button.style.background = hovered ? "var(--tg-accent-soft)" : "var(--tg-panel-bg)";
    button.style.borderColor = hovered ? "var(--tg-accent)" : "rgba(0, 247, 255, 0.4)";
};

const LoginPage = () => {
    const navigate = useNavigate();
    const { login } = useAppSession();
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [isRegisterMode, setIsRegisterMode] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [backendStatus, setBackendStatus] = useState<"unknown" | "online" | "offline">("unknown");

    useEffect(() => {
        document.body.classList.add("no-page-scroll", "no-scrollbar");
        // Probe backend health so user sees connection state before submitting
        fetch('/health', { method: 'GET', signal: AbortSignal.timeout(4000) })
          .then((r) => setBackendStatus(r.ok ? "online" : "offline"))
          .catch(() => setBackendStatus("offline"));
        return () => {
            document.body.classList.remove("no-page-scroll", "no-scrollbar");
        };
    }, []);

    const handleSubmit = (e: FormEvent) => {
        e.preventDefault();
        if (submitting) return;

        const redirectAfterLogin = () => {
            const dest = localStorage.getItem("sentinel_login_redirect") ?? "/tasks";
            localStorage.removeItem("sentinel_login_redirect");
            navigate(dest);
        };

        const trimmedUsername = username.trim();
        if (!trimmedUsername) { toast.error("用户名不能为空"); return; }
        if (!password) { toast.error("密码不能为空"); return; }

        if (isRegisterMode) {
            if (password.length < 6) { toast.error("密码长度至少为6位"); return; }
            if (password !== confirmPassword) { toast.error("两次输入的密码不一致"); return; }
        }

        setSubmitting(true);

        const doLocalFallbackLogin = (username: string) => {
            localStorage.setItem(STORAGE_KEY, JSON.stringify({ username, created: 0, completed: 0, running: 0, failed: 0 }));
            login();
            listTasks()
              .then((tasks) => {
                const c = { running: 0, finished: 0, failed: 0 };
                for (const t of tasks) {
                  const st = toFrontendStatus(t.status);
                  if (st === 'running') c.running++;
                  else if (st === 'finished') c.finished++;
                  else if (st === 'failed') c.failed++;
                }
                localStorage.setItem(STORAGE_KEY, JSON.stringify({ username, created: tasks.length, completed: c.finished, running: c.running, failed: c.failed }));
                const suffix = tasks.length > 0 ? `，共 ${tasks.length} 个任务` : "";
                toast.success(`欢迎，${username}${suffix}`);
              })
              .catch(() => { toast.success(`欢迎，${username}`); })
              .finally(() => { setSubmitting(false); });
        };

        if (isRegisterMode) {
            // ── Register path ──────────────────────────────────────────────
            if (backendStatus === "online") {
                backendRegister(trimmedUsername, password)
                  .then((result) => {
                    storeAuthToken(result.token);
                    const u = result.user;
                    localStorage.setItem(STORAGE_KEY, JSON.stringify({
                      username: u.username,
                      displayName: u.displayName ?? u.username,
                      role: u.role,
                      created: 0, completed: 0, running: 0, failed: 0,
                    }));
                    login(result.expiresIn);
                    toast.success(`账号已创建，欢迎 ${u.displayName ?? u.username}`);
                    setSubmitting(false);
                    redirectAfterLogin();
                  })
                  .catch((err: unknown) => {
                    const msg = err instanceof Error ? err.message : "注册失败";
                    if (msg.includes("已存在") || msg.includes("密码长度") || msg.includes("用户名不能")) {
                        toast.error(msg);
                        setSubmitting(false);
                    } else {
                        // Network error — fall back to local session
                        clearAuthToken();
                        doLocalFallbackLogin(trimmedUsername);
                        redirectAfterLogin();
                    }
                  });
            } else {
                // Backend offline — local session only
                doLocalFallbackLogin(trimmedUsername);
                redirectAfterLogin();
            }
        } else if (backendStatus === "online") {
            // ── Login path (backend online) ────────────────────────────────
            backendLogin(trimmedUsername, password)
              .then((result) => {
                storeAuthToken(result.token);
                const u = result.user;
                localStorage.setItem(STORAGE_KEY, JSON.stringify({
                  username: u.username,
                  displayName: u.displayName ?? u.username,
                  role: u.role,
                  created: 0, completed: 0, running: 0, failed: 0,
                }));
                login(result.expiresIn);
                toast.success(`欢迎，${u.displayName ?? u.username} (${u.role})`);
                setSubmitting(false);
                redirectAfterLogin();
              })
              .catch((err: unknown) => {
                const msg = err instanceof Error ? err.message : "登录失败";
                // If backend explicitly rejects credentials, don't fall through
                if (msg.includes("密码错误") || msg.includes("用户名或密码") || msg.includes("账号已被禁用") || msg.includes("用户不存在")) {
                  toast.error(msg);
                  setSubmitting(false);
                } else {
                  // Network/timeout — fall back to local session
                  clearAuthToken();
                  doLocalFallbackLogin(trimmedUsername);
                  redirectAfterLogin();
                }
              });
        } else {
            // ── Login path (backend offline) ──────────────────────────────
            doLocalFallbackLogin(trimmedUsername);
            redirectAfterLogin();
        }
    };

    const switchToRegisterMode = (e?: React.MouseEvent) => {
        e?.preventDefault();
        setIsRegisterMode(true);
        setConfirmPassword("");
    };

    const switchToLoginMode = (e?: React.MouseEvent) => {
        e?.preventDefault();
        setIsRegisterMode(false);
        setConfirmPassword("");
    };

    const buttonBaseStyle: React.CSSProperties = {
        width: "100%",
        padding: "14px",
        borderRadius: 10,
        border: "1px solid rgba(0, 247, 255, 0.4)",
        background: "var(--tg-panel-bg)",
        color: "var(--tg-accent)",
        fontWeight: 700,
        fontSize: "1rem",
        cursor: "pointer",
        transition: "all 0.2s ease",
    };

    return (
        <div style={{ minHeight: "100vh", background: "var(--tg-page-gradient)" }}>
            <Header />
            <main
                style={{
                    minHeight: "calc(100vh - 60px)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    padding: "80px 20px 48px",
                }}
            >
                <form
                    onSubmit={handleSubmit}
                    style={{
                        width: "100%",
                        maxWidth: 400,
                        background: "var(--tg-panel-bg)",
                        borderRadius: 16,
                        padding: "32px 28px",
                        boxShadow: "var(--tg-shadow), 0 0 0 1px var(--tg-panel-border)",
                        opacity: 1,
                    }}
                >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                      <h1 style={{ fontSize: "1.5rem", margin: 0, color: "var(--tg-accent)" }}>
                          {isRegisterMode ? "注册" : "登录"}
                      </h1>
                      {backendStatus !== "unknown" && (
                        <span style={{
                          display: "inline-flex", alignItems: "center", gap: 5,
                          fontSize: 11, fontFamily: "monospace",
                          color: backendStatus === "online" ? "rgba(52,211,153,0.9)" : "rgba(248,113,113,0.9)",
                        }}>
                          <span style={{
                            width: 6, height: 6, borderRadius: "50%",
                            background: backendStatus === "online" ? "#34d399" : "#f87171",
                            boxShadow: backendStatus === "online" ? "0 0 5px rgba(52,211,153,0.7)" : "0 0 5px rgba(248,113,113,0.7)",
                          }} />
                          {backendStatus === "online" ? "后端在线" : "后端离线"}
                        </span>
                      )}
                    </div>
                    <p style={{ fontSize: "0.88rem", color: "var(--tg-text-muted)", marginBottom: 24, marginTop: 8 }}>
                        {isRegisterMode
                            ? "创建新账号，即可访问运行日志和任务管理。"
                            : "请先登录再访问运行日志和任务管理。"}
                    </p>
                    <label style={{ display: "block", marginBottom: 8, fontSize: "0.82rem", color: "var(--tg-accent)", fontWeight: 600 }}>
                        用户名
                    </label>
                    <input
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        placeholder="请输入用户名"
                        autoFocus
                        autoComplete="username"
                        style={{
                            width: "100%",
                            boxSizing: "border-box",
                            padding: "12px 14px",
                            marginBottom: 18,
                            borderRadius: 10,
                            border: "1px solid rgba(0, 247, 255, 0.35)",
                            fontSize: "1rem",
                            background: "var(--tg-input-bg)",
                            color: "var(--tg-text)",
                        }}
                    />
                    <label style={{ display: "block", marginBottom: 8, fontSize: "0.82rem", color: "var(--tg-accent)", fontWeight: 600 }}>
                        密码
                    </label>
                    <input
                        type="password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        placeholder="请输入密码"
                        autoComplete={isRegisterMode ? "new-password" : "current-password"}
                        style={{
                            width: "100%",
                            boxSizing: "border-box",
                            padding: "12px 14px",
                            marginBottom: isRegisterMode ? 18 : 26,
                            borderRadius: 10,
                            border: "1px solid rgba(0, 247, 255, 0.35)",
                            fontSize: "1rem",
                            background: "var(--tg-input-bg)",
                            color: "var(--tg-text)",
                        }}
                    />

                    {isRegisterMode && (
                        <>
                            <label
                                style={{
                                    display: "block",
                                    marginBottom: 8,
                                    fontSize: "0.82rem",
                                    color: "var(--tg-accent)",
                                    fontWeight: 600,
                                }}
                            >
                                确认密码
                            </label>
                            <input
                                type="password"
                                value={confirmPassword}
                                onChange={(e) => setConfirmPassword(e.target.value)}
                                placeholder="请再次输入密码"
                                autoComplete="new-password"
                                style={{
                                    width: "100%",
                                    boxSizing: "border-box",
                                    padding: "12px 14px",
                                    marginBottom: 26,
                                    borderRadius: 10,
                                    border: "1px solid rgba(0, 247, 255, 0.35)",
                                    fontSize: "1rem",
                                    background: "var(--tg-input-bg)",
                                    color: "var(--tg-text)",
                                }}
                            />
                        </>
                    )}

                    {/* Demo quick login */}
                    {!isRegisterMode && (
                      <div style={{
                        marginBottom: 14, padding: "10px 14px", borderRadius: 8,
                        background: "rgba(34,211,238,0.05)", border: "1px solid rgba(34,211,238,0.18)",
                        display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10,
                      }}>
                        <div>
                          <div style={{ color: "#22d3ee", fontSize: 10, fontFamily: "monospace", letterSpacing: "0.08em", marginBottom: 2 }}>演示账号</div>
                          <div style={{ color: "#64748b", fontSize: 10, fontFamily: "monospace" }}>admin / admin123</div>
                        </div>
                        <button
                          type="button"
                          onClick={() => { setUsername("admin"); setPassword("admin123"); }}
                          style={{
                            padding: "4px 12px", borderRadius: 5, fontSize: 11, fontWeight: 700,
                            border: "1px solid rgba(34,211,238,0.35)", background: "rgba(34,211,238,0.08)",
                            color: "#22d3ee", cursor: "pointer", fontFamily: "monospace", whiteSpace: "nowrap",
                          }}
                        >一键填入</button>
                      </div>
                    )}

                    {isRegisterMode ? (
                        <button
                            type="button"
                            onClick={switchToLoginMode}
                            style={buttonBaseStyle}
                            onMouseOver={(e) => {
                                buttonHoverStyle(e.currentTarget, true);
                            }}
                            onMouseOut={(e) => {
                                buttonHoverStyle(e.currentTarget, false);
                            }}
                        >
                            返回登录
                        </button>
                    ) : (
                        <button
                            type="submit"
                            disabled={submitting}
                            style={{ ...buttonBaseStyle, opacity: submitting ? 0.65 : 1 }}
                            onMouseOver={(e) => {
                                buttonHoverStyle(e.currentTarget, true);
                            }}
                            onMouseOut={(e) => {
                                buttonHoverStyle(e.currentTarget, false);
                            }}
                        >
                            {submitting ? "登录中…" : "登录"}
                        </button>
                    )}

                    {isRegisterMode ? (
                        <button
                            type="submit"
                            disabled={submitting}
                            style={{ ...buttonBaseStyle, marginTop: "12px", opacity: submitting ? 0.65 : 1 }}
                            onMouseOver={(e) => {
                                buttonHoverStyle(e.currentTarget, true);
                            }}
                            onMouseOut={(e) => {
                                buttonHoverStyle(e.currentTarget, false);
                            }}
                        >
                            {submitting ? "注册中…" : "注册"}
                        </button>
                    ) : (
                        <button
                            type="button"
                            onClick={switchToRegisterMode}
                            style={{ ...buttonBaseStyle, marginTop: "12px" }}
                            onMouseOver={(e) => {
                                buttonHoverStyle(e.currentTarget, true);
                            }}
                            onMouseOut={(e) => {
                                buttonHoverStyle(e.currentTarget, false);
                            }}
                        >
                            注册账号
                        </button>
                    )}
                </form>
            </main>
        </div>
    );
};

export default LoginPage;
