import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  BookOpenCheck,
  Bot,
  CircleStop,
  Database,
  Globe2,
  LoaderCircle,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  ClipboardCheck,
  Settings2,
  ShieldCheck,
  Sparkles,
  UserCheck,
} from "lucide-react";
import { toast } from "sonner";

import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import {
  controlKnowledgeCrawlerJob,
  createKnowledgeCrawlerJob,
  getKnowledgeCrawlerDefaults,
  listKnowledgeBases,
  listKnowledgeCrawlerJobs,
  listKnowledgeCrawlerPresets,
  type ApiKnowledgeBase,
  type ApiKnowledgeCrawlerDefaults,
  type ApiKnowledgeCrawlerJob,
  type ApiKnowledgeCrawlerPreset,
} from "@/shared/lib/api";
import "./KnowledgeCollectionPage.css";

type PresetMode = "custom" | "preset" | null;

const DEFAULTS: ApiKnowledgeCrawlerDefaults = {
  max_results_per_keyword: 10,
  max_pages_per_site: 10,
  max_total_pages: 100,
  min_content_chars: 80,
  fetch_delay_seconds: 1,
  max_retries: 2,
  retry_base_seconds: 1,
  agent_review_available: false,
  agent_review_model: null,
};

const ACTIVE_STATUSES = new Set(["queued", "running"]);

function currentRole(): string {
  try {
    const raw = localStorage.getItem("sentinel_session_v1");
    const session = raw ? JSON.parse(raw) as { role?: string } : null;
    return (session?.role || "VIEWER").toUpperCase();
  } catch {
    return "VIEWER";
  }
}

function lines(value: string): string[] {
  return [...new Set(value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean))];
}

