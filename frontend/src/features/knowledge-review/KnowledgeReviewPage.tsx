import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  Check,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  Database,
  FileSearch,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { toast } from "sonner";

import Header from "@/shared/components/Header";
import { DEMO_FALLBACK_ENABLED } from "@/shared/constants/demoFallback";
import { useAppSession } from "@/shared/context/AppSessionContext";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/shared/ui/alert-dialog";
import {
  getKnowledgeCrawlerReview,
  getKnowledgeCrawlerReviewContent,
  reviewKnowledgeCrawlerItems,
  type ApiKnowledgeCrawlerReview,
  type ApiKnowledgeCrawlerReviewContent,
  type ApiKnowledgeCrawlerReviewItem,
} from "@/shared/lib/api";
import "./KnowledgeReviewPage.css";

function currentRole(): string {
  try {
    const raw = localStorage.getItem("sentinel_session_v1");
    return raw ? String((JSON.parse(raw) as { role?: string }).role || "VIEWER").toUpperCase() : "VIEWER";
  } catch {
    return "VIEWER";
  }
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function itemStatus(status: ApiKnowledgeCrawlerReviewItem["status"]): string {
  return { pending: "待审核", processing: "处理中", rejecting: "驳回中", approved: "已通过", rejected: "已驳回" }[status];
}

function canReReview(item: ApiKnowledgeCrawlerReviewItem): boolean {
  if (item.status !== "rejected" || item.reviewer !== "agent" || item.agent_decision !== "reject" || !item.review_content_available) return false;
  if (!item.review_content_expires_at) return false;
  return new Date(item.review_content_expires_at).getTime() > Date.now();
}

function canApprove(item: ApiKnowledgeCrawlerReviewItem): boolean {
  return item.status === "pending" || canReReview(item);
}

function buildDemoReview(jobId: string, knowledgeBaseId = "kb-demo-default"): ApiKnowledgeCrawlerReview {
  const now = Date.now();
  const items: ApiKnowledgeCrawlerReviewItem[] = [
    {
      id: "review-demo-001",
      knowledge_base_id: knowledgeBaseId,
      title: "Struts2 S2-045 远程代码执行漏洞处置要点",
      source_type: "web",
      content_preview: "漏洞成因来自 Jakarta Multipart 解析异常路径中的 OGNL 表达式执行，攻击者可通过 Content-Type 头触发命令执行...",
      content_chars: 4280,
      source_uri: "https://demo.trustguard.local/kb/cve-2017-5638",
      original_filename: "cve-2017-5638-struts2.md",
      status: "pending",
      reviewer: null,
      review_reason: null,
      review_confidence: null,
      agent_decision: null,
      manual_reviewer: null,
      manual_reviewed_at: null,
      rejected_at: null,
      review_content_expires_at: null,
      review_content_expired_at: null,
      review_content_available: true,
      created_at: new Date(now - 1000 * 60 * 38).toISOString(),
    },
    {
      id: "review-demo-002",
      knowledge_base_id: knowledgeBaseId,
      title: "Nuclei 模板误报复核规则",
      source_type: "document",
      content_preview: "当模板只命中响应状态码或标题关键字时，不应直接判定漏洞成立，需要结合响应体证据、认证状态、资产上下文和二次验证结果...",
      content_chars: 3150,
      source_uri: "https://demo.trustguard.local/kb/nuclei-fp-rules",
      original_filename: "nuclei-false-positive-review.md",
      status: "pending",
      reviewer: "agent",
      review_reason: "内容包含明确的误报判定策略，可用于 RAG 约束漏洞确认流程。",
      review_confidence: 0.91,
      agent_decision: "approve",
      manual_reviewer: null,
      manual_reviewed_at: null,
      rejected_at: null,
      review_content_expires_at: null,
      review_content_expired_at: null,
      review_content_available: true,
      created_at: new Date(now - 1000 * 60 * 26).toISOString(),
    },
    {
      id: "review-demo-003",
      knowledge_base_id: knowledgeBaseId,
      title: "过期 PoC 转载：Apache 2.2 默认页面误判",
      source_type: "web",
      content_preview: "该文章将 Apache 默认欢迎页直接判定为高危漏洞，缺少版本、配置和可利用条件说明...",
      content_chars: 1760,
      source_uri: "https://demo.trustguard.local/kb/outdated-apache-poc",
      original_filename: "outdated-apache-poc.html",
      status: "rejected",
      reviewer: "agent",
      review_reason: "证据链不足且结论过度泛化，容易污染漏洞确认知识库。",
      review_confidence: 0.86,
      agent_decision: "reject",
      manual_reviewer: null,
      manual_reviewed_at: null,
      rejected_at: new Date(now - 1000 * 60 * 14).toISOString(),
      review_content_expires_at: new Date(now + 1000 * 60 * 60 * 24 * 29).toISOString(),
      review_content_expired_at: null,
      review_content_available: true,
      created_at: new Date(now - 1000 * 60 * 50).toISOString(),
    },
    {
      id: "review-demo-004",
      knowledge_base_id: knowledgeBaseId,
      title: "WebShell 上传漏洞修复建议清单",
      source_type: "document",
      content_preview: "建议从上传目录权限、扩展名白名单、MIME 校验、内容魔数识别、对象存储隔离和执行权限关闭等维度进行修复...",
      content_chars: 2890,
      source_uri: "https://demo.trustguard.local/kb/upload-remediation",
      original_filename: "webshell-upload-remediation.md",
      status: "approved",
      reviewer: "human",
      review_reason: "修复建议完整，适合报告生成阶段引用。",
      review_confidence: null,
      agent_decision: null,
      manual_reviewer: "operator",
      manual_reviewed_at: new Date(now - 1000 * 60 * 9).toISOString(),
      rejected_at: null,
      review_content_expires_at: null,
      review_content_expired_at: null,
      review_content_available: true,
      created_at: new Date(now - 1000 * 60 * 42).toISOString(),
    },
  ];
  return {
    job_id: jobId,
    review_status: "pending",
    review_mode: "human",
    review_criteria: "仅允许证据充分、可复核、适合安全任务编排和报告生成的内容进入知识库。",
    items,
    pending: items.filter((item) => item.status === "pending").length,
    approved: items.filter((item) => item.status === "approved").length,
    rejected: items.filter((item) => item.status === "rejected").length,
  };
}

function buildDemoReviewContent(jobId: string, itemId: string, knowledgeBaseId?: string): ApiKnowledgeCrawlerReviewContent {
  const review = buildDemoReview(jobId, knowledgeBaseId);
  const item = review.items.find((entry) => entry.id === itemId) ?? review.items[0];
  return {
    item,
    content: [
      `# ${item.title}`,
      "",
      "## 适用场景",
      "用于 TrustGuard Agent 在任务规划、RAG 检索、漏洞确认和报告生成阶段引用。内容必须能解释触发条件、验证方式、误报边界和修复建议。",
      "",
      "## 关键证据",
      "- 资产指纹：服务版本、框架组件、端口和访问路径需要可复核。",
      "- 验证动作：至少包含一次主动请求、响应片段或执行结果。",
      "- 风险判断：区分信息泄露、可利用漏洞和模板误报。",
      "",
      "## Agent 使用方式",
      "当扫描技能返回弱证据时，优先使用该知识片段约束二次验证；当漏洞成立时，将证据和修复建议写入最终报告。",
    ].join("\n"),
  };
}

export default function KnowledgeReviewPage() {
  const { jobId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const knowledgeBaseId = searchParams.get("knowledge_base_id") || undefined;
  const navigate = useNavigate();
  const { loggedIn } = useAppSession();
  const canManage = useMemo(() => ["ADMIN", "OPERATOR"].includes(currentRole()), []);
  const [review, setReview] = useState<ApiKnowledgeCrawlerReview | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [activeItemId, setActiveItemId] = useState<string | null>(null);
  const [content, setContent] = useState<ApiKnowledgeCrawlerReviewContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [contentLoading, setContentLoading] = useState(false);
  const [submitting, setSubmitting] = useState<"approve" | "reject" | null>(null);
  const [confirmAction, setConfirmAction] = useState<"approve" | "reject" | null>(null);

  const loadReview = useCallback(async () => {
    if (!jobId) return;
    setLoading(true);
    try {
      const result = await getKnowledgeCrawlerReview(jobId, knowledgeBaseId);
      setReview(result);
      const firstPending = result.items.find((item) => item.status === "pending") ?? result.items[0];
      setActiveItemId((current) => current && result.items.some((item) => item.id === current) ? current : firstPending?.id ?? null);
      setSelected((current) => new Set([...current].filter((id) => result.items.some((item) => item.id === id && canApprove(item)))));
    } catch (error) {
      if (DEMO_FALLBACK_ENABLED) {
        const result = buildDemoReview(jobId, knowledgeBaseId);
        setReview(result);
        setActiveItemId((current) => current && result.items.some((item) => item.id === current) ? current : result.items[0]?.id ?? null);
        toast.warning(`审核任务加载失败，已显示演示数据：${error instanceof Error ? error.message : "后端未连接"}`);
      } else {
        setReview(null);
        setActiveItemId(null);
        toast.error(error instanceof Error ? error.message : "审核任务加载失败");
      }
      setSelected(new Set());
    } finally {
      setLoading(false);
    }
  }, [jobId, knowledgeBaseId]);

  const loadContent = useCallback(async (itemId: string) => {
    setContentLoading(true);
    try {
      setContent(await getKnowledgeCrawlerReviewContent(jobId, itemId, knowledgeBaseId));
    } catch (error) {
      if (DEMO_FALLBACK_ENABLED) {
        setContent(buildDemoReviewContent(jobId, itemId, knowledgeBaseId));
        toast.warning(`正文加载失败，已显示演示正文：${error instanceof Error ? error.message : "后端未连接"}`);
      } else {
        setContent(null);
        toast.error(error instanceof Error ? error.message : "正文加载失败");
      }
    } finally {
      setContentLoading(false);
    }
  }, [jobId, knowledgeBaseId]);

  useEffect(() => {
    if (!loggedIn) {
      const target = `/knowledge/collect/review/${jobId}${knowledgeBaseId ? `?knowledge_base_id=${knowledgeBaseId}` : ""}`;
      localStorage.setItem("sentinel_login_redirect", target);
      navigate("/login");
      return;
    }
    void loadReview();
  }, [jobId, knowledgeBaseId, loadReview, loggedIn, navigate]);

  useEffect(() => {
    const activeItem = review?.items.find((item) => item.id === activeItemId);
    if (activeItemId && !(activeItem?.status === "rejected" && !activeItem.review_content_available)) void loadContent(activeItemId);
    else setContent(null);
  }, [activeItemId, loadContent, review]);

  const actionableIds = useMemo(
    () => review?.items.filter(canApprove).map((item) => item.id) ?? [],
    [review],
  );
  const allActionableSelected = actionableIds.length > 0 && actionableIds.every((id) => selected.has(id));
  const selectedItems = useMemo(
    () => review?.items.filter((item) => selected.has(item.id)) ?? [],
    [review, selected],
  );
  const selectedHasReReview = selectedItems.some((item) => item.status === "rejected");
  const activeItem = review?.items.find((item) => item.id === activeItemId);

  const toggle = (itemId: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  };

  const toggleAll = () => setSelected(allActionableSelected ? new Set() : new Set(actionableIds));

  const requestReview = (action: "approve" | "reject") => {
    if (!canManage) return toast.error("当前角色没有审核权限");
    if (!selected.size) return toast.error("请至少勾选一条待审核数据");
    if (action === "reject" && selectedHasReReview) return toast.error("Agent 已驳回数据只能人工改判为通过");
    setConfirmAction(action);
  };

  const submitReview = async (action: "approve" | "reject") => {
    const itemIds = [...selected];
    if (!canManage) return toast.error("当前角色没有审核权限");
    if (!itemIds.length) return toast.error("请至少勾选一条待审核数据");
    setSubmitting(action);
    try {
      const result = await reviewKnowledgeCrawlerItems(jobId, action, itemIds, knowledgeBaseId);
      setReview(result);
      setSelected(new Set());
      const nextReviewable = result.items.find((item) => item.status === "pending") ?? result.items.find(canReReview);
      setActiveItemId(nextReviewable?.id ?? null);
      setConfirmAction(null);
      toast.success(action === "approve" ? "审核通过，已提交入库队列" : "所选数据已驳回");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "审核操作失败");
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <div className="review-page">
      <Header />
      <main className="review-shell">
        <section className="review-hero">
          <div>
            <button type="button" className="review-back" onClick={() => navigate("/knowledge/collect")}><ArrowLeft size={14} /> 返回数据采集</button>
            <div className="review-eyebrow"><ShieldCheck size={13} /> HUMAN REVIEW GATE</div>
            <h1>爬取数据审核</h1>
            <p>逐条检查清洗正文及来源。Agent 驳回内容保留 30 天，可由人工复核并改判入库。</p>
          </div>
          <button type="button" className="review-refresh" onClick={() => void loadReview()} disabled={loading}><RefreshCw size={14} className={loading ? "animate-spin" : undefined} /> 刷新</button>
        </section>

        <section className="review-metrics">
          <div><ClipboardCheck size={16} /><span>待审核</span><strong>{review?.pending ?? 0}</strong></div>
          <div><CheckCircle2 size={16} /><span>已通过</span><strong>{review?.approved ?? 0}</strong></div>
          <div><X size={16} /><span>已驳回</span><strong>{review?.rejected ?? 0}</strong></div>
          <div><Database size={16} /><span>审核状态</span><strong className="status-text">{review?.review_status === "completed" ? "已完成" : "进行中"}</strong></div>
        </section>

        {!canManage && <div className="review-warning">当前为只读角色，可以查看内容，但不能通过或驳回数据。</div>}

        <section className="review-workspace">
          <aside className="review-list-panel">
            <div className="review-list-toolbar">
              <label><input type="checkbox" checked={allActionableSelected} onChange={toggleAll} disabled={!actionableIds.length || !canManage} /> 全选可审核</label>
              <span>{selected.size} 条已选择</span>
            </div>
            {loading ? (
              <div className="review-placeholder"><LoaderCircle size={18} className="animate-spin" /> 正在加载审核清单…</div>
            ) : !review?.items.length ? (
              <div className="review-placeholder"><FileSearch size={24} /> 没有待审核数据</div>
            ) : (
              <div className="review-item-list">
                {review.items.map((item, index) => (
                  <article key={item.id} className={`review-item ${activeItemId === item.id ? "active" : ""}`} onClick={() => setActiveItemId(item.id)}>
                    <input type="checkbox" checked={selected.has(item.id)} disabled={!canManage || !canApprove(item)} onClick={(event) => event.stopPropagation()} onChange={() => toggle(item.id)} />
                    <div>
                      <div className="review-item-title"><span>{String(index + 1).padStart(2, "0")}</span><strong>{item.title}</strong></div>
                      <p>{item.content_preview}</p>
                      <div className="review-item-meta"><b className={item.status}>{itemStatus(item.status)}</b><span>{item.content_chars.toLocaleString()} 字符</span><span>{formatDate(item.created_at)}</span></div>
                      {item.status === "rejected" && item.review_content_expires_at && item.review_content_available && (
                        <div className="review-retention"><Clock3 size={10} /><span>可人工复核至 {formatDate(item.review_content_expires_at)}</span></div>
                      )}
                      {item.status === "rejected" && !item.review_content_available && (
                        <div className="review-retention expired"><Clock3 size={10} /><span>复核内容已过期或不可用</span></div>
                      )}
                      {item.reviewer === "agent" && item.review_reason && (
                        <div className="review-agent-result"><Bot size={11} /><span>{item.review_reason}</span>{typeof item.review_confidence === "number" && <b>{Math.round(item.review_confidence * 100)}%</b>}</div>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </aside>

          <div className="review-content-panel">
            {contentLoading ? (
              <div className="review-placeholder"><LoaderCircle size={18} className="animate-spin" /> 正在读取完整正文…</div>
            ) : content ? (
              <>
                <header>
                  <div><span>{itemStatus(content.item.status)}</span><h2>{content.item.title}</h2></div>
                  <a href={content.item.source_uri} target="_blank" rel="noreferrer">查看原始来源</a>
                </header>
                <div className="review-source"><span>目标知识库：{content.item.knowledge_base_id}</span><span>文件：{content.item.original_filename}</span><span>来源：{content.item.source_uri}</span></div>
                {content.item.reviewer === "agent" && content.item.review_reason && (
                  <div className="review-agent-banner"><Bot size={15} /><div><strong>Agent 审核意见</strong><span>{content.item.review_reason}</span></div>{typeof content.item.review_confidence === "number" && <b>{Math.round(content.item.review_confidence * 100)}%</b>}</div>
                )}
                {content.item.status === "rejected" && content.item.review_content_expires_at && (
                  <div className="review-retention-banner"><Clock3 size={14} /><span>该 Agent 驳回内容将在 {formatDate(content.item.review_content_expires_at)} 到期；到期前可勾选并人工改判为通过。</span></div>
                )}
                <pre>{content.content}</pre>
              </>
            ) : activeItem?.status === "rejected" && !activeItem.review_content_available ? (
              <div className="review-placeholder"><Clock3 size={28} /><strong>该驳回内容已超过保留期限或此前已被清理</strong></div>
            ) : (
              <div className="review-placeholder"><FileSearch size={28} /><strong>选择一条数据查看完整清洗正文</strong></div>
            )}
          </div>
        </section>

        <footer className="review-actions">
          <span>{review?.review_status === "completed" ? "本次采集审核已完成" : `还有 ${review?.pending ?? 0} 条数据等待处理`}</span>
          <div>
            <button type="button" className="reject" disabled={!canManage || !selected.size || selectedHasReReview || Boolean(submitting)} onClick={() => requestReview("reject")}><X size={14} /> 驳回所选</button>
            <button type="button" className="approve" disabled={!canManage || !selected.size || Boolean(submitting)} onClick={() => requestReview("approve")}><Check size={14} /> 通过并入库</button>
          </div>
        </footer>
      </main>

      <AlertDialog
        open={Boolean(confirmAction)}
        onOpenChange={(open) => {
          if (!open && !submitting) setConfirmAction(null);
        }}
      >
        <AlertDialogContent className={`review-confirm-dialog ${confirmAction ?? ""}`}>
          <div className="review-confirm-glow" aria-hidden="true" />
          <AlertDialogHeader className="review-confirm-header">
            <div className="review-confirm-icon" aria-hidden="true">
              {confirmAction === "approve" ? <ShieldCheck size={24} /> : <AlertTriangle size={24} />}
            </div>
            <div>
              <span className="review-confirm-eyebrow">
                {confirmAction === "approve" ? "APPROVE & INGEST" : "REJECT DATA"}
              </span>
              <AlertDialogTitle>
                {confirmAction === "approve" ? "确认通过所选数据？" : "确认驳回所选数据？"}
              </AlertDialogTitle>
              <AlertDialogDescription>
                {confirmAction === "approve"
                  ? "审核通过后，所选清洗内容将进入 RAG 入库队列。"
                  : "人工驳回后，所选数据不会进入知识库，暂存正文将立即删除。"}
              </AlertDialogDescription>
            </div>
          </AlertDialogHeader>

          <div className="review-confirm-summary">
            <div>
              <span>本次处理</span>
              <strong>{selected.size}</strong>
              <small>条数据</small>
            </div>
            <div>
              <span>处理结果</span>
              <strong className="review-confirm-result">
                {confirmAction === "approve" ? (selectedHasReReview ? "人工改判入库" : "提交入库") : "人工驳回"}
              </strong>
            </div>
          </div>

          <div className="review-confirm-notice">
            {confirmAction === "approve" ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
            <span>
              {confirmAction === "approve"
                ? "系统将为每条数据创建入库任务，后续可在采集任务中查看处理状态。"
                : "人工驳回会立即删除对应暂存正文；Agent 自动驳回的内容则会保留 30 天供人工复核。"}
            </span>
          </div>

          <AlertDialogFooter className="review-confirm-footer">
            <AlertDialogCancel disabled={Boolean(submitting)}>取消</AlertDialogCancel>
            <AlertDialogAction
              className="review-confirm-submit"
              disabled={Boolean(submitting)}
              onClick={(event) => {
                event.preventDefault();
                if (confirmAction) void submitReview(confirmAction);
              }}
            >
              {submitting ? <LoaderCircle size={15} className="animate-spin" /> : confirmAction === "approve" ? <Check size={15} /> : <X size={15} />}
              {submitting
                ? "正在处理…"
                : confirmAction === "approve"
                  ? `确认通过 ${selected.size} 条`
                  : `确认驳回 ${selected.size} 条`}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
