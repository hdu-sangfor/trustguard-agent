/**
 * API client for the TrustGuard gateway backend (port 18080).
 * Dev: requests are proxied from /api → http://localhost:18080 via vite.config.ts.
 * Prod: configure VITE_API_BASE env var or serve frontend from the same origin.
 */

import { decodePossiblyMojibake } from "@/shared/lib/text";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '';

export interface ApiTask {
  id: number;
  taskId: string;
  name: string;
  target: string;
  description: string;
  status: 'PENDING' | 'RUNNING' | 'DONE' | 'FAILED' | 'PAUSED' | 'CANCELLED';
  currentPhase: string;
  createdAt: string;
  updatedAt: string;
}

export interface ApiEvent {
  taskId: string;
  timestamp: string;
  eventType: string;
  sourceModule: string;
  payload: Record<string, unknown>;
}

export interface ApiTodo {
  todoId: string;
  name: string;
  target: string;
  phase: string;
  status: 'PENDING' | 'IN_PROGRESS' | 'DONE' | 'FAILED' | 'SKIPPED';
  description: string;
}

export interface ApiReportPhase {
  phase: string;
  status: string;
  notes: string;
}

export type ApiSeverity = 'critical' | 'high' | 'medium' | 'low' | 'info';

export interface ApiReportFinding {
  title: string;
  severity: ApiSeverity;
  cve?: string | null;
  evidence?: string | null;
  phase?: string | null;
  skill?: string | null;
}

export interface ApiReportRecommendation {
  finding: string;
  suggestion: string;
  severity: ApiSeverity;
}

export interface ApiReportArtifact {
  skillId: string;
  summary: string;
}

export interface ApiReportExecution {
  phase?: string | null;
  skillId?: string | null;
  status?: string | null;
  durationMs?: number | null;
  createdAt?: string | null;
}

export interface ApiReport {
  taskId: string;
  target: string;
  status: string;
  phases: ApiReportPhase[];
  summary?: string;
  createdAt?: string;
  findings?: ApiReportFinding[];
  recommendations?: ApiReportRecommendation[];
  artifacts?: ApiReportArtifact[];
  openPorts?: number[];
  services?: string[];
  severityHistogram?: Record<string, number>;
  riskLevel?: 'critical' | 'high' | 'medium' | 'low' | 'none';
  executions?: ApiReportExecution[];
}

export interface ApiObservation {
  task_id: string;
  status: string;
  current_phase: string;
  target: string;
  context: Record<string, unknown>;
  artifacts_summary: Array<{ skill_id: string; summary: string }>;
  generated_at: string;
}

interface ApiResponse<T> {
  code: string | number;
  message: string;
  data: T;
}

