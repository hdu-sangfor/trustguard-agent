/**
 * ConfigPage — 平台配置中心
 * 路由: /config
 * 展示当前运行时配置：LLM、执行引擎、功能开关、部署环境
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import {
  getRuntimeConfig,
  getConfigOverrides, setConfigOverride, clearConfigOverrides,
  type ApiRuntimeConfig,
} from "@/shared/lib/api";

// ─── Demo fallback ────────────────────────────────────────────────────────────
const DEMO_CONFIG: ApiRuntimeConfig = {
  llm: {
    provider: "openai_compat",
    model: "gpt-4o",
    endpoint_host: "api.openai.com",
    api_key_set: true,
  },
  execution: {
    dispatch_mode: "http",
    max_concurrent: "1",
    plan_mode: "true",
    task_store: "memory",
    subprocess_timeout_buffer: "10",
  },
  features: {
    kb_enabled: "false",
    manager_agent: "false",
    skill_containers: "true",
    trace_redact: "true",
  },
  deployment: {
    mode: "docker",
    workspace_root: "/data/workspace",
  },
  generated_at: new Date().toISOString(),
};

// ─── Helpers ──────────────────────────────────────────────────────────────────
const bool = (v: string | boolean | undefined): boolean =>
  String(v ?? "").toLowerCase() === "true" || v === true;

const PROVIDER_LABELS: Record<string, string> = {
  openai_compat: "OpenAI Compatible",
  anthropic: "Anthropic Claude",
  gemini: "Google Gemini",
  local: "Local (Ollama etc.)",
  auto: "Auto-detect",
};

const MODE_LABELS: Record<string, string> = {
  http: "HTTP Direct (同步)", mq: "RabbitMQ (异步分布式)",
  docker: "Docker 容器化", local: "本地开发模式",
  memory: "内存 (单机)", redis: "Redis (分布式)",
};
const lbl = (map: Record<string, string>, k: string) => map[k] ?? k;

// ─── Sub-components ───────────────────────────────────────────────────────────

function SectionCard({
  title, color, children,
}: { title: string; color: string; children: React.ReactNode }) {
  return (
    <div style={{
      background: "rgba(15,23,42,0.82)",
      border: `1px solid ${color}28`,
      borderRadius: 12, overflow: "hidden",
    }}>
      <div style={{
        padding: "12px 18px",
        borderBottom: `1px solid ${color}18`,
        background: `${color}08`,
        display: "flex", alignItems: "center", gap: 8,
      }}>
        <div style={{ width: 8, height: 8, borderRadius: "50%", background: color, boxShadow: `0 0 6px ${color}` }} />
        <span style={{ fontSize: 12, fontWeight: 700, color: "rgba(226,232,240,0.85)", letterSpacing: "0.07em", textTransform: "uppercase", fontFamily: "monospace" }}>
          {title}
        </span>
      </div>
      <div style={{ padding: "14px 18px", display: "flex", flexDirection: "column", gap: 8 }}>
        {children}
      </div>
    </div>
  );
}

function KV({ k, v, vColor, mono = true }: { k: string; v: React.ReactNode; vColor?: string; mono?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", minHeight: 22 }}>
      <span style={{ fontSize: 11, color: "rgba(148,163,184,0.55)", flexShrink: 0, marginRight: 12 }}>{k}</span>
      <span style={{
        fontSize: 11, fontFamily: mono ? "monospace" : undefined,
        color: vColor ?? "rgba(226,232,240,0.75)",
        textAlign: "right",
      }}>{v}</span>
    </div>
  );
}

function StatusDot({ active }: { active: boolean }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      fontSize: 11, color: active ? "#34d399" : "rgba(148,163,184,0.4)",
      fontFamily: "monospace",
    }}>
      <span style={{
        width: 7, height: 7, borderRadius: "50%",
        background: active ? "#34d399" : "#475569",
        boxShadow: active ? "0 0 5px rgba(52,211,153,0.7)" : "none",
      }} />
      {active ? "已启用" : "已禁用"}
    </span>
  );
}

function FlagRow({
  label, desc, active, onToggle, canToggle, pending,
}: { label: string; desc: string; active: boolean; onToggle?: () => void; canToggle: boolean; pending: boolean }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "10px 0", borderBottom: "1px solid rgba(71,85,105,0.15)",
    }}>
      <div style={{ flex: 1, minWidth: 0, marginRight: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: "rgba(226,232,240,0.8)" }}>{label}</div>
        <div style={{ fontSize: 10, color: "rgba(148,163,184,0.45)", marginTop: 2 }}>{desc}</div>
      </div>
      {canToggle ? (
        <button
          type="button"
          disabled={pending}
          onClick={onToggle}
          style={{
            width: 44, height: 24, borderRadius: 12, border: "none",
            background: active ? "rgba(34,211,238,0.25)" : "rgba(71,85,105,0.35)",
            cursor: pending ? "not-allowed" : "pointer",
            position: "relative", flexShrink: 0,
            transition: "background 0.2s",
            opacity: pending ? 0.5 : 1,
          }}
        >
          <span style={{
            position: "absolute", top: 3,
            left: active ? "calc(100% - 21px)" : 3,
            width: 18, height: 18, borderRadius: "50%",
            background: active ? "#22d3ee" : "#64748b",
            transition: "left 0.2s, background 0.2s",
            boxShadow: active ? "0 0 6px rgba(34,211,238,0.6)" : "none",
          }} />
        </button>
      ) : (
        <StatusDot active={active} />
      )}
    </div>
  );
}

function EditableField({
  label, value, onChange, disabled, onSave,
}: { label: string; value: string; onChange: (v: string) => void; disabled: boolean; onSave?: () => void }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
      <label style={{ fontSize: 11, color: "rgba(148,163,184,0.6)" }}>{label}</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && onSave && !disabled) {
            e.preventDefault();
            onSave();
          }
        }}
        disabled={disabled}
        style={{
          fontSize: 12, padding: "7px 10px", borderRadius: 6,
          border: disabled ? "1px solid rgba(71,85,105,0.2)" : "1px solid rgba(34,211,238,0.35)",
          background: disabled ? "rgba(15,23,42,0.3)" : "rgba(15,23,42,0.6)",
          color: disabled ? "rgba(148,163,184,0.4)" : "rgba(226,232,240,0.85)",
          fontFamily: "monospace",
        }}
      />
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────
export default function ConfigPage() {
  const navigate = useNavigate();
  const { loggedIn } = useAppSession();

  const [config, setConfig] = useState<ApiRuntimeConfig | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState("");
  const [editMode, setEditMode] = useState(false);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [savePending, setSavePending] = useState(false);

  // Auth guard
  useEffect(() => {
    if (!loggedIn) {
      toast.error("请先登录");
      localStorage.setItem("sentinel_login_redirect", "/config");
      navigate("/login");
    }
  }, [loggedIn, navigate]);

  const loadConfig = useCallback(async () => {
    try {
      const [cfg, overridesList] = await Promise.all([
        getRuntimeConfig(),
        getConfigOverrides(),
      ]);
      setConfig(cfg);
      setOverrides(overridesList ?? {});
      setEditValues(overridesList ?? {});
      setOnline(true);
    } catch {
      setConfig(DEMO_CONFIG);
      setOverrides({});
      setEditValues({});
      setOnline(false);
    } finally {
      setLoading(false);
      setLastRefresh(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    }
  }, []);

  useEffect(() => { void loadConfig(); }, [loadConfig]);

  const handleSaveOverrides = async () => {
    if (!online || savePending) return;
    setSavePending(true);
    try {
      await setConfigOverride(editValues);
      setOverrides(editValues);
      setEditMode(false);
      toast.success("配置覆盖已保存（内存，重启后重置）");
      void loadConfig();
    } catch (e) {
      toast.error(`保存失败: ${String(e)}`);
    } finally {
      setSavePending(false);
    }
  };

  const handleResetOverrides = async () => {
    if (!online || savePending) return;
    setSavePending(true);
    try {
      await clearConfigOverrides();
      setOverrides({});
      setEditValues({});
      setEditMode(false);
      toast.success("所有覆盖已清除");
      void loadConfig();
    } catch (e) {
      toast.error(`重置失败: ${String(e)}`);
    } finally {
      setSavePending(false);
    }
  };

  const cfg = config ?? DEMO_CONFIG;

  // Quick nav links
  const quickLinks = [
    { label: "管理中心", path: "/dashboard", color: "#22d3ee" },
    { label: "监控大屏", path: "/monitor",  color: "#34d399" },
    { label: "批量调度", path: "/batch",    color: "#38bdf8" },
    { label: "平台管理", path: "/admin",    color: "#818cf8" },
    { label: "漏洞库",   path: "/vulns",    color: "#ef4444" },
    { label: "系统状态", path: "/system",   color: "#a78bfa" },
    { label: "技能库",   path: "/skills",   color: "#fb923c" },
  ];

  return (
    <div style={{ minHeight: "100vh", background: "linear-gradient(180deg, #0a0f1e 0%, #0f172a 50%, #0a0f1e 100%)", paddingTop: 60 }}>
      <Header />

      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "28px 20px" }}>

        {/* ── Page header ── */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 28 }}>
          <div>
            <h1 style={{ margin: "0 0 4px", fontSize: 22, fontWeight: 800, color: "#e2e8f0", fontFamily: "monospace", letterSpacing: "0.08em" }}>
              平台配置中心
            </h1>
            <div style={{ fontSize: 12, color: "rgba(148,163,184,0.5)", fontFamily: "monospace" }}>
              {online === true ? (
                <span style={{ color: "rgba(52,211,153,0.8)" }}>● 实时配置 · 后端数据</span>
              ) : online === false ? (
                <span style={{ color: "rgba(251,191,36,0.7)" }}>○ 演示模式 · 典型配置值</span>
              ) : "正在加载…"}
              {Object.keys(overrides).length > 0 && (
                <span style={{ marginLeft: 12, color: "rgba(129,140,248,0.7)" }}>⚙ {Object.keys(overrides).length} 个覆盖项</span>
              )}
              {lastRefresh && <span style={{ marginLeft: 12 }}>更新于 {lastRefresh}</span>}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={() => { setLoading(true); void loadConfig(); }}
              style={{
                background: "rgba(34,211,238,0.1)", border: "1px solid rgba(34,211,238,0.35)",
                color: "#22d3ee", borderRadius: 7, padding: "7px 16px",
                fontSize: 12, cursor: "pointer", fontFamily: "monospace",
              }}
            >
              ⟳ 刷新
            </button>
            {!editMode ? (
              <button
                type="button"
                onClick={() => setEditMode(true)}
                disabled={!online}
                style={{
                  background: "rgba(129,140,248,0.15)", border: "1px solid rgba(129,140,248,0.35)",
                  color: online ? "#a5b4fc" : "#64748b", borderRadius: 7, padding: "7px 16px",
                  fontSize: 12, cursor: online ? "pointer" : "not-allowed", fontFamily: "monospace",
                  opacity: online ? 1 : 0.5,
                }}
              >
                ✏ 编辑
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => void handleSaveOverrides()}
                  disabled={savePending}
                  style={{
                    background: "rgba(52,211,153,0.15)", border: "1px solid rgba(52,211,153,0.35)",
                    color: savePending ? "#64748b" : "#34d399", borderRadius: 7, padding: "7px 16px",
                    fontSize: 12, cursor: savePending ? "not-allowed" : "pointer", fontFamily: "monospace",
                    opacity: savePending ? 0.5 : 1,
                  }}
                >
                  {savePending ? "保存中…" : "✓ 保存"}
                </button>
                <button
                  type="button"
                  onClick={() => { setEditMode(false); setEditValues(overrides); }}
                  disabled={savePending}
                  style={{
                    background: "rgba(251,191,36,0.1)", border: "1px solid rgba(251,191,36,0.3)",
                    color: savePending ? "#64748b" : "#fbbf24", borderRadius: 7, padding: "7px 16px",
                    fontSize: 12, cursor: savePending ? "not-allowed" : "pointer", fontFamily: "monospace",
                    opacity: savePending ? 0.5 : 1,
                  }}
                >
                  ✗ 取消
                </button>
              </>
            )}
            {Object.keys(overrides).length > 0 && (
              <button
                type="button"
                onClick={() => void handleResetOverrides()}
                disabled={savePending || editMode}
                style={{
                  background: "rgba(248,113,113,0.1)", border: "1px solid rgba(248,113,113,0.3)",
                  color: savePending || editMode ? "#64748b" : "#fca5a5", borderRadius: 7, padding: "7px 16px",
                  fontSize: 12, cursor: savePending || editMode ? "not-allowed" : "pointer", fontFamily: "monospace",
                  opacity: savePending || editMode ? 0.5 : 1,
                }}
              >
                🔄 重置
              </button>
            )}
            <button
              type="button"
              onClick={() => navigate("/admin")}
              style={{
                background: "rgba(15,23,42,0.5)", border: "1px solid rgba(71,85,105,0.4)",
                color: "#94a3b8", borderRadius: 7, padding: "7px 16px",
                fontSize: 12, cursor: "pointer", fontFamily: "monospace",
              }}
            >
              ← 平台管理
            </button>
          </div>
        </div>

        {loading ? (
          <div style={{ textAlign: "center", padding: "80px 0", color: "rgba(148,163,184,0.35)", fontFamily: "monospace" }}>
            正在加载配置…
          </div>
        ) : (
          <>
            {/* ── Config grid ── */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(500px, 1fr))", gap: 18, marginBottom: 20 }}>

              {/* AI 引擎 */}
              <SectionCard title="AI 引擎配置" color="#818cf8">
                {editMode ? (
                  <>
                    <EditableField
                      label="LLM 提供商"
                      value={editValues.LLM_PROVIDER ?? cfg.llm.provider}
                      onChange={(v) => setEditValues({ ...editValues, LLM_PROVIDER: v })}
                      disabled={!online}
                      onSave={() => void handleSaveOverrides()}
                    />
                    <EditableField
                      label="模型"
                      value={editValues.LLM_MODEL_ID ?? cfg.llm.model}
                      onChange={(v) => setEditValues({ ...editValues, LLM_MODEL_ID: v })}
                      disabled={!online}
                      onSave={() => void handleSaveOverrides()}
                    />
                    <EditableField
                      label="API 端点"
                      value={editValues.LLM_BASE_URL ?? cfg.llm.endpoint_host}
                      onChange={(v) => setEditValues({ ...editValues, LLM_BASE_URL: v })}
                      disabled={!online}
                      onSave={() => void handleSaveOverrides()}
                    />
                  </>
                ) : (
                  <>
                    <KV k="LLM 提供商" v={lbl(PROVIDER_LABELS, cfg.llm.provider)} vColor="#a5b4fc" />
                    <KV k="模型" v={cfg.llm.model} vColor="#c4b5fd" />
                    <KV k="API 端点" v={cfg.llm.endpoint_host} vColor="rgba(148,163,184,0.7)" />
                  </>
                )}
                <KV k="API Key" v={
                  cfg.llm.api_key_set
                    ? <span style={{ color: "#34d399" }}>✓ 已配置</span>
                    : <span style={{ color: "#f87171" }}>✗ 未配置</span>
                } />
                <div style={{ marginTop: 6, padding: "8px 12px", background: "rgba(129,140,248,0.07)", borderRadius: 6, border: "1px solid rgba(129,140,248,0.15)" }}>
                  <div style={{ fontSize: 10, color: "rgba(148,163,184,0.5)", marginBottom: 4 }}>支持的 LLM 提供商</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {["openai_compat", "anthropic", "gemini", "local", "auto"].map(p => (
                      <span key={p} style={{
                        fontSize: 9, padding: "2px 7px", borderRadius: 4,
                        background: p === cfg.llm.provider ? "rgba(129,140,248,0.2)" : "rgba(71,85,105,0.2)",
                        border: `1px solid ${p === cfg.llm.provider ? "rgba(129,140,248,0.5)" : "rgba(71,85,105,0.3)"}`,
                        color: p === cfg.llm.provider ? "#a5b4fc" : "rgba(148,163,184,0.4)",
                        fontFamily: "monospace",
                      }}>{p}</span>
                    ))}
                  </div>
                </div>
              </SectionCard>

              {/* 执行引擎 */}
              <SectionCard title="执行引擎配置" color="#22d3ee">
                <KV k="调度模式" v={lbl(MODE_LABELS, cfg.execution.dispatch_mode)} vColor={cfg.execution.dispatch_mode === "mq" ? "#22d3ee" : "#94a3b8"} />
                {editMode ? (
                  <EditableField
                    label="任务并发上限"
                    value={editValues.MAX_IN_FLIGHT_SKILLS_PER_TASK ?? cfg.execution.max_concurrent}
                    onChange={(v) => setEditValues({ ...editValues, MAX_IN_FLIGHT_SKILLS_PER_TASK: v })}
                    disabled={!online}
                    onSave={() => void handleSaveOverrides()}
                  />
                ) : (
                  <KV k="任务并发上限" v={`${cfg.execution.max_concurrent} 个技能 / 任务`} vColor="#e2e8f0" />
                )}
                <KV k="Plan 模式" v={<StatusDot active={bool(cfg.execution.plan_mode)} />} />
                <KV k="任务状态存储" v={lbl(MODE_LABELS, cfg.execution.task_store)} vColor="#94a3b8" />
                <div style={{ marginTop: 4, padding: "7px 12px", background: "rgba(34,211,238,0.05)", borderRadius: 6, border: "1px solid rgba(34,211,238,0.12)" }}>
                  <div style={{ fontSize: 10, color: "rgba(34,211,238,0.6)" }}>
                    {cfg.execution.dispatch_mode === "mq"
                      ? "MQ 模式：技能异步分发至 RabbitMQ，支持多 worker 水平扩展"
                      : "HTTP 模式：技能同步调用 executor，适合单机部署"}
                  </div>
                </div>
              </SectionCard>

              {/* 部署环境 */}
              <SectionCard title="部署环境" color="#fb923c">
                <KV k="部署模式" v={lbl(MODE_LABELS, cfg.deployment.mode)} vColor="#fed7aa" />
                <KV k="工作区路径" v={cfg.deployment.workspace_root} vColor="rgba(148,163,184,0.65)" />
                <div style={{ marginTop: 4, padding: "7px 12px", background: "rgba(251,146,60,0.06)", borderRadius: 6, border: "1px solid rgba(251,146,60,0.12)" }}>
                  <div style={{ fontSize: 10, color: "rgba(251,146,60,0.6)" }}>
                    工作区存储渗透测试生成的所有 artifacts、截图与扫描结果
                  </div>
                </div>
              </SectionCard>

              {/* 功能开关 */}
              <SectionCard title="功能开关" color="#34d399">
                <FlagRow
                  label="Knowledge Base"
                  desc="Qdrant 向量知识库 — 为 LLM 决策提供漏洞知识增强"
                  active={bool(cfg.features.kb_enabled)}
                  canToggle={false}
                  pending={false}
                />
                <FlagRow
                  label="Manager Agent"
                  desc="TodoList 驱动的 Manager Agent，维护 PTES 有序 Todo"
                  active={bool(cfg.features.manager_agent)}
                  canToggle={false}
                  pending={false}
                />
                <FlagRow
                  label="Skill Containers"
                  desc="使用 Docker 技能容器执行（false = 在宿主机运行）"
                  active={bool(cfg.features.skill_containers)}
                  canToggle={false}
                  pending={false}
                />
                <FlagRow
                  label="Trace Redaction"
                  desc="自动遮蔽轨迹响应中的 Bearer/Cookie 敏感信息"
                  active={bool(cfg.features.trace_redact)}
                  canToggle={false}
                  pending={false}
                />
                {!online && (
                  <div style={{ fontSize: 10, color: "rgba(251,191,36,0.6)", marginTop: 4, fontStyle: "italic" }}>
                    功能开关切换需要后端在线
                  </div>
                )}
              </SectionCard>
            </div>

            {/* ── LLM Provider info bar ── */}
            <div style={{
              padding: "14px 20px", marginBottom: 20,
              background: "rgba(15,23,42,0.7)", border: "1px solid rgba(129,140,248,0.2)",
              borderRadius: 10,
              display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12,
            }}>
              <div style={{ fontSize: 12, color: "rgba(148,163,184,0.6)", fontFamily: "monospace" }}>
                <span style={{ color: "#a5b4fc", fontWeight: 700, marginRight: 10 }}>当前 AI 推理链路</span>
                <span style={{ color: "#e2e8f0" }}>{cfg.llm.endpoint_host}</span>
                <span style={{ color: "rgba(148,163,184,0.4)", margin: "0 8px" }}>›</span>
                <span style={{ color: "#c4b5fd" }}>{lbl(PROVIDER_LABELS, cfg.llm.provider)}</span>
                <span style={{ color: "rgba(148,163,184,0.4)", margin: "0 8px" }}>›</span>
                <span style={{ color: "#e2e8f0", fontWeight: 700 }}>{cfg.llm.model}</span>
              </div>
              <div style={{ display: "flex", gap: 12, fontSize: 11, fontFamily: "monospace" }}>
                <span style={{ color: bool(cfg.execution.plan_mode) ? "#34d399" : "#64748b" }}>
                  {bool(cfg.execution.plan_mode) ? "✓" : "✗"} Plan Mode
                </span>
                <span style={{ color: cfg.execution.dispatch_mode === "mq" ? "#22d3ee" : "#64748b" }}>
                  {cfg.execution.dispatch_mode.toUpperCase()} Dispatch
                </span>
                <span style={{ color: "#94a3b8" }}>×{cfg.execution.max_concurrent} 并发</span>
              </div>
            </div>

            {/* ── Quick nav ── */}
            <div style={{
              padding: "14px 18px",
              background: "rgba(15,23,42,0.55)", border: "1px solid rgba(71,85,105,0.2)",
              borderRadius: 10, display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
            }}>
              <span style={{ fontSize: 11, color: "rgba(148,163,184,0.45)", fontFamily: "monospace", marginRight: 4 }}>
                快速导航：
              </span>
              {quickLinks.map(({ label, path, color }) => (
                <button
                  key={path}
                  type="button"
                  onClick={() => navigate(path)}
                  style={{
                    padding: "5px 14px", borderRadius: 6, fontSize: 11,
                    border: `1px solid ${color}35`, background: `${color}0a`,
                    color, cursor: "pointer", fontFamily: "monospace",
                    transition: "background 0.15s",
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = `${color}18`)}
                  onMouseLeave={e => (e.currentTarget.style.background = `${color}0a`)}
                >
                  {label}
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
