import { useState, type CSSProperties } from "react";
import { Bot, MessageSquareText, Quote, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import {
  answerKnowledge,
  type ApiKnowledgeAnswerResponse,
  type ApiKnowledgeBase,
} from "@/shared/lib/api";

type RetrievalMode = "auto" | "focused" | "comprehensive" | "enumeration";

const panelStyle: CSSProperties = {
  background: "var(--tg-panel-bg)",
  border: "1px solid var(--tg-panel-border)",
  borderRadius: 10,
  boxShadow: "var(--tg-shadow)",
};

const inputStyle: CSSProperties = {
  width: "100%",
  background: "var(--tg-input-bg)",
  border: "1px solid var(--tg-panel-border)",
  borderRadius: 7,
  color: "var(--tg-text)",
  fontFamily: "monospace",
  fontSize: 13,
  outline: "none",
};

interface KnowledgeAnswerPanelProps {
  bases: ApiKnowledgeBase[];
  selectedBaseId: string;
  onSelectedBaseIdChange: (knowledgeBaseId: string) => void;
}

export default function KnowledgeAnswerPanel({
  bases,
  selectedBaseId,
  onSelectedBaseIdChange,
}: KnowledgeAnswerPanelProps) {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<RetrievalMode>("auto");
  const [topK, setTopK] = useState(8);
  const [enableVector, setEnableVector] = useState(true);
  const [enableKeyword, setEnableKeyword] = useState(true);
  const [enableRerank, setEnableRerank] = useState(true);
  const [enableRewrite, setEnableRewrite] = useState(true);
  const [answering, setAnswering] = useState(false);
  const [result, setResult] = useState<ApiKnowledgeAnswerResponse | null>(null);

  const runAnswer = async () => {
    const normalizedQuestion = question.trim();
    if (!selectedBaseId) {
      toast.error("请先选择知识库");
      return;
    }
    if (!normalizedQuestion) {
      toast.error("请输入需要回答的问题");
      return;
    }
    if (!enableVector && !enableKeyword) {
      toast.error("请至少启用一种检索方式");
      return;
    }

    setAnswering(true);
    setResult(null);
    try {
      setResult(await answerKnowledge({
        query: normalizedQuestion,
        knowledgeBaseId: selectedBaseId,
        topK,
        retrievalMode: mode,
        enableQueryRewrite: enableRewrite,
        enableVector,
        enableKeyword,
        enableRerank,
      }));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "知识问答失败");
    } finally {
      setAnswering(false);
    }
  };

  return (
    <section className="knowledge-answer-layout">
      <div style={{ ...panelStyle, padding: 18, alignSelf: "start" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 17 }}>
          <MessageSquareText size={17} color="var(--tg-accent)" />
          <div>
            <div style={{ color: "var(--tg-text)", fontSize: 14, fontWeight: 700 }}>基于知识库回答</div>
            <div style={{ color: "var(--tg-text-faint)", fontSize: 10, marginTop: 2 }}>
              单轮检索增强生成 · 强制引用与拒答
            </div>
          </div>
        </div>

        <label style={{ display: "block", color: "var(--tg-text-muted)", fontSize: 12, marginBottom: 7 }}>
          知识库范围
        </label>
        <select
          value={selectedBaseId}
          onChange={(event) => {
            onSelectedBaseIdChange(event.target.value);
            setResult(null);
          }}
          disabled={bases.length === 0}
          style={{ ...inputStyle, padding: "9px 11px" }}
        >
          {bases.length === 0 && <option value="">没有可用知识库</option>}
          {bases.map((base) => (
            <option key={base.id} value={base.id}>
              {base.name} · {base.document_count} docs
            </option>
          ))}
        </select>

        <label style={{ display: "block", color: "var(--tg-text-muted)", fontSize: 12, margin: "16px 0 7px" }}>
          问题
        </label>
        <textarea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") void runAnswer();
          }}
          placeholder="例如：Apache Shiro RememberMe 漏洞的验证重点和修复建议是什么？"
          rows={7}
          style={{ ...inputStyle, padding: 11, resize: "vertical", lineHeight: 1.6 }}
        />

        <div style={{ display: "grid", gridTemplateColumns: "1fr 95px", gap: 10, marginTop: 13 }}>
          <select
            value={mode}
            onChange={(event) => setMode(event.target.value as RetrievalMode)}
            style={{ ...inputStyle, padding: "8px 10px" }}
          >
            <option value="auto">自动规划</option>
            <option value="focused">聚焦回答</option>
            <option value="comprehensive">综合回答</option>
            <option value="enumeration">枚举回答</option>
          </select>
          <input
            type="number"
            min={1}
            max={20}
            value={topK}
            onChange={(event) => setTopK(Math.max(1, Math.min(20, Number(event.target.value) || 1)))}
            aria-label="召回数量"
            style={{ ...inputStyle, padding: "8px 10px" }}
          />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 9, marginTop: 15 }}>
          {[
            ["向量召回", enableVector, setEnableVector],
            ["关键词召回", enableKeyword, setEnableKeyword],
            ["结果重排", enableRerank, setEnableRerank],
            ["Query 改写", enableRewrite, setEnableRewrite],
          ].map(([label, value, setter]) => (
            <label
              key={label as string}
              style={{ color: "var(--tg-text-muted)", fontSize: 12, display: "flex", gap: 7, alignItems: "center" }}
            >
              <input
                type="checkbox"
                checked={value as boolean}
                onChange={(event) => (setter as (next: boolean) => void)(event.target.checked)}
              />
              {label as string}
            </label>
          ))}
        </div>

        <button
          type="button"
          onClick={() => void runAnswer()}
          disabled={answering || !selectedBaseId}
          style={{
            marginTop: 18,
            width: "100%",
            border: "1px solid rgba(34,211,238,0.42)",
            borderRadius: 7,
            padding: "10px 14px",
            background: "var(--tg-accent-soft)",
            color: "var(--tg-accent)",
            fontFamily: "monospace",
            fontWeight: 700,
            cursor: answering ? "wait" : "pointer",
          }}
        >
          {answering ? "正在检索证据并生成回答…" : "生成回答  CTRL + ENTER"}
        </button>
      </div>

      <div style={{ minWidth: 0 }}>
        {!result && (
          <div style={{ ...panelStyle, minHeight: 400, display: "grid", placeItems: "center", padding: 30, textAlign: "center" }}>
            <div>
              <Bot size={38} color="var(--tg-accent)" style={{ margin: "0 auto 14px", opacity: 0.78 }} />
              <h2 style={{ color: "var(--tg-text)", fontSize: 16, margin: 0 }}>等待基于证据的回答</h2>
              <p style={{ color: "var(--tg-text-muted)", fontSize: 12, maxWidth: 460, lineHeight: 1.75 }}>
                RAG 会先检索证据，再生成带引用的回答。证据不足时应明确拒答，而不是补全未经验证的结论。
              </p>
            </div>
          </div>
        )}

        {result && (
          <>
            <div style={{ ...panelStyle, padding: 14, display: "flex", gap: 15, flexWrap: "wrap", color: "var(--tg-text-muted)", fontSize: 11 }}>
              <span>
                状态{" "}
                <strong style={{ color: result.status === "answered" ? "var(--tg-success)" : "var(--tg-warning)" }}>
                  {result.status}
                </strong>
              </span>
              <span>总耗时 <strong style={{ color: "var(--tg-text)" }}>{result.total_time_ms.toFixed(1)} ms</strong></span>
              <span>检索 <strong style={{ color: "var(--tg-text)" }}>{result.retrieval_time_ms.toFixed(1)} ms</strong></span>
              <span>生成 <strong style={{ color: "var(--tg-text)" }}>{result.generation_time_ms.toFixed(1)} ms</strong></span>
              <span>证据 <strong style={{ color: "var(--tg-text)" }}>{result.context_chunk_count}/{result.retrieved_count}</strong></span>
              <span>上下文 <strong style={{ color: "var(--tg-text)" }}>{result.context_token_count} tokens</strong></span>
              {result.model && <span>模型 <strong style={{ color: "var(--tg-text)" }}>{result.model}</strong></span>}
              {result.usage && <span>用量 <strong style={{ color: "var(--tg-text)" }}>{result.usage.total_tokens} tokens</strong></span>}
            </div>

            <article
              style={{
                ...panelStyle,
                marginTop: 12,
                padding: 20,
                borderColor: result.status === "answered" ? "var(--tg-panel-border)" : "rgba(250,204,21,0.38)",
              }}
            >
              <div style={{ display: "flex", gap: 9, alignItems: "center" }}>
                {result.status === "answered" ? (
                  <Bot size={18} color="var(--tg-accent)" />
                ) : (
                  <ShieldAlert size={18} color="var(--tg-warning)" />
                )}
                <span style={{ color: result.status === "answered" ? "var(--tg-accent)" : "var(--tg-warning)", fontSize: 11, fontFamily: "monospace" }}>
                  {result.status === "answered" ? "GROUNDED ANSWER" : "INSUFFICIENT EVIDENCE"}
                </span>
              </div>
              <p style={{ color: "var(--tg-text)", fontSize: 14, lineHeight: 1.85, whiteSpace: "pre-wrap", margin: "15px 0 0" }}>
                {result.answer}
              </p>
              {(result.abstention_reason || result.coverage_warning) && (
                <div style={{ marginTop: 15, padding: 12, borderRadius: 7, background: "var(--tg-warning-soft)", color: "var(--tg-warning)", fontSize: 12 }}>
                  {result.coverage_warning || `拒答原因：${result.abstention_reason}`}
                </div>
              )}
              {result.degraded_components.length > 0 && (
                <div style={{ marginTop: 12, color: "var(--tg-warning)", fontSize: 11 }}>
                  降级组件：{result.degraded_components.join("、")}
                </div>
              )}
            </article>

            <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
              {result.citations.map((citation) => (
                <article key={`${citation.citation_id}-${citation.chunk_id}`} style={{ ...panelStyle, padding: 16 }}>
                  <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                    <Quote size={17} color="var(--tg-accent)" style={{ flexShrink: 0, marginTop: 2 }} />
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ color: "var(--tg-text)", fontSize: 12, fontWeight: 700 }}>
                        [{citation.citation_id}] {citation.original_filename || citation.source_uri}
                      </div>
                      <div style={{ color: "var(--tg-text-faint)", fontSize: 10, fontFamily: "monospace", marginTop: 4 }}>
                        CHUNK {citation.chunk_index + 1}
                        {citation.page_no ? ` · PAGE ${citation.page_no}` : ""}
                        {" · "}{citation.document_id}
                      </div>
                      <p style={{ color: "var(--tg-text-muted)", fontSize: 12, lineHeight: 1.7, margin: "10px 0 0", whiteSpace: "pre-wrap" }}>
                        {citation.excerpt}
                      </p>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