function authHeaders(): Record<string, string> {
  try {
    const token = localStorage.getItem('sentinel_auth_token_v1');
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch { return {}; }
}

/** Fires a custom event so AppSessionContext can log the user out when the token is rejected. */
function fireUnauthorized() {
  try { window.dispatchEvent(new Event('sentinel-unauthorized')); } catch { /* noop */ }
}

async function apiFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...opts,
    headers: { ...authHeaders(), ...(opts?.headers as Record<string, string> | undefined) },
  });
  if (resp.status === 401) { fireUnauthorized(); throw new Error('未授权，请重新登录'); }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status} ${resp.statusText}`;
    try {
      const errorBody = await resp.json() as { message?: string; detail?: string };
      detail = errorBody.message || errorBody.detail || detail;
    } catch { /* keep HTTP fallback */ }
    throw new Error(detail);
  }
  const json = (await resp.json()) as ApiResponse<T>;
  // Backend returns code as string "0"; tolerate both "0" and 0 for older responses.
  if (json.code !== '0' && json.code !== 0) throw new Error(json.message ?? 'API error');
  return json.data;
}

function normalizeUser(user: ApiUser): ApiUser {
  return {
    ...user,
    username: decodePossiblyMojibake(user.username) || user.username,
    displayName: decodePossiblyMojibake(user.displayName) || user.displayName,
  };
}

function normalizeAuthResult(result: ApiAuthResult): ApiAuthResult {
  return { ...result, user: normalizeUser(result.user) };
}

export async function createTask(params: {
  name: string;
  description: string;
  target: string;
}): Promise<ApiTask> {
  return apiFetch<ApiTask>('/api/v1/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
}

export async function listTasks(): Promise<ApiTask[]> {
  return apiFetch<ApiTask[]>('/api/v1/tasks');
}

/** Reports gallery: all DONE tasks, ordered by completion time desc. */
export async function listCompletedTasks(limit = 100): Promise<ApiTask[]> {
  return apiFetch<ApiTask[]>(`/api/v1/admin/reports?limit=${limit}`);
}

export async function getTask(taskId: string): Promise<ApiTask> {
  return apiFetch<ApiTask>(`/api/v1/tasks/${taskId}`);
}

function taskRunQuery(maxTicks?: number, maxDurationSeconds?: number): string {
  const qs = new URLSearchParams();
  if (maxTicks != null) qs.set('maxTicks', String(maxTicks));
  if (maxDurationSeconds != null) qs.set('max_duration_seconds', String(maxDurationSeconds));
  const s = qs.toString();
  return s ? `?${s}` : '';
}

export async function runTask(taskId: string, maxTicks?: number, maxDurationSeconds?: number): Promise<void> {
  const qs = taskRunQuery(maxTicks, maxDurationSeconds);
  await apiFetch<null>(`/api/v1/tasks/${taskId}/run${qs}`, { method: 'POST' });
}

export async function stopTask(taskId: string): Promise<void> {
  await apiFetch<null>(`/api/v1/tasks/${taskId}/stop`, { method: 'POST' });
}

export async function resumeTask(taskId: string, maxTicks?: number, maxDurationSeconds?: number): Promise<void> {
  const qs = taskRunQuery(maxTicks, maxDurationSeconds);
  await apiFetch<null>(`/api/v1/tasks/${taskId}/resume${qs}`, { method: 'POST' });
}

export async function tickTask(taskId: string): Promise<void> {
  await apiFetch<null>(`/api/v1/tasks/${taskId}/tick`, { method: 'POST' });
}

export async function deleteTask(taskId: string): Promise<void> {
  await apiFetch<null>(`/api/v1/tasks/${taskId}`, { method: 'DELETE' });
}

export async function getTaskTodos(taskId: string): Promise<ApiTodo[]> {
  const data = await apiFetch<{ taskId: string; todos: ApiTodo[] }>(
    `/api/v1/tasks/${taskId}/todos`,
  );
  return data.todos ?? [];
}

export async function getTaskReport(taskId: string): Promise<ApiReport> {
  return apiFetch<ApiReport>(`/api/v1/tasks/${taskId}/report`);
}

export async function getTaskEvents(taskId: string, limit = 500): Promise<ApiEvent[]> {
  const data = await apiFetch<ApiEvent[] | { taskId: string; events: ApiEvent[] }>(
    `/api/v1/tasks/${taskId}/events?limit=${limit}`,
  );
  // Backend returns events as direct array; tolerate both formats
  if (Array.isArray(data)) return data;
  return data.events ?? [];
}

export async function getTaskObservation(taskId: string): Promise<ApiObservation> {
  return apiFetch<ApiObservation>(`/api/v1/tasks/${taskId}/observation`);
}

export interface ApiTaskFull {
  task: ApiTask;
  events: ApiEvent[];
  observation: ApiObservation | null;
  generatedAt: string;
}

export async function getTaskFull(taskId: string, eventsLimit = 100): Promise<ApiTaskFull> {
  return apiFetch<ApiTaskFull>(`/api/v1/tasks/${taskId}/full?events_limit=${eventsLimit}`);
}

export interface ApiTraceExecution {
  request_id?: string;
  skill_id?: string;
  phase?: string;
  status?: string;
  reasoning?: string;
  duration_ms?: number;
  [key: string]: unknown;
}

export interface ApiTrace {
  task_id?: string;
  plan?: Record<string, unknown>;
  compile?: Record<string, unknown>;
  executions?: ApiTraceExecution[];
  [key: string]: unknown;
}

/**
 * GET /api/v1/tasks/{taskId}/trace
 * The gateway proxies this directly from the orchestrator ("原样代理"),
 * so the response may or may not be wrapped in ApiResponse.
 * We handle both cases.
 */
export async function getTaskTrace(
  taskId: string,
  executionsLimit = 50,
): Promise<ApiTrace> {
  const resp = await fetch(
    `${API_BASE}/api/v1/tasks/${taskId}/trace?executions_limit=${executionsLimit}`,
  );
  if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
  const json = (await resp.json()) as Record<string, unknown>;
  // Unwrap standard ApiResponse wrapper if present
  if ('code' in json && 'data' in json) {
    if (json.code !== '0' && json.code !== 0) throw new Error(String(json.message) || 'API error');
    return json.data as ApiTrace;
  }
  return json as ApiTrace;
}

/**
 * GET /api/v1/tasks/{taskId}/trace/plan — plan segment only.
 * query: include_validation_error (default true)
 */
export async function getTracePlan(taskId: string): Promise<Record<string, unknown>> {
  const resp = await fetch(`${API_BASE}/api/v1/tasks/${taskId}/trace/plan`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
  const json = (await resp.json()) as Record<string, unknown>;
  if ('code' in json && 'data' in json) {
    if (json.code !== '0' && json.code !== 0) throw new Error(String(json.message) || 'API error');
    return json.data as Record<string, unknown>;
  }
  return json;
}

/**
 * GET /api/v1/tasks/{taskId}/trace/compile — compile segment only.
 */
export async function getTraceCompile(taskId: string): Promise<Record<string, unknown>> {
  const resp = await fetch(`${API_BASE}/api/v1/tasks/${taskId}/trace/compile`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
  const json = (await resp.json()) as Record<string, unknown>;
  if ('code' in json && 'data' in json) {
    if (json.code !== '0' && json.code !== 0) throw new Error(String(json.message) || 'API error');
    return json.data as Record<string, unknown>;
  }
  return json;
}

export interface ApiMqStatus {
  mode?: string;
  queue?: string;
  messages_ready?: number;
  consumers?: number;
  [key: string]: unknown;
}

export interface ApiSliSnapshot {
  total_ticks?: number;
  failed_ticks?: number;
  tick_error_rate?: number;
  active_tasks?: number;
  [key: string]: unknown;
}

export interface ApiHealthStatus {
  status: string;
  [key: string]: unknown;
}

export async function getSystemHealth(): Promise<ApiHealthStatus> {
  const resp = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(5000) });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = (await resp.json()) as ApiHealthStatus;
  return json;
}

export interface ApiTaskStats {
  total?: number;
  running?: number;
  paused?: number;
  done?: number;
  failed?: number;
  pending?: number;
  cancelled?: number;
  [key: string]: unknown;
}

export async function getTaskStats(): Promise<ApiTaskStats> {
  return apiFetch<ApiTaskStats>('/api/v1/admin/tasks/stats');
}

export interface ApiAnalyticsOverview {
  task_stats: ApiTaskStats;
  completion_rate: number;
  recent_events_count: number;
  event_type_breakdown: Record<string, number>;
  skill_execution_breakdown?: Record<string, number>;
  total_executions?: number;
  total_plans?: number;
  generated_at: string;
}

export async function getAnalyticsOverview(): Promise<ApiAnalyticsOverview> {
  return apiFetch<ApiAnalyticsOverview>('/api/v1/admin/analytics/overview');
}

export async function bulkStopRunningTasks(): Promise<{ stopped: number }> {
  return apiFetch<{ stopped: number }>('/api/v1/admin/tasks/bulk-stop', { method: 'POST' });
}

export async function cleanupFinishedTasks(): Promise<{ deleted: number }> {
  const resp = await fetch(`${API_BASE}/api/v1/admin/tasks/completed`, { method: 'DELETE' });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
  const json = (await resp.json()) as { code: string | number; message?: string; data: { deleted: number } };
  if (json.code !== '0' && json.code !== 0) throw new Error(json.message ?? 'API error');
  return json.data;
}

export async function getMqStatus(): Promise<ApiMqStatus> {
  return apiFetch<ApiMqStatus>('/api/v1/admin/mq-status');
}

export interface ApiGlobalEvent {
  taskId?: string;
  eventType?: string;
  ts?: string;
  sourceModule?: string;
}

export async function getRecentGlobalEvents(limit = 30): Promise<ApiGlobalEvent[]> {
  return apiFetch<ApiGlobalEvent[]>(`/api/v1/admin/events/recent?limit=${limit}`);
}

// ──────────────────────────────────────────────
// Monitor Snapshot (combined real-time dashboard data)
// ──────────────────────────────────────────────

export interface ApiActiveTask {
  taskId: string;
  name: string;
  target: string;
  status: string;
  currentPhase: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface ApiMonitorSnapshot {
  taskStats: ApiTaskStats;
  activeTasks: ApiActiveTask[];
  recentTasks: Array<{ taskId: string; name: string; status: string; currentPhase: string | null; updatedAt: string | null }>;
  recentEvents: ApiGlobalEvent[];
  mqStatus: ApiMqStatus;
  snapshotAt: string;
}

export async function getMonitorSnapshot(eventLimit = 30): Promise<ApiMonitorSnapshot> {
  return apiFetch<ApiMonitorSnapshot>(`/api/v1/admin/monitor/snapshot?event_limit=${eventLimit}`);
}

export interface ApiExecutionRecord {
  request_id?: string;
  task_id?: string;
  skill_id?: string;
  phase?: string;
  status?: string;
  reasoning?: string;
  duration_ms?: number;
  worker_id?: string;
  created_at?: string;
  [key: string]: unknown;
}

export async function getTaskExecutions(
  taskId: string,
  limit = 50,
  offset = 0,
): Promise<ApiExecutionRecord[]> {
  const resp = await fetch(
    `${API_BASE}/api/v1/tasks/${taskId}/executions?limit=${limit}&offset=${offset}`,
  );
  if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
  const json = (await resp.json()) as Record<string, unknown>;
  if ('code' in json && 'data' in json) {
    if (json.code !== '0' && json.code !== 0) throw new Error(String(json.message) || 'API error');
    const d = json.data as Record<string, unknown>;
    return Array.isArray(d.executions) ? (d.executions as ApiExecutionRecord[]) : (Array.isArray(d) ? d as ApiExecutionRecord[] : []);
  }
  if (Array.isArray(json.executions)) return json.executions as ApiExecutionRecord[];
  if (Array.isArray(json)) return json as ApiExecutionRecord[];
  return [];
}

export async function getExecutionRecord(requestId: string): Promise<ApiExecutionRecord> {
  const resp = await fetch(`${API_BASE}/api/v1/executions/${requestId}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
  const json = (await resp.json()) as Record<string, unknown>;
  if ('code' in json && 'data' in json) {
    if (json.code !== '0' && json.code !== 0) throw new Error(String(json.message) || 'API error');
    return json.data as ApiExecutionRecord;
  }
  return json as ApiExecutionRecord;
}

