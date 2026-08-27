import { useMemo, useRef, useEffect } from "react";
import MarkdownIt from "markdown-it";
import hljs from "highlight.js";

const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  breaks: true,
  highlight(str: string, lang: string) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        const highlighted = hljs.highlight(str, { language: lang, ignoreIllegals: true }).value;
        return `<pre class="hljs"><code>${highlighted}</code></pre>`;
      } catch { /* fall through */ }
    }
    return `<pre class="hljs"><code>${md.utils.escapeHtml(str)}</code></pre>`;
  },
});

interface Props {
  content: string;
}

export default function MarkdownBlock({ content }: Props) {
  const html = useMemo(() => md.render(content), [content]);
  return <div className="tg-markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}