/**
 * BatchPage — 批量渗透调度中心
 * 路由: /batch
 * 支持一次性提交多个渗透目标，自动创建并（可选）启动任务，
 * 直观展示并发执行能力（≥3 目标）。
 */
import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import { batchCreateTasks, type ApiBatchTaskResult } from "@/shared/lib/api";

// ── Demo target presets ───────────────────────────────────────────────────────
const DEMO_PRESETS = [
  {
    label: "Web 三连",
    targets: "http://192.168.1.100/dvwa/\nhttp://192.168.1.101:8080/\nhttp://192.168.1.102:8080/struts2/",
    prefix: "Web渗透",
    desc: "Web 应用自动化渗透测试，含 SQL 注入、RCE、未授权访问等检测",
  },
  {
    label: "内网扫描",
    targets: "192.168.10.1\n192.168.10.5\n192.168.10.10\n192.168.10.20",
    prefix: "内网渗透",
    desc: "内网主机综合渗透：端口扫描 + 服务识别 + 漏洞利用",
  },
  {
    label: "靶场组合",
    targets: "http://target.vulnlab.local/\nhttp://target.vulnlab.local:8080/\nhttp://target.vulnlab.local:8443/",
    prefix: "靶场测试",
    desc: "多端口渗透测试，覆盖 Web、REST API、管理界面",
  },
];

// ── Phase & status helpers ────────────────────────────────────────────────────
const STATUS_COLORS: Record<string, string> = {
  ok:    "#34d399",
  error: "#f87171",
  wait:  "#fbbf24",
};