export async function getSliSnapshot(includeMq = true): Promise<ApiSliSnapshot> {
  return apiFetch<ApiSliSnapshot>(
    `/api/v1/admin/orchestrator/sli/snapshot?include_mq=${includeMq}`,
  );
}

export interface ApiV1Overview {
  v1_scheduling?: {
    plan_item_dispatch_enabled?: boolean;
    mode?: string;
    active_tasks?: number;
    [key: string]: unknown;
  };
  v1_mq_lanes?: {
    execution_dispatch_mode?: string;
    mq_dispatch_ready?: boolean;
    agent_lane_routing_active?: boolean;
    agent_lane_publish_ready?: boolean;
    [key: string]: unknown;
  };
  v1_kb?: {
    enabled?: boolean;
    has_embed_api_key?: boolean;
    kb_federation_store_enabled?: boolean;
    [key: string]: unknown;
  };
  v1_agent_registry?: {
    total?: number;
    enabled?: number;
    agent_ids?: string[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export async function getV1Overview(): Promise<ApiV1Overview> {
  const resp = await fetch(`${API_BASE}/api/v1/admin/v1/overview`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = (await resp.json()) as Record<string, unknown>;
  if ('code' in json && 'data' in json) {
    if (json.code !== '0' && json.code !== 0) throw new Error(String(json.message) || 'API error');
    return json.data as ApiV1Overview;
  }
  return json as ApiV1Overview;
}

export interface ApiV1HealthOverview {
  health?: {
    status?: string;
    message?: string;
    [key: string]: unknown;
  };
  overview?: ApiV1Overview;
  [key: string]: unknown;
}

export async function getV1HealthOverview(): Promise<ApiV1HealthOverview> {
  const resp = await fetch(`${API_BASE}/api/v1/admin/v1/health-overview`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = (await resp.json()) as Record<string, unknown>;
  if ('code' in json && 'data' in json) {
    if (json.code !== '0' && json.code !== 0) throw new Error(String(json.message) || 'API error');
    return json.data as ApiV1HealthOverview;
  }
  return json as ApiV1HealthOverview;
}

export interface ApiSchedulingObserve {
  scheduling?: {
    mode?: string;
    active_tasks?: number;
    queued_items?: number;
    [key: string]: unknown;
  };
  capability_scores?: Record<string, number>;
  agent_candidates?: Array<{ agent_id?: string; score?: number; [key: string]: unknown }>;
  selected_agent?: string;
  [key: string]: unknown;
}

export async function getV1SchedulingObserve(
  phase?: string,
  taskId?: string,
  preferredCapability?: string,
): Promise<ApiSchedulingObserve> {
  const qs = new URLSearchParams();
  if (phase) qs.set('phase', phase);
  if (taskId) qs.set('task_id', taskId);
  if (preferredCapability) qs.set('preferred_capability', preferredCapability);
  const q = qs.toString() ? `?${qs.toString()}` : '';
  const resp = await fetch(`${API_BASE}/api/v1/admin/v1/scheduling-observe${q}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = (await resp.json()) as Record<string, unknown>;
  if ('code' in json && 'data' in json) {
    if (json.code !== '0' && json.code !== 0) throw new Error(String(json.message) || 'API error');
    return json.data as ApiSchedulingObserve;
  }
  return json as ApiSchedulingObserve;
}

export interface ApiV1KbObserve {
  enabled?: boolean;
  kb_backend?: string;
  has_embed_api_key?: boolean;
  collection_name?: string;
  vector_size?: number;
  total_chunks?: number;
  skill_ids?: string[];
  federation_enabled?: boolean;
  [key: string]: unknown;
}

export async function getV1KbObserve(): Promise<ApiV1KbObserve> {
  const resp = await fetch(`${API_BASE}/api/v1/admin/v1/kb-observe`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = (await resp.json()) as Record<string, unknown>;
  if ('code' in json && 'data' in json) {
    if (json.code !== '0' && json.code !== 0) throw new Error(String(json.message) || 'API error');
    return json.data as ApiV1KbObserve;
  }
  return json as ApiV1KbObserve;
}

export interface ApiV1KbFederation {
  enabled?: boolean;
  store_type?: string;
  federation_provider?: string;
  stores?: Array<{ id?: string; type?: string; active?: boolean; [key: string]: unknown }>;
  [key: string]: unknown;
}

export async function getV1KbFederationObserve(): Promise<ApiV1KbFederation> {
  const resp = await fetch(`${API_BASE}/api/v1/admin/v1/kb-federation-observe`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = (await resp.json()) as Record<string, unknown>;
  if ('code' in json && 'data' in json) {
    if (json.code !== '0' && json.code !== 0) throw new Error(String(json.message) || 'API error');
    return json.data as ApiV1KbFederation;
  }
  return json as ApiV1KbFederation;
}

/**
 * Format a list of backend events as readable log lines.
 * Returns an array of strings — one per event — suitable for display or export.
 */
export function formatEventsAsLog(events: ApiEvent[]): string[] {
  return events.map((e) => {
    const ts = new Date(e.timestamp).toLocaleString('zh-CN');
    const p = e.payload ?? {};
    let detail: string;
    if (typeof p.message === 'string') detail = p.message;
    else if (typeof p.skill_id === 'string') detail = `skill=${p.skill_id}${typeof p.phase === 'string' ? ' phase=' + p.phase : ''}`;
    else if (typeof p.phase === 'string') detail = `phase=${p.phase}`;
    else detail = JSON.stringify(p).slice(0, 200);
    return `[${ts}] [${e.eventType ?? 'EVENT'}] ${detail}`;
  });
}

/** The canonical 6-phase order for the state machine */
export const TRUSTGUARD_PHASES = [
  'RECON',
  'THREAT_MODEL',
  'VULN_SCAN',
  'EXPLOIT',
  'REPORT',
  'DONE',
] as const;

/** Map backend task status to frontend display status */
export function toFrontendStatus(
  apiStatus: ApiTask['status'],
): 'not_started' | 'running' | 'paused' | 'failed' | 'finished' {
  switch (apiStatus) {
    case 'PENDING':
      return 'not_started';
    case 'RUNNING':
      return 'running';
    case 'PAUSED':
      return 'paused';
    case 'FAILED':
    case 'CANCELLED':
      return 'failed';
    case 'DONE':
      return 'finished';
    default:
      return 'not_started';
  }
}

export interface ApiSkillEntry {
  skill_id: string;
  category: string;
  [key: string]: unknown;
}

export interface ApiSkillRegistry {
  skill_ids: string[];
  skills: ApiSkillEntry[];
  error?: string;
}

export async function getSkillRegistry(phase?: string): Promise<ApiSkillRegistry> {
  const qs = phase ? `?phase=${encodeURIComponent(phase)}` : '';
  const resp = await fetch(`${API_BASE}/api/v1/admin/skills${qs}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = (await resp.json()) as Record<string, unknown>;
  if ('code' in json && 'data' in json) {
    if (json.code !== '0' && json.code !== 0) throw new Error(String(json.message) || 'API error');
    return json.data as ApiSkillRegistry;
  }
  return json as unknown as ApiSkillRegistry;
}

// ──────────────────────────────────────────────
// User Management (Platform Admin)
// ──────────────────────────────────────────────

export interface ApiUser {
  id: number;
  userId: string;
  username: string;
  displayName: string;
  email: string;
  role: 'ADMIN' | 'OPERATOR' | 'VIEWER';
  status: 'ACTIVE' | 'DISABLED';
  lastLoginAt: string | null;
  createdAt: string;
  updatedAt: string;
}

async function userApiCall<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...(init?.headers as Record<string, string> | undefined) },
  });
  if (resp.status === 401) { fireUnauthorized(); throw new Error('未授权，请重新登录'); }
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = (await resp.json()) as { code: string; data: T; message?: string };
  if (json.code !== '0') throw new Error(json.message ?? 'API error');
  return json.data;
}

export async function listUsers(): Promise<ApiUser[]> {
  const users = await userApiCall<ApiUser[]>('/api/v1/admin/users');
  return users.map(normalizeUser);
}

export async function getUser(userId: string): Promise<ApiUser> {
  return normalizeUser(await userApiCall<ApiUser>(`/api/v1/admin/users/${encodeURIComponent(userId)}`));
}

export async function createUser(params: {
  username: string;
  displayName?: string;
  email?: string;
  role?: string;
}): Promise<ApiUser> {
  return normalizeUser(await userApiCall<ApiUser>('/api/v1/admin/users', {
    method: 'POST',
    body: JSON.stringify(params),
  }));
}

export async function updateUser(
  userId: string,
  params: { displayName?: string; email?: string; role?: string; status?: string }
): Promise<void> {
  await userApiCall<unknown>(`/api/v1/admin/users/${encodeURIComponent(userId)}`, {
    method: 'PUT',
    body: JSON.stringify(params),
  });
}

export async function deleteUser(userId: string): Promise<void> {
  await userApiCall<unknown>(`/api/v1/admin/users/${encodeURIComponent(userId)}`, {
    method: 'DELETE',
  });
}

export async function setUserPassword(userId: string, newPassword: string): Promise<void> {
  await userApiCall<unknown>(`/api/v1/admin/users/${encodeURIComponent(userId)}/password`, {
    method: 'PUT',
    body: JSON.stringify({ password: newPassword }),
  });
}

// ──────────────────────────────────────────────
// Audit Log
// ──────────────────────────────────────────────

export interface ApiAuditEvent {
  type: string;
  actor: string;
  target: string;
  detail: string;
  timestamp: string;
}

export async function getAuditEvents(limit = 50): Promise<ApiAuditEvent[]> {
  return apiFetch<ApiAuditEvent[]>(`/api/v1/admin/audit/events?limit=${limit}`);
}

export interface ApiAuditSummary {
  total: number;
  by_type: Record<string, number>;
  login_failures: number;
  generated_at: string;
}

export async function getAuditSummary(): Promise<ApiAuditSummary> {
  return apiFetch<ApiAuditSummary>('/api/v1/admin/audit/summary');
}

// ──────────────────────────────────────────────
// Dashboard Summary
// ──────────────────────────────────────────────

export interface ApiDashboardActiveTask {
  taskId: string;
  name: string;
  target: string;
  status: string;
  currentPhase: string;
  updatedAt: string | null;
}

export interface ApiDashboardRecentTask {
  taskId: string;
  name: string;
  target: string;
  updatedAt: string | null;
}

export interface ApiDashboardSummary {
  task_stats: ApiTaskStats;
  recent_events: ApiGlobalEvent[];
  active_tasks: ApiDashboardActiveTask[];
  recent_completed: ApiDashboardRecentTask[];
  generated_at: string;
}

export async function getDashboardSummary(): Promise<ApiDashboardSummary> {
  return apiFetch<ApiDashboardSummary>('/api/v1/admin/dashboard/summary');
}

// ──────────────────────────────────────────────
// Batch Task Creation
// ──────────────────────────────────────────────

export interface ApiBatchTaskResult {
  taskId?: string;
  name?: string;
  target: string;
  error?: string;
}

export interface ApiBatchCreateResponse {
  created: number;
  auto_started: boolean;
  started_count: number;
  tasks: ApiBatchTaskResult[];
  generated_at: string;
}

export async function batchCreateTasks(params: {
  targets: string[];
  name_prefix?: string;
  description?: string;
  auto_start?: boolean;
}): Promise<ApiBatchCreateResponse> {
  return apiFetch<ApiBatchCreateResponse>('/api/v1/admin/tasks/batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(params),
  });
}

// ──────────────────────────────────────────────
// Vulnerability Inventory
// ──────────────────────────────────────────────

export interface ApiVulnEntry {
  /** CVE / title / description (free-form string from LLM context) */
  name?: string;
  cve?: string;
  severity?: string;
  description?: string;
  affected_service?: string;
  remediation?: string;
  [key: string]: unknown;
}

export interface ApiVulnsTaskRow {
  task_id: string;
  task_name: string;
  target: string;
  vuln_count: number;
  vulnerabilities: ApiVulnEntry[];
}

export interface ApiVulnsSummary {
  tasks_analyzed: number;
  total_vulns: number;
  by_task: ApiVulnsTaskRow[];
  generated_at: string;
}

export async function getVulnsSummary(taskLimit = 20): Promise<ApiVulnsSummary> {
  return apiFetch<ApiVulnsSummary>(`/api/v1/admin/vulns/summary?task_limit=${taskLimit}`);
}

// ──────────────────────────────────────────────
// System Info
// ──────────────────────────────────────────────

export interface ApiSystemInfo {
  platform: string;
  version: string;
  edition: string;
  apiVersion: string;
  runtime: string;
  os: string;
  startTime: string;
  uptimeSeconds: number;
  taskStats: Record<string, unknown>;
  services: Record<string, { role: string; port: number; status: string; detail?: string; checkedAt?: string }>;
  capabilities: {
    skillContainers: string;
    phases: number;
    phaseList: string[];
    concurrentTargets: string;
    dispatchModes: string[];
    llmProviders: string[];
  };
}

export async function getSystemInfoFull(): Promise<ApiSystemInfo> {
  return apiFetch<ApiSystemInfo>('/api/v1/system/info');
}

// ──────────────────────────────────────────────
// Runtime Configuration
// ──────────────────────────────────────────────

export interface ApiRuntimeConfig {
  llm: {
    provider: string;
    model: string;
    endpoint_host: string;
    api_key_set: boolean;
  };
  execution: {
    dispatch_mode: string;
    max_concurrent: string;
    plan_mode: string;
    task_store: string;
    subprocess_timeout_buffer?: string;
  };
  features: {
    kb_enabled: string;
    manager_agent: string;
    skill_containers: string;
    trace_redact?: string;
  };
  deployment: {
    mode: string;
    workspace_root: string;
  };
  generated_at: string;
}

export async function getRuntimeConfig(): Promise<ApiRuntimeConfig> {
  return apiFetch<ApiRuntimeConfig>('/api/v1/admin/config/runtime');
}

// ─── RAG knowledge center ───────────────────────────────────────────────────

export interface ApiRagDependency {
  status: string;
  latency_ms?: number | null;
  detail?: string | null;
}

export interface ApiRagHealth {
  status: string;
  service?: string;
  version?: string;
  env?: string;
  dependencies?: Record<string, ApiRagDependency>;
}

export interface ApiKnowledgeBase {
  id: string;
  name: string;
  description?: string | null;
  embedding_profile: string;
  embedding_provider: string;
  embedding_api_driver?: string | null;
  embedding_model: string;
  embedding_dim: number;
  content_revision: number;
  is_default: boolean;
  is_system: boolean;
  document_count: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ApiKnowledgeBaseList {
  items: ApiKnowledgeBase[];
  total: number;
}

export interface ApiKnowledgeSourceCapabilities {
  gateway?: {
    max_upload_bytes?: number;
  };
  embedding_profiles?: Array<{
    id: string;
    provider?: string;
    model?: string;
    dimension?: number;
    default?: boolean;
  }>;
  sources?: Array<{
    source_type: string;
    mime_types?: string[];
    max_bytes?: number;
    max_pdf_pages?: number;
  }>;
}

export interface ApiKnowledgeDocument {
  id: string;
  knowledge_base_id?: string | null;
  source_type: string;
  source_uri: string;
  content_hash: string;
  status: string;
  title?: string | null;
  mime_type?: string | null;
  original_filename?: string | null;
  doc_version: number;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ApiKnowledgeDocumentList {
  items: ApiKnowledgeDocument[];
  total: number;
  offset: number;
  limit: number;
}

export interface ApiKnowledgeChunk {
  id: string;
  chunk_index: number;
  text: string;
  token_count: number;
  page_no?: number | null;
  metadata?: Record<string, unknown> | null;
}

export interface ApiKnowledgeSearchSource {
  document_id: string;
  source_uri: string;
  original_filename?: string | null;
  chunk_index: number;
  page_no?: number | null;
}

export interface ApiKnowledgeSearchHit {
  chunk_id: string;
  text: string;
  score: number;
  vector_score?: number | null;
  keyword_score?: number | null;
  rerank_score?: number | null;
  title?: string | null;
  entity_id?: string | null;
  entity_type?: string | null;
  exact_entity_match?: string | null;
  source: ApiKnowledgeSearchSource;
  metadata?: Record<string, unknown> | null;
  expanded: boolean;
}

export interface ApiKnowledgeSearchResponse {
  schema_version: string;
  request_id: string;
  query: string;
  knowledge_base_id: string;
  content_revision: number;
  search_status: string;
  effective_mode: string;
  results: ApiKnowledgeSearchHit[];
  total: number;
  fusion_method: string;
  retrieval_time_ms: number;
  components: Record<string, number>;
  degraded_components: string[];
  query_entities: string[];
  abstained: boolean;
  abstention_reason?: string | null;
  query_plan?: Record<string, unknown>;
  coverage?: { status?: string; warning?: string | null };
  coverage_warning?: string | null;
}

export interface ApiKnowledgeAnswerCitation {
  citation_id: number;
  chunk_id: string;
  document_id: string;
  source_uri: string;
  original_filename?: string | null;
  chunk_index: number;
  page_no?: number | null;
  excerpt: string;
}

export interface ApiKnowledgeAnswerUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface ApiKnowledgeAnswerResponse {
  query: string;
  knowledge_base_id: string;
  status: string;
  answer: string;
  citations: ApiKnowledgeAnswerCitation[];
  search_status: string;
  effective_mode: string;
  degraded_components: string[];
  abstained: boolean;
  abstention_reason?: string | null;
  query_entities: string[];
  retrieved_count: number;
  context_chunk_count: number;
  context_token_count: number;
  retrieval_time_ms: number;
  generation_time_ms: number;
  total_time_ms: number;
  model?: string | null;
  usage?: ApiKnowledgeAnswerUsage | null;
  query_plan?: Record<string, unknown> | null;
  coverage_status: string;
  coverage_warning?: string | null;
}

export interface ApiKnowledgeIngestJob {
  id: string;
  source_type: string;
  status: string;
  current_step?: string | null;
  document_id?: string | null;
  pending_document_id?: string | null;
  conflict_candidates: string[];
  error_code?: string | null;
  error_message?: string | null;
  attempt: number;
  max_attempts: number;
  step_logs?: Array<Record<string, unknown>>;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  knowledge_base_id?: string | null;
}

export interface ApiKnowledgeIngestCreated {
  job_id: string;
  status: string;
  knowledge_base_id: string;
  embedding_profile: string;
  embedding_model?: string | null;
  embedding_dim?: number | null;
}

export interface ApiKnowledgeCrawlerPreset {
  id: string;
  name: string;
  description: string;
  kind: 'category';
  category_name?: string | null;
  site_urls: string[];
  keywords: string[];
  structured_sources: string[];
  domain_category?: string | null;
  kb_tier?: string | null;
  phases: string[];
  topic_tags: string[];
  priority?: string | null;
  review_criteria: string;
}

export interface ApiKnowledgeCrawlerDefaults {
  max_results_per_keyword: number;
  max_pages_per_site: number;
  max_total_pages: number;
  min_content_chars: number;
  fetch_delay_seconds: number;
  max_retries: number;
  retry_base_seconds: number;
  agent_review_available: boolean;
  agent_review_model?: string | null;
}

export interface ApiKnowledgeCrawlerJob {
  id: string;
  knowledge_base_id: string;
  status: 'queued' | 'running' | 'paused' | 'succeeded' | 'failed' | 'cancelled';
  config: Record<string, unknown>;
  progress: Record<string, unknown>;
  ingest_job_ids: string[];
  error_message?: string | null;
  attempt: number;
  cancel_requested: boolean;
  pause_requested: boolean;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at?: string | null;
}

export interface ApiKnowledgeCrawlerJobList {
  items: ApiKnowledgeCrawlerJob[];
  total: number;
}

export interface ApiKnowledgeCrawlerReviewItem {
  id: string;
  status: 'pending' | 'processing' | 'rejecting' | 'approved' | 'rejected';
  knowledge_base_id: string;
  title: string;
  source_uri: string;
  source_type: string;
  original_filename: string;
  content_preview: string;
  content_chars: number;
  created_at: string;
  ingest_job_id?: string | null;
  reviewer?: 'human' | 'agent' | null;
  review_reason?: string | null;
  review_confidence?: number | null;
  agent_decision?: 'approve' | 'reject' | 'manual_review' | null;
  manual_reviewer?: string | null;
  manual_reviewed_at?: string | null;
  rejected_at?: string | null;
  review_content_expires_at?: string | null;
  review_content_expired_at?: string | null;
  review_content_available: boolean;
}

export interface ApiKnowledgeCrawlerReview {
  job_id: string;
  review_status: 'pending' | 'completed';
  review_mode: 'human' | 'agent';
  review_criteria: string;
  items: ApiKnowledgeCrawlerReviewItem[];
  pending: number;
  approved: number;
  rejected: number;
}

export interface ApiKnowledgeCrawlerReviewContent {
  item: ApiKnowledgeCrawlerReviewItem;
  content: string;
}

export async function getRagHealth(): Promise<ApiRagHealth> {
  return apiFetch<ApiRagHealth>('/api/v1/knowledge/health');
}

export async function listKnowledgeCrawlerPresets(): Promise<{ items: ApiKnowledgeCrawlerPreset[] }> {
  return apiFetch<{ items: ApiKnowledgeCrawlerPreset[] }>('/api/v1/knowledge/crawler/presets');
}

export async function getKnowledgeCrawlerDefaults(): Promise<ApiKnowledgeCrawlerDefaults> {
  return apiFetch<ApiKnowledgeCrawlerDefaults>('/api/v1/knowledge/crawler/defaults');
}

export async function listKnowledgeCrawlerJobs(params?: {
  knowledgeBaseId?: string;
  offset?: number;
  limit?: number;
}): Promise<ApiKnowledgeCrawlerJobList> {
  const search = new URLSearchParams({
    offset: String(params?.offset ?? 0),
    limit: String(params?.limit ?? 30),
  });
  if (params?.knowledgeBaseId) search.set('knowledge_base_id', params.knowledgeBaseId);
  return apiFetch<ApiKnowledgeCrawlerJobList>(`/api/v1/knowledge/crawler/jobs?${search.toString()}`);
}

export async function getKnowledgeCrawlerJob(
  jobId: string,
  knowledgeBaseId?: string,
): Promise<ApiKnowledgeCrawlerJob> {
  const search = new URLSearchParams();
  if (knowledgeBaseId) search.set('knowledge_base_id', knowledgeBaseId);
  const suffix = search.size ? `?${search.toString()}` : '';
  return apiFetch<ApiKnowledgeCrawlerJob>(
    `/api/v1/knowledge/crawler/jobs/${encodeURIComponent(jobId)}${suffix}`,
  );
}

export async function createKnowledgeCrawlerJob(params: {
  knowledgeBaseId: string;
  presetIds?: string[];
  urls?: string[];
  keywords?: string[];
  siteUrls?: string[];
  maxResultsPerKeyword: number;
  maxPagesPerSite: number;
  maxTotalPages: number;
  minContentChars: number;
  fetchDelaySeconds: number;
  maxRetries: number;
  retryBaseSeconds: number;
  force: boolean;
  reviewMode: 'human' | 'agent';
  reviewCriteria: string;
}): Promise<ApiKnowledgeCrawlerJob> {
  return apiFetch<ApiKnowledgeCrawlerJob>('/api/v1/knowledge/crawler/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      knowledge_base_id: params.knowledgeBaseId,
      preset_ids: params.presetIds ?? [],
      urls: params.urls ?? [],
      keywords: params.keywords ?? [],
      site_urls: params.siteUrls ?? [],
      max_results_per_keyword: params.maxResultsPerKeyword,
      max_pages_per_site: params.maxPagesPerSite,
      max_total_pages: params.maxTotalPages,
      min_content_chars: params.minContentChars,
      fetch_delay_seconds: params.fetchDelaySeconds,
      max_retries: params.maxRetries,
      retry_base_seconds: params.retryBaseSeconds,
      force: params.force,
      review_mode: params.reviewMode,
      review_criteria: params.reviewCriteria,
    }),
  });
}

