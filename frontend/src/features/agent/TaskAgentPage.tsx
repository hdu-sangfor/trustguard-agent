import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, ChevronDown, ChevronLeft, ChevronRight, Circle, Loader2, PanelLeftClose, PanelLeftOpen, Pencil, Pin, PinOff, Plus, Search, Send, ShieldCheck, Square, SquareTerminal, Trash2 } from 'lucide-react';
import Header from '@/shared/components/Header';
import { useAppSession } from '@/shared/context/AppSessionContext';
import {
  confirmTaskAgentDraft,
  getTask,
  getTaskAgentConversation,
  getTaskEvents,
  getTaskReasoningSteps,
  listTaskAgentConversations,
  streamTaskAgentDraft,
  streamTaskEvents,
  updateTaskAgentConversation,
  deleteTaskAgentConversation,
  toFrontendStatus,
  type ApiAgentActivity,
  type ApiAlertTriageDraft,
  type ApiConversationMessage,
  type ApiEvent,
  type ApiPentestDraft,
  type ApiReasoningStep,
  type ApiTask,
  type ApiTaskAgentConversationSummary,
} from '@/shared/lib/api';
import { SENTINEL_ORBIT_TASKS_KEY, ORBIT_TASKS_UPDATED_EVENT, readStoredOrbitTasks, type StoredOrbitTask } from '@/shared/constants/orbitTasksStorage';
import MarkdownBlock from '@/shared/components/MarkdownBlock';
import ThinkingBlock from '@/shared/components/ThinkingBlock';
import ToolCallBlock from '@/shared/components/ToolCallBlock';

const CONVERSATION_STORAGE_KEY = 'trustguard.agent.conversationId';
const ACTIVITY_SETTLE_DELAY_MS = 800;
const WELCOME_MESSAGE: ChatMessage = {
  id: 'welcome',
  role: 'assistant',
  text: '告诉我你想测试的目标，或提供需要研判的 XDR 告警 UUID。我会先整理工作流草稿，确认后再执行并在对话中展示每一步。',
};

type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  taskId?: string | null;
  activities?: ApiAgentActivity[];
  draft?: ApiPentestDraft | ApiAlertTriageDraft | null;
  confirmationToken?: string | null;
  streaming?: boolean;
  thinking?: { startedAt: number } | null;
};

function toChatMessage(message: ApiConversationMessage, confirmedTaskId?: string | null): ChatMessage {
  return {
    id: message.id,
    role: message.role,
    text: message.text,
    taskId: message.taskId,
    activities: message.activities,
    draft: message.draft,
    confirmationToken: confirmedTaskId ? null : message.confirmationToken,
  };
}

function conversationTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  }
  return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

function conversationStatus(status?: string | null): string {
  const normalized = String(status ?? '').toUpperCase();
  if (normalized === 'PENDING') return '待启动';
  if (normalized === 'RUNNING') return '执行中';
  if (normalized === 'DONE') return '已完成';
  if (normalized === 'FAILED') return '失败';
  if (normalized === 'CANCELLED') return '已取消';
  return '';
}

const terminalStatuses = new Set<ApiTask['status']>(['DONE', 'FAILED', 'CANCELLED']);

function settleActivities(
  activities: ApiAgentActivity[] | undefined,
  status: 'done' | 'blocked' = 'done',
): ApiAgentActivity[] | undefined {
  const finishedAt = new Date().toISOString();
  const now = Date.now();
  return activities?.map((activity) => activity.status === 'running'
    ? {
        ...activity,
        status,
        finishedAt,
        durationMs: activityDurationMs(activity, now),
      }
    : activity);
}

function blockDraftResponse(
  activities: ApiAgentActivity[] | undefined,
  detail: string,
): ApiAgentActivity[] {
  const settled = settleActivities(activities, 'blocked') ?? [];
  if (settled.some((activity) => activity.status === 'blocked')) return settled;
  const timestamp = new Date().toISOString();
  return [...settled, {
    id: 'draft-response-blocked',
    kind: 'result',
    title: '生成任务回复',
    detail,
    status: 'blocked',
    timestamp,
    startedAt: timestamp,
    finishedAt: timestamp,
    durationMs: 0,
  }];
}

function settleMessages(items: ChatMessage[]): ChatMessage[] {
  return items.map((message) => ({ ...message, activities: settleActivities(message.activities) }));
}

function mergeActivity(items: ApiAgentActivity[] | undefined, next: ApiAgentActivity): ApiAgentActivity[] {
  const current = items ?? [];
  const index = current.findIndex((item) => item.id === next.id);
  if (index < 0) return [...current, next];
  return current.map((item, itemIndex) => itemIndex === index ? next : item);
}

function eventActivity(event: ApiEvent, index: number): ApiAgentActivity {
  const payload = event.payload ?? {};
  const detail = String(
    payload.message ?? payload.reason ?? payload.detail ?? payload.skill_id ?? payload.skillId ?? '',
  ).slice(0, 280);
  const type = String(event.eventType ?? 'EVENT');
  const isBlocked = type.includes('BLOCK') || type.includes('REJECT') || type.includes('FAILED');
  const isGuard = isBlocked || type.includes('GATE');
  const isTool = type.includes('SKILL') || type.includes('EXECUTION') || type.includes('PLAN');
  return {
    id: event.eventId ?? `event-${index}`,
    kind: isGuard ? 'guard' : isTool ? 'tool' : 'progress',
    title: type.replaceAll('_', ' '),
    detail,
    status: isBlocked ? 'blocked' : 'done',
    timestamp: event.timestamp ?? new Date().toISOString(),
  };
}

const triageResourceLabels: Record<string, string> = {
  alert: '告警详情',
  alert_proof: '告警原始证据',
  endpoint_logs: '端点日志',
  assets: '资产信息',
  incidents: '关联事件',
  incident_proof: '事件证据',
  whitelist: '白名单',
};

const triageStageLabels: Record<string, string> = {
  LOAD_ALERT: '读取告警详情',
  COLLECT_EVIDENCE: '收集关联证据',
  CHECK_WHITELIST: '核验白名单',
  QUERY_RAG: '查询辅助知识',
  MAKE_DECISION: '整理证据并判断',
  VALIDATE_DECISION: '校验研判结论',
  PERSIST_RESULT: '保存研判结果',
  RECOMMEND_ACTION: '生成处置建议',
  WORKFLOW: '完成告警研判',
};

type WorkflowPhase = {
  id: string;
  label: string;
};

