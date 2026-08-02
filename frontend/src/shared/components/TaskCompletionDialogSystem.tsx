import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bot, CheckCircle2, ClipboardCheck, Database, ShieldCheck, XCircle } from "lucide-react";

import { useAppSession } from "@/shared/context/AppSessionContext";
import {
  getKnowledgeCrawlerReview,
  listKnowledgeCrawlerJobs,
  type ApiKnowledgeCrawlerJob,
  type ApiKnowledgeCrawlerReview,
} from "@/shared/lib/api";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/shared/ui/dialog";
import "./TaskCompletionDialogSystem.css";

const SEEN_KEY = "sentinel_completion_notices_v1";

interface ReviewerSession {
  identity: string;
  storageKey: string;
}

function currentReviewer(): ReviewerSession | null {
  try {
    const raw = localStorage.getItem("sentinel_session_v1");
    const session = raw
      ? JSON.parse(raw) as { username?: string; role?: string }
      : null;
    const username = String(session?.username || "").trim().toLowerCase();
    const role = String(session?.role || "").toUpperCase();
    if (!username || !["ADMIN", "OPERATOR"].includes(role)) return null;
    return {
      identity: username,
      storageKey: `${SEEN_KEY}:${encodeURIComponent(username)}`,
    };
  } catch {
    return null;
  }
}

function readSeen(storageKey: string): { exists: boolean; values: Set<string> } {
  try {
    const raw = localStorage.getItem(storageKey);
    if (raw === null) return { exists: false, values: new Set() };
    const parsed = JSON.parse(raw) as unknown;
    return {
      exists: true,
      values: new Set(Array.isArray(parsed) ? parsed.map(String) : []),
    };
  } catch {
    return { exists: false, values: new Set() };
  }
}

function writeSeen(storageKey: string, seen: Set<string>) {
  localStorage.setItem(storageKey, JSON.stringify([...seen].slice(-300)));
}

function markSeen(storageKey: string, jobId: string) {
  const seen = readSeen(storageKey).values;
  seen.add(`crawler:${jobId}`);
  writeSeen(storageKey, seen);
}

function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

interface CompletionNotice {
  job: ApiKnowledgeCrawlerJob;
  review: ApiKnowledgeCrawlerReview | null;
  seenStorageKey: string;
}