export async function controlKnowledgeCrawlerJob(
  jobId: string,
  action: 'pause' | 'resume' | 'stop',
  knowledgeBaseId?: string,
): Promise<ApiKnowledgeCrawlerJob> {
  const search = new URLSearchParams();
  if (knowledgeBaseId) search.set('knowledge_base_id', knowledgeBaseId);
  const suffix = search.size ? `?${search.toString()}` : '';
  return apiFetch<ApiKnowledgeCrawlerJob>(
    `/api/v1/knowledge/crawler/jobs/${encodeURIComponent(jobId)}/${action}${suffix}`,
    { method: 'POST' },
  );
}

export async function getKnowledgeCrawlerReview(
  jobId: string,
  knowledgeBaseId?: string,
): Promise<ApiKnowledgeCrawlerReview> {
  const search = new URLSearchParams();
  if (knowledgeBaseId) search.set('knowledge_base_id', knowledgeBaseId);
  const suffix = search.size ? `?${search.toString()}` : '';
  return apiFetch<ApiKnowledgeCrawlerReview>(
    `/api/v1/knowledge/crawler/jobs/${encodeURIComponent(jobId)}/review${suffix}`,
  );
}

export async function getKnowledgeCrawlerReviewContent(
  jobId: string,
  itemId: string,
  knowledgeBaseId?: string,
): Promise<ApiKnowledgeCrawlerReviewContent> {
  const search = new URLSearchParams();
  if (knowledgeBaseId) search.set('knowledge_base_id', knowledgeBaseId);
  const suffix = search.size ? `?${search.toString()}` : '';
  return apiFetch<ApiKnowledgeCrawlerReviewContent>(
    `/api/v1/knowledge/crawler/jobs/${encodeURIComponent(jobId)}/review/items/${encodeURIComponent(itemId)}${suffix}`,
  );
}

