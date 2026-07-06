/** Must match `src/constants/orbitTasksStorage.ts` in the host app. */
export const SENTINEL_ORBIT_TASKS_KEY = 'sentinel_orbit_tasks_v1';

export const ORBIT_TASKS_UPDATED_EVENT = 'sentinel-orbit-tasks-updated';

/** Written by TasksPage when user clicks "查看日志"; read+cleared by CRTerminal on mount. */
export const PENDING_LOG_TASK_KEY = 'sentinel_pending_log_task_id';

export type StoredOrbitTask = {
  id: string;
  name: string;
  desc: string;
  url: string;
  log?: string;
  createdAt: number;
  /** Last backend-synced update time (epoch ms). */
  updatedAt?: number;
  status: string;
  /** Current execution phase from backend, e.g. RECON / VULN_SCAN / EXPLOIT */
  currentPhase?: string;
};

export function readStoredOrbitTasks(): StoredOrbitTask[] {
  try {
    const raw = localStorage.getItem(SENTINEL_ORBIT_TASKS_KEY);
    if (!raw) return [];
    const p = JSON.parse(raw) as unknown;
    return Array.isArray(p) ? (p as StoredOrbitTask[]) : [];
  } catch {
    return [];
  }
}
