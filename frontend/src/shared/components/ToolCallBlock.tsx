import { useState, useEffect, useMemo } from "react";
import type { ApiAgentActivity } from "@/shared/lib/api";

const STATUS_ICONS: Record<string, string> = {
  pending: "○",
  running: "●",
  done: "✓",
  blocked: "×",
};

const STATUS_COLORS: Record<string, string> = {
  pending: "var(--tg-text-muted)",
  running: "var(--tg-accent)",
  done: "#34d399",
  blocked: "#f87171",
};

function formatDuration(ms: number | null): string {
  if (ms === null) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

interface Props {
  activities: ApiAgentActivity[];
  live?: boolean;
}

export default function ToolCallBlock({ activities, live = false }: Props) {
  const [expanded, setExpanded] = useState(live);
  const [now, setNow] = useState(() => Date.now());
  const hasRunning = activities.some((a) => a.status === "running");
  const hasBlocked = !hasRunning && activities.some((a) => a.status === "blocked");

  useEffect(() => {
    setExpanded(live);
  }, [live]);

  // Refresh timestamps while running
  useEffect(() => {
    if (!hasRunning) return;
    const iv = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(iv);
  }, [hasRunning]);

  const totalDuration = useMemo(() => {
    const starts = activities
      .map((a) => a.startedAt ?? a.timestamp)
      .filter(Boolean)
      .map((s) => new Date(s).getTime())
      .filter((t) => !isNaN(t));
    if (starts.length === 0) return null;
    const first = Math.min(...starts);
    const ends = activities
      .map((a) => a.finishedAt)
      .filter(Boolean)
      .map((s) => new Date(s).getTime())
      .filter((t) => !isNaN(t));
    if (hasRunning) return now - first;
    if (ends.length === 0) return null;
    return Math.max(...ends) - first;
  }, [activities, hasRunning, now]);

  const runningLabel = useMemo(() => {
    const r = [...activities].reverse().find((a) => a.status === "running");
    return r?.title;
  }, [activities]);

  return (
    <div className="tg-toolcall">
      <button
        type="button"
        className="tg-toolcall-header"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span className={`tg-toolcall-icon${hasRunning ? " running" : hasBlocked ? " blocked" : ""}`}>
          {hasRunning ? "●" : hasBlocked ? "×" : "✓"}
        </span>
        <span className="tg-toolcall-title">
          <strong>{hasRunning ? `正在执行${runningLabel ? `：${runningLabel}` : "..."}` : hasBlocked ? "步骤执行已停止" : `已完成 ${activities.length} 个步骤`}</strong>
          {totalDuration !== null && <small>{formatDuration(totalDuration)}</small>}
        </span>
        <span className={`tg-toolcall-chevron${expanded ? " expanded" : ""}`} />
      </button>
      <div className={`tg-toolcall-body-shell${expanded ? " expanded" : ""}`} aria-hidden={!expanded}>
        <div className="tg-toolcall-body">
          {activities.map((activity) => (
            <div key={activity.id} className={`tg-toolcall-item ${activity.status}`}>
              <span className="tg-toolcall-item-icon" style={{ color: STATUS_COLORS[activity.status] ?? "var(--tg-text-muted)" }}>
                {STATUS_ICONS[activity.status] ?? "○"}
              </span>
              <div className="tg-toolcall-item-content">
                <span className="tg-toolcall-item-title">{activity.title}</span>
                {activity.detail && <span className="tg-toolcall-item-detail">{activity.detail}</span>}
              </div>
              {activity.durationMs != null && (
                <span className="tg-toolcall-item-duration">{formatDuration(activity.durationMs)}</span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