export async function reviewKnowledgeCrawlerItems(
  jobId: string,
  action: 'approve' | 'reject',
  itemIds: string[],
  knowledgeBaseId?: string,
): Promise<ApiKnowledgeCrawlerReview> {
  const search = new URLSearchParams();
  if (knowledgeBaseId) search.set('knowledge_base_id', knowledgeBaseId);
  const suffix = search.size ? `?${search.toString()}` : '';
  return apiFetch<ApiKnowledgeCrawlerReview>(
    `/api/v1/knowledge/crawler/jobs/${encodeURIComponent(jobId)}/review${suffix}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, item_ids: itemIds }),
    },
  );
}

export async function listKnowledgeBases(): Promise<ApiKnowledgeBaseList> {
  return apiFetch<ApiKnowledgeBaseList>('/api/v1/knowledge/bases');
}

export async function getKnowledgeCapabilities(): Promise<ApiKnowledgeSourceCapabilities> {
  return apiFetch<ApiKnowledgeSourceCapabilities>('/api/v1/knowledge/capabilities');
}

export async function createKnowledgeBase(params: {
  name: string;
  description?: string;
  embeddingProfile?: string;
}): Promise<ApiKnowledgeBase> {
  return apiFetch<ApiKnowledgeBase>('/api/v1/knowledge/bases', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: params.name,
      description: params.description || null,
      embedding_profile: params.embeddingProfile ?? 'configured',
    }),
  });
}

export async function updateKnowledgeBase(
  knowledgeBaseId: string,
  params: { name?: string; description?: string | null },
): Promise<ApiKnowledgeBase> {
  return apiFetch<ApiKnowledgeBase>(
    `/api/v1/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    },
  );
}

