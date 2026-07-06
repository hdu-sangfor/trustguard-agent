/**
 * ProfilePage — 用户个人中心
 * 查看当前账户信息、更新昵称/邮箱、自助修改密码。
 * GET  /api/v1/auth/me           — 获取当前用户信息
 * PUT  /api/v1/auth/me           — 更新昵称/邮箱
 * PUT  /api/v1/auth/me/password  — 修改密码（需验证旧密码）
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import { getMe, changeMyPassword, updateMyProfile, getAuditEvents, type ApiUser, type ApiAuditEvent } from "@/shared/lib/api";
import { displayNameOrUsername } from "@/shared/lib/text";

const SESSION_UPDATED_EVENT = "sentinel_session_updated";

// ─── Role / status badges ───────────────────────────────────────────────────

const ROLE_COLOR: Record<string, string> = {
  ADMIN: "#f87171", OPERATOR: "#fbbf24", VIEWER: "#64748b",
};
const ROLE_LABEL: Record<string, string> = {
  ADMIN: "系统管理员", OPERATOR: "运维操作员", VIEWER: "只读用户",
};

function RoleBadge({ role }: { role: string }) {
  const c = ROLE_COLOR[role] ?? "#64748b";
  return (
    <span style={{
      fontSize: 11, padding: "3px 10px", borderRadius: 4,
      background: `${c}18`, border: `1px solid ${c}45`,
      color: c, fontFamily: "monospace", fontWeight: 700,
    }}>{ROLE_LABEL[role] ?? role}</span>
  );
}

// ─── Section card ────────────────────────────────────────────────────────────

function Section({ title, accent = "rgba(34,211,238,0.3)", children }: {
  title: string; accent?: string; children: React.ReactNode;
}) {
  return (
    <div style={{
      background: "var(--tg-panel-bg)", border: `1px solid ${accent}`,
      borderRadius: 12, padding: "20px 24px",
      boxShadow: "var(--tg-shadow)",
    }}>
      <div style={{ fontSize: 10, fontWeight: 800, color: "rgba(148,163,184,0.8)", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 18 }}>
        {title}
      </div>
      {children}
    </div>
  );
}

// ─── Input field ──────────────────────────────────────────────────────────────

function Field({ label, value, onChange, type = "text", placeholder, disabled }: {
  label: string; value: string; onChange?: (v: string) => void;
  type?: string; placeholder?: string; disabled?: boolean;
}) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 11, color: "rgba(148,163,184,0.6)", marginBottom: 5, fontFamily: "monospace" }}>{label}</div>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={e => onChange?.(e.target.value)}
        style={{
          width: "100%", padding: "9px 12px", borderRadius: 7,
          background: disabled ? "var(--tg-panel-muted)" : "var(--tg-input-bg)",
          border: "1px solid var(--tg-panel-border)",
          color: disabled ? "var(--tg-text-faint)" : "var(--tg-text)",
          fontFamily: "monospace", fontSize: 13, outline: "none",
          boxSizing: "border-box",
          cursor: disabled ? "not-allowed" : "text",
        }}
      />
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function ProfilePage() {
  const { loggedIn, logout } = useAppSession();
  const navigate = useNavigate();

  const [user, setUser]               = useState<ApiUser | null>(null);
  const [loading, setLoading]         = useState(true);
  const [auditEvents, setAuditEvents] = useState<ApiAuditEvent[]>([]);

  // Profile edit form
  const [editDisplayName, setEditDisplayName] = useState("");
  const [editEmail, setEditEmail]             = useState("");
  const [profileSaving, setProfileSaving]     = useState(false);

  // Password change form
  const [oldPwd, setOldPwd]   = useState("");
  const [newPwd, setNewPwd]   = useState("");
  const [confPwd, setConfPwd] = useState("");
  const [pwdSaving, setPwdSaving] = useState(false);
  const [pwdError, setPwdError]   = useState<string | null>(null);

  useEffect(() => {
    if (!loggedIn) {
      toast.error("请先登录");
      localStorage.setItem("sentinel_login_redirect", "/profile");
      navigate("/login", { replace: true });
    }
  }, [loggedIn, navigate]);

  const loadUser = useCallback(async () => {
    setLoading(true);
    try {
      const u = await getMe();
      setUser(u);
      setEditDisplayName(u.displayName ?? "");
      setEditEmail(u.email ?? "");
    } catch {
      // offline — read from localStorage session
      try {
        const raw = localStorage.getItem("sentinel_session_v1");
        if (raw) {
          const s = JSON.parse(raw) as Record<string, unknown>;
          const fallback: ApiUser = {
            id: 0,
            userId: String(s.userId ?? ""),
            username: String(s.username ?? ""),
            displayName: displayNameOrUsername(s),
            email: "",
            role: (s.role as ApiUser["role"]) ?? "VIEWER",
            status: "ACTIVE",
            lastLoginAt: null,
            createdAt: "",
            updatedAt: "",
          };
          setUser(fallback);
          setEditDisplayName(fallback.displayName);
        }
      } catch { /* ignore */ }
    }
    setLoading(false);
  }, []);

  const loadAuditEvents = useCallback(async () => {
    try {
      const events = await getAuditEvents(50);
      // Filter to events relevant to this user
      const username = user?.username;
      if (username) {
        setAuditEvents(events.filter(e => e.actor === username || e.target === user?.userId).slice(0, 10));
      } else {
        setAuditEvents(events.slice(0, 5));
      }
    } catch { /* offline, ignore */ }
  }, [user]);

  useEffect(() => { if (loggedIn) void loadUser(); }, [loggedIn, loadUser]);
  useEffect(() => { if (user) void loadAuditEvents(); }, [user, loadAuditEvents]);

  const handleSaveProfile = async () => {
    if (!user) return;
    setProfileSaving(true);
    try {
      const updated = await updateMyProfile({
        displayName: editDisplayName.trim() || undefined,
        email: editEmail.trim() || undefined,
      });
      setUser(updated);
      // Update localStorage session
      try {
        const raw = localStorage.getItem("sentinel_session_v1");
        if (raw) {
          const s = JSON.parse(raw) as Record<string, unknown>;
          localStorage.setItem("sentinel_session_v1", JSON.stringify({ ...s, displayName: updated.displayName }));
          window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
        }
      } catch { /* quota */ }
      toast.success("资料已更新");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "更新失败");
    }
    setProfileSaving(false);
  };

  const handleChangePassword = async () => {
    setPwdError(null);
    if (!newPwd || newPwd.length < 6) { setPwdError("新密码至少 6 位"); return; }
    if (newPwd !== confPwd) { setPwdError("两次输入的密码不一致"); return; }
    if (!oldPwd) { setPwdError("请输入当前密码"); return; }
    setPwdSaving(true);
    try {
      await changeMyPassword(oldPwd, newPwd);
      toast.success("密码已修改，请重新登录");
      setOldPwd(""); setNewPwd(""); setConfPwd("");
      // Force re-login since token is still valid but security best practice
      setTimeout(() => { logout(); navigate("/login"); }, 1500);
    } catch (err) {
      setPwdError(err instanceof Error ? err.message : "修改失败");
    }
    setPwdSaving(false);
  };

  if (!loggedIn) return null;

  const roleColor = ROLE_COLOR[user?.role ?? ""] ?? "#64748b";

  return (
    <div style={{ minHeight: "100vh", background: "var(--tg-page-gradient)", paddingTop: 60 }}>
      <Header />

      <div style={{ maxWidth: 800, margin: "0 auto", padding: "28px 20px 60px" }}>

        {/* ── Page title ── */}
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 28 }}>
          {/* Avatar circle */}
          <div style={{
            width: 52, height: 52, borderRadius: "50%",
            background: `linear-gradient(135deg, ${roleColor}40, ${roleColor}18)`,
            border: `2px solid ${roleColor}60`,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 20, fontWeight: 900, color: roleColor, fontFamily: "monospace",
            flexShrink: 0,
          }}>
            {user ? (displayNameOrUsername(user)?.[0] ?? "?").toUpperCase() : "?"}
          </div>
          <div>
            <div style={{ fontSize: 18, fontWeight: 800, color: "var(--tg-text)", fontFamily: "monospace" }}>
              {loading ? "加载中…" : (displayNameOrUsername(user) || "—")}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 5 }}>
              {user && <RoleBadge role={user.role} />}
              <span style={{ fontSize: 11, color: "rgba(148,163,184,0.45)", fontFamily: "monospace" }}>
                {user?.username}
              </span>
            </div>
          </div>
          <div style={{ marginLeft: "auto" }}>
            <button
              type="button"
              onClick={() => navigate(-1)}
              style={{
                background: "var(--tg-panel-muted)", border: "1px solid var(--tg-panel-border)",
                color: "var(--tg-text-muted)", borderRadius: 7, padding: "6px 14px",
                fontSize: 12, cursor: "pointer", fontFamily: "monospace",
              }}
            >← 返回</button>
          </div>
        </div>

        {/* ── Account summary ── */}
        {user && (
          <Section title="账户信息" accent="rgba(34,211,238,0.25)">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 24px" }}>
              {[
                ["用户名", user.username],
                ["角色", ROLE_LABEL[user.role] ?? user.role],
                ["账户状态", user.status === "ACTIVE" ? "正常" : "已禁用"],
                ["注册时间", user.createdAt ? new Date(user.createdAt).toLocaleDateString("zh-CN") : "—"],
                ["最近登录", user.lastLoginAt ? new Date(user.lastLoginAt).toLocaleString("zh-CN") : "—"],
                ["用户 ID", user.userId || "—"],
              ].map(([k, v]) => (
                <div key={k} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid rgba(71,85,105,0.1)" }}>
                  <span style={{ fontSize: 12, color: "rgba(148,163,184,0.5)", fontFamily: "monospace" }}>{k}</span>
                  <span style={{ fontSize: 12, color: "var(--tg-text)", fontFamily: "monospace", maxWidth: 200, textOverflow: "ellipsis", overflow: "hidden" }}>{v}</span>
                </div>
              ))}
            </div>
          </Section>
        )}

        <div style={{ height: 16 }} />

        {/* ── Edit profile ── */}
        <Section title="编辑资料" accent="rgba(129,140,248,0.25)">
          <Field label="显示名称" value={editDisplayName} onChange={setEditDisplayName} placeholder="输入显示名称…" />
          <Field label="邮箱地址" value={editEmail} onChange={setEditEmail} type="email" placeholder="输入邮箱地址…" />
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 4 }}>
            <button
              type="button"
              disabled={profileSaving}
              onClick={() => { void handleSaveProfile(); }}
              style={{
                padding: "8px 20px", borderRadius: 7,
                background: profileSaving ? "rgba(129,140,248,0.1)" : "rgba(129,140,248,0.2)",
                border: "1px solid rgba(129,140,248,0.45)",
                color: profileSaving ? "rgba(129,140,248,0.4)" : "#818cf8",
                fontFamily: "monospace", fontSize: 12, fontWeight: 700,
                cursor: profileSaving ? "not-allowed" : "pointer",
              }}
            >{profileSaving ? "保存中…" : "保存修改"}</button>
          </div>
        </Section>

        <div style={{ height: 16 }} />

        {/* ── Change password ── */}
        <Section title="修改密码" accent="rgba(251,191,36,0.25)">
          <Field label="当前密码" value={oldPwd} onChange={setOldPwd} type="password" placeholder="输入当前密码" />
          <Field label="新密码（至少 6 位）" value={newPwd} onChange={setNewPwd} type="password" placeholder="输入新密码" />
          <Field label="确认新密码" value={confPwd} onChange={setConfPwd} type="password" placeholder="再次输入新密码" />
          {pwdError && (
            <div style={{ fontSize: 12, color: "#f87171", marginBottom: 10, fontFamily: "monospace" }}>
              ✕ {pwdError}
            </div>
          )}
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
              type="button"
              disabled={pwdSaving}
              onClick={() => { void handleChangePassword(); }}
              style={{
                padding: "8px 20px", borderRadius: 7,
                background: pwdSaving ? "rgba(251,191,36,0.05)" : "rgba(251,191,36,0.12)",
                border: "1px solid rgba(251,191,36,0.4)",
                color: pwdSaving ? "rgba(251,191,36,0.35)" : "#fbbf24",
                fontFamily: "monospace", fontSize: 12, fontWeight: 700,
                cursor: pwdSaving ? "not-allowed" : "pointer",
              }}
            >{pwdSaving ? "修改中…" : "修改密码"}</button>
          </div>
          <div style={{ marginTop: 10, fontSize: 11, color: "rgba(148,163,184,0.35)", fontFamily: "monospace" }}>
            修改密码后需重新登录。管理员可在平台管理页面重置任意用户密码。
          </div>
        </Section>

        {/* ── Recent activity ── */}
        {auditEvents.length > 0 && (
          <>
            <div style={{ height: 16 }} />
            <Section title="近期操作记录" accent="rgba(52,211,153,0.2)">
              <div>
                {auditEvents.map((ev, i) => {
                  const typeColors: Record<string, string> = {
                    LOGIN_SUCCESS: "#34d399", LOGIN_FAILED: "#f87171", LOGOUT: "#94a3b8",
                    REGISTER: "#a78bfa", PASSWORD_CHANGED: "#fbbf24", USER_UPDATED: "#38bdf8",
                  };
                  const c = typeColors[ev.type] ?? "#64748b";
                  return (
                    <div
                      key={i}
                      style={{
                        display: "flex", gap: 10, alignItems: "flex-start",
                        padding: "8px 0",
                        borderBottom: i < auditEvents.length - 1 ? "1px solid rgba(71,85,105,0.1)" : "none",
                      }}
                    >
                      <div style={{ width: 7, height: 7, borderRadius: "50%", background: c, flexShrink: 0, marginTop: 5 }} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 11, color: c, fontFamily: "monospace", fontWeight: 600 }}>{ev.type}</div>
                        {ev.detail && <div style={{ fontSize: 11, color: "rgba(148,163,184,0.5)", fontFamily: "monospace" }}>{ev.detail}</div>}
                      </div>
                      <div style={{ fontSize: 10, color: "rgba(148,163,184,0.3)", fontFamily: "monospace", flexShrink: 0 }}>
                        {ev.timestamp ? new Date(ev.timestamp).toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" }) : "—"}
                      </div>
                    </div>
                  );
                })}
              </div>
            </Section>
          </>
        )}

        {/* ── Danger zone (VIEWER/OPERATOR only; admin is managed separately) ── */}
        {user && user.role !== "ADMIN" && (
          <>
            <div style={{ height: 16 }} />
            <div style={{
              padding: "14px 24px", borderRadius: 12,
              border: "1px solid rgba(248,113,113,0.2)", background: "rgba(248,113,113,0.04)",
            }}>
              <div style={{ fontSize: 10, fontWeight: 800, color: "rgba(248,113,113,0.6)", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 10 }}>
                账户操作
              </div>
              <div style={{ display: "flex", gap: 10 }}>
                <button
                  type="button"
                  onClick={() => { logout(); navigate("/"); }}
                  style={{
                    padding: "7px 16px", borderRadius: 7,
                    background: "rgba(248,113,113,0.1)", border: "1px solid rgba(248,113,113,0.35)",
                    color: "#fca5a5", fontFamily: "monospace", fontSize: 12, cursor: "pointer",
                  }}
                >退出登录</button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
