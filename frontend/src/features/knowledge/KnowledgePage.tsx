import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  BookOpen,
  ChevronDown,
  ChevronRight,
  Database,
  Edit3,
  FileText,
  MessageSquareText,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { toast } from "sonner";

import Header from "@/shared/components/Header";
import KnowledgeAnswerPanel from "@/features/knowledge/KnowledgeAnswerPanel";
import { useAppSession } from "@/shared/context/AppSessionContext";
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  deleteKnowledgeDocument,
  getKnowledgeDocument,
  getKnowledgeDocumentChunks,
  getKnowledgeCapabilities,
  getKnowledgeIngestJob,
  getRagHealth,
  listKnowledgeBases,
  listKnowledgeDocuments,
  resolveKnowledgeIngestConflict,
  searchKnowledge,
  updateKnowledgeBase,
  updateKnowledgeDocument,
  uploadKnowledgeDocument,
  type ApiKnowledgeBase,
  type ApiKnowledgeChunk,
  type ApiKnowledgeDocument,
  type ApiKnowledgeIngestJob,
  type ApiKnowledgeSourceCapabilities,
  type ApiKnowledgeSearchResponse,
  type ApiRagHealth,
} from "@/shared/lib/api";
import "./KnowledgePage.css";

type ViewName = "answer" | "search" | "documents";
type RetrievalMode = "auto" | "focused" | "comprehensive" | "enumeration";
type TrackedIngestJob = ApiKnowledgeIngestJob & {
  filename: string;
  knowledgeBaseId: string;
};

const TERMINAL_INGEST_STATUSES = new Set([
  "succeeded",
  "deduplicated",
  "failed",
  "conflict",
  "cancelled",
  "discarded",
]);

const panelStyle = {
  background: "var(--tg-panel-bg)",
  border: "1px solid var(--tg-panel-border)",
  borderRadius: 10,
  boxShadow: "var(--tg-shadow)",
} as const;

const inputStyle = {
  width: "100%",
  background: "var(--tg-input-bg)",
  border: "1px solid var(--tg-panel-border)",
  borderRadius: 7,
  color: "var(--tg-text)",
  fontFamily: "var(--tg-font-ui)",
  fontSize: 14,
  outline: "none",
} as const;

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatScore(value?: number | null): string {
  return typeof value === "number" ? value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "") : "—";
}

function statusColor(status?: string): string {
  const normalized = (status ?? "").toLowerCase();
  if (["ok", "up", "ready", "alive"].includes(normalized)) return "var(--tg-success)";
  if (["degraded", "processing", "pending", "queued", "running", "resolving", "ingest_retrying", "resolve_retrying"].includes(normalized)) return "var(--tg-warning)";
  if (["failed", "down", "error"].includes(normalized)) return "var(--tg-danger)";
  return "var(--tg-text-muted)";
}

function currentRole(): string {
  try {
    const raw = localStorage.getItem("sentinel_session_v1");
    if (!raw) return "VIEWER";
    const session = JSON.parse(raw) as { role?: string };
    return (session.role || "VIEWER").toUpperCase();
  } catch {
    return "VIEWER";
  }
}

function toTrackedJob(
  job: ApiKnowledgeIngestJob,
  filename: string,
  knowledgeBaseId: string,
): TrackedIngestJob {
  return {
    ...job,
    conflict_candidates: job.conflict_candidates ?? [],
    filename,
    knowledgeBaseId,
  };
}