export default function TaskCompletionDialogSystem() {
  const navigate = useNavigate();
  const { loggedIn } = useAppSession();
  const [notice, setNotice] = useState<CompletionNotice | null>(null);
  const initializedReviewer = useRef<string | null>(null);

  const checkCompletions = useCallback(async () => {
    if (!loggedIn || notice) return;
    const reviewer = currentReviewer();
    if (!reviewer) return;
    try {
      const result = await listKnowledgeCrawlerJobs({ limit: 50 });
      const eligibleJobs = (result.items ?? []).filter((job) => {
        const awaitingHumanReview = job.progress.review_status === "pending"
          && numberValue(job.progress.pending_review) > 0;
        const agentReviewCompleted = job.config.review_mode === "agent"
          && job.progress.review_status === "completed";
        return job.status === "succeeded"
          && (awaitingHumanReview || agentReviewCompleted);
      });

      const seenState = readSeen(reviewer.storageKey);
      if (initializedReviewer.current !== reviewer.identity) {
        initializedReviewer.current = reviewer.identity;
        if (!seenState.exists) {
          // Establish a per-account baseline so deploying the watcher does not
          // replay every historical completion. Running jobs remain unseen and
          // will notify normally when a later poll observes their completion.
          writeSeen(
            reviewer.storageKey,
            new Set(eligibleJobs.map((job) => `crawler:${job.id}`)),
          );
          return;
        }
      }

      const candidate = eligibleJobs.find(
        (job) => !seenState.values.has(`crawler:${job.id}`),
      );
      if (candidate) {
        let review: ApiKnowledgeCrawlerReview | null = null;
        try {
          review = await getKnowledgeCrawlerReview(candidate.id, candidate.knowledge_base_id);
        } catch {
          // The job progress still contains enough information for a useful notice.
        }
        setNotice({ job: candidate, review, seenStorageKey: reviewer.storageKey });
      }
    } catch {
      // Completion notifications are best effort and must not block the app.
    }
  }, [loggedIn, notice]);

  useEffect(() => {
    if (!loggedIn) {
      setNotice(null);
      initializedReviewer.current = null;
      return;
    }
    void checkCompletions();
    const timer = window.setInterval(() => void checkCompletions(), 8000);
    return () => window.clearInterval(timer);
  }, [checkCompletions, loggedIn]);

  const close = () => {
    if (notice) markSeen(notice.seenStorageKey, notice.job.id);
    setNotice(null);
  };

  const review = () => {
    if (!notice) return;
    markSeen(notice.seenStorageKey, notice.job.id);
    const path = `/knowledge/collect/review/${encodeURIComponent(notice.job.id)}?knowledge_base_id=${encodeURIComponent(notice.job.knowledge_base_id)}`;
    setNotice(null);
    navigate(path);
  };

  const category = typeof notice?.job.config.target_category === "string"
    ? notice.job.config.target_category
    : "自定义数据采集";
  const pending = notice?.review?.pending ?? numberValue(notice?.job.progress.pending_review);
  const approved = notice?.review?.approved ?? numberValue(notice?.job.progress.agent_review_summary && (notice.job.progress.agent_review_summary as Record<string, unknown>).approved);
  const rejected = notice?.review?.rejected ?? numberValue(notice?.job.progress.agent_review_summary && (notice.job.progress.agent_review_summary as Record<string, unknown>).rejected);
  const cleaned = numberValue(notice?.job.progress.cleaned);
  const agentReview = notice?.job.config.review_mode === "agent";
  const agentCompleted = agentReview && notice?.job.progress.review_status === "completed";

  return (
    <Dialog open={Boolean(notice)} onOpenChange={(open) => { if (!open) close(); }}>
      <DialogContent className="completion-dialog" showCloseButton={false}>
        <div className="completion-dialog-icon"><CheckCircle2 size={27} /></div>
        <DialogHeader>
          <div className="completion-dialog-eyebrow"><ShieldCheck size={13} /> TASK COMPLETED</div>
          <DialogTitle className="completion-dialog-title">{agentCompleted ? "Agent 审核已完成" : "数据采集已完成"}</DialogTitle>
          <DialogDescription className="completion-dialog-description">
            {agentCompleted
              ? "Agent 已完成全部内容审核。通过的数据已提交入库流程，未通过的数据已驳回，本次没有待人工复核内容。"
              : agentReview
                ? "Agent 已完成自动审核，仍有低置信度或异常数据需要人工复核，审核通过前不会入库。"
              : "清洗后的数据正在等待人工审核，审核通过前不会进入 RAG 知识库。"}
          </DialogDescription>
        </DialogHeader>
        <div className="completion-dialog-summary">
          <div><Database size={14} /><span>知识分类</span><strong>{category}</strong></div>
          {agentReview && <div><Bot size={14} /><span>审核方式</span><strong>{agentCompleted ? "Agent 自动审核" : "Agent + 人工兜底"}</strong></div>}
          {agentCompleted ? (
            <>
              <div className="completion-dialog-approved"><CheckCircle2 size={14} /><span>已通过</span><strong>{approved} 条</strong></div>
              <div className="completion-dialog-rejected"><XCircle size={14} /><span>已驳回</span><strong>{rejected} 条</strong></div>
            </>
          ) : <div><ClipboardCheck size={14} /><span>待审核</span><strong>{pending} 条</strong></div>}
          <div><CheckCircle2 size={14} /><span>已清洗</span><strong>{cleaned} 条</strong></div>
        </div>
        <DialogFooter className="completion-dialog-actions">
          <button type="button" className="completion-dialog-later" onClick={close}>{agentCompleted ? "知道了" : "稍后处理"}</button>
          <button type="button" className="completion-dialog-review" onClick={review}><ClipboardCheck size={14} /> {agentCompleted ? "查看审核结果" : "去审核"}</button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
