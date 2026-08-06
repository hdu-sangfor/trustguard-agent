import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, ChevronDown, ChevronLeft, ChevronRight, Circle, Clock, Loader2, Plus, RefreshCw, Send, ShieldCheck, Square, SquareTerminal } from 'lucide-react';
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
  type ApiAgentActivity,
  type ApiAlertTriageDraft,
  type ApiConversationMessage,
  type ApiEvent,
  type ApiPentestDraft,
  type ApiReasoningStep,
  type ApiTask,
  type ApiTaskAgentConversationSummary,
} from '@/shared/lib/api';

const CONVERSATION_STORAGE_KEY = 'trustguard.agent.conversationId';
const WELCOME_MESSAGE: ChatMessage = {
  id: 'welcome',
  role: 'assistant',
  text: '告诉我你想测试什么目标、测试范围和限制条件。我会先整理任务草稿，确认后再启动现有 Pentest Workflow。',
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

const phases = ['RECON', 'THREAT_MODEL', 'VULN_SCAN', 'EXPLOIT', 'REPORT'];
const terminalStatuses = new Set<ApiTask['status']>(['DONE', 'FAILED', 'CANCELLED']);

function settleActivities(activities: ApiAgentActivity[] | undefined): ApiAgentActivity[] | undefined {
  const finishedAt = new Date().toISOString();
  const now = Date.now();
  return activities?.map((activity) => activity.status === 'running'
    ? {
        ...activity,
        status: 'done',
        finishedAt,
        durationMs: activityDurationMs(activity, now),
      }
    : activity);
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

function formatDuration(durationMs: number | null): string {
  if (durationMs === null) return '—';
  if (durationMs < 1000) return `${Math.max(0, Math.round(durationMs))} 毫秒`;
  const seconds = durationMs / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)} 秒`;
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return `${minutes} 分 ${String(remainingSeconds).padStart(2, '0')} 秒`;
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

function InlineReasoningTrace({ activities }: { activities: ApiAgentActivity[] }) {
  const [expanded, setExpanded] = useState(true);
  const [page, setPage] = useState(1);
  const hasRunning = activities.some((activity) => activity.status === 'running');
  const [now, setNow] = useState(() => Date.now());
  const pageSize = 8;
  const totalPages = Math.max(1, Math.ceil(activities.length / pageSize));
  const visibleActivities = activities.slice((page - 1) * pageSize, page * pageSize);

  useEffect(() => {
    if (!hasRunning) return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [hasRunning]);
  useEffect(() => setPage(totalPages), [activities.length, totalPages]);

  const startedTimes = activities
    .map((activity) => parsedTime(activity.startedAt ?? activity.timestamp))
    .filter((value): value is number => value !== null);
  const firstStartedAt = startedTimes.length > 0 ? Math.min(...startedTimes) : null;
  const finishedTimes = activities
    .map((activity) => parsedTime(activity.finishedAt))
    .filter((value): value is number => value !== null);
  const lastFinishedAt = finishedTimes.length > 0 ? Math.max(...finishedTimes) : null;
  const totalDuration = firstStartedAt === null
    ? null
    : Math.max(0, (hasRunning ? now : lastFinishedAt ?? now) - firstStartedAt);
  const runningActivity = [...activities].reverse().find((activity) => activity.status === 'running');

  return (
    <div className="task-agent-inline-reasoning">
      <button
        type="button"
        className="task-agent-inline-reasoning-header"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        <span className={`task-agent-inline-reasoning-icon${hasRunning ? ' running' : ''}`}>
          {hasRunning ? <Loader2 size={14} className="tg-spin" /> : <Check size={14} />}
        </span>
        <span className="task-agent-inline-reasoning-title">
          <strong>{hasRunning ? `正在执行：${runningActivity?.title ?? '结构化推理'}` : `已执行 ${activities.length} 个推理步骤`}</strong>
          <small><Clock size={11} /> {formatDuration(totalDuration)}</small>
        </span>
        <ChevronDown size={14} className={expanded ? 'expanded' : undefined} />
      </button>
      {expanded && (
        <div className="task-agent-inline-reasoning-body">
          {visibleActivities.map((activity) => (
            <div key={activity.id} className={`task-agent-inline-step ${activity.status}`}>
              <span className="task-agent-inline-step-state">
                {activity.status === 'running'
                  ? <Loader2 size={12} className="tg-spin" />
                  : activity.status === 'blocked'
                    ? <Circle size={9} fill="currentColor" />
                    : <Check size={12} />}
              </span>
              <span className="task-agent-inline-step-content">
                <span className="task-agent-inline-step-heading">
                  <strong>{activity.title}</strong>
                  <time>{formatDuration(activityDurationMs(activity, now))}</time>
                </span>
                {activity.detail && <small>{activity.detail}</small>}
              </span>
            </div>
          ))}
          {totalPages > 1 && (
            <div className="task-agent-activity-pagination task-agent-inline-reasoning-pagination">
              <button type="button" aria-label="上一页推理步骤" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={page === 1}><ChevronLeft size={13} /></button>
              <span>步骤 {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, activities.length)} / {activities.length}</span>
              <button type="button" aria-label="下一页推理步骤" onClick={() => setPage((current) => Math.min(totalPages, current + 1))} disabled={page === totalPages}><ChevronRight size={13} /></button>
            </div>
          )}
        </div>
      )}
    </div>
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
  const endRef = useRef<HTMLDivElement>(null);
  const draftAbortRef = useRef<AbortController | null>(null);
  const conversationLoadRef = useRef(0);

  useEffect(() => {
    document.documentElement.classList.add('task-agent-no-page-scroll');
    document.body.classList.add('no-page-scroll');
    return () => {
      document.documentElement.classList.remove('task-agent-no-page-scroll');
      document.body.classList.remove('no-page-scroll');
    };
  }, []);

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

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, liveActivities, reasoningActivities]);
  useEffect(() => () => draftAbortRef.current?.abort(), []);

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
        setReasoningActivities(reasoningStepActivities(reasoningSteps));
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
        onEvent: mergeEvent,
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
      { id: assistantId, role: 'assistant', text: '', activities: [], streaming: true },
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
      setMessages((items) => items.map((item) => item.id === assistantId ? {
        ...item,
        text: controller.signal.aborted
          ? `${item.text}${item.text ? '\n\n' : ''}已停止生成。`
          : `${item.text}${item.text ? '\n\n' : ''}请求失败：${error instanceof Error ? error.message : String(error)}`,
      } : item));
    } finally {
      setMessages((items) => items.map((item) => item.id === assistantId ? { ...item, streaming: false } : item));
      if (draftAbortRef.current === controller) draftAbortRef.current = null;
      setBusy(false);
    }
  };

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
    } catch (error) {
      setMessages((items) => [...items, { id: `e-${Date.now()}`, role: 'assistant', text: `确认失败：${error instanceof Error ? error.message : String(error)}` }]);
    } finally { setDraftBusy(false); }
  };

  const phaseIndex = useMemo(
    () => task?.status === 'DONE' ? phases.length : Math.max(0, phases.indexOf(task?.currentPhase ?? 'RECON')),
    [task?.currentPhase, task?.status],
  );
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
  const phaseLabels: Record<string, string> = {
    RECON: '信息收集',
    THREAT_MODEL: '威胁建模',
    VULN_SCAN: '漏洞扫描',
    EXPLOIT: '利用验证',
    REPORT: '生成报告',
  };
  if (!loggedIn) {
    return <><Header /><main style={{ paddingTop: 110, maxWidth: 760, margin: '0 auto', paddingInline: 24 }}><h2>请先登录可信卫士</h2><button type="button" onClick={() => navigate('/login')}>前往登录</button></main></>;
  }

  return (
    <div className="task-agent-page">
      <Header />
      <main className="task-agent-layout">
        <nav className="task-agent-conversations" aria-label="可信卫士会话列表">
          <div className="task-agent-conversations-header">
            <span className="task-agent-product-name"><ShieldCheck size={17} /> 可信卫士</span>
            <button type="button" onClick={() => void refreshConversations()} aria-label="刷新会话列表" title="刷新会话列表">
              <RefreshCw size={13} className={conversationsLoading ? 'tg-spin' : undefined} />
            </button>
          </div>
          <button type="button" className={`task-agent-new-conversation${conversationId ? '' : ' active'}`} onClick={startNewConversation}>
            <Plus size={14} /> 新建会话
          </button>
          {conversationError && <div className="task-agent-conversation-error">{conversationError}</div>}
          <div className="task-agent-conversation-list">
            {!conversationsLoading && conversations.length === 0 && (
              <div className="task-agent-conversation-empty">还没有历史会话。发送第一条消息后，会话会保存在这里。</div>
            )}
            {conversations.map((conversation) => {
              const statusText = conversationStatus(conversation.taskStatus);
              return (
                <button
                  type="button"
                  key={conversation.conversationId}
                  className={`task-agent-conversation-item${conversation.conversationId === conversationId ? ' active' : ''}`}
                  onClick={() => {
                    if (conversation.conversationId !== conversationId) void loadConversation(conversation.conversationId);
                  }}
                  title={conversation.title}
                >
                  <span className="task-agent-conversation-title">{conversation.title}</span>
                  <span className="task-agent-conversation-preview">{conversation.preview || '暂无回复'}</span>
                  <span className="task-agent-conversation-meta">
                    <span>{conversationTime(conversation.updatedAt)}</span>
                    {statusText && <span className={`status-${String(conversation.taskStatus).toLowerCase()}`}>{statusText}</span>}
                  </span>
                </button>
              );
            })}
          </div>
        </nav>
        <section className="task-agent-chat" aria-label="对话">
          <div className="task-agent-chat-scroll">
            {conversationLoading && <div className="task-agent-conversation-loading"><Loader2 size={14} className="tg-spin" /> 正在恢复会话…</div>}
            <div className="task-agent-message-column">
              {messages.map((message) => (
                <Fragment key={message.id}>
                  <div className={`task-agent-message ${message.role}`}>
                    {message.role === 'assistant' && <span className="task-agent-message-mark" aria-hidden="true"><ShieldCheck size={15} /></span>}
                    <div className="task-agent-message-bubble">
                      {message.text || (message.streaming ? '正在整理任务…' : '')}
                      {message.streaming && <span className="tg-stream-cursor" aria-hidden="true" />}
                      {message.draft && message.confirmationToken && <DraftCard draft={message.draft} onConfirm={() => void confirm(message)} busy={draftBusy || Boolean(message.streaming)} />}
                    </div>
                  </div>
                  {message.id === reasoningAnchorMessageId && <InlineReasoningTrace activities={reasoningActivities} />}
                </Fragment>
              ))}
              {reasoningActivities.length > 0 && !reasoningAnchorMessageId && <InlineReasoningTrace activities={reasoningActivities} />}
            </div>
            <div ref={endRef} />
          </div>
          <div className="task-agent-composer-wrap">
            <div className="task-agent-composer">
              <textarea value={input} disabled={conversationLoading} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder="描述测试目标、范围和限制条件" rows={2} />
              <button type="button" onClick={() => busy ? draftAbortRef.current?.abort() : void submit()} disabled={conversationLoading || (!busy && !input.trim())} aria-label={busy ? '停止生成' : '发送'}>{busy ? <Square size={13} fill="currentColor" /> : <Send size={16} />}</button>
            </div>
            <span className="task-agent-composer-hint">Enter 发送 · Shift + Enter 换行</span>
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
                  {phases.map((phase, index) => (
                    <div key={phase} className={index <= phaseIndex ? 'active' : ''}>
                      <span>{index < phaseIndex ? <Check size={11} /> : index === phaseIndex ? <Loader2 size={11} className="tg-spin" /> : <Circle size={7} />}</span>
                      {phaseLabels[phase]}
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
    </div>
  );
}
