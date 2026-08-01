import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, ChevronDown, Circle, Loader2, Send, ShieldCheck, SquareTerminal } from 'lucide-react';
import Header from '@/shared/components/Header';
import { useAppSession } from '@/shared/context/AppSessionContext';
import {
  confirmTaskAgentDraft,
  createTaskAgentDraft,
  getTask,
  getTaskEvents,
  type ApiAgentActivity,
  type ApiEvent,
  type ApiPentestDraft,
  type ApiTask,
  type ApiTaskAgentDraft,
} from '@/shared/lib/api';

type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  activities?: ApiAgentActivity[];
  draft?: ApiPentestDraft | null;
  confirmationToken?: string | null;
};

const phases = ['RECON', 'THREAT_MODEL', 'VULN_SCAN', 'EXPLOIT', 'REPORT'];

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
  return (
    <div style={{ marginTop: 13, border: '1px solid var(--tg-panel-border)', borderRadius: 10, overflow: 'hidden', background: 'rgba(5,12,24,.42)' }}>
      <button type="button" onClick={() => setExpanded((value) => !value)} style={{ width: '100%', border: 0, background: 'transparent', color: 'var(--tg-text-muted)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '9px 12px', cursor: 'pointer', fontSize: 11, letterSpacing: '.08em', textTransform: 'uppercase' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}><SquareTerminal size={13} /> 执行轨迹 · {activities.length} steps</span>
        <ChevronDown size={14} style={{ transform: expanded ? 'rotate(180deg)' : undefined, transition: 'transform .18s' }} />
      </button>
      {expanded && (
        <div style={{ padding: '2px 12px 11px' }}>
          {activities.map((activity) => (
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
  const [messages, setMessages] = useState<ChatMessage[]>([
    { id: 'welcome', role: 'assistant', text: '告诉我你想测试什么目标、授权范围和限制条件。我会先整理任务草稿，确认后再启动现有 Pentest Workflow。' },
  ]);
  const [input, setInput] = useState('');
  const [conversationId, setConversationId] = useState<string>();
  const [task, setTask] = useState<ApiTask | null>(null);
  const [busy, setBusy] = useState(false);
  const [draftBusy, setDraftBusy] = useState(false);
  const [liveActivities, setLiveActivities] = useState<ApiAgentActivity[]>([]);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, liveActivities]);

  useEffect(() => {
    if (!task?.taskId) return;
    let active = true;
    let interval: number | undefined;
    const poll = async () => {
      try {
        const [latest, events] = await Promise.all([getTask(task.taskId), getTaskEvents(task.taskId, 120)]);
        if (!active) return;
        setTask(latest);
        setLiveActivities(events.map(eventActivity));
        if (latest.status === 'DONE' || latest.status === 'FAILED' || latest.status === 'CANCELLED') {
          if (interval !== undefined) window.clearInterval(interval);
        }
      } catch { /* keep the conversation usable during a transient API failure */ }
    };
    void poll();
    interval = window.setInterval(poll, 3500);
    return () => { active = false; if (interval !== undefined) window.clearInterval(interval); };
  }, [task?.taskId]);

  const submit = async () => {
    const message = input.trim();
    if (!message || busy) return;
    setInput('');
    setMessages((items) => [...items, { id: `u-${Date.now()}`, role: 'user', text: message }]);
    setBusy(true);
    try {
      const response = await createTaskAgentDraft(message, conversationId);
      setConversationId(response.conversationId);
      setMessages((items) => [...items, {
        id: `a-${Date.now()}`,
        role: 'assistant',
        text: response.assistantMessage,
        activities: response.activities,
        draft: response.draft,
        confirmationToken: response.confirmationToken,
      }]);
    } catch (error) {
      setMessages((items) => [...items, { id: `e-${Date.now()}`, role: 'assistant', text: `请求失败：${error instanceof Error ? error.message : String(error)}` }]);
    } finally { setBusy(false); }
  };

  const confirm = async (message: ChatMessage) => {
    if (!message.confirmationToken || draftBusy) return;
    setDraftBusy(true);
    try {
      const response = await confirmTaskAgentDraft(message.confirmationToken, { idempotencyKey: `ui-${message.id}` });
      setTask(response.task);
      setMessages((items) => [
        ...items.map((item) => item.id === message.id ? { ...item, confirmationToken: null } : item),
        { id: `s-${Date.now()}`, role: 'assistant', text: `任务 ${response.task.taskId} 已创建并启动。我会持续把 Orchestrator 的阶段和工具事件写入这段对话。`, activities: response.activities },
      ]);
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
            {messages.map((message) => <div key={message.id} style={{ display: 'flex', justifyContent: message.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 18 }}><div style={{ maxWidth: message.role === 'user' ? '80%' : '92%', borderRadius: message.role === 'user' ? '14px 14px 4px 14px' : '4px 14px 14px 14px', padding: '11px 14px', background: message.role === 'user' ? 'rgba(8,145,178,.32)' : 'rgba(15,23,42,.82)', border: `1px solid ${message.role === 'user' ? 'rgba(34,211,238,.25)' : 'var(--tg-panel-border)'}`, fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{message.text}{message.activities && message.activities.length > 0 && <ActivityList activities={message.activities} />}{message.draft && message.confirmationToken && <DraftCard draft={message.draft} onConfirm={() => void confirm(message)} busy={draftBusy} />}</div></div>)}
            {liveActivities.length > 0 && task && <div style={{ margin: '8px 0 18px', maxWidth: '92%' }}><div style={{ color: '#67e8f9', fontSize: 12, fontWeight: 800, marginBottom: 6 }}>实时 Pentest Workflow · {task.currentPhase} · {task.status}</div><ActivityList activities={liveActivities} /></div>}
            <div ref={endRef} />
          </div>
          <div style={{ marginTop: 13, display: 'flex', gap: 10, border: '1px solid var(--tg-panel-border)', background: 'var(--tg-panel-bg)', borderRadius: 12, padding: 9 }}><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder="例如：对 https://test.example.com 做已授权的非破坏性 Web 渗透，重点看未授权和 Struts2，运行 20 分钟" rows={2} style={{ flex: 1, resize: 'none', border: 0, outline: 0, background: 'transparent', color: 'var(--tg-text)', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.5 }} /><button type="button" onClick={() => void submit()} disabled={busy || !input.trim()} aria-label="发送" style={{ alignSelf: 'flex-end', width: 38, height: 38, border: 0, borderRadius: 9, background: 'var(--tg-accent)', color: '#03121d', cursor: 'pointer', opacity: busy || !input.trim() ? .45 : 1 }}><Send size={16} /></button></div>
        </section>
        <aside style={{ alignSelf: 'start', border: '1px solid var(--tg-panel-border)', borderRadius: 14, background: 'rgba(2,8,23,.52)', padding: 17, position: 'sticky', top: 82 }}><div style={{ color: 'var(--tg-text-muted)', fontSize: 11, letterSpacing: '.12em', textTransform: 'uppercase' }}>Workflow monitor</div>{task ? <><div style={{ marginTop: 12, fontSize: 12, color: 'var(--tg-text-muted)' }}>Task ID</div><div style={{ color: '#67e8f9', fontFamily: 'monospace', fontSize: 12, marginTop: 3, wordBreak: 'break-all' }}>{task.taskId}</div><div style={{ marginTop: 16, display: 'grid', gap: 9 }}>{phases.map((phase, index) => <div key={phase} style={{ display: 'flex', alignItems: 'center', gap: 9, color: index <= phaseIndex ? '#67e8f9' : 'var(--tg-text-muted)', fontSize: 12 }}><span style={{ width: 18, height: 18, borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', border: `1px solid ${index <= phaseIndex ? '#22d3ee' : 'var(--tg-panel-border)'}`, background: index < phaseIndex ? 'rgba(34,211,238,.18)' : 'transparent' }}>{index < phaseIndex ? <Check size={11} /> : index === phaseIndex ? <Loader2 size={11} className="tg-spin" /> : <Circle size={7} />}</span>{phase}</div>)}</div>{(task.status === 'DONE' || task.status === 'FAILED') && <button type="button" onClick={() => navigate(`/reports?taskId=${task.taskId}`)} style={{ marginTop: 18, width: '100%', border: '1px solid var(--tg-panel-border)', background: 'transparent', color: 'var(--tg-text)', borderRadius: 7, padding: '8px 10px', cursor: 'pointer' }}>打开报告中心</button>}</> : <p style={{ color: 'var(--tg-text-muted)', fontSize: 12, lineHeight: 1.6 }}>尚未启动任务。确认草稿后，这里会显示 RECON、VULN_SCAN、EXPLOIT 和 REPORT 的实时进度。</p>}</aside>
      </main>
    </div>
  );
}