const pentestPhases: WorkflowPhase[] = [
  { id: 'RECON', label: '信息收集' },
  { id: 'THREAT_MODEL', label: '威胁建模' },
  { id: 'VULN_SCAN', label: '漏洞扫描' },
  { id: 'EXPLOIT', label: '利用验证' },
  { id: 'REPORT', label: '生成报告' },
];

const alertTriagePhases: WorkflowPhase[] = [
  'LOAD_ALERT',
  'COLLECT_EVIDENCE',
  'CHECK_WHITELIST',
  'QUERY_RAG',
  'MAKE_DECISION',
  'VALIDATE_DECISION',
  'PERSIST_RESULT',
  'RECOMMEND_ACTION',
].map((id) => ({ id, label: triageStageLabels[id] }));

function triageStage(eventType: string): string {
  const normalized = eventType.replace(/^AT_/, '');
  return Object.keys(triageStageLabels).find((key) => normalized.startsWith(key)) ?? 'WORKFLOW';
}

function triageParamsDetail(resource: string, params: Record<string, unknown>): string {
  const values = Object.entries(params)
    .map(([key, value]) => `${key}=${typeof value === 'string' ? value : JSON.stringify(value)}`)
    .join('，');
  return `${triageResourceLabels[resource] ?? resource}，请求条件：${values}`.slice(0, 520);
}

function triageEventActivity(event: ApiEvent, index: number): ApiAgentActivity {
  const payload = event.payload ?? {};
  const type = String(event.eventType ?? 'EVENT').toUpperCase();
  const requestId = String(payload.request_id ?? event.eventId ?? `triage-${index}`);
  const isRequest = type === 'AT_XDR_REQUEST' || type === 'AT_LLM_REQUEST';
  const isResponse = type === 'AT_XDR_RESPONSE' || type === 'AT_LLM_RESPONSE';
  const resource = String(payload.resource ?? (type.includes('LLM') ? 'llm' : ''));
  const stage = triageStage(type);
  let title = triageStageLabels[stage] ?? type.replaceAll('_', ' ');
  let detail = '';
  let status: ApiAgentActivity['status'] = 'done';
  let kind: ApiAgentActivity['kind'] = 'analysis';
  let id = `triage-stage-${stage}`;

  if (isRequest) {
    id = `triage-request-${requestId}`;
    kind = 'tool';
    status = 'running';
    title = type === 'AT_LLM_REQUEST' ? '请求 AI 生成结构化结论' : `查询 XDR：${payload.operation ?? triageResourceLabels[resource] ?? resource}`;
    detail = type === 'AT_LLM_REQUEST'
      ? `模型：${payload.model ?? '当前配置'}；输入包括告警、原始证据、端点日志、资产、关联事件和白名单结果。`
      : triageParamsDetail(resource, (payload.params as Record<string, unknown>) ?? {});
  } else if (isResponse) {
    id = `triage-request-${requestId}`;
    kind = 'tool';
    const responseStatus = String(payload.status ?? 'success').toLowerCase();
    status = responseStatus === 'failed' ? 'blocked' : 'done';
    title = type === 'AT_LLM_RESPONSE' ? '收到 AI 结构化响应' : `收到 XDR：${triageResourceLabels[resource] ?? resource}`;
    if (type === 'AT_LLM_RESPONSE') {
      detail = `模型已返回结构化结果，输入 token ${payload.input_tokens ?? '-'}，输出 token ${payload.output_tokens ?? '-'}。`;
    } else {
      const ids = Array.isArray(payload.record_ids) ? payload.record_ids.filter(Boolean).join('、') : '';
      detail = responseStatus === 'failed'
        ? `请求失败：${payload.error ?? 'XDR 查询失败'}`
        : `${responseStatus === 'empty' ? '没有返回记录' : `返回 ${payload.record_count ?? 0} 条记录`}${ids ? `；证据 ID：${ids}` : ''}`;
    }
  } else {
    id = `triage-stage-${stage}`;
    if (type.endsWith('_START')) {
      status = 'running';
      kind = type.includes('WHITELIST') ? 'guard' : 'analysis';
      title = triageStageLabels[stage] ?? title;
      detail = stage === 'COLLECT_EVIDENCE'
        ? '依次查询告警 proof、端点日志、资产、关联事件及事件证据。'
        : stage === 'MAKE_DECISION'
          ? '将已收集的 XDR 原始证据整理后提交给 AI，生成结构化 verdict。'
          : stage === 'CHECK_WHITELIST'
            ? '用告警特征匹配有效白名单，误报结论必须有精确匹配。'
            : String(payload.detail ?? '正在执行当前研判阶段。');
    } else if (type.endsWith('_COMPLETE')) {
      title = triageStageLabels[stage] ?? title;
      kind = stage === 'CHECK_WHITELIST' ? 'guard' : stage === 'WORKFLOW' ? 'result' : 'analysis';
      if (stage === 'LOAD_ALERT') {
        detail = `告警：${payload.name ?? payload.alert_uuid ?? '未知'}；严重度：${payload.severity ?? '未知'}；资产：${payload.asset_id ?? '未关联'}；主机：${payload.host_ip ?? '未提供'}。`;
      } else if (stage === 'COLLECT_EVIDENCE') {
        const endpointLogCount = Array.isArray(payload.endpoint_log_ids) ? payload.endpoint_log_ids.length : 0;
        detail = `证据收集完成：${payload.has_proof ? '有原始 proof' : '缺少 proof'}，端点日志 ${endpointLogCount} 条，资产 ${payload.asset_count ?? 0} 个，关联事件 ${payload.incident_count ?? 0} 个。`;
      } else if (stage === 'CHECK_WHITELIST') {
        const matches = Array.isArray(payload.match_ids) ? payload.match_ids.filter(Boolean).join('、') : '';
        detail = Number(payload.match_count ?? 0) > 0
          ? `精确命中 ${payload.match_count} 条有效白名单：${matches}。该结果将参与误报判断。`
          : '未命中有效白名单，不能仅凭相似脚本名或历史审批判定为误报。';
      } else if (stage === 'QUERY_RAG') {
        const citations = Array.isArray(payload.citation_ids) ? payload.citation_ids.filter(Boolean).join('、') : '';
        detail = `辅助知识查询完成，获得 ${payload.citation_count ?? 0} 条引用${citations ? `：${citations}` : ''}。RAG 只作为参考，不覆盖 XDR 原始证据。`;
      } else if (stage === 'MAKE_DECISION') {
        const confidence = typeof payload.confidence === 'number' ? `${Math.round(payload.confidence * 100)}%` : '未提供';
        detail = `AI 判断：${payload.verdict ?? '未知'}，置信度 ${confidence}。${payload.summary ?? ''}${payload.reasoning ? `\n判断依据：${payload.reasoning}` : ''}`;
      } else if (stage === 'VALIDATE_DECISION') {
        const errors = Array.isArray(payload.errors) ? payload.errors.join('；') : '';
        detail = Number(payload.error_count ?? 0) === 0
          ? '结构化结论通过校验：verdict、置信度、证据充分性和动作权限均符合策略。'
          : `结论校验发现 ${payload.error_count} 个问题并执行降级：${errors}`;
      } else if (stage === 'PERSIST_RESULT') {
        const refs = Array.isArray(payload.evidence_refs) ? payload.evidence_refs.join('、') : '';
        detail = `结果已保存。${refs ? `关键证据：${refs}` : ''}`;
      } else if (stage === 'RECOMMEND_ACTION') {
        detail = `已生成 ${payload.action_count ?? 0} 条处置建议；所有动作保持人工确认级别，Agent 未自动执行。`;
      } else if (stage === 'WORKFLOW') {
        detail = `流程状态：${payload.status ?? 'DONE'}；结论：${payload.verdict ?? '待查看详情'}。`;
      } else {
        detail = String(payload.detail ?? payload.reason ?? '当前阶段已完成。');
      }
    } else if (type.includes('FAILED') || type.includes('ERROR')) {
      status = 'blocked';
      kind = 'guard';
      title = `${triageStageLabels[stage] ?? title}失败`;
      detail = String(payload.error ?? payload.message ?? payload.reason ?? '阶段执行失败。');
    } else if (type === 'AT_QUERY_RAG_SKIPPED') {
      title = '跳过辅助知识查询';
      detail = '按请求不启用 RAG，继续只依据 XDR 原始证据判断。';
    } else if (type === 'AT_QUERY_RAG_DEGRADED') {
      title = '辅助知识不可用';
      detail = `RAG 已降级：${payload.reason ?? '未返回知识引用'}。不会阻断 XDR 原始证据研判。`;
      status = 'blocked';
    }
  }

  return {
    id,
    kind,
    title,
    detail: detail.slice(0, 900),
    status,
    timestamp: event.timestamp ?? new Date().toISOString(),
  };
}

