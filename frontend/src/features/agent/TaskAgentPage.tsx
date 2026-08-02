import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, ChevronDown, ChevronLeft, ChevronRight, Circle, Loader2, Plus, RefreshCw, Send, ShieldCheck, Square, SquareTerminal } from 'lucide-react';
import Header from '@/shared/components/Header';
import { useAppSession } from '@/shared/context/AppSessionContext';
import {
  confirmTaskAgentDraft,
  getTask,
  getTaskAgentConversation,
  getTaskEvents,
  listTaskAgentConversations,
  streamTaskAgentDraft,
  streamTaskEvents,
  type ApiAgentActivity,
  type ApiConversationMessage,
  type ApiEvent,
  type ApiPentestDraft,
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
  activities?: ApiAgentActivity[];
  draft?: ApiPentestDraft | null;
  confirmationToken?: string | null;
  streaming?: boolean;
};

function toChatMessage(message: ApiConversationMessage, confirmedTaskId?: string | null): ChatMessage {
  return {
    id: message.id,
    role: message.role,
    text: message.text,
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
  return activities?.map((activity) => activity.status === 'running'
    ? { ...activity, status: 'done' }
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

function ActivityList({ activities }: { activities: ApiAgentActivity[] }) {
  const [expanded, setExpanded] = useState(true);
  const [page, setPage] = useState(1);
  const pageSize = 10;
  const totalPages = Math.max(1, Math.ceil(activities.length / pageSize));
  const visibleActivities = activities.slice((page - 1) * pageSize, page * pageSize);
  useEffect(() => setPage((current) => Math.min(current, totalPages)), [totalPages]);
  return (
    <div className="task-agent-activity-list">
      <button type="button" onClick={() => setExpanded((value) => !value)} className="task-agent-activity-header">
        <span><SquareTerminal size={14} /> 实时执行轨迹 <b>{activities.length}</b></span>
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

function DraftCard({ draft, onConfirm, busy }: { draft: ApiPentestDraft; onConfirm: () => void; busy: boolean }) {
  return (
    <div className="task-agent-draft">
      <div className="task-agent-draft-title"><ShieldCheck size={15} /> 任务草稿</div>
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
    setInput('');
    setBusy(false);
    setDraftBusy(false);
    setConversationLoading(false);
    setConversationError('');
  }, []);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, liveActivities]);
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
    const pollFallback = async () => {
      try {
        const [latest, events] = await Promise.all([getTask(task.taskId), getTaskEvents(task.taskId, 500)]);
        if (!active) return;
        setTask(latest);
        setLiveActivities(events.map(eventActivity));
        if (terminalStatuses.has(latest.status)) {
          setMessages((items) => settleMessages(items));
          setLiveActivities((items) => settleActivities(items) ?? []);
          appendMessage({
            id: `task-${task.taskId}-terminal`,
            role: 'assistant',
            text: latest.status === 'DONE'
              ? `渗透测试任务 ${task.taskId} 已完成。可以打开报告中心查看测试结果。`
              : latest.status === 'FAILED'
                ? `渗透测试任务 ${task.taskId} 执行失败。你可以查看执行轨迹定位失败步骤。`
                : `渗透测试任务 ${task.taskId} 已取消。`,
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
        onStatus: (latest) => {
          if (!active) return;
          setTask(latest);
          if (terminalStatuses.has(latest.status)) {
            setMessages((items) => settleMessages(items));
            setLiveActivities((items) => settleActivities(items) ?? []);
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
                <div key={message.id} className={`task-agent-message ${message.role}`}>
                  {message.role === 'assistant' && <span className="task-agent-message-mark" aria-hidden="true"><ShieldCheck size={15} /></span>}
                  <div className="task-agent-message-bubble">
                    {message.text || (message.streaming ? '正在整理任务…' : '')}
                    {message.streaming && <span className="tg-stream-cursor" aria-hidden="true" />}
                    {message.draft && message.confirmationToken && <DraftCard draft={message.draft} onConfirm={() => void confirm(message)} busy={draftBusy || Boolean(message.streaming)} />}
                  </div>
                </div>
              ))}
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
                {(task.status === 'DONE' || task.status === 'FAILED') && <button type="button" className="task-agent-report-button" onClick={() => navigate(`/reports?taskId=${task.taskId}`)}>打开报告中心</button>}
              </>
            ) : <p className="task-agent-monitor-empty">确认任务草稿后，这里会显示执行阶段和状态。</p>}
          </section>
          <section className="task-agent-monitor-trace">
            {traceActivities.length > 0
              ? <ActivityList activities={traceActivities} />
              : <><div className="task-agent-activity-empty-heading"><SquareTerminal size={14} /> 实时执行轨迹</div><p>任务开始后，执行步骤会实时显示在这里。</p></>}
          </section>
        </aside>
      </main>
    </div>
  );
}