function TaskResultRow({ item, idx }: { item: ApiBatchTaskResult & { _status?: string }; idx: number }) {
  const navigate = useNavigate();
  const ok = !item.error && !!item.taskId;
  const color = ok ? STATUS_COLORS.ok : STATUS_COLORS.error;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "10px 14px",
      background: idx % 2 === 0 ? "transparent" : "rgba(15,23,42,0.25)",
      borderBottom: "1px solid rgba(51,65,85,0.2)",
    }}>
      <span style={{
        width: 8, height: 8, borderRadius: "50%", background: color,
        boxShadow: `0 0 6px ${color}`, flexShrink: 0,
      }} />
      <span style={{ flex: "0 0 200px", fontSize: 12, fontFamily: "monospace", color: "#94a3b8", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {item.target}
      </span>
      {ok ? (
        <>
          <span style={{ flex: 1, fontSize: 12, color: "#e2e8f0", fontWeight: 600 }}>{item.name}</span>
          <span style={{ fontSize: 10, fontFamily: "monospace", color: "rgba(148,163,184,0.4)" }}>
            {item.taskId?.slice(-12)}
          </span>
          <button
            type="button"
            onClick={() => navigate(`/trace/${item.taskId}`)}
            style={{
              padding: "3px 10px", borderRadius: 4,
              background: "rgba(34,211,238,0.08)", border: "1px solid rgba(34,211,238,0.3)",
              color: "#22d3ee", fontSize: 11, cursor: "pointer", fontFamily: "monospace",
            }}
          >追踪</button>
        </>
      ) : (
        <span style={{ flex: 1, fontSize: 12, color: "#f87171" }}>{item.error ?? "创建失败"}</span>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function BatchPage() {
  const navigate = useNavigate();
  const { loggedIn } = useAppSession();

  const [targetsRaw, setTargetsRaw] = useState("");
  const [namePrefix, setNamePrefix] = useState("批量任务");
  const [description, setDescription] = useState("自动化渗透测试 — 批量目标");
  const [autoStart, setAutoStart] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<null | {
    created: number; started_count: number; auto_started: boolean;
    tasks: ApiBatchTaskResult[]; generated_at: string;
  }>(null);

  // Auth guard (soft — demo mode still works)
  const isLoggedIn = loggedIn;

  const parsedTargets = targetsRaw
    .split("\n")
    .map((t) => t.trim())
    .filter((t) => t.length > 0 && !t.startsWith("#"));

  const handleSubmit = useCallback(async () => {
    if (parsedTargets.length === 0) {
      toast.error("请至少输入一个目标地址");
      return;
    }
    if (parsedTargets.length > 20) {
      toast.error("单次最多 20 个目标");
      return;
    }
    if (!isLoggedIn) {
      toast.error("请先登录");
      localStorage.setItem("sentinel_login_redirect", "/batch");
      navigate("/login");
      return;
    }
    setSubmitting(true);
    try {
      const data = await batchCreateTasks({
        targets: parsedTargets,
        name_prefix: namePrefix,
        description,
        auto_start: autoStart,
      });
      setResult(data);
      toast.success(`成功创建 ${String(data.created)} 个任务${autoStart ? `，已启动 ${String(data.started_count)} 个` : ""}`);
    } catch (e) {
      toast.error(`批量创建失败：${String(e)}`);
    } finally {
      setSubmitting(false);
    }
  }, [parsedTargets, namePrefix, description, autoStart, isLoggedIn, navigate]);

  return (
    <div style={{ minHeight: "100vh", background: "#020a12", paddingTop: 80, paddingBottom: 60 }}>
      <Header />

      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "0 24px" }}>

        {/* Title */}
        <div style={{ marginBottom: 28 }}>
          <h1 style={{ margin: "0 0 6px", fontSize: 22, fontWeight: 800, color: "#e2e8f0", fontFamily: "monospace", letterSpacing: "0.06em" }}>
            批量渗透调度
            <span style={{ marginLeft: 12, fontSize: 12, color: "rgba(148,163,184,0.5)", fontWeight: 400 }}>Batch Security Dispatch</span>
          </h1>
          <div style={{ fontSize: 12, color: "rgba(148,163,184,0.5)", fontFamily: "monospace" }}>
            一次提交多个目标，平台并发执行全自动渗透测试 · 每目标独立工作区 · ≥3 并发能力
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 20, alignItems: "start" }}>

          {/* ── Left: Target list ── */}
          <div style={{
            background: "rgba(15,23,42,0.6)", border: "1px solid rgba(51,65,85,0.5)",
            borderRadius: 10, overflow: "hidden",
          }}>
            <div style={{
              padding: "12px 16px", borderBottom: "1px solid rgba(51,65,85,0.4)",
              display: "flex", justifyContent: "space-between", alignItems: "center",
            }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: "rgba(226,232,240,0.85)", fontFamily: "monospace", letterSpacing: "0.05em" }}>
                目标列表
              </span>
              <span style={{ fontSize: 11, color: parsedTargets.length > 0 ? "#22d3ee" : "rgba(148,163,184,0.4)", fontFamily: "monospace" }}>
                {parsedTargets.length} 个目标{parsedTargets.length > 20 ? " ⚠ 最多20" : ""}
              </span>
            </div>
            <div style={{ padding: 16 }}>
              <textarea
                value={targetsRaw}
                onChange={(e) => setTargetsRaw(e.target.value)}
                placeholder={"每行一个目标地址，例如：\nhttp://192.168.1.100/\nhttp://target.example.com:8080/api\n192.168.1.50\n# 以 # 开头的行为注释，会被忽略"}
                rows={12}
                style={{
                  width: "100%", boxSizing: "border-box",
                  background: "rgba(2,6,23,0.8)", border: "1px solid rgba(51,65,85,0.4)",
                  borderRadius: 6, color: "#e2e8f0", fontSize: 13, fontFamily: "monospace",
                  padding: "10px 12px", resize: "vertical", outline: "none",
                  lineHeight: 1.7,
                }}
              />

              {/* Preset buttons */}
              <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
                <span style={{ fontSize: 11, color: "rgba(148,163,184,0.4)", fontFamily: "monospace", alignSelf: "center" }}>
                  预设：
                </span>
                {DEMO_PRESETS.map((p) => (
                  <button
                    key={p.label}
                    type="button"
                    onClick={() => {
                      setTargetsRaw(p.targets);
                      setNamePrefix(p.prefix);
                      setDescription(p.desc);
                    }}
                    style={{
                      padding: "4px 12px", borderRadius: 5,
                      background: "rgba(34,211,238,0.06)", border: "1px solid rgba(34,211,238,0.2)",
                      color: "#94a3b8", fontSize: 11, cursor: "pointer", fontFamily: "monospace",
                    }}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Parsed preview */}
            {parsedTargets.length > 0 && (
              <div style={{ borderTop: "1px solid rgba(51,65,85,0.3)", padding: "10px 16px" }}>
                <div style={{ fontSize: 10, fontFamily: "monospace", color: "rgba(148,163,184,0.4)", marginBottom: 6 }}>解析预览</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                  {parsedTargets.slice(0, 6).map((t, i) => (
                    <div key={i} style={{ fontSize: 12, fontFamily: "monospace", color: "rgba(226,232,240,0.6)" }}>
                      <span style={{ color: "rgba(34,211,238,0.5)", marginRight: 8 }}>{String(i + 1).padStart(2, "0")}</span>
                      {t}
                    </div>
                  ))}
                  {parsedTargets.length > 6 && (
                    <div style={{ fontSize: 11, color: "rgba(148,163,184,0.3)", fontFamily: "monospace" }}>
                      …还有 {parsedTargets.length - 6} 个
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* ── Right: Settings + Submit ── */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>

            {/* Settings card */}
            <div style={{
              background: "rgba(15,23,42,0.6)", border: "1px solid rgba(51,65,85,0.5)",
              borderRadius: 10, padding: 16,
            }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "rgba(226,232,240,0.85)", fontFamily: "monospace", letterSpacing: "0.05em", marginBottom: 14 }}>
                任务设置
              </div>

              <label style={{ display: "block", marginBottom: 12 }}>
                <div style={{ fontSize: 11, color: "rgba(148,163,184,0.6)", fontFamily: "monospace", marginBottom: 4 }}>名称前缀</div>
                <input
                  type="text"
                  value={namePrefix}
                  onChange={(e) => setNamePrefix(e.target.value)}
                  placeholder="批量任务"
                  style={{
                    width: "100%", boxSizing: "border-box",
                    background: "rgba(2,6,23,0.7)", border: "1px solid rgba(51,65,85,0.4)",
                    borderRadius: 5, color: "#e2e8f0", fontSize: 12, fontFamily: "monospace",
                    padding: "7px 10px", outline: "none",
                  }}
                />
                <div style={{ fontSize: 10, color: "rgba(148,163,184,0.3)", marginTop: 3, fontFamily: "monospace" }}>
                  任务将命名为 "{namePrefix}-01", "{namePrefix}-02", …
                </div>
              </label>

              <label style={{ display: "block", marginBottom: 12 }}>
                <div style={{ fontSize: 11, color: "rgba(148,163,184,0.6)", fontFamily: "monospace", marginBottom: 4 }}>描述（所有任务共用）</div>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={3}
                  style={{
                    width: "100%", boxSizing: "border-box",
                    background: "rgba(2,6,23,0.7)", border: "1px solid rgba(51,65,85,0.4)",
                    borderRadius: 5, color: "#e2e8f0", fontSize: 12, fontFamily: "monospace",
                    padding: "7px 10px", resize: "none", outline: "none",
                  }}
                />
              </label>

              {/* Auto-start toggle */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 0", borderTop: "1px solid rgba(51,65,85,0.3)" }}>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: "rgba(226,232,240,0.8)" }}>创建后立即启动</div>
                  <div style={{ fontSize: 10, color: "rgba(148,163,184,0.4)", marginTop: 2 }}>调用编排器开始渗透流程</div>
                </div>
                <button
                  type="button"
                  onClick={() => setAutoStart(!autoStart)}
                  style={{
                    width: 44, height: 24, borderRadius: 12, border: "none",
                    background: autoStart ? "rgba(34,211,238,0.25)" : "rgba(71,85,105,0.35)",
                    cursor: "pointer", position: "relative", transition: "background 0.2s",
                  }}
                >
                  <span style={{
                    position: "absolute", top: 3,
                    left: autoStart ? "calc(100% - 21px)" : 3,
                    width: 18, height: 18, borderRadius: "50%",
                    background: autoStart ? "#22d3ee" : "#64748b",
                    transition: "left 0.2s, background 0.2s",
                    boxShadow: autoStart ? "0 0 6px rgba(34,211,238,0.6)" : "none",
                  }} />
                </button>
              </div>
            </div>

            {/* Submit button */}
            <button
              type="button"
              onClick={() => void handleSubmit()}
              disabled={submitting || parsedTargets.length === 0}
              style={{
                padding: "14px",
                background: parsedTargets.length > 0 && !submitting
                  ? "linear-gradient(135deg, rgba(34,211,238,0.2), rgba(129,140,248,0.2))"
                  : "rgba(51,65,85,0.2)",
                border: `1px solid ${parsedTargets.length > 0 && !submitting ? "rgba(34,211,238,0.5)" : "rgba(51,65,85,0.3)"}`,
                borderRadius: 8, cursor: parsedTargets.length > 0 && !submitting ? "pointer" : "not-allowed",
                color: parsedTargets.length > 0 && !submitting ? "#e2e8f0" : "#475569",
                fontSize: 14, fontWeight: 700, fontFamily: "monospace",
                letterSpacing: "0.08em", transition: "all 0.2s",
              }}
            >
              {submitting
                ? "创建中…"
                : autoStart
                  ? `▶  启动 ${parsedTargets.length} 个渗透任务`
                  : `+ 创建 ${parsedTargets.length} 个任务`
              }
            </button>

            {/* Concurrent capability callout */}
            <div style={{
              padding: "12px 14px",
              background: "rgba(34,197,94,0.05)", border: "1px solid rgba(34,197,94,0.15)",
              borderRadius: 8,
            }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: "#86efac", fontFamily: "monospace", marginBottom: 6, letterSpacing: "0.05em" }}>
                ◎ 平台并发能力
              </div>
              {[
                ["并发目标", "≥ 3 独立工作区"],
                ["执行模式", autoStart ? "HTTP 直连编排器" : "PENDING 待启动"],
                ["单目标时间", "≤ 15 分钟"],
                ["工具覆盖", "40+ 安全技能"],
              ].map(([k, v]) => (
                <div key={k} style={{ display: "flex", justifyContent: "space-between", fontSize: 11, fontFamily: "monospace", marginBottom: 3 }}>
                  <span style={{ color: "#64748b" }}>{k}</span>
                  <span style={{ color: "#22d3ee" }}>{v}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ── Results ── */}
        {result && (
          <div style={{
            marginTop: 28,
            background: "rgba(15,23,42,0.6)", border: "1px solid rgba(51,65,85,0.5)",
            borderRadius: 10, overflow: "hidden",
          }}>
            <div style={{
              padding: "12px 16px", borderBottom: "1px solid rgba(51,65,85,0.4)",
              display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap",
            }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: "rgba(226,232,240,0.9)", fontFamily: "monospace" }}>
                创建结果
              </span>
              <span style={{ fontSize: 11, fontFamily: "monospace", color: "#34d399" }}>
                ✓ 成功创建 {result.created} 个
              </span>
              {result.auto_started && (
                <span style={{ fontSize: 11, fontFamily: "monospace", color: "#22d3ee" }}>
                  ▶ 已启动 {result.started_count} 个
                </span>
              )}
              <span style={{ fontSize: 10, fontFamily: "monospace", color: "rgba(148,163,184,0.3)", marginLeft: "auto" }}>
                {new Date(result.generated_at).toLocaleTimeString("zh-CN")}
              </span>
            </div>
            <div>
              {result.tasks.map((item, i) => (
                <TaskResultRow key={item.taskId ?? i} item={item} idx={i} />
              ))}
            </div>
            <div style={{ padding: "12px 16px", borderTop: "1px solid rgba(51,65,85,0.3)", display: "flex", gap: 12 }}>
              <button type="button" onClick={() => navigate("/tasks")}
                style={{ background: "none", border: "none", color: "#38bdf8", cursor: "pointer", fontSize: 12, fontFamily: "monospace", padding: 0 }}>
                → 任务管理
              </button>
              <button type="button" onClick={() => navigate("/monitor")}
                style={{ background: "none", border: "none", color: "#34d399", cursor: "pointer", fontSize: 12, fontFamily: "monospace", padding: 0 }}>
                → 监控大屏
              </button>
              <button type="button" onClick={() => setResult(null)}
                style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer", fontSize: 12, fontFamily: "monospace", padding: 0, marginLeft: "auto" }}>
                清除结果
              </button>
            </div>
          </div>
        )}

        {/* Quick nav */}
        <div style={{
          marginTop: 28, padding: "10px 16px",
          background: "rgba(15,23,42,0.4)", border: "1px solid rgba(51,65,85,0.3)",
          borderRadius: 8, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center",
          fontSize: 11, fontFamily: "monospace",
        }}>
          <span style={{ color: "#475569" }}>导航：</span>
          {[
            { label: "任务管理", path: "/tasks",     color: "#38bdf8" },
            { label: "监控大屏", path: "/monitor",  color: "#34d399" },
            { label: "统计分析", path: "/stats",    color: "#f87171" },
            { label: "漏洞库",   path: "/vulns",    color: "#ef4444" },
            { label: "报告中心", path: "/reports",  color: "#fb923c" },
            { label: "管理中心", path: "/dashboard", color: "#22d3ee" },
            { label: "平台管理", path: "/admin",    color: "#a78bfa" },
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