function MetricCard({
  icon,
  label,
  value,
  detail,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  detail: string;
}) {
  return (
    <div className="knowledge-metric-card" style={{ ...panelStyle, padding: "18px 20px", flex: "1 1 190px", minWidth: 180 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--tg-text-muted)", fontSize: 13, fontWeight: 600 }}>
        {icon}
        <span>{label}</span>
      </div>
      <div style={{ marginTop: 11, color: "var(--tg-text)", fontSize: 26, lineHeight: 1.15, fontWeight: 800, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
      <div style={{ marginTop: 6, color: "var(--tg-text-muted)", fontSize: 12, lineHeight: 1.5 }}>{detail}</div>
    </div>
  );
}

export default function KnowledgePage() {
  const navigate = useNavigate();
  const { loggedIn } = useAppSession();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const canManage = useMemo(() => ["ADMIN", "OPERATOR"].includes(currentRole()), []);

  const [view, setView] = useState<ViewName>("answer");
  const [health, setHealth] = useState<ApiRagHealth | null>(null);
  const [capabilities, setCapabilities] = useState<ApiKnowledgeSourceCapabilities | null>(null);
  const [bases, setBases] = useState<ApiKnowledgeBase[]>([]);
  const [selectedBaseId, setSelectedBaseId] = useState("");
  const [bootstrapLoading, setBootstrapLoading] = useState(true);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<RetrievalMode>("auto");
  const [topK, setTopK] = useState(8);
  const [enableVector, setEnableVector] = useState(true);
  const [enableKeyword, setEnableKeyword] = useState(true);
  const [enableRerank, setEnableRerank] = useState(true);
  const [enableRewrite, setEnableRewrite] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<ApiKnowledgeSearchResponse | null>(null);

  const [documents, setDocuments] = useState<ApiKnowledgeDocument[]>([]);
  const [documentTotal, setDocumentTotal] = useState(0);
  const [documentQuery, setDocumentQuery] = useState("");
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [expandedDocumentId, setExpandedDocumentId] = useState<string | null>(null);
  const [documentDetails, setDocumentDetails] = useState<Record<string, ApiKnowledgeDocument>>({});
  const [documentChunks, setDocumentChunks] = useState<Record<string, ApiKnowledgeChunk[]>>({});
  const [documentDetailLoading, setDocumentDetailLoading] = useState<string | null>(null);
  const [baseEditorOpen, setBaseEditorOpen] = useState(false);
  const [editingBaseId, setEditingBaseId] = useState<string | null>(null);
  const [baseName, setBaseName] = useState("");
  const [baseDescription, setBaseDescription] = useState("");
  const [savingBase, setSavingBase] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadJobs, setUploadJobs] = useState<TrackedIngestJob[]>([]);

  const selectedBase = useMemo(
    () => bases.find((item) => item.id === selectedBaseId) ?? null,
    [bases, selectedBaseId],
  );

  const loadBootstrap = useCallback(async () => {
    setBootstrapLoading(true);
    setBootstrapError(null);
    const [healthResult, basesResult, capabilitiesResult] = await Promise.allSettled([
      getRagHealth(),
      listKnowledgeBases(),
      getKnowledgeCapabilities(),
    ]);

    if (healthResult.status === "fulfilled") setHealth(healthResult.value);
    else setHealth(null);
    if (capabilitiesResult.status === "fulfilled") setCapabilities(capabilitiesResult.value);
    else setCapabilities(null);

    if (basesResult.status === "fulfilled") {
      const nextBases = basesResult.value.items ?? [];
      setBases(nextBases);
      setSelectedBaseId((current) => {
        if (current && nextBases.some((item) => item.id === current)) return current;
        return nextBases.find((item) => item.is_default)?.id ?? nextBases[0]?.id ?? "";
      });
    } else {
      setBases([]);
      setSelectedBaseId("");
      setBootstrapError(basesResult.reason instanceof Error ? basesResult.reason.message : "知识库列表加载失败");
    }
    setBootstrapLoading(false);
  }, []);

  const loadDocuments = useCallback(async () => {
    if (!selectedBaseId) {
      setDocuments([]);
      setDocumentTotal(0);
      return;
    }
    setDocumentsLoading(true);
    try {
      const data = await listKnowledgeDocuments({
        knowledgeBaseId: selectedBaseId,
        limit: 50,
        query: documentQuery,
      });
      setDocuments(data.items ?? []);
      setDocumentTotal(data.total ?? 0);
    } catch (error) {
      setDocuments([]);
      setDocumentTotal(0);
      toast.error(error instanceof Error ? error.message : "文档列表加载失败");
    } finally {
      setDocumentsLoading(false);
    }
  }, [documentQuery, selectedBaseId]);

  useEffect(() => {
    if (!loggedIn) {
      localStorage.setItem("sentinel_login_redirect", "/knowledge");
      navigate("/login");
      return;
    }
    void loadBootstrap();
  }, [loadBootstrap, loggedIn, navigate]);

  useEffect(() => {
    if (loggedIn && selectedBaseId) void loadDocuments();
  }, [loadDocuments, loggedIn, selectedBaseId]);

  const runSearch = async () => {
    const normalizedQuery = query.trim();
    if (!selectedBaseId) {
      toast.error("请先选择知识库");
      return;
    }
    if (!normalizedQuery) {
      toast.error("请输入检索问题");
      return;
    }
    if (!enableVector && !enableKeyword) {
      toast.error("请至少启用一种检索方式");
      return;
    }
    setSearching(true);
    setSearchResult(null);
    try {
      setSearchResult(await searchKnowledge({
        query: normalizedQuery,
        knowledgeBaseId: selectedBaseId,
        topK,
        retrievalMode: mode,
        enableQueryRewrite: enableRewrite,
        enableVector,
        enableKeyword,
        enableRerank,
      }));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "知识检索失败");
    } finally {
      setSearching(false);
    }
  };

  const toggleDocument = async (document: ApiKnowledgeDocument) => {
    if (expandedDocumentId === document.id) {
      setExpandedDocumentId(null);
      return;
    }
    setExpandedDocumentId(document.id);
    if (documentChunks[document.id]) return;
    setDocumentDetailLoading(document.id);
    try {
      const [detail, chunks] = await Promise.all([
        getKnowledgeDocument(document.id, selectedBaseId),
        getKnowledgeDocumentChunks(document.id, selectedBaseId),
      ]);
      setDocumentDetails((current) => ({ ...current, [document.id]: detail }));
      setDocumentChunks((current) => ({ ...current, [document.id]: chunks }));
    } catch (error) {
      setExpandedDocumentId(null);
      toast.error(error instanceof Error ? error.message : "文档详情加载失败");
    } finally {
      setDocumentDetailLoading(null);
    }
  };

  const openCreateBase = () => {
    setEditingBaseId(null);
    setBaseName("");
    setBaseDescription("");
    setBaseEditorOpen(true);
  };

  const openEditBase = () => {
    if (!selectedBase) return;
    setEditingBaseId(selectedBase.id);
    setBaseName(selectedBase.name);
    setBaseDescription(selectedBase.description ?? "");
    setBaseEditorOpen(true);
  };

  const saveKnowledgeBase = async () => {
    const name = baseName.trim();
    if (!name) {
      toast.error("请输入知识库名称");
      return;
    }
    setSavingBase(true);
    try {
      const saved = editingBaseId
        ? await updateKnowledgeBase(editingBaseId, {
            name,
            description: baseDescription.trim() || null,
          })
        : await createKnowledgeBase({
            name,
            description: baseDescription.trim() || undefined,
          });
      setBaseEditorOpen(false);
      await loadBootstrap();
      setSelectedBaseId(saved.id);
      toast.success(editingBaseId ? "知识库已更新" : "知识库已创建");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "知识库保存失败");
    } finally {
      setSavingBase(false);
    }
  };

  const removeSelectedBase = async () => {
    if (!selectedBase || selectedBase.is_default || selectedBase.is_system) return;
    if (!window.confirm(`确认删除知识库“${selectedBase.name}”？仅空知识库允许删除。`)) return;
    try {
      await deleteKnowledgeBase(selectedBase.id);
      setSelectedBaseId("");
      await loadBootstrap();
      toast.success("知识库已删除");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "知识库删除失败");
    }
  };

  const refreshAfterIngest = useCallback(async () => {
    await Promise.all([loadDocuments(), loadBootstrap()]);
  }, [loadBootstrap, loadDocuments]);

  const watchIngestJob = useCallback(async (
    knowledgeBaseId: string,
    jobId: string,
    filename: string,
  ) => {
    for (let attempt = 0; attempt < 90; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, attempt === 0 ? 300 : 2000));
      try {
        const job = await getKnowledgeIngestJob(knowledgeBaseId, jobId);
        const tracked = toTrackedJob(job, filename, knowledgeBaseId);
        setUploadJobs((current) => current.map((item) => item.id === jobId ? tracked : item));
        if (TERMINAL_INGEST_STATUSES.has(job.status)) {
          if (job.status === "succeeded") toast.success(`${filename} 已完成入库`);
          else if (job.status === "deduplicated") toast.info(`${filename} 与已有文档重复，已复用现有文档`);
          else if (job.status === "conflict") toast.warning(`${filename} 与已有同名文档冲突，请选择保留版本`);
          else toast.error(job.error_message || `${filename} 入库失败`);
          if (job.status === "succeeded" || job.status === "deduplicated") {
            await refreshAfterIngest();
          }
          return;
        }
      } catch (error) {
        if (attempt >= 2) {
          toast.error(error instanceof Error ? error.message : "入库任务状态查询失败");
          return;
        }
      }
    }
    toast.warning(`${filename} 仍在处理，可稍后刷新文档列表`);
  }, [refreshAfterIngest]);

  const uploadFiles = async (files: FileList | null) => {
    if (!files?.length || !selectedBaseId) return;
    const source = capabilities?.sources?.find((item) => item.source_type === "file");
    const maxBytes = source?.max_bytes;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        if (maxBytes && file.size > maxBytes) {
          toast.error(`${file.name} 超过 ${(maxBytes / 1024 / 1024).toFixed(0)} MiB 限制`);
          continue;
        }
        const created = await uploadKnowledgeDocument(selectedBaseId, file);
        const initial: TrackedIngestJob = {
          id: created.job_id,
          source_type: "file",
          status: created.status,
          conflict_candidates: [],
          attempt: 0,
          max_attempts: 3,
          knowledge_base_id: created.knowledge_base_id,
          filename: file.name,
          knowledgeBaseId: selectedBaseId,
        };
        setUploadJobs((current) => [initial, ...current.filter((item) => item.id !== initial.id)].slice(0, 12));
        toast.success(`${file.name} 已提交入库`);
        void watchIngestJob(selectedBaseId, created.job_id, file.name);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "文档上传失败");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const resolveConflict = async (job: TrackedIngestJob, keepDocumentId: string) => {
    try {
      const resolved = await resolveKnowledgeIngestConflict(job.knowledgeBaseId, job.id, keepDocumentId);
      const tracked = toTrackedJob(resolved, job.filename, job.knowledgeBaseId);
      setUploadJobs((current) => current.map((item) => item.id === job.id ? tracked : item));
      if (TERMINAL_INGEST_STATUSES.has(resolved.status) && resolved.status !== "conflict") {
        toast.success("文档冲突已解决");
        await refreshAfterIngest();
      } else {
        void watchIngestJob(job.knowledgeBaseId, job.id, job.filename);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "冲突解决失败");
    }
  };

  const renameDocument = async (document: ApiKnowledgeDocument) => {
    const currentTitle = document.title || document.original_filename || "";
    const nextTitle = window.prompt("请输入新的文档标题", currentTitle)?.trim();
    if (!nextTitle || nextTitle === currentTitle) return;
    try {
      const updated = await updateKnowledgeDocument(document.id, selectedBaseId, { title: nextTitle });
      setDocuments((current) => current.map((item) => item.id === document.id ? updated : item));
      setDocumentDetails((current) => ({ ...current, [document.id]: updated }));
      toast.success("文档标题已更新");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "文档更新失败");
    }
  };

  const removeDocument = async (document: ApiKnowledgeDocument) => {
    const title = document.title || document.original_filename || "未命名文档";
    if (!window.confirm(`确认删除文档“${title}”？索引清理将异步执行。`)) return;
    try {
      await deleteKnowledgeDocument(document.id, selectedBaseId);
      setExpandedDocumentId(null);
      await refreshAfterIngest();
      toast.success("文档已进入删除流程");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "文档删除失败");
    }
  };

  return (
    <div className="knowledge-page" style={{ minHeight: "100vh", background: "var(--tg-page-gradient)", paddingTop: 82, paddingBottom: 60 }}>
      <Header />
      <main className="knowledge-page-content" style={{ maxWidth: 1380, margin: "0 auto", padding: "0 24px" }}>
        <div className="knowledge-page-heading" style={{ display: "flex", justifyContent: "space-between", gap: 18, alignItems: "flex-start", flexWrap: "wrap" }}>
          <div>
            <div style={{ color: "var(--tg-accent)", fontSize: 11, letterSpacing: "0.18em", fontFamily: "var(--tg-font-mono)", fontWeight: 700 }}>
              TRUSTGUARD KNOWLEDGE
            </div>
            <h1 style={{ margin: "7px 0 7px", color: "var(--tg-text)", fontSize: 28, fontWeight: 800 }}>
              知识中心
            </h1>
            <p style={{ margin: 0, color: "var(--tg-text-muted)", fontSize: 14, lineHeight: 1.7 }}>
              单租户共享知识中心：通过 Agent Gateway 完成知识问答、检索、知识库与文档管理。
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadBootstrap()}
            disabled={bootstrapLoading}
            style={{
              ...inputStyle,
              width: "auto",
              padding: "8px 13px",
              display: "flex",
              alignItems: "center",
              gap: 7,
              cursor: bootstrapLoading ? "wait" : "pointer",
              color: "var(--tg-accent)",
            }}
          >
            <RefreshCw size={14} className={bootstrapLoading ? "animate-spin" : undefined} />
            刷新连接
          </button>
        </div>

        <div className="knowledge-metrics" style={{ display: "flex", gap: 13, flexWrap: "wrap", marginTop: 24 }}>
          <MetricCard
            icon={<Activity size={15} />}
            label="RAG 服务"
            value={<span style={{ color: statusColor(health?.status) }}>{health?.status?.toUpperCase() ?? "OFFLINE"}</span>}
            detail={health?.service ?? bootstrapError ?? "等待连接"}
          />
          <MetricCard
            icon={<Database size={15} />}
            label="知识库"
            value={bases.length}
            detail={selectedBase?.name ?? "尚未选择知识库"}
          />
          <MetricCard
            icon={<FileText size={15} />}
            label="当前库文档"
            value={selectedBase?.document_count ?? documentTotal}
            detail={`内容版本 ${selectedBase?.content_revision ?? "—"}`}
          />
          <MetricCard
            icon={<BookOpen size={15} />}
            label="Embedding"
            value={<span style={{ fontSize: 16 }}>{selectedBase?.embedding_model ?? "—"}</span>}
            detail={selectedBase ? `${selectedBase.embedding_provider} · ${selectedBase.embedding_dim}d` : "等待知识库信息"}
          />
        </div>

        <div className="knowledge-view-tabs" style={{ ...panelStyle, marginTop: 20, padding: 6, display: "flex", gap: 6, width: "fit-content" }}>
          {([
            ["answer", "知识问答", MessageSquareText],
            ["search", "知识检索", Search],
            ["documents", canManage ? "知识管理" : "文档浏览", FileText],
          ] as const).map(([name, label, Icon]) => (
            <button
              key={name}
              type="button"
              onClick={() => setView(name)}
              style={{
                border: view === name ? "1px solid var(--tg-panel-border)" : "1px solid transparent",
                borderRadius: 7,
                padding: "8px 14px",
                background: view === name ? "var(--tg-accent-soft)" : "transparent",
                color: view === name ? "var(--tg-accent)" : "var(--tg-text-muted)",
                display: "flex",
                gap: 7,
                alignItems: "center",
                cursor: "pointer",
                fontFamily: "var(--tg-font-ui)",
                fontSize: 13,
                fontWeight: view === name ? 700 : 600,
              }}
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
        </div>

        {bootstrapError && (
          <div style={{ marginTop: 18, ...panelStyle, borderColor: "rgba(248,113,113,0.35)", padding: 16, color: "var(--tg-danger)", fontSize: 13 }}>
            无法读取 RAG 知识库：{bootstrapError}。请检查 Gateway 的 RAG_SERVICE_BASE_URL。
          </div>
        )}

        {view === "answer" ? (
          <KnowledgeAnswerPanel
            bases={bases}
            selectedBaseId={selectedBaseId}
            onSelectedBaseIdChange={(knowledgeBaseId) => {
              setSelectedBaseId(knowledgeBaseId);
              setSearchResult(null);
            }}
          />
        ) : view === "search" ? (
          <section className="knowledge-search-layout">
            <div className="knowledge-form-panel" style={{ ...panelStyle, padding: 20, alignSelf: "start" }}>
              <label style={{ display: "block", color: "var(--tg-text-muted)", fontSize: 13, fontWeight: 600, marginBottom: 8 }}>知识库范围</label>
              <select
                value={selectedBaseId}
                onChange={(event) => {
                  setSelectedBaseId(event.target.value);
                  setSearchResult(null);
                }}
                disabled={bootstrapLoading || bases.length === 0}
                style={{ ...inputStyle, padding: "9px 11px" }}
              >
                {bases.length === 0 && <option value="">没有可用知识库</option>}
                {bases.map((base) => (
                  <option key={base.id} value={base.id}>
                    {base.name} · {base.document_count} docs
                  </option>
                ))}
              </select>

              <label style={{ display: "block", color: "var(--tg-text-muted)", fontSize: 13, fontWeight: 600, margin: "18px 0 8px" }}>检索问题</label>
              <textarea
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") void runSearch();
                }}
                placeholder="例如：Apache Shiro RememberMe 识别后应验证哪些风险？"
                rows={7}
                style={{ ...inputStyle, padding: 11, resize: "vertical", lineHeight: 1.6 }}
              />

              <div style={{ display: "grid", gridTemplateColumns: "1fr 95px", gap: 10, marginTop: 13 }}>
                <select value={mode} onChange={(event) => setMode(event.target.value as RetrievalMode)} style={{ ...inputStyle, padding: "8px 10px" }}>
                  <option value="auto">自动规划</option>
                  <option value="focused">聚焦检索</option>
                  <option value="comprehensive">综合检索</option>
                  <option value="enumeration">枚举检索</option>
                </select>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={topK}
                  onChange={(event) => setTopK(Math.max(1, Math.min(20, Number(event.target.value) || 1)))}
                  aria-label="返回数量"
                  style={{ ...inputStyle, padding: "8px 10px" }}
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 9, marginTop: 15 }}>
                {[
                  ["向量召回", enableVector, setEnableVector],
                  ["关键词召回", enableKeyword, setEnableKeyword],
                  ["结果重排", enableRerank, setEnableRerank],
                  ["Query 改写", enableRewrite, setEnableRewrite],
                ].map(([label, value, setter]) => (
                  <label key={label as string} style={{ color: "var(--tg-text-muted)", fontSize: 13, fontWeight: 550, display: "flex", gap: 8, alignItems: "center" }}>
                    <input
                      type="checkbox"
                      checked={value as boolean}
                      onChange={(event) => (setter as (next: boolean) => void)(event.target.checked)}
                    />
                    {label as string}
                  </label>
                ))}
              </div>

              <button
                type="button"
                onClick={() => void runSearch()}
                disabled={searching || !selectedBaseId}
                style={{
                  marginTop: 18,
                  width: "100%",
                  border: "1px solid rgba(34,211,238,0.42)",
                  borderRadius: 7,
                  padding: "10px 14px",
                  background: "var(--tg-accent-soft)",
                  color: "var(--tg-accent)",
                  fontFamily: "var(--tg-font-ui)",
                  fontSize: 13,
                  fontWeight: 700,
                  cursor: searching ? "wait" : "pointer",
                }}
              >
                {searching ? "正在执行混合检索…" : "开始检索  CTRL + ENTER"}
              </button>
            </div>

            <div style={{ minWidth: 0 }}>
              {!searchResult && (
                <div className="knowledge-empty-state" style={{ ...panelStyle, minHeight: 380, display: "grid", placeItems: "center", padding: 34, textAlign: "center" }}>
                  <div>
                    <Search size={34} color="var(--tg-accent)" style={{ margin: "0 auto 14px", opacity: 0.75 }} />
                    <h2 style={{ color: "var(--tg-text)", fontSize: 18, fontWeight: 700, margin: 0 }}>等待知识检索</h2>
                    <p style={{ color: "var(--tg-text-muted)", fontSize: 14, maxWidth: 480, lineHeight: 1.8 }}>
                      检索结果由 TrustGuard RAG 提供。当前页面只读取结构化结果，不直接访问 Qdrant 或 OpenSearch。
                    </p>
                  </div>
                </div>
              )}

              {searchResult && (
                <>
                  <div style={{ ...panelStyle, padding: 15, display: "flex", gap: 16, flexWrap: "wrap", color: "var(--tg-text-muted)", fontSize: 12 }}>
                    <span>结果 <strong style={{ color: "var(--tg-text)" }}>{searchResult.total}</strong></span>
                    <span>耗时 <strong style={{ color: "var(--tg-text)" }}>{searchResult.retrieval_time_ms.toFixed(1)} ms</strong></span>
                    <span>模式 <strong style={{ color: "var(--tg-text)" }}>{searchResult.effective_mode}</strong></span>
                    <span>融合 <strong style={{ color: "var(--tg-text)" }}>{searchResult.fusion_method}</strong></span>
                    <span>版本 <strong style={{ color: "var(--tg-text)" }}>{searchResult.content_revision}</strong></span>
                    {searchResult.degraded_components.length > 0 && (
                      <span style={{ color: "var(--tg-warning)" }}>降级：{searchResult.degraded_components.join("、")}</span>
                    )}
                  </div>

                  {searchResult.abstained && searchResult.results.length === 0 && (
                    <div style={{ ...panelStyle, marginTop: 12, padding: 22, color: "var(--tg-warning)" }}>
                      当前知识库证据不足，RAG 已拒绝返回低可信结果：{searchResult.abstention_reason ?? "low_confidence"}
                    </div>
                  )}

                  <div style={{ display: "grid", gap: 12, marginTop: 12 }}>
                    {searchResult.results.map((hit, index) => (
                      <article key={hit.chunk_id} style={{ ...panelStyle, padding: 17 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", gap: 14 }}>
                          <div style={{ minWidth: 0 }}>
                            <div style={{ color: "var(--tg-accent)", fontFamily: "var(--tg-font-mono)", fontSize: 12, fontWeight: 700 }}>
                              #{String(index + 1).padStart(2, "0")} · CHUNK {hit.source.chunk_index + 1}
                              {hit.source.page_no ? ` · PAGE ${hit.source.page_no}` : ""}
                            </div>
                            <h3 style={{ margin: "6px 0 0", color: "var(--tg-text)", fontSize: 14 }}>
                              {hit.title || hit.source.original_filename || hit.source.source_uri}
                            </h3>
                          </div>
                          <div style={{ color: "var(--tg-text-muted)", fontSize: 11, lineHeight: 1.55, textAlign: "right", fontFamily: "var(--tg-font-mono)", flexShrink: 0 }}>
                            <div>RERANK {formatScore(hit.rerank_score)}</div>
                            <div>VECTOR {formatScore(hit.vector_score)}</div>
                            <div>KEYWORD {formatScore(hit.keyword_score)}</div>
                          </div>
                        </div>
                        <p style={{ color: "var(--tg-text-muted)", fontSize: 13, lineHeight: 1.75, whiteSpace: "pre-wrap", margin: "13px 0 0" }}>
                          {hit.text}
                        </p>
                        <div style={{ marginTop: 12, color: "var(--tg-text-muted)", fontSize: 11, fontFamily: "var(--tg-font-mono)", overflowWrap: "anywhere" }}>
                          {hit.source.document_id}
                        </div>
                      </article>
                    ))}
                  </div>
                </>
              )}
            </div>
          </section>
        ) : (
          <section style={{ marginTop: 18 }}>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              accept={capabilities?.sources?.find((item) => item.source_type === "file")?.mime_types?.join(",")}
              onChange={(event) => void uploadFiles(event.target.files)}
            />
            <div style={{ ...panelStyle, padding: 14, display: "flex", gap: 10, flexWrap: "wrap" }}>
              <select
                value={selectedBaseId}
                onChange={(event) => setSelectedBaseId(event.target.value)}
                style={{ ...inputStyle, width: "min(360px, 100%)", padding: "8px 11px" }}
              >
                {bases.length === 0 && <option value="">没有可用知识库</option>}
                {bases.map((base) => (
                  <option key={base.id} value={base.id}>{base.name}</option>
                ))}
              </select>
              <input
                value={documentQuery}
                onChange={(event) => setDocumentQuery(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && void loadDocuments()}
                placeholder="按标题或文件名筛选文档"
                style={{ ...inputStyle, flex: "1 1 260px", padding: "8px 11px" }}
              />
              <button
                type="button"
                onClick={() => void loadDocuments()}
                disabled={documentsLoading}
                style={{ ...inputStyle, width: "auto", padding: "8px 13px", color: "var(--tg-accent)", cursor: "pointer" }}
              >
                {documentsLoading ? "加载中…" : "刷新文档"}
              </button>
              {canManage && (
                <>
                  <button
                    type="button"
                    onClick={openCreateBase}
                    style={{ ...inputStyle, width: "auto", padding: "8px 13px", color: "var(--tg-success)", cursor: "pointer", display: "flex", gap: 6, alignItems: "center" }}
                  >
                    <Plus size={14} />
                    新建知识库
                  </button>
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={!selectedBaseId || uploading}
                    style={{
                      ...inputStyle,
                      width: "auto",
                      padding: "8px 13px",
                      color: "var(--tg-accent)",
                      cursor: !selectedBaseId || uploading ? "not-allowed" : "pointer",
                      display: "flex",
                      gap: 6,
                      alignItems: "center",
                      opacity: !selectedBaseId ? 0.55 : 1,
                    }}
                  >
                    <UploadCloud size={14} />
                    {uploading ? "正在提交…" : "上传文档"}
                  </button>
                </>
              )}
            </div>

            {canManage && selectedBase && (
              <div className="knowledge-management-grid">
                <div style={{ ...panelStyle, padding: 17 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 14, alignItems: "flex-start" }}>
                    <div>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", color: "var(--tg-accent)", fontSize: 11, fontFamily: "var(--tg-font-mono)", fontWeight: 700 }}>
                        <ShieldCheck size={14} />
                        SINGLE-TENANT SHARED KNOWLEDGE
                      </div>
                      <h2 style={{ color: "var(--tg-text)", fontSize: 16, margin: "8px 0 4px" }}>{selectedBase.name}</h2>
                      <p style={{ color: "var(--tg-text-muted)", fontSize: 14, lineHeight: 1.75, margin: 0 }}>
                        {selectedBase.description || "暂无知识库描述"}
                      </p>
                    </div>
                    <div style={{ display: "flex", gap: 7, flexShrink: 0 }}>
                      <button
                        type="button"
                        onClick={openEditBase}
                        className="knowledge-icon-button"
                        title="编辑知识库"
                      >
                        <Edit3 size={14} />
                      </button>
                      <button
                        type="button"
                        onClick={() => void removeSelectedBase()}
                        disabled={selectedBase.is_default || selectedBase.is_system}
                        className="knowledge-icon-button knowledge-danger-button"
                        title={selectedBase.is_default || selectedBase.is_system ? "默认或系统知识库不可删除" : "删除知识库"}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 14, color: "var(--tg-text-muted)", fontSize: 11, fontFamily: "var(--tg-font-mono)" }}>
                    <span>{selectedBase.document_count} DOCUMENTS</span>
                    <span>REVISION {selectedBase.content_revision}</span>
                    <span>{selectedBase.embedding_profile}</span>
                    {(selectedBase.is_default || selectedBase.is_system) && <span style={{ color: "var(--tg-warning)" }}>PROTECTED</span>}
                  </div>
                </div>

                <div style={{ ...panelStyle, padding: 17 }}>
                  <div style={{ color: "var(--tg-text)", fontSize: 13, fontWeight: 700 }}>文件入库</div>
                  <p style={{ color: "var(--tg-text-muted)", fontSize: 13, lineHeight: 1.7, margin: "8px 0 0" }}>
                    支持 PDF、DOCX、文本、Markdown、CSV、JSON、HTML 与启用 OCR 后的图片。
                    {capabilities?.sources?.[0]?.max_bytes
                      ? ` 单文件最大 ${(capabilities.sources[0].max_bytes! / 1024 / 1024).toFixed(0)} MiB。`
                      : ""}
                  </p>
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploading}
                    className="knowledge-upload-zone"
                  >
                    <UploadCloud size={22} />
                    <span>{uploading ? "正在向 RAG 提交文件…" : "选择一个或多个文件入库"}</span>
                  </button>
                </div>
              </div>
            )}

            {canManage && baseEditorOpen && (
              <div style={{ ...panelStyle, marginTop: 12, padding: 17, borderColor: "rgba(34,211,238,0.35)" }}>
                <div style={{ color: "var(--tg-text)", fontSize: 14, fontWeight: 700 }}>
                  {editingBaseId ? "编辑知识库" : "新建知识库"}
                </div>
                <div className="knowledge-base-form">
                  <input
                    value={baseName}
                    onChange={(event) => setBaseName(event.target.value)}
                    placeholder="知识库名称"
                    maxLength={128}
                    style={{ ...inputStyle, padding: "9px 11px" }}
                  />
                  <input
                    value={baseDescription}
                    onChange={(event) => setBaseDescription(event.target.value)}
                    placeholder="知识库描述（可选）"
                    maxLength={1024}
                    style={{ ...inputStyle, padding: "9px 11px" }}
                  />
                  <button
                    type="button"
                    onClick={() => void saveKnowledgeBase()}
                    disabled={savingBase}
                    className="knowledge-primary-button"
                  >
                    {savingBase ? "保存中…" : "保存"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setBaseEditorOpen(false)}
                    className="knowledge-secondary-button"
                  >
                    取消
                  </button>
                </div>
              </div>
            )}

            {canManage && uploadJobs.length > 0 && (
              <div style={{ ...panelStyle, marginTop: 12, padding: 17 }}>
                <div style={{ color: "var(--tg-text)", fontSize: 13, fontWeight: 700 }}>最近入库任务</div>
                <div style={{ display: "grid", gap: 9, marginTop: 11 }}>
                  {uploadJobs.map((job) => (
                    <div key={job.id} className="knowledge-job-row">
                      <div style={{ minWidth: 0 }}>
                        <div style={{ color: "var(--tg-text)", fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {job.filename}
                        </div>
                        <div style={{ color: "var(--tg-text-muted)", fontSize: 11, fontFamily: "var(--tg-font-mono)", marginTop: 4 }}>
                          {job.current_step || "queued"} · {job.id}
                        </div>
                        {job.error_message && (
                          <div style={{ color: "var(--tg-danger)", fontSize: 12, marginTop: 6 }}>{job.error_message}</div>
                        )}
                      </div>
                      <span style={{ color: statusColor(job.status), fontSize: 11, fontFamily: "var(--tg-font-mono)", fontWeight: 700, textTransform: "uppercase" }}>
                        {job.status}
                      </span>
                      {job.status === "conflict" && job.pending_document_id && (
                        <div className="knowledge-conflict-actions">
                          <button
                            type="button"
                            onClick={() => void resolveConflict(job, job.pending_document_id!)}
                            className="knowledge-primary-button"
                          >
                            使用新版本
                          </button>
                          {job.conflict_candidates[0] && (
                            <button
                              type="button"
                              onClick={() => void resolveConflict(job, job.conflict_candidates[0])}
                              className="knowledge-secondary-button"
                            >
                              保留现有
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {!canManage && (
              <div style={{ ...panelStyle, marginTop: 12, padding: 14, color: "var(--tg-text-muted)", fontSize: 13, lineHeight: 1.65 }}>
                当前账号为只读角色。知识库创建、文档上传、编辑和删除仅对 ADMIN/OPERATOR 开放。
              </div>
            )}

            <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
              {!documentsLoading && documents.length === 0 && (
                <div style={{ ...panelStyle, padding: 32, textAlign: "center", color: "var(--tg-text-muted)" }}>
                  当前知识库没有可显示的文档。
                </div>
              )}
              {documents.map((document) => {
                const expanded = expandedDocumentId === document.id;
                const detail = documentDetails[document.id] ?? document;
                const chunks = documentChunks[document.id] ?? [];
                return (
                  <article key={document.id} style={{ ...panelStyle, overflow: "hidden" }}>
                    <button
                      type="button"
                      onClick={() => void toggleDocument(document)}
                      style={{
                        width: "100%",
                        padding: "15px 17px",
                        border: 0,
                        background: "transparent",
                        color: "inherit",
                        display: "grid",
                        gridTemplateColumns: "22px minmax(0,1fr) auto",
                        alignItems: "center",
                        gap: 10,
                        cursor: "pointer",
                        textAlign: "left",
                      }}
                    >
                      {expanded ? <ChevronDown size={16} color="var(--tg-accent)" /> : <ChevronRight size={16} color="var(--tg-text-faint)" />}
                      <div style={{ minWidth: 0 }}>
                        <div style={{ color: "var(--tg-text)", fontSize: 13, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {document.title || document.original_filename || "未命名文档"}
                        </div>
                        <div style={{ marginTop: 5, color: "var(--tg-text-muted)", fontSize: 11, fontFamily: "var(--tg-font-mono)", overflow: "hidden", textOverflow: "ellipsis" }}>
                          {document.original_filename || document.source_uri} · {formatDate(document.created_at)}
                        </div>
                      </div>
                      <span style={{ color: statusColor(document.status), fontSize: 11, fontFamily: "var(--tg-font-mono)", fontWeight: 700, textTransform: "uppercase" }}>
                        {document.status}
                      </span>
                    </button>

                    {expanded && (
                      <div style={{ borderTop: "1px solid var(--tg-panel-border)", padding: 17, background: "var(--tg-panel-muted)" }}>
                        {documentDetailLoading === document.id ? (
                          <div style={{ color: "var(--tg-text-muted)", fontSize: 13 }}>正在读取文档与分块…</div>
                        ) : (
                          <>
                            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", color: "var(--tg-text-muted)", fontSize: 12 }}>
                              <span>版本 <strong style={{ color: "var(--tg-text)" }}>{detail.doc_version}</strong></span>
                              <span>分块 <strong style={{ color: "var(--tg-text)" }}>{chunks.length}</strong></span>
                              <span>类型 <strong style={{ color: "var(--tg-text)" }}>{detail.mime_type ?? detail.source_type}</strong></span>
                              <span style={{ overflowWrap: "anywhere" }}>ID <strong style={{ color: "var(--tg-text)" }}>{detail.id}</strong></span>
                            </div>
                            {canManage && (
                              <div style={{ display: "flex", gap: 8, marginTop: 13 }}>
                                <button
                                  type="button"
                                  onClick={() => void renameDocument(document)}
                                  className="knowledge-secondary-button"
                                >
                                  <Edit3 size={13} />
                                  编辑标题
                                </button>
                                <button
                                  type="button"
                                  onClick={() => void removeDocument(document)}
                                  className="knowledge-secondary-button knowledge-danger-button"
                                >
                                  <Trash2 size={13} />
                                  删除文档
                                </button>
                              </div>
                            )}
                            <div style={{ display: "grid", gap: 9, marginTop: 14 }}>
                              {chunks.map((chunk) => (
                                <div key={chunk.id} style={{ background: "var(--tg-code-bg)", border: "1px solid var(--tg-panel-border)", borderRadius: 7, padding: 13 }}>
                                  <div style={{ color: "var(--tg-accent)", fontSize: 11, fontFamily: "var(--tg-font-mono)", fontWeight: 700 }}>
                                    CHUNK {chunk.chunk_index + 1} · PAGE {chunk.page_no ?? "—"} · {chunk.token_count} TOKENS
                                  </div>
                                  <p style={{ margin: "10px 0 0", color: "var(--tg-text-muted)", fontSize: 14, lineHeight: 1.8, whiteSpace: "pre-wrap" }}>
                                    {chunk.text}
                                  </p>
                                </div>
                              ))}
                            </div>
                          </>
                        )}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