function parsedTime(value?: string | null): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function activityDurationMs(activity: ApiAgentActivity, now: number): number | null {
  if (typeof activity.durationMs === 'number' && Number.isFinite(activity.durationMs) && activity.durationMs >= 0) {
    return activity.durationMs;
  }
  const started = parsedTime(activity.startedAt ?? activity.timestamp);
  if (started === null) return null;
  const finished = parsedTime(activity.finishedAt);
  if (finished !== null) return Math.max(0, finished - started);
  if (activity.status === 'running') return Math.max(0, now - started);
  return null;
}

const reasoningLabels: Record<string, string> = {
  TASK_UNDERSTANDING: '任务理解',
  TASK_PLANNING: '任务规划',
  RAG_RETRIEVAL: 'RAG 检索',
  TOOL_CALL: '工具调用',
  RESULT_OBSERVATION: '结果观察',
  EVIDENCE_JUDGMENT: '证据判断',
  REPLANNING: '重新规划',
  FINAL_CONCLUSION: '最终结论',
};

function reasoningActivity(step: ApiReasoningStep, index: number): ApiAgentActivity {
  const status = String(step.status ?? '').toUpperCase();
  const startedAt = step.startedAt ?? new Date().toISOString();
  return {
    id: step.stepId || `reasoning-${index}`,
    kind: step.stepType === 'TOOL_CALL' ? 'tool' : step.stepType === 'EVIDENCE_JUDGMENT' ? 'guard' : 'analysis',
    title: reasoningLabels[step.stepType] ?? step.stepType.replaceAll('_', ' '),
    // Payload can contain tool parameters. The UI intentionally displays only
    // the redacted summary produced by Orchestrator.
    detail: String(step.summary ?? '').slice(0, 500),
    status: status === 'RUNNING' ? 'running' : status === 'FAILED' ? 'blocked' : 'done',
    timestamp: startedAt,
    startedAt,
    finishedAt: step.finishedAt,
    durationMs: step.durationMs,
    correlationId: typeof step.payload?.request_id === 'string' ? step.payload.request_id : null,
  };
}

function mergeReasoningStep(
  items: ApiAgentActivity[],
  step: ApiReasoningStep,
  index: number,
): ApiAgentActivity[] {
  const activity = reasoningActivity(step, index);
  let merged = items;
  if (step.stepType === 'RESULT_OBSERVATION' && activity.correlationId) {
    merged = merged.map((item) => (
      item.kind === 'tool' && item.status === 'running' && item.correlationId === activity.correlationId
        ? {
            ...item,
            status: activity.status === 'blocked' ? 'blocked' : 'done',
            finishedAt: activity.finishedAt ?? activity.startedAt,
            durationMs: activity.durationMs,
          }
        : item
    ));
  }
  const existing = merged.findIndex((item) => item.id === activity.id);
  return existing < 0
    ? [...merged, activity]
    : merged.map((item, itemIndex) => itemIndex === existing ? activity : item);
}

function reasoningStepActivities(steps: ApiReasoningStep[]): ApiAgentActivity[] {
  return steps.reduce<ApiAgentActivity[]>(
    (activities, step, index) => mergeReasoningStep(activities, step, index),
    [],
  );
}

