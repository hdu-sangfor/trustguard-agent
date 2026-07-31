import { useEffect, useState } from 'react';
import {
  getTaskKnowledgeChunks,
  type ApiTaskKnowledgeChunk,
} from '@/shared/lib/api';

interface KnowledgeSearchDetailsProps {
  taskId: string;
  chunkIds: string[];
  resourceRefs: string[];
}

function sourceLabel(chunk: ApiTaskKnowledgeChunk): string {
  const source = chunk.filename || chunk.sourceType || '知识库片段';
  return chunk.pageNo ? `${source} · 第 ${chunk.pageNo} 页` : source;
}

function KnowledgeChunkCard({ chunk, initiallyOpen }: { chunk: ApiTaskKnowledgeChunk; initiallyOpen: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <article className="rounded-md border log-soft-panel overflow-hidden">
      <button
        type="button"
        className="flex w-full items-start justify-between gap-3 px-3 py-2 text-left log-hover-row"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="min-w-0">
          <span className="log-accent-text font-semibold">
            {chunk.title || chunk.filename || '知识片段'}
          </span>
          <span className="ml-2 log-muted-text text-[11px]">{sourceLabel(chunk)}</span>
        </span>
        <span className="log-faint-text shrink-0 text-[11px]">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="border-t px-3 py-2.5" style={{ borderColor: 'var(--tg-panel-border)' }}>
          <div className="mb-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] log-muted-text">
            {chunk.scope && <span>scope: {chunk.scope}</span>}
            {chunk.contentType && <span>类型: {chunk.contentType}</span>}
            <span>{chunk.textLength} 字符</span>
            <span className="font-mono">{chunk.chunkId}</span>
          </div>
          <div className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words leading-relaxed text-[12px]">
            {chunk.preview}
            {chunk.truncated && <span className="log-faint-text">{`\n…内容过长，预览已截断`}</span>}
          </div>
        </div>
      )}
    </article>
  );
}

export function KnowledgeSearchDetails({
  taskId,
  chunkIds,
  resourceRefs,
}: KnowledgeSearchDetailsProps) {
  const chunkKey = chunkIds.join('\n');
  const [chunks, setChunks] = useState<ApiTaskKnowledgeChunk[]>([]);
  const [missingCount, setMissingCount] = useState(0);
  const [loading, setLoading] = useState(chunkIds.length > 0);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    const requestedIds = chunkKey ? chunkKey.split('\n') : [];
    if (!taskId || requestedIds.length === 0) {
      setChunks([]);
      setMissingCount(0);
      setLoading(false);
      setError('');
      return () => { active = false; };
    }

    setLoading(true);
    setError('');
    getTaskKnowledgeChunks(taskId, requestedIds)
      .then((result) => {
        if (!active) return;
        setChunks(result.chunks ?? []);
        setMissingCount(result.missingChunkIds?.length ?? 0);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setChunks([]);
        setMissingCount(0);
        setError(reason instanceof Error ? reason.message : '知识片段读取失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [chunkKey, taskId]);

  if (loading) {
    return <div className="log-muted-text py-2">正在读取 {chunkIds.length} 个知识片段…</div>;
  }

  if (error) {
    return (
      <div className="space-y-2">
        <div className="log-warning-text">{error}</div>
        <div className="log-muted-text break-all">已保留 {resourceRefs.length} 个 MCP 资源引用。</div>
      </div>
    );
  }

  if (chunks.length === 0) {
    return (
      <div className="space-y-2">
        <div className="log-muted-text">
          {chunkIds.length > 0 ? '知识片段已经过期或不可读取。' : '本次检索未物化知识正文。'}
        </div>
        {resourceRefs.length > 0 && (
          <div className="log-faint-text break-all">MCP 资源引用：{resourceRefs.length} 个</div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="log-success-text font-semibold">已读取 {chunks.length} 个知识片段</span>
        {missingCount > 0 && <span className="log-warning-text">{missingCount} 个引用不可用</span>}
      </div>
      {chunks.map((chunk, index) => (
        <KnowledgeChunkCard
          key={chunk.chunkId}
          chunk={chunk}
          initiallyOpen={index === 0}
        />
      ))}
    </div>
  );
}
