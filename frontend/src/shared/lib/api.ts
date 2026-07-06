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
  if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
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