function ActivityList({ activities, label = '实时执行轨迹' }: { activities: ApiAgentActivity[]; label?: string }) {
  const [expanded, setExpanded] = useState(true);
  const [page, setPage] = useState(1);
  const pageSize = 10;
  const totalPages = Math.max(1, Math.ceil(activities.length / pageSize));
  const visibleActivities = activities.slice((page - 1) * pageSize, page * pageSize);
  useEffect(() => setPage((current) => Math.min(current, totalPages)), [totalPages]);
  return (
    <div className="task-agent-activity-list">
      <button type="button" onClick={() => setExpanded((value) => !value)} className="task-agent-activity-header">
        <span><SquareTerminal size={14} /> {label} <b>{activities.length}</b></span>
        <ChevronDown size={14} style={{ transform: expanded ? 'rotate(180deg)' : undefined, transition: 'transform .18s' }} />
      </button>
      {expanded && (
        <div className="task-agent-activity-body">
          {visibleActivities.map((activity) => (
            <div key={activity.id} className="task-agent-activity-item">
              <span className={`task-agent-activity-state ${activity.status}`}>
                {activity.status === 'running' ? <Loader2 size={13} className="tg-spin" /> : activity.status === 'blocked' ? <Circle size={10} fill="currentColor" /> : <Check size={13} />}
              </span>
              <span>
                <strong>{activity.title}</strong>
                {activity.detail && <small>{activity.detail}</small>}
              </span>
            </div>
          ))}
          {totalPages > 1 && (
            <div className="task-agent-activity-pagination">
              <button type="button" aria-label="上一页" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={page === 1}><ChevronLeft size={13} /></button>
              <span>{page} / {totalPages}</span>
              <button type="button" aria-label="下一页" onClick={() => setPage((current) => Math.min(totalPages, current + 1))} disabled={page === totalPages}><ChevronRight size={13} /></button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DraftCard({ draft, onConfirm, busy }: { draft: ApiPentestDraft | ApiAlertTriageDraft; onConfirm: () => void; busy: boolean }) {
  if (draft.workflowId === 'alert_triage') {
    return (
      <div className="task-agent-draft">
        <div className="task-agent-draft-title"><ShieldCheck size={15} /> 告警研判草稿</div>
        <div className="task-agent-draft-grid">
          <span>告警 UUID</span><strong>{draft.alertUuid}</strong>
          <span>场景</span><strong>{draft.scenarioId || '未指定'}</strong>
          <span>知识辅助</span><strong>{draft.enableRag ? '已启用（仅辅助）' : '未启用'}</strong>
        </div>
        <button type="button" onClick={onConfirm} disabled={busy}>
          {busy ? '正在创建并启动…' : '确认并启动告警研判'}
        </button>
      </div>
    );
  }
  return (
    <div className="task-agent-draft">
        <div className="task-agent-draft-title"><ShieldCheck size={15} /> 渗透测试草稿</div>
      <div className="task-agent-draft-grid">
        <span>名称</span><strong>{draft.name}</strong>
        <span>目标</span><strong>{draft.target}</strong>
        <span>策略</span><strong>{draft.testProfile} · {draft.allowExploit ? '允许利用验证' : '默认不主动利用'}</strong>
        <span>时长</span><strong>{Math.round(draft.maxDurationSeconds / 60)} 分钟</strong>
      </div>
      <button type="button" onClick={onConfirm} disabled={busy}>
        {busy ? '正在创建并启动…' : '确认并启动渗透测试'}
      </button>
    </div>
  );
}

export default function TaskAgentPage() {
  const { loggedIn } = useAppSession();
  const navigate = useNavigate();
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME_MESSAGE]);
  const [input, setInput] = useState('');
  const [conversationId, setConversationId] = useState<string>();
  const [task, setTask] = useState<ApiTask | null>(null);
  const [busy, setBusy] = useState(false);
  const [draftBusy, setDraftBusy] = useState(false);
  const [liveActivities, setLiveActivities] = useState<ApiAgentActivity[]>([]);
  const [reasoningActivities, setReasoningActivities] = useState<ApiAgentActivity[]>([]);
  const [conversations, setConversations] = useState<ApiTaskAgentConversationSummary[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(false);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [conversationError, setConversationError] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const [isNearBottom, setIsNearBottom] = useState(true);
  const draftAbortRef = useRef<AbortController | null>(null);
  const conversationLoadRef = useRef(0);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [conversationSearch, setConversationSearch] = useState('');
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.classList.add('task-agent-no-page-scroll');
    document.body.classList.add('no-page-scroll');
    return () => {
      document.documentElement.classList.remove('task-agent-no-page-scroll');
      document.body.classList.remove('no-page-scroll');
    };
  }, []);

  useEffect(() => {
    if (!searchOpen) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSearchOpen(false);
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [searchOpen]);

  const appendMessage = useCallback((message: ChatMessage) => setMessages((items) => (
    items.some((item) => item.id === message.id) ? items : [...items, message]
  )), []);

  const refreshConversations = useCallback(async (): Promise<ApiTaskAgentConversationSummary[]> => {
    setConversationsLoading(true);
    try {
      const items = await listTaskAgentConversations(50);
      setConversations(items);
      setConversationError('');
      return items;
    } catch (error) {
      setConversationError(`会话列表加载失败：${error instanceof Error ? error.message : String(error)}`);
      return [];
    } finally {
      setConversationsLoading(false);
    }
  }, []);

  const loadConversation = useCallback(async (nextConversationId: string) => {
    const requestId = ++conversationLoadRef.current;
    draftAbortRef.current?.abort();
    setBusy(false);
    setDraftBusy(false);
    setConversationLoading(true);
    setConversationError('');
    setConversationId(nextConversationId);
    setTask(null);
    setLiveActivities([]);
    setReasoningActivities([]);
    setMessages([WELCOME_MESSAGE]);
    try {
      const conversation = await getTaskAgentConversation(nextConversationId);
      if (requestId !== conversationLoadRef.current) return;
      window.localStorage.setItem(CONVERSATION_STORAGE_KEY, conversation.conversationId);
      setConversationId(conversation.conversationId);
      setMessages([
        WELCOME_MESSAGE,
        ...conversation.messages.map((message) => toChatMessage(message, conversation.taskId)),
      ]);
      if (conversation.taskId) {
        try {
          const restoredTask = await getTask(conversation.taskId);
          if (requestId !== conversationLoadRef.current) return;
          setTask(restoredTask);
          if (terminalStatuses.has(restoredTask.status)) {
            setMessages((items) => settleMessages(items));
          }
        } catch { /* conversation history remains useful when a task was removed */ }
      }
    } catch (error) {
      if (requestId !== conversationLoadRef.current) return;
      setMessages([WELCOME_MESSAGE]);
      setConversationError(`会话加载失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      if (requestId === conversationLoadRef.current) setConversationLoading(false);
    }
  }, []);

  const startNewConversation = useCallback(() => {
    conversationLoadRef.current += 1;
    draftAbortRef.current?.abort();
    draftAbortRef.current = null;
    window.localStorage.removeItem(CONVERSATION_STORAGE_KEY);
    setConversationId(undefined);
    setTask(null);
    setMessages([WELCOME_MESSAGE]);
    setLiveActivities([]);
    setReasoningActivities([]);
    setInput('');
    setBusy(false);
    setDraftBusy(false);
    setConversationLoading(false);
    setConversationError('');
  }, []);

  // Auto-scroll: follow content when user is near bottom
  useEffect(() => {
    if (isNearBottom) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'instant' });
    }
  }, [messages, liveActivities, reasoningActivities, isNearBottom]);

  // Detect user scroll to determine if near bottom
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const threshold = 100;
      const near = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
      setIsNearBottom(near);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  const scrollToBottom = useCallback(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
    setIsNearBottom(true);
  }, []);

  useEffect(() => () => draftAbortRef.current?.abort(), []);

  useEffect(() => {
    const textarea = composerRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 130)}px`;
  }, [input]);

  useEffect(() => {
    if (!loggedIn) return;
    let active = true;
    void refreshConversations().then((items) => {
      if (!active || items.length === 0) return;
      const savedConversationId = window.localStorage.getItem(CONVERSATION_STORAGE_KEY);
      const initialId = items.some((item) => item.conversationId === savedConversationId)
        ? savedConversationId
        : items[0].conversationId;
      if (initialId) void loadConversation(initialId);
    });
    return () => {
      active = false;
      conversationLoadRef.current += 1;
    };
  }, [loadConversation, loggedIn, refreshConversations]);

  useEffect(() => {
    if (!task?.taskId) return;
    let active = true;
    let interval: number | undefined;
    const controller = new AbortController();
    const mergeEvent = (event: ApiEvent) => setLiveActivities((items) => {
      const activity = eventActivity(event, items.length);
      const existing = items.findIndex((item) => item.id === activity.id);
      return existing < 0
        ? [...items, activity]
        : items.map((item, index) => index === existing ? activity : item);
    });
    const mergeTriageEvent = (event: ApiEvent) => {
      if (task.workflowId !== 'alert_triage') return;
      setReasoningActivities((items) => mergeActivity(items, triageEventActivity(event, items.length)));
    };
    const mergeReasoning = (step: ApiReasoningStep) => setReasoningActivities((items) => {
      return mergeReasoningStep(items, step, items.length);
    });
    const pollFallback = async () => {
      try {
        const [latest, events, reasoningSteps] = await Promise.all([
          getTask(task.taskId),
          getTaskEvents(task.taskId, 500),
          getTaskReasoningSteps(task.taskId, 500).catch(() => []),
        ]);
        if (!active) return;
        setTask({ ...latest, workflowId: latest.workflowId || task.workflowId });
        setLiveActivities(events.map(eventActivity));
        setReasoningActivities(task.workflowId === 'alert_triage'
          ? events.reduce<ApiAgentActivity[]>((items, event, index) => mergeActivity(items, triageEventActivity(event, index)), [])
          : reasoningStepActivities(reasoningSteps));
        if (terminalStatuses.has(latest.status)) {
          setMessages((items) => settleMessages(items));
          setLiveActivities((items) => settleActivities(items) ?? []);
          setReasoningActivities((items) => settleActivities(items) ?? []);
          appendMessage({
            id: `task-${task.taskId}-terminal`,
            role: 'assistant',
            text: latest.status === 'DONE'
              ? `${task.workflowId === 'alert_triage' ? '告警研判' : '渗透测试'}任务 ${task.taskId} 已完成。`
              : latest.status === 'FAILED'
                ? `${task.workflowId === 'alert_triage' ? '告警研判' : '渗透测试'}任务 ${task.taskId} 执行失败。你可以查看执行轨迹定位失败步骤。`
                : `${task.workflowId === 'alert_triage' ? '告警研判' : '渗透测试'}任务 ${task.taskId} 已取消。`,
          });
          void refreshConversations();
          if (interval !== undefined) window.clearInterval(interval);
        }
      } catch { /* keep the conversation usable during a transient API failure */ }
    };
    void streamTaskEvents(
      task.taskId,
      {
        onEvent: (event) => {
          mergeEvent(event);
          mergeTriageEvent(event);
        },
        onReasoning: mergeReasoning,
        onStatus: (latest) => {
          if (!active) return;
          setTask({ ...latest, workflowId: latest.workflowId || task.workflowId });
          if (terminalStatuses.has(latest.status)) {
            setMessages((items) => settleMessages(items));
            setLiveActivities((items) => settleActivities(items) ?? []);
            setReasoningActivities((items) => settleActivities(items) ?? []);
          }
        },
        onAssistant: (message) => {
          if (active) {
            appendMessage(toChatMessage(message, message.taskId));
            void refreshConversations();
          }
        },
        onDone: (result) => {
          if (!active) return;
          setMessages((items) => {
            const settled = settleMessages(items);
            return settled.some((item) => item.id === result.message.id)
              ? settled
              : [...settled, toChatMessage(result.message, result.taskId)];
          });
          setLiveActivities((items) => settleActivities(items) ?? []);
          setReasoningActivities((items) => settleActivities(items) ?? []);
          void refreshConversations();
        },
      },
      controller.signal,
      conversationId,
    ).catch(() => {
      if (!active || controller.signal.aborted) return;
      void pollFallback();
      interval = window.setInterval(pollFallback, 3500);
    });
    return () => {
      active = false;
      controller.abort();
      if (interval !== undefined) window.clearInterval(interval);
    };
  }, [appendMessage, conversationId, refreshConversations, task?.taskId]);

  const submit = async () => {
    const message = input.trim();
    if (!message || busy) return;
    const requestId = Date.now().toString(36);
    const assistantId = `a-${requestId}`;
    const controller = new AbortController();
    draftAbortRef.current = controller;
    setInput('');
    setMessages((items) => [
      ...items,
      { id: `u-${requestId}`, role: 'user', text: message },
      { id: assistantId, role: 'assistant', text: '', activities: [], streaming: true, thinking: { startedAt: Date.now() } },
    ]);
    setBusy(true);
    try {
      await streamTaskAgentDraft(
        message,
        conversationId,
        {
          onActivity: (activity) => setMessages((items) => items.map((item) =>
            item.id === assistantId
              ? { ...item, activities: mergeActivity(item.activities, activity) }
              : item,
          )),
          onDelta: (delta) => setMessages((items) => items.map((item) =>
            item.id === assistantId ? { ...item, text: item.text + delta } : item,
          )),
          onResult: (response) => {
            setConversationId(response.conversationId);
            window.localStorage.setItem(CONVERSATION_STORAGE_KEY, response.conversationId);
            setMessages((items) => items.map((item) => item.id === assistantId ? {
              ...item,
              text: item.text || response.assistantMessage,
              activities: response.activities,
              draft: response.draft,
              confirmationToken: response.confirmationToken,
            } : item));
            void refreshConversations();
          },
        },
        controller.signal,
      );
    } catch (error) {
      const stopped = controller.signal.aborted;
      const detail = stopped
        ? '用户已停止生成'
        : `回复生成失败：${error instanceof Error ? error.message : String(error)}`;
      setMessages((items) => items.map((item) => item.id === assistantId ? {
        ...item,
        text: stopped
          ? `${item.text}${item.text ? '\n\n' : ''}已停止生成。`
          : `${item.text}${item.text ? '\n\n' : ''}请求失败：${error instanceof Error ? error.message : String(error)}`,
        activities: blockDraftResponse(item.activities, detail),
      } : item));
    } finally {
      setMessages((items) => items.map((item) => item.id === assistantId ? { ...item, streaming: false } : item));
      window.setTimeout(() => {
        setMessages((items) => items.map((item) => item.id === assistantId ? { ...item, thinking: null } : item));
      }, ACTIVITY_SETTLE_DELAY_MS);
      if (draftAbortRef.current === controller) draftAbortRef.current = null;
      setBusy(false);
    }
  };

  const composerHint = conversationLoading
    ? '正在恢复会话…'
    : busy
      ? '正在生成，点击停止按钮可中断'
      : 'Enter 发送 · Shift + Enter 换行';

  const confirm = async (message: ChatMessage) => {
    if (!message.confirmationToken || draftBusy) return;
    setDraftBusy(true);
    try {
      const response = await confirmTaskAgentDraft(message.confirmationToken, { idempotencyKey: `ui-${message.id}` });
      setLiveActivities([]);
      setReasoningActivities([]);
      setTask(response.task);
      if (response.conversationId) {
        setConversationId(response.conversationId);
        window.localStorage.setItem(CONVERSATION_STORAGE_KEY, response.conversationId);
      }
      setMessages((items) => {
        const updated = items.map((item) => item.id === message.id ? { ...item, confirmationToken: null } : item);
        return updated.some((item) => item.id === response.message.id)
          ? updated
          : [...updated, toChatMessage(response.message, response.task.taskId)];
      });
      void refreshConversations();
      syncTaskToLocalStorage(response.task);
    } catch (error) {
      setMessages((items) => [...items, { id: `e-${Date.now()}`, role: 'assistant', text: `确认失败：${error instanceof Error ? error.message : String(error)}` }]);
    } finally { setDraftBusy(false); }
  };

  function syncTaskToLocalStorage(apiTask: ApiTask) {
    try {
      const existing = readStoredOrbitTasks();
      const existingIds = new Set(existing.map((t) => t.id));
      if (!existingIds.has(apiTask.taskId)) {
        const entry: StoredOrbitTask = {
          id: apiTask.taskId,
          name: apiTask.name ?? '未命名任务',
          desc: apiTask.description ?? '',
          url: apiTask.target ?? '',
          log: '',
          createdAt: new Date(apiTask.createdAt).getTime() || Date.now(),
          updatedAt: apiTask.updatedAt ? new Date(apiTask.updatedAt).getTime() || undefined : undefined,
          status: toFrontendStatus(apiTask.status),
          currentPhase: apiTask.currentPhase,
        };
        existing.push(entry);
        localStorage.setItem(SENTINEL_ORBIT_TASKS_KEY, JSON.stringify(existing));
        window.dispatchEvent(new Event(ORBIT_TASKS_UPDATED_EVENT));
      }
    } catch { /* quota */ }
  }

  const isAlertTriage = task?.workflowId === 'alert_triage' || task?.taskId.startsWith('at-') === true;
  const workflowPhases = isAlertTriage ? alertTriagePhases : pentestPhases;
  const phaseIndex = useMemo(() => {
    if (task?.status === 'DONE') return workflowPhases.length;
    if (!isAlertTriage) {
      return Math.max(0, pentestPhases.findIndex((phase) => phase.id === (task?.currentPhase ?? 'RECON')));
    }

    let latestIndex = -1;
    let latestStatus: ApiAgentActivity['status'] | undefined;
    workflowPhases.forEach((phase, index) => {
      const activity = reasoningActivities.find((item) => item.id === `triage-stage-${phase.id}`);
      if (activity) {
        latestIndex = index;
        latestStatus = activity.status;
      }
    });
    if (latestIndex < 0) return 0;
    if (task?.status === 'RUNNING' && latestStatus !== 'running') {
      return Math.min(latestIndex + 1, workflowPhases.length);
    }
    return latestIndex;
  }, [isAlertTriage, reasoningActivities, task?.currentPhase, task?.status, workflowPhases]);
  const traceActivities = useMemo(() => {
    if (liveActivities.length > 0) return liveActivities;
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const activities = messages[index].activities;
      if (activities && activities.length > 0) return activities;
    }
    return [];
  }, [liveActivities, messages]);
  const reasoningAnchorMessageId = useMemo(() => {
    if (reasoningActivities.length === 0) return undefined;
    const taskMessage = task?.taskId
      ? messages.find((message) => message.taskId === task.taskId)
      : undefined;
    return taskMessage?.id;
  }, [messages, reasoningActivities.length, task?.taskId]);

  const groupConversations = (list: ApiTaskAgentConversationSummary[]) => {
    const now = Date.now();
    const day = 86400000;
    const today: ApiTaskAgentConversationSummary[] = [];
    const yesterday: ApiTaskAgentConversationSummary[] = [];
    const thisWeek: ApiTaskAgentConversationSummary[] = [];
    const older: ApiTaskAgentConversationSummary[] = [];
    for (const c of list) {
      const diff = now - new Date(c.updatedAt).getTime();
      if (diff < day) today.push(c);
      else if (diff < day * 2) yesterday.push(c);
      else if (diff < day * 7) thisWeek.push(c);
      else older.push(c);
    }
    return { today, yesterday, thisWeek, older };
  };

  const pinnedConversations = conversations.filter((conversation) => conversation.pinned);
  const groupedConversations = groupConversations(conversations.filter((conversation) => !conversation.pinned));
  const searchResults = conversationSearch.trim()
    ? conversations.filter((conversation) => conversation.title.toLowerCase().includes(conversationSearch.trim().toLowerCase()))
    : conversations;
  const groupLabels: Record<string, string> = { today: '今天', yesterday: '昨天', thisWeek: '最近 7 天', older: '更早' };

  const handlePinConversation = async (conversation: ApiTaskAgentConversationSummary) => {
    setOpenMenuId(null);
    try {
      await updateTaskAgentConversation(conversation.conversationId, { pinned: !conversation.pinned });
      await refreshConversations();
    } catch (error) {
      setConversationError(`会话${conversation.pinned ? '取消置顶' : '置顶'}失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const handleRenameConversation = async (conversation: ApiTaskAgentConversationSummary) => {
    const title = renameValue.trim();
    if (!title) {
      setRenamingId(null);
      return;
    }
    try {
      await updateTaskAgentConversation(conversation.conversationId, { title });
      setRenamingId(null);
      await refreshConversations();
    } catch (error) {
      setRenamingId(null);
      setConversationError(`会话重命名失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const handleDeleteConversation = async (conversation: ApiTaskAgentConversationSummary) => {
    setOpenMenuId(null);
    try {
      await deleteTaskAgentConversation(conversation.conversationId);
      if (conversation.conversationId === conversationId) startNewConversation();
      await refreshConversations();
    } catch (error) {
      setConversationError(`会话删除失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const renderConversation = (conversation: ApiTaskAgentConversationSummary) => {
    const statusText = conversationStatus(conversation.taskStatus);
    const isRenaming = renamingId === conversation.conversationId;
    const openConversation = () => {
      if (conversation.conversationId !== conversationId) void loadConversation(conversation.conversationId);
    };
    return (
      <div
        key={conversation.conversationId}
        className={`task-agent-conversation-item${conversation.conversationId === conversationId ? ' active' : ''}`}
        onClick={openConversation}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return;
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            openConversation();
          }
        }}
        role="button"
        tabIndex={0}
        aria-label={conversation.title}
        title={conversation.title}
      >
        {isRenaming ? (
          <input
            type="text"
            value={renameValue}
            onChange={(event) => setRenameValue(event.target.value)}
            onKeyDown={async (event) => {
              event.stopPropagation();
              if (event.key === 'Enter') {
                await handleRenameConversation(conversation);
              }
              if (event.key === 'Escape') setRenamingId(null);
            }}
            onBlur={() => setRenamingId(null)}
            autoFocus
            onClick={(event) => event.stopPropagation()}
            className="task-agent-rename-input"
          />
        ) : (
          <>
            <span className="task-agent-conversation-title">{conversation.title}</span>
            <span className="task-agent-conversation-preview">{conversation.preview || '暂无回复'}</span>
            <span className="task-agent-conversation-meta">
              <span>{conversationTime(conversation.updatedAt)}</span>
              {statusText && <span className={`status-${String(conversation.taskStatus).toLowerCase()}`}>{statusText}</span>}
            </span>
          </>
        )}
        <div className="task-agent-conversation-item-actions" onClick={(event) => event.stopPropagation()}>
          <button
            type="button"
            className="task-agent-conversation-item-menu"
            aria-label={`管理 ${conversation.title}`}
            onClick={() => setOpenMenuId(openMenuId === conversation.conversationId ? null : conversation.conversationId)}
          >···</button>
          {openMenuId === conversation.conversationId && (
            <div className="task-agent-conversation-item-dropdown">
              <button
                type="button"
                className="task-agent-dropdown-item"
                onClick={() => {
                  setRenamingId(conversation.conversationId);
                  setRenameValue(conversation.title);
                  setOpenMenuId(null);
                }}
              ><Pencil size={13} /> <span>重命名</span></button>
              <button
                type="button"
                className="task-agent-dropdown-item"
                onClick={() => void handlePinConversation(conversation)}
              >{conversation.pinned ? <PinOff size={13} /> : <Pin size={13} />} <span>{conversation.pinned ? '取消置顶' : '置顶'}</span></button>
              <button
                type="button"
                className="task-agent-dropdown-item danger"
                onClick={() => void handleDeleteConversation(conversation)}
              ><Trash2 size={13} /> <span>删除</span></button>
            </div>
          )}
        </div>
      </div>
    );
  };

  if (!loggedIn) {
    return <><Header /><main style={{ paddingTop: 110, maxWidth: 760, margin: '0 auto', paddingInline: 24 }}><h2>请先登录可信卫士</h2><button type="button" onClick={() => navigate('/login')}>前往登录</button></main></>;
  }

  return (
    <div className="task-agent-page">
      <Header />
      <main className={`task-agent-layout${sidebarCollapsed ? ' sidebar-collapsed' : ''}`}>
        <nav className={`task-agent-conversations${sidebarCollapsed ? ' collapsed' : ''}`} aria-label="可信卫士会话列表">
          {sidebarCollapsed ? (
            <div className="task-agent-conversation-rail">
              <button type="button" className="task-agent-sidebar-icon-button" onClick={() => setSidebarCollapsed(false)} aria-label="展开会话栏" title="展开会话栏">
                <PanelLeftOpen size={17} />
              </button>
              <button type="button" className="task-agent-sidebar-icon-button" onClick={startNewConversation} aria-label="新建会话" title="新建会话">
                <Plus size={17} />
              </button>
              <button type="button" className="task-agent-sidebar-icon-button" onClick={() => setSearchOpen(true)} aria-label="搜索会话" title="搜索会话">
                <Search size={17} />
              </button>
            </div>
          ) : (
            <>
              <div className="task-agent-conversations-header">
                <span className="task-agent-product-name"><ShieldCheck size={17} /> 可信卫士</span>
                <span className="task-agent-conversations-header-actions">
                  <button type="button" onClick={() => setSearchOpen(true)} aria-label="搜索会话" title="搜索会话">
                    <Search size={14} />
                  </button>
                  <button type="button" onClick={() => setSidebarCollapsed(true)} aria-label="收起会话栏" title="收起会话栏">
                    <PanelLeftClose size={14} />
                  </button>
                </span>
              </div>
              <button type="button" className={`task-agent-new-conversation${conversationId ? '' : ' active'}`} onClick={startNewConversation}>
                <Plus size={14} /> 新建会话
              </button>
              {conversationError && <div className="task-agent-conversation-error">{conversationError}</div>}
              <div className="task-agent-conversation-list">
                {!conversationsLoading && conversations.length === 0 && (
                  <div className="task-agent-conversation-empty">还没有历史会话。发送第一条消息后，会话会保存在这里。</div>
                )}
                {conversationsLoading && conversations.length === 0 && <div className="task-agent-conversation-empty">加载中...</div>}
                {pinnedConversations.length > 0 && (
                  <div className="task-agent-conversation-group">
                    <div className="task-agent-conversation-group-label">置顶</div>
                    {pinnedConversations.map(renderConversation)}
                  </div>
                )}
                {Object.entries(groupedConversations).map(([key, items]) => items.length > 0 && (
                  <div className="task-agent-conversation-group" key={key}>
                    <div className="task-agent-conversation-group-label">{groupLabels[key] ?? key}</div>
                    {items.map(renderConversation)}
                  </div>
                ))}
              </div>
            </>
          )}
        </nav>
        <section className="task-agent-chat" aria-label="对话">
          <div className="task-agent-chat-scroll" ref={scrollRef}>
            {conversationLoading && <div className="task-agent-conversation-loading"><Loader2 size={14} className="tg-spin" /> 正在恢复会话…</div>}
            <div className="task-agent-message-column">
              {messages.map((message) => (
                <Fragment key={message.id}>
                  <div className={`task-agent-message ${message.role}`}>
                    {message.role === 'assistant' && <span className="task-agent-message-mark" aria-hidden="true"><ShieldCheck size={15} /></span>}
                    <div className="task-agent-message-bubble">
                      {message.role === 'assistant' ? (
                        <>
                          {message.thinking && !(message.activities?.length) && <ThinkingBlock
                            hasContent={!!message.text}
                            streaming={!!message.streaming}
                          />}
                          {message.text && <MarkdownBlock content={message.text} />}
                          {message.activities && message.activities.length > 0 && <ToolCallBlock
                            activities={message.activities}
                            live={Boolean(message.thinking)}
                          />}
                        </>
                      ) : (
                        message.text
                      )}
                      {message.streaming && <span className="tg-stream-cursor" aria-hidden="true" />}
                      {message.draft && message.confirmationToken && <DraftCard draft={message.draft} onConfirm={() => void confirm(message)} busy={draftBusy || Boolean(message.streaming)} />}
                    </div>
                  </div>
                  {message.id === reasoningAnchorMessageId && <ToolCallBlock activities={reasoningActivities} />}
                </Fragment>
              ))}
              {reasoningActivities.length > 0 && !reasoningAnchorMessageId && <ToolCallBlock activities={reasoningActivities} />}
            </div>
          </div>
          {!isNearBottom && (
            <button type="button" className="tg-scroll-bottom" onClick={scrollToBottom} aria-label="滚动到底部">↓</button>
          )}
          <div className="task-agent-composer-wrap">
            <div className="task-agent-composer">
              <textarea ref={composerRef} value={input} disabled={conversationLoading} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder="描述测试目标，或输入告警 UUID 进行研判" rows={2} />
              <button type="button" onClick={() => busy ? draftAbortRef.current?.abort() : void submit()} disabled={conversationLoading || (!busy && !input.trim())} aria-label={busy ? '停止生成' : '发送'}>{busy ? <Square size={13} fill="currentColor" /> : <Send size={16} />}</button>
            </div>
            <span className="task-agent-composer-hint" role="status" aria-live="polite">{composerHint}</span>
          </div>
        </section>
        <aside className="task-agent-monitor" aria-label="任务状态与执行轨迹">
          <section className="task-agent-monitor-status">
            <div className="task-agent-monitor-heading">
              <span>任务状态</span>
              {task && <b className={`status-${task.status.toLowerCase()}`}>{conversationStatus(task.status)}</b>}
            </div>
            {task ? (
              <>
                <div className="task-agent-id-label">任务 ID</div>
                <div className="task-agent-id">{task.taskId}</div>
                <div className="task-agent-phases">
                  {workflowPhases.map((phase, index) => (
                    <div key={phase.id} className={index <= phaseIndex ? 'active' : ''}>
                      <span>{index < phaseIndex ? <Check size={11} /> : index === phaseIndex && task.status === 'RUNNING' ? <Loader2 size={11} className="tg-spin" /> : <Circle size={7} />}</span>
                      {phase.label}
                    </div>
                  ))}
                </div>
                {(task.status === 'DONE' || task.status === 'FAILED') && <button type="button" className="task-agent-report-button" onClick={() => navigate(task.workflowId === 'alert_triage' ? `/triage/${task.taskId}` : `/reports?taskId=${task.taskId}`)}>{task.workflowId === 'alert_triage' ? '打开研判详情' : '打开报告中心'}</button>}
              </>
            ) : <p className="task-agent-monitor-empty">确认任务草稿后，这里会显示执行阶段和状态。</p>}
          </section>
          <section className="task-agent-monitor-trace">
            {traceActivities.length > 0 && (
              <ActivityList activities={traceActivities} label="实时执行轨迹" />
            )}
            {traceActivities.length === 0 && (
              <><div className="task-agent-activity-empty-heading"><SquareTerminal size={14} /> 执行轨迹</div><p>任务开始后，技能调用与状态事件会显示在这里；结构化推理步骤会出现在中间对话中。</p></>
            )}
          </section>
        </aside>
      </main>
      {searchOpen && (
        <div className="task-agent-search-overlay" onClick={() => setSearchOpen(false)}>
          <section
            className="task-agent-search-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="task-agent-search-title"
            onClick={(event) => event.stopPropagation()}
          >
            <h2 id="task-agent-search-title">搜索会话</h2>
            <div className="task-agent-search-field">
              <Search size={16} aria-hidden="true" />
              <input
                type="text"
                value={conversationSearch}
                onChange={(event) => setConversationSearch(event.target.value)}
                aria-label="搜索会话"
                placeholder="按标题搜索"
                autoFocus
              />
            </div>
            <div className="task-agent-search-results">
              {searchResults.length === 0 && <p className="task-agent-search-empty">没有匹配的会话</p>}
              {searchResults.map((conversation) => (
                <button
                  type="button"
                  key={conversation.conversationId}
                  className={conversation.conversationId === conversationId ? 'current' : undefined}
                  onClick={() => {
                    void loadConversation(conversation.conversationId);
                    setSearchOpen(false);
                    setConversationSearch('');
                  }}
                >
                  <span>{conversation.title}</span>
                  <small>{conversation.preview || '暂无回复'}</small>
                </button>
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
