export function parseTimestampMs(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;

  const text = value.trim();
  if (!text) return null;

  const numeric = Number(text);
  if (Number.isFinite(numeric) && numeric > 0) {
    return numeric > 1e12 ? numeric : numeric * 1000;
  }

  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(text);
  const normalized = hasZone ? text : `${text.replace(" ", "T")}Z`;
  const ms = Date.parse(normalized);
  return Number.isFinite(ms) ? ms : null;
}

export function formatLocalDateTime(value: string | number | null | undefined): string {
  const ms = parseTimestampMs(value);
  if (ms == null) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(ms));
}

export function formatLocalTime(value: string | number | null | undefined): string {
  const ms = parseTimestampMs(value);
  if (ms == null) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(ms));
}

export function formatElapsed(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) {
    const rm = m % 60;
    return rm > 0 ? `${h}h${rm}m` : `${h}h`;
  }
  const d = Math.floor(h / 24);
  const rh = h % 24;
  return rh > 0 ? `${d}d${rh}h` : `${d}d`;
}

export function elapsedForStatus(params: {
  createdAt: number;
  updatedAt?: number;
  status: "not_started" | "running" | "paused" | "failed" | "finished";
  now?: number;
}): string {
  const { createdAt, updatedAt, status, now = Date.now() } = params;
  if (status === "not_started") return "";
  const end = status === "finished" || status === "failed"
    ? (updatedAt && updatedAt >= createdAt ? updatedAt : createdAt)
    : now;
  return formatElapsed(end - createdAt);
}