function numeric(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function statusLabel(status: ApiKnowledgeCrawlerJob["status"]): string {
  return {
    queued: "排队中",
    running: "采集中",
    paused: "已暂停",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已停止",
  }[status];
}

export default function KnowledgeCollectionPage() {
  const navigate = useNavigate();
  const { loggedIn } = useAppSession();
  const canManage = useMemo(() => ["ADMIN", "OPERATOR"].includes(currentRole()), []);

  const [bases, setBases] = useState<ApiKnowledgeBase[]>([]);
  const [presets, setPresets] = useState<ApiKnowledgeCrawlerPreset[]>([]);
  const [defaults, setDefaults] = useState(DEFAULTS);
  const [jobs, setJobs] = useState<ApiKnowledgeCrawlerJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<PresetMode>(null);
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState("");
  const [urls, setUrls] = useState("");
  const [keywords, setKeywords] = useState("");
  const [siteUrls, setSiteUrls] = useState("");
  const [maxResults, setMaxResults] = useState(DEFAULTS.max_results_per_keyword);
  const [maxSitePages, setMaxSitePages] = useState(DEFAULTS.max_pages_per_site);
  const [maxTotalPages, setMaxTotalPages] = useState(DEFAULTS.max_total_pages);
  const [minContentChars, setMinContentChars] = useState(DEFAULTS.min_content_chars);
  const [delaySeconds, setDelaySeconds] = useState(DEFAULTS.fetch_delay_seconds);
  const [maxRetries, setMaxRetries] = useState(DEFAULTS.max_retries);
  const [retryBaseSeconds, setRetryBaseSeconds] = useState(DEFAULTS.retry_base_seconds);
  const [force, setForce] = useState(false);
  const [reviewMode, setReviewMode] = useState<"human" | "agent">("human");
  const [reviewCriteria, setReviewCriteria] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [controllingId, setControllingId] = useState<string | null>(null);

  const selectedPreset = useMemo(
    () => presets.find((item) => item.id === selectedPresetId) ?? null,
    [presets, selectedPresetId],
  );
  const baseNames = useMemo(
    () => new Map(bases.map((item) => [item.id, item.name])),
    [bases],
  );
  const runningCount = jobs.filter((job) => ACTIVE_STATUSES.has(job.status)).length;
  const completedCount = jobs.filter((job) => job.status === "succeeded").length;

  const applyDefaults = useCallback((next: ApiKnowledgeCrawlerDefaults) => {
    setDefaults(next);
    setMaxResults(next.max_results_per_keyword);
    setMaxSitePages(next.max_pages_per_site);
    setMaxTotalPages(next.max_total_pages);
    setMinContentChars(next.min_content_chars);
    setDelaySeconds(next.fetch_delay_seconds);
    setMaxRetries(next.max_retries);
    setRetryBaseSeconds(next.retry_base_seconds);
  }, []);

  const loadAll = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      const [baseResult, presetResult, defaultResult, jobResult] = await Promise.all([
        listKnowledgeBases(),
        listKnowledgeCrawlerPresets(),
        getKnowledgeCrawlerDefaults(),
        listKnowledgeCrawlerJobs({ limit: 30 }),
      ]);
      const nextBases = baseResult.items ?? [];
      setBases(nextBases);
      setPresets(presetResult.items ?? []);
      if (quiet) setDefaults(defaultResult);
      else applyDefaults(defaultResult);
      setJobs(jobResult.items ?? []);
      setKnowledgeBaseId((current) => current && nextBases.some((item) => item.id === current)
        ? current
        : nextBases.find((item) => item.is_default)?.id ?? nextBases[0]?.id ?? "");
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "采集工作台加载失败";
      setError(message);
      if (quiet) toast.error(message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [applyDefaults]);

  const refreshJobs = useCallback(async () => {
    try {
      const result = await listKnowledgeCrawlerJobs({ limit: 30 });
      setJobs(result.items ?? []);
    } catch {
      // Background polling is best effort; explicit refresh still reports errors.
    }
  }, []);

  useEffect(() => {
    if (!loggedIn) {
      localStorage.setItem("sentinel_login_redirect", "/knowledge/collect");
      navigate("/login");
      return;
    }
    void loadAll();
  }, [loadAll, loggedIn, navigate]);

  useEffect(() => {
    if (!loggedIn || runningCount === 0) return;
    const timer = window.setInterval(() => void refreshJobs(), 4000);
    return () => window.clearInterval(timer);
  }, [loggedIn, refreshJobs, runningCount]);

  const choosePreset = (preset: ApiKnowledgeCrawlerPreset) => {
    setMode("preset");
    setSelectedPresetId(preset.id);
    setKeywords((preset.keywords ?? []).join("\n"));
    setSiteUrls((preset.site_urls ?? []).join("\n"));
    setReviewCriteria(preset.review_criteria ?? "");
    setUrls("");
    const sourceCount = preset.keywords.length + preset.site_urls.length + preset.structured_sources.length;
    setMaxTotalPages(Math.min(200, Math.max(30, sourceCount * 10)));
    const matched = bases.find((base) => base.name === preset.category_name)
      ?? bases.find((base) => base.is_default)
      ?? bases[0];
    if (matched) setKnowledgeBaseId(matched.id);
    window.setTimeout(() => document.getElementById("collection-form")?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };

  const chooseCustom = () => {
    setMode("custom");
    setSelectedPresetId(null);
    setUrls("");
    setKeywords("");
    setSiteUrls("");
    setReviewCriteria("");
    setMaxTotalPages(30);
    window.setTimeout(() => document.getElementById("collection-form")?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };

  const resetLimits = () => applyDefaults(defaults);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canManage) return toast.error("当前角色没有创建采集任务的权限");
    if (!mode) return toast.error("请先选择知识库分类预置或自定义采集");
    if (!knowledgeBaseId) return toast.error("请选择目标知识库");
    const sourceGroups = [lines(urls), lines(keywords), lines(siteUrls)];
    if (mode === "custom" && !sourceGroups.some((items) => items.length > 0)) {
      return toast.error("自定义采集至少填写一个 URL、关键词或站点入口");
    }
    if (reviewMode === "agent" && !reviewCriteria.trim()) {
      return toast.error("Agent 审核必须填写审核标准");
    }
    setSubmitting(true);
    try {
      const job = await createKnowledgeCrawlerJob({
        knowledgeBaseId,
        presetIds: selectedPresetId ? [selectedPresetId] : [],
        urls: sourceGroups[0],
        keywords: sourceGroups[1],
        siteUrls: sourceGroups[2],
        maxResultsPerKeyword: maxResults,
        maxPagesPerSite: maxSitePages,
        maxTotalPages,
        minContentChars,
        fetchDelaySeconds: delaySeconds,
        maxRetries,
        retryBaseSeconds,
        force,
        reviewMode,
        reviewCriteria: reviewCriteria.trim(),
      });
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      toast.success("采集任务已提交", { description: "清洗完成后需人工审核，审核通过才会入库" });
    } catch (submitError) {
      toast.error(submitError instanceof Error ? submitError.message : "创建采集任务失败");
    } finally {
      setSubmitting(false);
    }
  };

  const controlJob = async (job: ApiKnowledgeCrawlerJob, action: "pause" | "resume" | "stop") => {
    if (!canManage) return toast.error("当前角色没有操作采集任务的权限");
    setControllingId(job.id);
    try {
      const updated = await controlKnowledgeCrawlerJob(job.id, action, job.knowledge_base_id);
      setJobs((current) => current.map((item) => item.id === updated.id ? updated : item));
      toast.success({ pause: "已请求暂停", resume: "采集任务已恢复", stop: "已请求停止" }[action]);
    } catch (controlError) {
      toast.error(controlError instanceof Error ? controlError.message : "任务操作失败");
    } finally {
      setControllingId(null);
    }
  };

  return (
    <div className="collection-page">
      <Header />
      <main className="collection-shell">
        <section className="collection-hero">
          <div>
            <div className="collection-eyebrow"><ShieldCheck size={14} /> TRUSTGUARD RAG PIPELINE</div>
            <h1>知识数据采集</h1>
            <p>按 Agent 任务阶段预置安全知识分类，完成抓取、清洗和去重；人工审核通过后再提交 RAG 入库。</p>
          </div>
          <button className="collection-button secondary" type="button" onClick={() => void loadAll(true)} disabled={refreshing}>
            <RefreshCw size={14} className={refreshing ? "animate-spin" : undefined} />
            刷新数据
          </button>
        </section>

        <section className="collection-metrics" aria-label="采集概览">
          <div><Database size={17} /><span>可用知识库</span><strong>{bases.length}</strong></div>
          <div><Sparkles size={17} /><span>分类预置</span><strong>{presets.length}</strong></div>
          <div><LoaderCircle size={17} /><span>执行中</span><strong>{runningCount}</strong></div>
          <div><BookOpenCheck size={17} /><span>已完成</span><strong>{completedCount}</strong></div>
        </section>

        {error && <div className="collection-error">无法连接 RAG 采集服务：{error}。请检查 Gateway 的 RAG_SERVICE_BASE_URL。</div>}

        <section className="collection-section">
          <div className="collection-section-heading">
            <div><span>01</span><div><h2>选择知识库分类预置</h2><p>预置会自动填写目标知识库、关键词和入口站点；也可以选择自定义采集。</p></div></div>
          </div>
          {loading ? (
            <div className="collection-loading"><LoaderCircle size={18} className="animate-spin" /> 正在读取分类预置…</div>
          ) : (
            <div className="collection-preset-grid">
              <button type="button" className={`collection-preset-card custom ${mode === "custom" ? "selected" : ""}`} onClick={chooseCustom}>
                <span className="preset-top"><strong>自定义采集</strong><b>CUSTOM</b></span>
                <small>不使用分类预置，自行填写 URL、检索关键词或站点入口。</small>
                <span className="preset-meta">不绑定分类路由 · 手动选择知识库</span>
              </button>
              {presets.map((preset) => (
                <button key={preset.id} type="button" className={`collection-preset-card ${selectedPresetId === preset.id ? "selected" : ""}`} onClick={() => choosePreset(preset)}>
                  <span className="preset-top"><strong>{preset.name}</strong><b>{preset.priority ?? ""}</b></span>
                  <small>{preset.description}</small>
                  <span className="preset-meta">
                    {preset.site_urls.length + preset.keywords.length + preset.structured_sources.length} 项配置
                    {preset.phases.length ? ` · ${preset.phases.join(" → ")}` : ""}
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>

        <form id="collection-form" className="collection-section collection-form" onSubmit={submit}>
          <div className="collection-section-heading">
            <div><span>02</span><div><h2>确认采集任务</h2><p>{selectedPreset ? `${selectedPreset.name} · ${selectedPreset.description}` : mode === "custom" ? "自定义采集 · 请至少填写一种采集来源" : "选择上方卡片后自动填写表单"}</p></div></div>
          </div>

          <div className="collection-form-grid">
            <label className="span-2">目标知识库
              <select value={knowledgeBaseId} onChange={(event) => setKnowledgeBaseId(event.target.value)} disabled={!canManage || bases.length === 0}>
                {bases.length === 0 && <option value="">没有可用知识库</option>}
                {bases.map((base) => <option key={base.id} value={base.id}>{base.name} · {base.document_count} docs</option>)}
              </select>
            </label>
            <label>直接 URL <em>每行一个</em>
              <textarea value={urls} onChange={(event) => setUrls(event.target.value)} rows={6} placeholder="https://example.com/security/advisory" disabled={!canManage || !mode} />
            </label>
            <label>站点入口 <em>站内发现页面</em>
              <textarea value={siteUrls} onChange={(event) => setSiteUrls(event.target.value)} rows={6} placeholder="https://example.com/security/" disabled={!canManage || !mode} />
            </label>
            <label className="span-2">检索关键词 <em>每行一个</em>
              <textarea value={keywords} onChange={(event) => setKeywords(event.target.value)} rows={5} placeholder="critical CVE vulnerability exploitation" disabled={!canManage || !mode} />
            </label>
          </div>

          <section className="collection-review-gate" aria-labelledby="review-gate-title">
            <div className="collection-review-heading">
              <div>
                <span>REVIEW GATE</span>
                <h3 id="review-gate-title">选择内容审核方式</h3>
                <p>所有内容仍会先进入暂存区，审核通过后才创建 RAG 入库任务。</p>
              </div>
              {reviewMode === "agent" && defaults.agent_review_model && <code>{defaults.agent_review_model}</code>}
            </div>
            <div className="collection-review-modes">
              <button type="button" className={reviewMode === "human" ? "selected" : ""} onClick={() => setReviewMode("human")} disabled={!canManage || !mode}>
                <span className="review-mode-icon"><UserCheck size={19} /></span>
                <span><strong>人工审核</strong><small>采集完成后通知审核人，逐条通过或驳回。</small></span>
                <i>{reviewMode === "human" ? "已选择" : "HUMAN"}</i>
              </button>
              <button type="button" className={reviewMode === "agent" ? "selected agent" : "agent"} onClick={() => setReviewMode("agent")} disabled={!canManage || !mode || !defaults.agent_review_available} title={defaults.agent_review_available ? undefined : "RAG 服务尚未配置审核模型"}>
                <span className="review-mode-icon"><Bot size={19} /></span>
                <span><strong>Agent 审核</strong><small>按审核标准自动判定；低置信度或异常自动转人工。</small></span>
                <i>{reviewMode === "agent" ? "已选择" : defaults.agent_review_available ? "AI READY" : "未配置"}</i>
              </button>
            </div>
            {reviewMode === "agent" && (
              <label className="collection-review-criteria">
                <span>Agent 审核标准 <em>{selectedPreset ? "已由分类预置自动填写，可继续调整" : "必填"}</em></span>
                <textarea
                  rows={6}
                  maxLength={8000}
                  value={reviewCriteria}
                  onChange={(event) => setReviewCriteria(event.target.value)}
                  placeholder="例如：仅通过包含明确 CVE 编号、受影响版本、漏洞成因及修复建议的内容；拒绝纯新闻摘要、无技术细节或来源不可核验的页面。"
                  disabled={!canManage}
                />
                <small><ShieldCheck size={12} /> 爬取正文会发送给已配置的审核模型；网页中的指令会被视为不可信内容。</small>
              </label>
            )}
          </section>

          <details className="collection-advanced">
            <summary><Settings2 size={14} /> 高级采集参数</summary>
            <div className="collection-limit-grid">
              <label>关键词结果上限<input type="number" min={1} max={20} value={maxResults} onChange={(e) => setMaxResults(Number(e.target.value))} /></label>
              <label>单站页面上限<input type="number" min={1} max={50} value={maxSitePages} onChange={(e) => setMaxSitePages(Number(e.target.value))} /></label>
              <label>任务页面上限<input type="number" min={1} max={200} value={maxTotalPages} onChange={(e) => setMaxTotalPages(Number(e.target.value))} /></label>
              <label>最少正文字符<input type="number" min={0} max={10000} value={minContentChars} onChange={(e) => setMinContentChars(Number(e.target.value))} /></label>
              <label>请求间隔（秒）<input type="number" min={0} max={30} step={0.1} value={delaySeconds} onChange={(e) => setDelaySeconds(Number(e.target.value))} /></label>
              <label>失败重试次数<input type="number" min={0} max={10} value={maxRetries} onChange={(e) => setMaxRetries(Number(e.target.value))} /></label>
              <label>重试基数（秒）<input type="number" min={0} max={60} step={0.1} value={retryBaseSeconds} onChange={(e) => setRetryBaseSeconds(Number(e.target.value))} /></label>
              <label className="collection-check"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> 强制重新采集已存在内容</label>
            </div>
            <button type="button" className="collection-reset" onClick={resetLimits}><RotateCcw size={13} /> 恢复服务默认值</button>
          </details>

          <div className="collection-submit-row">
            {!canManage && <span>当前为只读角色，可查看预置和任务，但不能创建采集。</span>}
            <button className="collection-button primary" type="submit" disabled={!canManage || !mode || !knowledgeBaseId || submitting}>
              {submitting ? <LoaderCircle size={15} className="animate-spin" /> : <Send size={15} />}
              {submitting ? "正在提交…" : "创建采集任务"}
            </button>
          </div>
        </form>

        <section className="collection-section">
          <div className="collection-section-heading jobs-heading">
            <div><span>03</span><div><h2>最近采集任务</h2><p>执行中的任务每 4 秒自动刷新；清洗完成后按所选模式审核，通过后才会入库。</p></div></div>
            <button type="button" className="collection-icon-button" onClick={() => void loadAll(true)} aria-label="刷新任务"><RefreshCw size={14} /></button>
          </div>
          {jobs.length === 0 ? (
            <div className="collection-empty"><Globe2 size={28} /><strong>暂无采集任务</strong><span>选择分类预置并提交后，任务会显示在这里。</span></div>
          ) : (
            <div className="collection-jobs">
              {jobs.map((job) => {
                const fetched = numeric(job.progress.fetched);
                const cleaned = numeric(job.progress.cleaned);
                const queued = numeric(job.progress.queued_for_ingest);
                const pendingReview = numeric(job.progress.pending_review);
                const failed = numeric(job.progress.failed);
                const maxPages = numeric(job.config.max_total_pages) || 1;
                const percent = job.status === "succeeded" ? 100 : Math.min(99, Math.round((fetched / maxPages) * 100));
                const presetId = Array.isArray(job.config.preset_ids) ? String(job.config.preset_ids[0] ?? "") : "";
                const preset = presets.find((item) => item.id === presetId);
                const awaitingReview = job.status === "succeeded"
                  && job.progress.review_status === "pending"
                  && pendingReview > 0;
                const reviewCompleted = job.status === "succeeded"
                  && job.progress.review_status === "completed";
                return (
                  <article className="collection-job" key={job.id}>
                    <div className="job-main">
                      <div className="job-title-row">
                        <span className={`job-status ${job.status}`}>{statusLabel(job.status)}</span>
                        <strong>{preset?.name ?? "自定义采集"}</strong>
                        <span className={`job-review-mode ${job.config.review_mode === "agent" ? "agent" : ""}`}>
                          {job.config.review_mode === "agent" ? <Bot size={11} /> : <UserCheck size={11} />}
                          {job.config.review_mode === "agent" ? "Agent 审核" : "人工审核"}
                        </span>
                        <code>{job.id.slice(0, 8)}</code>
                      </div>
                      <div className="job-meta">
                        <span><Database size={12} /> {baseNames.get(job.knowledge_base_id) ?? job.knowledge_base_id}</span>
                        <span>{formatDate(job.updated_at ?? job.created_at)}</span>
                      </div>
                      <div className="job-progress"><i style={{ width: `${percent}%` }} /></div>
                      <div className="job-stats">
                        <span>发现 <b>{numeric(job.progress.discovered)}</b></span>
                        <span>抓取 <b>{fetched}</b></span>
                        <span>清洗 <b>{cleaned}</b></span>
                        <span>待审核 <b>{pendingReview}</b></span>
                        <span>已送入库 <b>{queued}</b></span>
                        {failed > 0 && <span className="danger">失败 <b>{failed}</b></span>}
                        <span>{percent}%</span>
                      </div>
                      {(job.error_message || typeof job.progress.current_url === "string") && (
                        <div className={`job-detail ${job.error_message ? "danger" : ""}`}>
                          {job.error_message ?? `当前：${String(job.progress.current_url)}`}
                        </div>
                      )}
                    </div>
                    {canManage && (
                      <div className="job-actions">
                        {(awaitingReview || reviewCompleted) && (
                          <button type="button" onClick={() => navigate(`/knowledge/collect/review/${job.id}?knowledge_base_id=${encodeURIComponent(job.knowledge_base_id)}`)}>
                            <ClipboardCheck size={13} /> {awaitingReview ? "审核" : "查看审核结果"}
                          </button>
                        )}
                        {job.status === "running" && <button type="button" onClick={() => void controlJob(job, "pause")} disabled={controllingId === job.id}><Pause size={13} /> 暂停</button>}
                        {(job.status === "paused" || job.status === "failed") && <button type="button" onClick={() => void controlJob(job, "resume")} disabled={controllingId === job.id}><Play size={13} /> 恢复</button>}
                        {!["succeeded", "cancelled"].includes(job.status) && <button type="button" className="danger" onClick={() => void controlJob(job, "stop")} disabled={controllingId === job.id}><CircleStop size={13} /> 停止</button>}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