export async function deleteKnowledgeBase(knowledgeBaseId: string): Promise<void> {
  await apiFetch<unknown>(
    `/api/v1/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}`,
    { method: 'DELETE' },
  );
}

export async function listKnowledgeDocuments(params: {
  knowledgeBaseId: string;
  offset?: number;
  limit?: number;
  status?: string;
  query?: string;
}): Promise<ApiKnowledgeDocumentList> {
  const search = new URLSearchParams({
    knowledge_base_id: params.knowledgeBaseId,
    offset: String(params.offset ?? 0),
    limit: String(params.limit ?? 20),
  });
  if (params.status) search.set('status', params.status);
  if (params.query?.trim()) search.set('query', params.query.trim());
  return apiFetch<ApiKnowledgeDocumentList>(`/api/v1/knowledge/documents?${search.toString()}`);
}

export async function getKnowledgeDocument(
  documentId: string,
  knowledgeBaseId: string,
): Promise<ApiKnowledgeDocument> {
  return apiFetch<ApiKnowledgeDocument>(
    `/api/v1/knowledge/documents/${encodeURIComponent(documentId)}?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}`,
  );
}

export async function getKnowledgeDocumentChunks(
  documentId: string,
  knowledgeBaseId: string,
): Promise<ApiKnowledgeChunk[]> {
  return apiFetch<ApiKnowledgeChunk[]>(
    `/api/v1/knowledge/documents/${encodeURIComponent(documentId)}/chunks?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}`,
  );
}

