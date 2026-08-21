/**
 * TriageDetailPage — 告警研判详情页
 * 路由: /triage/:taskId
 * 功能: 展示研判任务完整详情、告警信息、证据、研判结果、建议动作
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Shield, AlertTriangle, CheckCircle, XCircle, HelpCircle, Clock, FileText, Activity, ShieldAlert, Info, Database } from "lucide-react";
import { toast } from "sonner";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import {
  getMe,
  getTriageTask,
  submitTriageReview,
  type ApiTriageReview,
  type ApiTriageTask,
} from "@/shared/lib/api";

const VERDICT_CONFIG: Record<string, { label: string; color: string; bg: string; icon: React.ReactNode }> = {
  true_positive:     { label: "确认恶意", color: "#f87171", bg: "rgba(248,113,113,0.15)", icon: <AlertTriangle size={18} /> },
  false_positive:    { label: "误报",     color: "#34d399", bg: "rgba(52,211,153,0.15)", icon: <CheckCircle size={18} /> },
  suspicious:        { label: "可疑",     color: "#fbbf24", bg: "rgba(251,191,36,0.15)",  icon: <HelpCircle size={18} /> },
  insufficient_evidence: { label: "证据不足", color: "#94a3b8", bg: "rgba(148,163,184,0.15)", icon: <XCircle size={18} /> },
};

const ACTION_CATEGORY: Record<string, { label: string; color: string }> = {
  safe_auto:        { label: "自动安全", color: "#34d399" },
  manual_required:  { label: "需人工确认", color: "#fbbf24" },
  forbidden:        { label: "禁止执行", color: "#f87171" },
};

const SectionBlock = ({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) => (
  <div style={{ marginBottom: 20, borderRadius: 8, border: "1px solid var(--tg-panel-border)", background: "var(--tg-panel-bg)", overflow: "hidden" }}>
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 18px", borderBottom: "1px solid var(--tg-panel-border)", background: "var(--tg-input-bg)" }}>
      {icon}
      <span style={{ fontSize: 13, fontWeight: 800, fontFamily: "'Courier New', monospace", color: "var(--tg-text)" }}>{title}</span>
    </div>
    <div style={{ padding: "14px 18px" }}>{children}</div>
  </div>
);

const LabelValue = ({ label, value, monospace }: { label: string; value: React.ReactNode; monospace?: boolean }) => (
  <div style={{ display: "flex", marginBottom: 6, fontSize: 12 }}>
    <span style={{ color: "var(--tg-text-muted)", minWidth: 100, flexShrink: 0, fontFamily: "monospace", fontSize: 11 }}>{label}</span>
    <span style={{ color: "var(--tg-text)", fontFamily: monospace ? "monospace" : undefined, fontSize: monospace ? 11 : 12, wordBreak: "break-all" }}>
      {value ?? "—"}
    </span>
  </div>
);

const TriageDetailPage = () => {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const { loggedIn } = useAppSession();
  const [task, setTask] = useState<ApiTriageTask | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [canReview, setCanReview] = useState(false);
  const [reviewDecision, setReviewDecision] = useState<ApiTriageReview['decision']>('CONFIRMED');
  const [humanVerdict, setHumanVerdict] = useState<Exclude<ApiTriageTask['verdict'], null>>('suspicious');
  const [reviewNotes, setReviewNotes] = useState("");
  const [selectedActions, setSelectedActions] = useState<string[]>([]);
  const [submittingReview, setSubmittingReview] = useState(false);

  const fetchTask = useCallback(async (showLoading = true) => {
    if (!taskId) return;
    if (showLoading) setLoading(true);
    setError("");
    try {
      const t = await getTriageTask(taskId);
      setTask(t);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    if (!loggedIn) {
      navigate("/login", { replace: true });
      return;
    }
    void fetchTask();
    void getMe()
      .then((user) => setCanReview(user.role === 'ADMIN' || user.role === 'OPERATOR'))
      .catch(() => setCanReview(false));
  }, [fetchTask, loggedIn, navigate]);

  // Refresh active runs until a terminal result is available.
  useEffect(() => {
    if (task?.status !== "PENDING" && task?.status !== "RUNNING") return;
    const timer = window.setInterval(() => { void fetchTask(false); }, 3000);
    return () => window.clearInterval(timer);
  }, [task?.status, fetchTask]);

  const formatTime = (iso: string | null) => {
    if (!iso) return "—";
    return new Date(iso).toLocaleString("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  };

  const verdictCfg = task?.verdict ? VERDICT_CONFIG[task.verdict] : null;

  const handleReview = async () => {
    if (!taskId) return;
    setSubmittingReview(true);
    try {
      await submitTriageReview(taskId, {
        decision: reviewDecision,
        humanVerdict: reviewDecision === 'OVERRIDDEN' ? humanVerdict : undefined,
        notes: reviewNotes,
        selectedActions,
      });
      toast.success("人工复核已写入审计记录");
      setReviewNotes("");
      setSelectedActions([]);
      await fetchTask(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "提交人工复核失败");
    } finally {
      setSubmittingReview(false);
    }
  };

  if (loading) {
    return (
      <div style={{ minHeight: "100vh", background: "var(--tg-page-bg)", color: "var(--tg-text)" }}>
        <Header />
        <div style={{ paddingTop: 140, textAlign: "center", fontFamily: "monospace", fontSize: 12, color: "var(--tg-text-muted)" }}>
          加载研判详情...
        </div>
      </div>
    );
  }

  if (error || !task) {
    return (
      <div style={{ minHeight: "100vh", background: "var(--tg-page-bg)", color: "var(--tg-text)" }}>
        <Header />
        <div style={{ paddingTop: 140, textAlign: "center", fontFamily: "monospace", fontSize: 12, color: "#f87171" }}>
          {error || "任务不存在"}
        </div>
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", background: "var(--tg-page-bg)", color: "var(--tg-text)" }}>
      <Header />
      <div style={{ paddingTop: 80, paddingBottom: 40, maxWidth: 960, margin: "0 auto", paddingLeft: 24, paddingRight: 24 }}>
        {/* Back + title bar */}
        <button
          type="button"
          onClick={() => navigate("/triage")}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6, marginBottom: 20,
            background: "none", border: "none", cursor: "pointer", color: "var(--tg-text-muted)",
            fontFamily: "monospace", fontSize: 12, padding: 0,
          }}
        >
          <ArrowLeft size={14} /> 返回研判列表
        </button>

        {/* Hero verdict card */}
        <div style={{
          padding: 24, borderRadius: 10, marginBottom: 24,
          border: verdictCfg ? `1px solid ${verdictCfg.color}40` : "1px solid var(--tg-panel-border)",
          background: verdictCfg ? verdictCfg.bg : "var(--tg-panel-bg)",
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12, flexWrap: "wrap", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Shield size={20} color="var(--neon-blue)" />
              <span style={{ fontFamily: "'Courier New', monospace", fontSize: 15, fontWeight: 800 }}>
                {task.alertSummary?.name || "告警研判"}
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{
                padding: "4px 12px", borderRadius: 4, fontSize: 11, fontWeight: 800,
                background: task.status === "DONE" ? "rgba(52,211,153,0.15)" : task.status === "FAILED" ? "rgba(248,113,113,0.15)" : "rgba(56,189,248,0.15)",
                color: task.status === "DONE" ? "#34d399" : task.status === "FAILED" ? "#f87171" : "#38bdf8",
                fontFamily: "monospace", border: `1px solid ${task.status === "DONE" ? "#34d399" : task.status === "FAILED" ? "#f87171" : "#38bdf8"}30`,
              }}>
                {task.status === "DONE" ? "已完成" : task.status === "FAILED" ? "失败" : task.status === "RUNNING" ? "研判中" : task.status}
              </span>
              {verdictCfg && (
                <span style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  padding: "6px 16px", borderRadius: 6, fontSize: 13, fontWeight: 800,
                  background: verdictCfg.bg, color: verdictCfg.color,
                  fontFamily: "monospace", border: `1px solid ${verdictCfg.color}40`,
                }}>
                  {verdictCfg.icon} {verdictCfg.label}
                </span>
              )}
            </div>
          </div>

          {/* Confidence / Severity bar */}
          {(task.confidence != null || task.severity != null) && (
            <div style={{ display: "flex", gap: 20, marginTop: 8 }}>
              {task.confidence != null && (
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 10, color: "var(--tg-text-muted)", fontFamily: "monospace", marginBottom: 4 }}>置信度 {(task.confidence * 100).toFixed(0)}%</div>
                  <div style={{ height: 6, borderRadius: 3, background: "var(--tg-input-bg)", overflow: "hidden" }}>
                    <div style={{ height: "100%", width: `${task.confidence * 100}%`, borderRadius: 3, background: verdictCfg?.color ?? "var(--neon-blue)", transition: "width 0.3s" }} />
                  </div>
                </div>
              )}
              {task.severity != null && (
                <div style={{ width: 100, flexShrink: 0 }}>
                  <div style={{ fontSize: 10, color: "var(--tg-text-muted)", fontFamily: "monospace", marginBottom: 4 }}>
                    严重级别 <span style={{ fontWeight: 800, color: task.severity >= 4 ? "#f87171" : task.severity >= 2 ? "#fbbf24" : "#34d399" }}>{task.severity}</span>/5
                  </div>
                  <div style={{ height: 6, borderRadius: 3, background: "var(--tg-input-bg)", overflow: "hidden" }}>
                    <div style={{
                      height: "100%", width: `${(task.severity / 5) * 100}%`, borderRadius: 3,
                      background: task.severity >= 4 ? "#f87171" : task.severity >= 2 ? "#fbbf24" : "#34d399",
                    }} />
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Reasoning */}
        {task.reasoning && (
          <SectionBlock icon={<FileText size={16} color="var(--neon-blue)" />} title="研判分析">
            <p style={{ margin: 0, fontSize: 13, lineHeight: 1.7, color: "var(--tg-text)", fontFamily: "'Inter', sans-serif" }}>{task.reasoning}</p>
          </SectionBlock>
        )}

        {/* Alert summary */}
        {task.alertSummary && (
          <SectionBlock icon={<Activity size={16} color="var(--neon-blue)" />} title="告警信息">
            <LabelValue label="告警名称" value={task.alertSummary.name} />
            <LabelValue label="告警 UUID" value={task.alertSummary.uuId} monospace />
            <LabelValue label="严重级别" value={task.alertSummary.severity} />
            <LabelValue label="威胁定义" value={task.alertSummary.threatDefine ? JSON.stringify(task.alertSummary.threatDefine) : null} />
            <LabelValue label="Proof 摘要" value={task.alertSummary.proofSummary || "无"} />
          </SectionBlock>
        )}

        {/* Related Incidents */}
        {task.relatedIncidents && task.relatedIncidents.length > 0 && (
          <SectionBlock icon={<ShieldAlert size={16} color={task.relatedIncidents.length > 0 ? "#fbbf24" : "var(--neon-blue)"} />} title="关联安全事件">
            {task.relatedIncidents.map((inc, i) => (
              <div key={i} style={{ padding: "8px 12px", marginBottom: 6, borderRadius: 6, background: "var(--tg-input-bg)", fontFamily: "monospace", fontSize: 11 }}>
                <span style={{ color: "var(--neon-blue)", fontWeight: 700 }}>{inc.name}</span>
                <span style={{ color: "var(--tg-text-muted)", marginLeft: 8 }}>{inc.uuId}</span>
                {inc.severity != null && (
                  <span style={{ float: "right", color: inc.severity >= 4 ? "#f87171" : inc.severity >= 2 ? "#fbbf24" : "#34d399" }}>
                    严重级别 {inc.severity}
                  </span>
                )}
              </div>
            ))}
          </SectionBlock>
        )}

        {/* Matched Whitelists */}
        {task.matchedWhitelists && task.matchedWhitelists.length > 0 && (
          <SectionBlock icon={<CheckCircle size={16} color="#34d399" />} title={`匹配白名单 (${task.matchedWhitelists.length})`}>
            {task.matchedWhitelists.map((wl, i) => (
              <div key={i} style={{ padding: "8px 12px", marginBottom: 6, borderRadius: 6, background: "var(--tg-input-bg)", fontFamily: "monospace", fontSize: 11 }}>
                {JSON.stringify(wl).slice(0, 120)}...
              </div>
            ))}
          </SectionBlock>
        )}

        <SectionBlock icon={<Database size={16} color={task.ragDegraded ? "#fbbf24" : "var(--neon-blue)"} />} title="RAG 知识增强">
          {task.ragDegraded ? (
            <div style={{ padding: "10px 14px", borderRadius: 6, background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.2)", fontSize: 12, color: "#fbbf24", fontFamily: "monospace" }}>
              <Info size={14} style={{ verticalAlign: "middle", marginRight: 6 }} />
              {task.ragNote || "RAG 服务不可用，研判仅基于 XDR 原始证据"}
            </div>
          ) : (
            <div style={{ fontSize: 12, color: "var(--tg-text-muted)", fontFamily: "monospace" }}>
              {task.ragCitations && task.ragCitations.length > 0 ? task.ragCitations.map((citation, index) => {
                const item = citation as Record<string, unknown>;
                return <div key={String(item.chunk_id ?? index)} style={{ padding: "10px 12px", marginBottom: 8, borderRadius: 6, background: "var(--tg-input-bg)" }}>
                  <div style={{ color: "var(--neon-blue)", fontWeight: 700 }}>{String(item.source ?? "trustguard-rag")}</div>
                  <div style={{ marginTop: 5, whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{String(item.content_snippet ?? "")}</div>
                  <div style={{ marginTop: 6, opacity: 0.7, fontSize: 10 }}>{String(item.resource_uri ?? item.chunk_id ?? "")}{item.page_no ? ` · 第 ${String(item.page_no)} 页` : ""}</div>
                </div>;
              }) : "暂无 RAG 引用"}
            </div>
          )}
        </SectionBlock>

        {/* Recommended Actions */}
        {task.recommendedActions && task.recommendedActions.length > 0 && (
          <SectionBlock icon={<ShieldAlert size={16} color="var(--neon-blue)" />} title="建议动作">
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {task.recommendedActions.map((action, i) => {
                const catCfg = ACTION_CATEGORY[action.category] ?? { label: action.category, color: "#64748b" };
                return (
                  <div key={i} style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    padding: "10px 14px", borderRadius: 6, background: "var(--tg-input-bg)",
                    fontSize: 12, flexWrap: "wrap", gap: 8,
                  }}>
                    <span style={{ color: "var(--tg-text)", fontWeight: 600 }}>{action.label}</span>
                    <span style={{
                      padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 800,
                      background: `${catCfg.color}18`, color: catCfg.color,
                      fontFamily: "monospace", border: `1px solid ${catCfg.color}40`,
                    }}>
                      {catCfg.label}
                    </span>
                  </div>
                );
              })}
            </div>
          </SectionBlock>
        )}

        {task.status === "DONE" && canReview && (
          <SectionBlock icon={<CheckCircle size={16} color="#34d399" />} title="人工复核">
            <div style={{ display: "grid", gap: 10 }}>
              <label style={{ fontSize: 11, color: "var(--tg-text-muted)", fontFamily: "monospace" }}>
                复核决定
                <select
                  value={reviewDecision}
                  onChange={(e) => setReviewDecision(e.target.value as ApiTriageReview['decision'])}
                  style={{ display: "block", width: "100%", marginTop: 5, padding: "8px 10px", borderRadius: 6, border: "1px solid var(--tg-panel-border)", background: "var(--tg-input-bg)", color: "var(--tg-text)" }}
                >
                  <option value="CONFIRMED">确认 Agent 结论</option>
                  <option value="OVERRIDDEN">覆盖 Agent 结论</option>
                  <option value="NEEDS_MORE_EVIDENCE">需要补充证据</option>
                </select>
              </label>
              {reviewDecision === 'OVERRIDDEN' && (
                <label style={{ fontSize: 11, color: "var(--tg-text-muted)", fontFamily: "monospace" }}>
                  人工结论
                  <select
                    value={humanVerdict}
                    onChange={(e) => setHumanVerdict(e.target.value as Exclude<ApiTriageTask['verdict'], null>)}
                    style={{ display: "block", width: "100%", marginTop: 5, padding: "8px 10px", borderRadius: 6, border: "1px solid var(--tg-panel-border)", background: "var(--tg-input-bg)", color: "var(--tg-text)" }}
                  >
                    {Object.entries(VERDICT_CONFIG).map(([value, config]) => <option key={value} value={value}>{config.label}</option>)}
                  </select>
                </label>
              )}
              {(task.recommendedActions ?? []).length > 0 && (
                <div style={{ fontSize: 11, color: "var(--tg-text-muted)", fontFamily: "monospace" }}>
                  确认后续动作
                  <div style={{ display: "grid", gap: 6, marginTop: 6 }}>
                    {(task.recommendedActions ?? []).map((action) => (
                      <label key={action.action} style={{ display: "flex", alignItems: "center", gap: 7 }}>
                        <input
                          type="checkbox"
                          checked={selectedActions.includes(action.action)}
                          onChange={(e) => setSelectedActions((items) => e.target.checked
                            ? [...items, action.action]
                            : items.filter((item) => item !== action.action))}
                        />
                        {action.label}
                      </label>
                    ))}
                  </div>
                </div>
              )}
              <textarea
                value={reviewNotes}
                onChange={(e) => setReviewNotes(e.target.value)}
                maxLength={4000}
                rows={3}
                placeholder="填写复核依据或待补充证据（可选）"
                style={{ resize: "vertical", padding: "9px 10px", borderRadius: 6, border: "1px solid var(--tg-panel-border)", background: "var(--tg-input-bg)", color: "var(--tg-text)", fontSize: 12 }}
              />
              <button
                type="button"
                disabled={submittingReview}
                onClick={() => { void handleReview(); }}
                style={{ justifySelf: "start", padding: "8px 16px", borderRadius: 6, border: "none", background: "#34d399", color: "#052e25", fontWeight: 800, cursor: submittingReview ? "not-allowed" : "pointer", opacity: submittingReview ? 0.6 : 1 }}
              >
                {submittingReview ? "提交中..." : "提交复核"}
              </button>
            </div>
          </SectionBlock>
        )}

        {(task.humanReviews ?? []).length > 0 && (
          <SectionBlock icon={<FileText size={16} color="var(--neon-blue)" />} title={`人工复核记录 (${task.humanReviews?.length ?? 0})`}>
            {(task.humanReviews ?? []).map((review) => (
              <div key={review.reviewId} style={{ padding: "10px 12px", marginBottom: 8, borderRadius: 6, background: "var(--tg-input-bg)", fontSize: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontFamily: "monospace" }}>
                  <strong style={{ color: "var(--neon-blue)" }}>{review.decision}</strong>
                  <span style={{ color: "var(--tg-text-muted)", fontSize: 10 }}>{review.reviewerUsername} · {formatTime(review.createdAt)}</span>
                </div>
                {review.humanVerdict && <div style={{ marginTop: 6 }}>人工结论：{VERDICT_CONFIG[review.humanVerdict]?.label ?? review.humanVerdict}</div>}
                {review.notes && <div style={{ marginTop: 6, color: "var(--tg-text-muted)", whiteSpace: "pre-wrap" }}>{review.notes}</div>}
                {review.selectedActions.length > 0 && <div style={{ marginTop: 6 }}>确认动作：{review.selectedActions.join("；")}</div>}
              </div>
            ))}
          </SectionBlock>
        )}

        {/* Warnings */}
        {task.warnings && task.warnings.length > 0 && (
          <SectionBlock icon={<AlertTriangle size={16} color="#fbbf24" />} title="告警/降级">
            {task.warnings.map((w, i) => (
              <div key={i} style={{ padding: "8px 12px", marginBottom: 4, borderRadius: 6, background: "rgba(251,191,36,0.06)", color: "#fbbf24", fontSize: 12, fontFamily: "monospace" }}>
                {w}
              </div>
            ))}
          </SectionBlock>
        )}

        {/* Missing Evidence */}
        {task.missingEvidence && task.missingEvidence.length > 0 && (
          <SectionBlock icon={<XCircle size={16} color="#94a3b8" />} title="证据缺失">
            {task.missingEvidence.map((e, i) => (
              <div key={i} style={{ padding: "8px 12px", marginBottom: 4, borderRadius: 6, background: "var(--tg-input-bg)", color: "var(--tg-text-muted)", fontSize: 12, fontFamily: "monospace" }}>
                {e}
              </div>
            ))}
          </SectionBlock>
        )}

        {/* Meta */}
        <SectionBlock icon={<Clock size={16} color="var(--neon-blue)" />} title="任务元数据">
          <LabelValue label="任务 ID" value={task.taskId} monospace />
          <LabelValue label="告警 UUID" value={task.alertUuid} monospace />
          <LabelValue label="RAG 启用" value={task.ragEnabled ? "是" : "否"} />
          <LabelValue label="创建时间" value={formatTime(task.createdAt)} />
          <LabelValue label="完成时间" value={formatTime(task.finishedAt)} />
        </SectionBlock>
      </div>
    </div>
  );
};

export default TriageDetailPage;
