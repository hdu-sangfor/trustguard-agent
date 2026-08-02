import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, ChevronDown, ChevronLeft, ChevronRight, Circle, Loader2, Send, ShieldCheck, Square, SquareTerminal } from 'lucide-react';
import Header from '@/shared/components/Header';
import { useAppSession } from '@/shared/context/AppSessionContext';
import {
  confirmTaskAgentDraft,
  getTask,
  getTaskAgentConversation,
  getTaskEvents,
  streamTaskAgentDraft,
  streamTaskEvents,
  type ApiAgentActivity,
  type ApiConversationMessage,
  type ApiEvent,
  type ApiPentestDraft,
  type ApiTask,
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
    <div style={{ marginTop: 13, border: '1px solid var(--tg-panel-border)', borderRadius: 10, overflow: 'hidden', background: 'rgba(5,12,24,.42)' }}>
      <button type="button" onClick={() => setExpanded((value) => !value)} style={{ width: '100%', border: 0, background: 'transparent', color: 'var(--tg-text-muted)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '9px 12px', cursor: 'pointer', fontSize: 11, letterSpacing: '.08em', textTransform: 'uppercase' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}><SquareTerminal size={13} /> 执行轨迹 · {activities.length} steps</span>
        <ChevronDown size={14} style={{ transform: expanded ? 'rotate(180deg)' : undefined, transition: 'transform .18s' }} />
      </button>
      {expanded && (
        <div style={{ padding: '2px 12px 11px' }}>
          {visibleActivities.map((activity) => (
            <div key={activity.id} style={{ display: 'grid', gridTemplateColumns: '18px 1fr', gap: 7, padding: '8px 0', borderTop: '1px solid rgba(148,163,184,.09)' }}>
              <span style={{ color: activity.status === 'blocked' ? '#fb7185' : activity.status === 'running' ? '#22d3ee' : '#34d399', paddingTop: 2 }}>
                {activity.status === 'running' ? <Loader2 size={13} className="tg-spin" /> : activity.status === 'blocked' ? <Circle size={10} fill="currentColor" /> : <Check size={13} />}
              </span>
              <span>
                <span style={{ display: 'block', color: 'var(--tg-text)', fontSize: 12, fontWeight: 700 }}>{activity.title}</span>
                {activity.detail && <span style={{ display: 'block', color: 'var(--tg-text-muted)', fontSize: 11, lineHeight: 1.5, marginTop: 2 }}>{activity.detail}</span>}
              </span>
            </div>
          ))}
          {totalPages > 1 && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, paddingTop: 9, borderTop: '1px solid rgba(148,163,184,.09)' }}>
              <button type="button" aria-label="上一页" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={page === 1} style={{ border: '1px solid var(--tg-panel-border)', borderRadius: 6, background: 'transparent', color: 'var(--tg-text)', padding: '4px 7px', cursor: page === 1 ? 'default' : 'pointer', opacity: page === 1 ? .4 : 1 }}><ChevronLeft size={13} /></button>
              <span style={{ color: 'var(--tg-text-muted)', fontSize: 11 }}>第 {page} / {totalPages} 页 · 每页 {pageSize} 步</span>
              <button type="button" aria-label="下一页" onClick={() => setPage((current) => Math.min(totalPages, current + 1))} disabled={page === totalPages} style={{ border: '1px solid var(--tg-panel-border)', borderRadius: 6, background: 'transparent', color: 'var(--tg-text)', padding: '4px 7px', cursor: page === totalPages ? 'default' : 'pointer', opacity: page === totalPages ? .4 : 1 }}><ChevronRight size={13} /></button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DraftCard({ draft, onConfirm, busy }: { draft: ApiPentestDraft; onConfirm: () => void; busy: boolean }) {
  return (
    <div style={{ marginTop: 14, border: '1px solid rgba(34,211,238,.36)', background: 'rgba(8,47,73,.22)', borderRadius: 12, padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#67e8f9', fontWeight: 800, fontSize: 13 }}><ShieldCheck size={15} /> 任务草稿</div>
      <div style={{ display: 'grid', gridTemplateColumns: '72px 1fr', gap: '7px 12px', marginTop: 12, fontSize: 12 }}>
        <span style={{ color: 'var(--tg-text-muted)' }}>名称</span><span>{draft.name}</span>
        <span style={{ color: 'var(--tg-text-muted)' }}>目标</span><span style={{ wordBreak: 'break-all' }}>{draft.target}</span>
        <span style={{ color: 'var(--tg-text-muted)' }}>策略</span><span>{draft.testProfile} · {draft.allowExploit ? '允许利用验证' : '默认不主动利用'}</span>
        <span style={{ color: 'var(--tg-text-muted)' }}>时长</span><span>{Math.round(draft.maxDurationSeconds / 60)} 分钟</span>
      </div>
      <button type="button" onClick={onConfirm} disabled={busy} style={{ marginTop: 14, width: '100%', border: 0, borderRadius: 7, background: 'linear-gradient(90deg,#06b6d4,#22d3ee)', color: '#03121d', fontWeight: 900, padding: '10px 12px', cursor: busy ? 'wait' : 'pointer', opacity: busy ? .65 : 1 }}>
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
  const endRef = useRef<HTMLDivElement>(null);
  const draftAbortRef = useRef<AbortController | null>(null);

  const appendMessage = (message: ChatMessage) => setMessages((items) => (
    items.some((item) => item.id === message.id) ? items : [...items, message]
  ));

  const toChatMessage = (message: ApiConversationMessage, confirmedTaskId?: string | null): ChatMessage => ({
    id: message.id,
    role: message.role,
    text: message.text,
    activities: message.activities,
    draft: message.draft,
    confirmationToken: confirmedTaskId ? null : message.confirmationToken,
  });

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, liveActivities]);
  useEffect(() => () => draftAbortRef.current?.abort(), []);

  useEffect(() => {
    if (!loggedIn) return;
    const savedConversationId = window.localStorage.getItem(CONVERSATION_STORAGE_KEY);
    if (!savedConversationId) return;
    let active = true;
    void getTaskAgentConversation(savedConversationId).then(async (conversation) => {
      if (!active) return;
      setConversationId(conversation.conversationId);
      setMessages([
        WELCOME_MESSAGE,
        ...conversation.messages.map((message) => toChatMessage(message, conversation.taskId)),
      ]);
      if (conversation.taskId) {
        try {
          const restoredTask = await getTask(conversation.taskId);
          if (active) {
            setTask(restoredTask);
            if (terminalStatuses.has(restoredTask.status)) {
              setMessages((items) => settleMessages(items));
            }
          }
        } catch { /* conversation history remains useful when a task was removed */ }
      }
    }).catch(() => { /* keep the welcome message during a transient restore failure */ });
    return () => { active = false; };
  }, [loggedIn]);

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
          if (active) appendMessage(toChatMessage(message, message.taskId));
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
  }, [task?.taskId, conversationId]);

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
    } catch (error) {
      setMessages((items) => [...items, { id: `e-${Date.now()}`, role: 'assistant', text: `确认失败：${error instanceof Error ? error.message : String(error)}` }]);
    } finally { setDraftBusy(false); }
  };

  const phaseIndex = useMemo(
    () => task?.status === 'DONE' ? phases.length : Math.max(0, phases.indexOf(task?.currentPhase ?? 'RECON')),
    [task?.currentPhase, task?.status],
  );
  if (!loggedIn) {
    return <><Header /><main style={{ paddingTop: 110, maxWidth: 760, margin: '0 auto', paddingInline: 24 }}><h2>请先登录 Supervisor</h2><button type="button" onClick={() => navigate('/login')}>前往登录</button></main></>;
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--tg-page-gradient)', color: 'var(--tg-text)' }}>
      <Header />
      <main className="task-agent-layout" style={{ paddingTop: 84, paddingBottom: 40, maxWidth: 1180, margin: '0 auto', paddingInline: 22, display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 300px', gap: 20 }}>
        <section style={{ minHeight: 'calc(100vh - 125px)', display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '10px 0 16px' }}><div style={{ color: '#67e8f9', fontSize: 11, letterSpacing: '.18em', textTransform: 'uppercase' }}>TrustGuard Supervisor</div><h1 style={{ margin: '5px 0 0', fontSize: 27, letterSpacing: '-.03em' }}>自然语言渗透测试</h1><p style={{ margin: '8px 0 0', color: 'var(--tg-text-muted)', fontSize: 13 }}>先理解和校验，再启动任务。下层工具的真实执行状态会回到这段对话。</p></div>
          <div style={{ flex: 1, border: '1px solid var(--tg-panel-border)', borderRadius: 14, background: 'rgba(2,8,23,.5)', padding: '18px clamp(12px,3vw,32px)', overflow: 'hidden' }}>
            {messages.map((message) => <div key={message.id} style={{ display: 'flex', justifyContent: message.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 18 }}><div style={{ maxWidth: message.role === 'user' ? '80%' : '92%', borderRadius: message.role === 'user' ? '14px 14px 4px 14px' : '4px 14px 14px 14px', padding: '11px 14px', background: message.role === 'user' ? 'rgba(8,145,178,.32)' : 'rgba(15,23,42,.82)', border: `1px solid ${message.role === 'user' ? 'rgba(34,211,238,.25)' : 'var(--tg-panel-border)'}`, fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{message.text || (message.streaming ? '正在分析任务' : '')}{message.streaming && <span className="tg-stream-cursor" aria-hidden="true" />}{message.activities && message.activities.length > 0 && <ActivityList activities={message.activities} />}{message.draft && message.confirmationToken && <DraftCard draft={message.draft} onConfirm={() => void confirm(message)} busy={draftBusy || Boolean(message.streaming)} />}</div></div>)}
            {liveActivities.length > 0 && task && <div style={{ margin: '8px 0 18px', maxWidth: '92%' }}><div style={{ color: '#67e8f9', fontSize: 12, fontWeight: 800, marginBottom: 6 }}>实时 Pentest Workflow · {task.currentPhase} · {task.status}</div><ActivityList activities={liveActivities} /></div>}
            <div ref={endRef} />
          </div>
          <div style={{ marginTop: 13, display: 'flex', gap: 10, border: '1px solid var(--tg-panel-border)', background: 'var(--tg-panel-bg)', borderRadius: 12, padding: 9 }}><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder="例如：对 https://test.example.com 做非破坏性 Web 渗透，重点看未授权和 Struts2，运行 20 分钟" rows={2} style={{ flex: 1, resize: 'none', border: 0, outline: 0, background: 'transparent', color: 'var(--tg-text)', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.5 }} /><button type="button" onClick={() => busy ? draftAbortRef.current?.abort() : void submit()} disabled={!busy && !input.trim()} aria-label={busy ? '停止生成' : '发送'} style={{ alignSelf: 'flex-end', width: 38, height: 38, border: 0, borderRadius: 9, background: 'var(--tg-accent)', color: '#03121d', cursor: 'pointer', opacity: !busy && !input.trim() ? .45 : 1 }}>{busy ? <Square size={13} fill="currentColor" /> : <Send size={16} />}</button></div>
        </section>
        <aside style={{ alignSelf: 'start', border: '1px solid var(--tg-panel-border)', borderRadius: 14, background: 'rgba(2,8,23,.52)', padding: 17, position: 'sticky', top: 82 }}><div style={{ color: 'var(--tg-text-muted)', fontSize: 11, letterSpacing: '.12em', textTransform: 'uppercase' }}>Workflow monitor</div>{task ? <><div style={{ marginTop: 12, fontSize: 12, color: 'var(--tg-text-muted)' }}>Task ID</div><div style={{ color: '#67e8f9', fontFamily: 'monospace', fontSize: 12, marginTop: 3, wordBreak: 'break-all' }}>{task.taskId}</div><div style={{ marginTop: 16, display: 'grid', gap: 9 }}>{phases.map((phase, index) => <div key={phase} style={{ display: 'flex', alignItems: 'center', gap: 9, color: index <= phaseIndex ? '#67e8f9' : 'var(--tg-text-muted)', fontSize: 12 }}><span style={{ width: 18, height: 18, borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', border: `1px solid ${index <= phaseIndex ? '#22d3ee' : 'var(--tg-panel-border)'}`, background: index < phaseIndex ? 'rgba(34,211,238,.18)' : 'transparent' }}>{index < phaseIndex ? <Check size={11} /> : index === phaseIndex ? <Loader2 size={11} className="tg-spin" /> : <Circle size={7} />}</span>{phase}</div>)}</div>{(task.status === 'DONE' || task.status === 'FAILED') && <button type="button" onClick={() => navigate(`/reports?taskId=${task.taskId}`)} style={{ marginTop: 18, width: '100%', border: '1px solid var(--tg-panel-border)', background: 'transparent', color: 'var(--tg-text)', borderRadius: 7, padding: '8px 10px', cursor: 'pointer' }}>打开报告中心</button>}</> : <p style={{ color: 'var(--tg-text-muted)', fontSize: 12, lineHeight: 1.6 }}>尚未启动任务。确认草稿后，这里会显示 RECON、VULN_SCAN、EXPLOIT 和 REPORT 的实时进度。</p>}</aside>
      </main>
    </div>
  );
}