export async function updateKnowledgeDocument(
  documentId: string,
  knowledgeBaseId: string,
  params: { title?: string; original_filename?: string; metadata?: Record<string, unknown> | null },
): Promise<ApiKnowledgeDocument> {
  return apiFetch<ApiKnowledgeDocument>(
    `/api/v1/knowledge/documents/${encodeURIComponent(documentId)}?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    },
  );
}

export async function deleteKnowledgeDocument(
  documentId: string,
  knowledgeBaseId: string,
): Promise<void> {
  await apiFetch<unknown>(
    `/api/v1/knowledge/documents/${encodeURIComponent(documentId)}?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}`,
    { method: 'DELETE' },
  );
}

export async function uploadKnowledgeDocument(
  knowledgeBaseId: string,
  file: File,
): Promise<ApiKnowledgeIngestCreated> {
  const query = new URLSearchParams({ filename: file.name });
  return apiFetch<ApiKnowledgeIngestCreated>(
    `/api/v1/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}/documents?${query.toString()}`,
    {
      method: 'POST',
      headers: { 'Content-Type': file.type || 'application/octet-stream' },
      body: file,
    },
  );
}

export async function getKnowledgeIngestJob(
  knowledgeBaseId: string,
  jobId: string,
): Promise<ApiKnowledgeIngestJob> {
  return apiFetch<ApiKnowledgeIngestJob>(
    `/api/v1/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}/ingest/jobs/${encodeURIComponent(jobId)}`,
  );
}

export async function resolveKnowledgeIngestConflict(
  knowledgeBaseId: string,
  jobId: string,
  keepDocumentId: string,
): Promise<ApiKnowledgeIngestJob> {
  return apiFetch<ApiKnowledgeIngestJob>(
    `/api/v1/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}/ingest/jobs/${encodeURIComponent(jobId)}/resolve`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keep_document_id: keepDocumentId }),
    },
  );
}

export async function searchKnowledge(params: {
  query: string;
  knowledgeBaseId: string;
  topK?: number;
  retrievalMode?: 'auto' | 'focused' | 'comprehensive' | 'enumeration';
  enableQueryRewrite?: boolean;
  enableVector?: boolean;
  enableKeyword?: boolean;
  enableRerank?: boolean;
}): Promise<ApiKnowledgeSearchResponse> {
  return apiFetch<ApiKnowledgeSearchResponse>('/api/v1/knowledge/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query: params.query,
      knowledge_base_id: params.knowledgeBaseId,
      top_k: params.topK ?? 8,
      retrieval_mode: params.retrievalMode ?? 'auto',
      enable_query_rewrite: params.enableQueryRewrite ?? false,
      enable_vector: params.enableVector ?? true,
      enable_keyword: params.enableKeyword ?? true,
      enable_rerank: params.enableRerank ?? true,
    }),
  });
}

export async function answerKnowledge(params: {
  query: string;
  knowledgeBaseId: string;
  topK?: number;
  retrievalMode?: 'auto' | 'focused' | 'comprehensive' | 'enumeration';
  enableQueryRewrite?: boolean;
  enableVector?: boolean;
  enableKeyword?: boolean;
  enableRerank?: boolean;
}): Promise<ApiKnowledgeAnswerResponse> {
  return apiFetch<ApiKnowledgeAnswerResponse>('/api/v1/knowledge/answer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query: params.query,
      knowledge_base_id: params.knowledgeBaseId,
      top_k: params.topK ?? 8,
      retrieval_mode: params.retrievalMode ?? 'auto',
      enable_query_rewrite: params.enableQueryRewrite ?? true,
      enable_vector: params.enableVector ?? true,
      enable_keyword: params.enableKeyword ?? true,
      enable_rerank: params.enableRerank ?? true,
    }),
  });
}

export async function getConfigOverrides(): Promise<Record<string, string>> {
  return apiFetch<Record<string, string>>('/api/v1/admin/config/overrides');
}

export async function setConfigOverride(overrides: Record<string, string>): Promise<{ applied: number; total_overrides: number }> {
  return apiFetch<{ applied: number; total_overrides: number }>('/api/v1/admin/config/override', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(overrides),
  });
}

export async function clearConfigOverrides(): Promise<void> {
  await apiFetch<unknown>('/api/v1/admin/config/override', { method: 'DELETE' });
}

export async function removeConfigOverride(key: string): Promise<void> {
  await apiFetch<unknown>(`/api/v1/admin/config/override/${encodeURIComponent(key)}`, { method: 'DELETE' });
}

// ─── Auth ──────────────────────────────────────────────────────────────────

export interface ApiAuthResult {
  token: string;
  user: ApiUser;
  expiresIn: number;
}

const AUTH_TOKEN_KEY = 'sentinel_auth_token_v1';

export function getStoredAuthToken(): string | null {
  try { return localStorage.getItem(AUTH_TOKEN_KEY); } catch { return null; }
}

export function storeAuthToken(token: string): void {
  try { localStorage.setItem(AUTH_TOKEN_KEY, token); } catch { /* quota */ }
}

export function clearAuthToken(): void {
  try { localStorage.removeItem(AUTH_TOKEN_KEY); } catch { /* quota */ }
}

/**
 * POST /api/v1/auth/login — validate credentials against backend.
 * Returns auth result on success; throws on failure (network or wrong credentials).
 */
export async function backendLogin(username: string, password: string): Promise<ApiAuthResult> {
  const resp = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
    signal: AbortSignal.timeout(6000),
  });
  const json = await resp.json() as { code: string; data: ApiAuthResult; message: string };
  if (json.code !== '0' && json.code !== 'SUCCESS' && json.code !== '200') {
    throw new Error(json.message ?? '登录失败');
  }
  return normalizeAuthResult(json.data);
}

/**
 * POST /api/v1/auth/logout — notify backend (stateless; mainly for completeness).
 */
export async function backendLogout(): Promise<void> {
  const token = getStoredAuthToken();
  if (!token) return;
  try {
    await fetch(`${API_BASE}/api/v1/auth/logout`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: AbortSignal.timeout(4000),
    });
  } catch { /* best-effort */ }
  clearAuthToken();
}

/**
 * POST /api/v1/auth/register — create new VIEWER account with password, then return auth token.
 * Throws on failure (username taken, weak password, or network error).
 */
export async function backendRegister(username: string, password: string, displayName?: string): Promise<ApiAuthResult> {
  const resp = await fetch(`${API_BASE}/api/v1/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, displayName }),
    signal: AbortSignal.timeout(6000),
  });
  const json = await resp.json() as { code: string; data: ApiAuthResult; message: string };
  if (json.code !== '0' && json.code !== 'SUCCESS' && json.code !== '200') {
    throw new Error(json.message ?? '注册失败');
  }
  return normalizeAuthResult(json.data);
}

