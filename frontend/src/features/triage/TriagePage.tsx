/**
 * TriagePage — 告警研判中心
 * 路由: /triage
 * 功能: 创建研判任务、查看研判历史、查看研判结果概要
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Search, Shield, AlertTriangle, CheckCircle, XCircle, HelpCircle, Plus, RefreshCw, Clock, Database } from "lucide-react";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import {
  createTriageTask,
  listTriageTasks,
  getXdrProxyHealth as getGatewayHealth,
  runTriageTask,
  type ApiTriageTask,
} from "@/shared/lib/api";

const VERDICT_CONFIG: Record<string, { label: string; color: string; bg: string; icon: React.ReactNode }> = {
  true_positive:     { label: "确认恶意", color: "#f87171", bg: "rgba(248,113,113,0.12)", icon: <AlertTriangle size={14} /> },
  false_positive:    { label: "误报",     color: "#34d399", bg: "rgba(52,211,153,0.12)", icon: <CheckCircle size={14} /> },
  suspicious:        { label: "可疑",     color: "#fbbf24", bg: "rgba(251,191,36,0.12)",  icon: <HelpCircle size={14} /> },
  insufficient_evidence: { label: "证据不足", color: "#94a3b8", bg: "rgba(148,163,184,0.12)", icon: <XCircle size={14} /> },
};

const STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  PENDING: { label: "排队中", color: "#64748b" },
  RUNNING: { label: "研判中", color: "#38bdf8" },
  DONE:    { label: "已完成", color: "#34d399" },
  FAILED:  { label: "失败",   color: "#f87171" },
};

const TriagePage = () => {
  const navigate = useNavigate();
  const { loggedIn } = useAppSession();
  const [tasks, setTasks] = useState<ApiTriageTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [proxyOnline, setProxyOnline] = useState<boolean | null>(null);
  const [alertUuid, setAlertUuid] = useState("");
  const [enableRag, setEnableRag] = useState(true);
  const [creating, setCreating] = useState(false);

  const fetchTasks = useCallback(async () => {
    setLoading(true);
    try {
      const t = await listTriageTasks();
      setTasks(t);
    } catch {
      // proxy may be down
    } finally {
      setLoading(false);
    }
  }, []);

  const checkProxy = useCallback(async () => {
    try {
      const h = await getGatewayHealth();
      setProxyOnline(h.status === "ok");
    } catch {
      setProxyOnline(false);
    }
  }, []);

  useEffect(() => {
    checkProxy();
    fetchTasks();
  }, [checkProxy, fetchTasks]);

  const handleCreate = async () => {
    const uuid = alertUuid.trim();
    if (!uuid) {
      toast.error("请输入告警 UUID");
      return;
    }
    setCreating(true);
    try {
      await createTriageTask(uuid, enableRag);
      toast.success("研判任务已创建");
      setAlertUuid("");
      await fetchTasks();
    } catch (e) {
      toast.error(`创建失败: ${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setCreating(false);
    }
  };

  const formatTime = (iso: string | null) => {
    if (!iso) return "—";
    return new Date(iso).toLocaleString("zh-CN", {
      month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  };

  return (
    <div style={{ minHeight: "100vh", background: "var(--tg-page-bg)", color: "var(--tg-text)" }}>
      <Header />
      <div style={{ paddingTop: 80, paddingBottom: 40, maxWidth: 1100, margin: "0 auto", paddingLeft: 24, paddingRight: 24 }}>
        {/* Page header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 28 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Shield size={22} color="var(--neon-blue)" />
            <h1 style={{ margin: 0, fontSize: "1.25rem", fontWeight: 800, letterSpacing: "0.05em", fontFamily: "'Courier New', monospace" }}>
              告警研判中心
            </h1>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {proxyOnline !== null && (
              <span style={{
                fontSize: 11, fontFamily: "monospace",
                color: proxyOnline ? "rgba(52,211,153,0.9)" : "rgba(251,191,36,0.9)",
                display: "inline-flex", alignItems: "center", gap: 5,
              }}>
                <span style={{
                  width: 6, height: 6, borderRadius: "50%",
                  background: proxyOnline ? "#34d399" : "#fbbf24",
                  boxShadow: proxyOnline ? "0 0 5px rgba(52,211,153,0.7)" : "0 0 5px rgba(251,191,36,0.7)",
                }} />
                {proxyOnline ? "Gateway 在线" : "Gateway 离线"}
              </span>
            )}
            <button
              type="button"
              onClick={fetchTasks}
              disabled={loading}
              style={{
                display: "inline-flex", alignItems: "center", gap: 5, padding: "6px 14px",
                borderRadius: 6, border: "1px solid var(--tg-panel-border)",
                background: "var(--tg-input-bg)", color: "var(--tg-text-muted)",
                cursor: "pointer", fontSize: 12, fontFamily: "monospace",
              }}
            >
              <RefreshCw size={13} /> 刷新
            </button>
          </div>
        </div>

        {/* Create new triage */}
        <div style={{
          display: "flex", gap: 10, marginBottom: 32,
          padding: 18, borderRadius: 8,
          border: "1px solid var(--tg-panel-border)",
          background: "var(--tg-panel-bg)",
          flexWrap: "wrap" as const,
        }}>
          <input
            type="text"
            placeholder="输入 XDR 告警 UUID..."
            value={alertUuid}
            onChange={(e) => setAlertUuid(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            style={{
              flex: 1, minWidth: 260, padding: "9px 14px",
              borderRadius: 6, border: "1px solid var(--tg-panel-border)",
              background: "var(--tg-input-bg)", color: "var(--tg-text)",
              fontSize: 13, fontFamily: "monospace", outline: "none",
            }}
          />
          <button
            type="button"
            onClick={handleCreate}
            disabled={creating}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "9px 20px", borderRadius: 6,
              background: "var(--neon-blue)", color: "#020a12",
              border: "none", fontWeight: 700, fontSize: 13,
              cursor: creating ? "not-allowed" : "pointer",
              opacity: creating ? 0.6 : 1,
              fontFamily: "monospace",
            }}
          >
            <Plus size={15} /> {creating ? "创建中..." : "创建研判"}
          </button>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--tg-text-muted)", fontFamily: "monospace" }}>
            <input type="checkbox" checked={enableRag} onChange={(e) => setEnableRag(e.target.checked)} />
            启用知识增强
          </label>
        </div>

        
        {/* Quick presets — 快速预设告警 */}
        <div style={{ marginBottom: 32 }}>
          <div style={{ fontSize: 10, color: "var(--tg-text-muted)", marginBottom: 5, letterSpacing: "0.04em", fontFamily: "monospace" }}>快速预设告警</div>
          <div style={{
            marginBottom: 8,
            padding: "8px 10px",
            borderRadius: 6,
            border: "1px solid rgba(56,189,248,0.28)",
            background: "rgba(56,189,248,0.06)",
            color: "rgba(147,197,253,0.85)",
            fontSize: 11,
            lineHeight: 1.55,
          }}>
            点击预设按钮自动填入告警 UUID，点击"创建研判"后会拉取 XDR Mock 真实数据并执行规则研判。
          </div>
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
            {([
              { label: "Webshell 威胁", uuid: "alert-ee961e6b-b234-4e9f-bc0f-4a99e2216026" },
              { label: "鱼叉钓鱼", uuid: "alert-51444a62-0c9e-4714-bbe7-01e9a4ebcae6" },
              { label: "冰蝎 Webshell", uuid: "alert-d68be41c-9f52-4d29-8475-9c15a346db9e" },
              { label: "Kindeditor 上传", uuid: "alert-fdb0f028-fc19-4d8c-91e4-d9906ef62fff" },
              { label: "应用执行命令", uuid: "alert-6ee2abf1-4424-4dc7-b47d-ad08fb722e87" },
              { label: "钓鱼文件", uuid: "alert-f1b34c3a-43a1-4acc-8a0b-ebca712a7c5c" },
            ] as { label: string; uuid: string }[]).map((p) => (
              <button
                key={p.label}
                type="button"
                onClick={() => { setAlertUuid(p.uuid); }}
                style={{
                  padding: "3px 9px", borderRadius: 5, fontSize: 10, fontWeight: 600,
                  border: "1px solid rgba(71,85,105,0.5)", background: "var(--tg-input-bg)",
                  color: "var(--tg-text-muted)", cursor: "pointer", fontFamily: "monospace",
                  transition: "color 0.15s, border-color 0.15s",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.color = "#a5f3fc"; e.currentTarget.style.borderColor = "rgba(34,211,238,0.5)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.color = "var(--tg-text-muted)"; e.currentTarget.style.borderColor = "rgba(71,85,105,0.5)"; }}
              >{p.label}</button>
            ))}
          </div>
        </div>

{/* Task list */}
        <div>
          {loading && tasks.length === 0 && (
            <div style={{ textAlign: "center", padding: 60, color: "var(--tg-text-muted)", fontFamily: "monospace", fontSize: 12 }}>
              <Shield size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
              <div>加载研判任务...</div>
            </div>
          )}
          {!loading && tasks.length === 0 && (
            <div style={{ textAlign: "center", padding: 60, color: "var(--tg-text-muted)", fontFamily: "monospace", fontSize: 12 }}>
              <Database size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
              <div>暂无研判任务</div>
              <div style={{ marginTop: 6, opacity: 0.5 }}>输入告警 UUID 创建第一个研判任务</div>
            </div>
          )}
          {tasks.map((task) => {
            const verdict = task.verdict ? VERDICT_CONFIG[task.verdict] : null;
            const status = STATUS_CONFIG[task.status] ?? { label: task.status, color: "#64748b" };
            return (
              <div
                key={task.taskId}
                onClick={() => navigate(`/triage/${task.taskId}`)}
                onKeyDown={(e) => e.key === "Enter" && navigate(`/triage/${task.taskId}`)}
                role="button"
                tabIndex={0}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "14px 18px", marginBottom: 8, borderRadius: 8,
                  border: "1px solid var(--tg-panel-border)",
                  background: "var(--tg-panel-bg)",
                  cursor: "pointer",
                  transition: "border-color 0.15s",
                  gap: 16,
                  flexWrap: "wrap" as const,
                }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.borderColor = "var(--neon-blue)"; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = "var(--tg-panel-border)"; }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <span style={{ fontFamily: "monospace", fontSize: 12, fontWeight: 700, color: "var(--neon-blue)" }}>
                      {task.alertSummary?.name || task.alertUuid.slice(0, 8)}
                    </span>
                    {verdict && (
                      <span style={{
                        display: "inline-flex", alignItems: "center", gap: 4,
                        padding: "2px 8px", borderRadius: 4,
                        background: verdict.bg, color: verdict.color,
                        fontSize: 11, fontWeight: 800, fontFamily: "monospace",
                      }}>
                        {verdict.icon} {verdict.label}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--tg-text-muted)", fontFamily: "monospace", display: "flex", gap: 16 }}>
                    <span><Clock size={10} style={{ marginRight: 3 }} />{formatTime(task.createdAt)}</span>
                    <span style={{ opacity: 0.7 }}>UUID: {task.alertUuid.slice(0, 16)}...</span>
                    {task.confidence != null && (
                      <span>置信度: {(task.confidence * 100).toFixed(0)}%</span>
                    )}
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
                  {task.ragDegraded && (
                    <span style={{ fontSize: 10, color: "#fbbf24", fontFamily: "monospace", opacity: 0.7 }}>RAG 降级</span>
                  )}
                  <span style={{
                    padding: "3px 10px", borderRadius: 4,
                    background: `${status.color}18`, color: status.color,
                    fontSize: 11, fontWeight: 800, fontFamily: "monospace",
                    border: `1px solid ${status.color}40`,
                  }}>
                    {status.label}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default TriagePage;
