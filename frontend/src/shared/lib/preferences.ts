export type ThemeMode = "dark" | "light";
export type TaskViewMode = "orbit" | "list";

export const PREFERENCES_CHANGED_EVENT = "sentinel_preferences_changed";

const THEME_KEY = "sentinel_theme_mode_v1";
const TASK_VIEW_KEY = "sentinel_task_view_mode_v1";

function canUseStorage(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

function emitPreferenceChanged(name: string, value: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(PREFERENCES_CHANGED_EVENT, { detail: { name, value } }));
}

export function readThemeMode(): ThemeMode {
  if (!canUseStorage()) return "light";
  try {
    const raw = localStorage.getItem(THEME_KEY);
    if (raw === "dark" || raw === "light") return raw;
    return "light";
  } catch {
    return "light";
  }
}

export function applyThemeMode(theme: ThemeMode): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.classList.toggle("light", theme === "light");
  document.documentElement.style.colorScheme = theme;
  document.body.style.background = "var(--tg-page-bg)";
}

export function writeThemeMode(theme: ThemeMode): void {
  if (canUseStorage()) {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore quota */
    }
  }
  applyThemeMode(theme);
  emitPreferenceChanged("theme", theme);
}

export function readTaskViewMode(): TaskViewMode | null {
  if (!canUseStorage()) return null;
  try {
    const raw = localStorage.getItem(TASK_VIEW_KEY);
    return raw === "orbit" || raw === "list" ? raw : null;
  } catch {
    return null;
  }
}

export function writeTaskViewMode(mode: TaskViewMode): void {
  if (canUseStorage()) {
    try {
      localStorage.setItem(TASK_VIEW_KEY, mode);
    } catch {
      /* ignore quota */
    }
  }
  emitPreferenceChanged("taskView", mode);
}