/**
 * PUT /api/v1/auth/me/password — change own password (requires Bearer token + old password).
 */
export async function changeMyPassword(oldPassword: string, newPassword: string): Promise<void> {
  const token = getStoredAuthToken();
  if (!token) throw new Error('未登录');
  const resp = await fetch(`${API_BASE}/api/v1/auth/me/password`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ oldPassword, newPassword }),
    signal: AbortSignal.timeout(6000),
  });
  const json = await resp.json() as { code: string; message: string };
  if (json.code !== '0' && json.code !== 'SUCCESS') throw new Error(json.message ?? '修改失败');
}

/**
 * PUT /api/v1/auth/me — update own profile (displayName, email).
 */
export async function updateMyProfile(params: { displayName?: string; email?: string }): Promise<ApiUser> {
  const token = getStoredAuthToken();
  if (!token) throw new Error('未登录');
  const resp = await fetch(`${API_BASE}/api/v1/auth/me`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(params),
    signal: AbortSignal.timeout(6000),
  });
  const json = await resp.json() as { code: string; data: ApiUser; message: string };
  if (json.code !== '0' && json.code !== 'SUCCESS') throw new Error(json.message ?? '更新失败');
  return normalizeUser(json.data);
}

/**
 * GET /api/v1/auth/me — fetch current user from stored token.
 */
export async function getMe(): Promise<ApiUser> {
  const token = getStoredAuthToken();
  if (!token) throw new Error('未登录');
  const resp = await fetch(`${API_BASE}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(5000),
  });
  const json = await resp.json() as { code: string; data: ApiUser; message: string };
  if (json.code !== '0' && json.code !== 'SUCCESS' && json.code !== '200') {
    throw new Error(json.message ?? '未授权');
  }
  return normalizeUser(json.data);
}
