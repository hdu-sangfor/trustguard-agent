interface Props {
  hasContent: boolean;
  streaming: boolean;
}

export default function ThinkingBlock({ hasContent, streaming }: Props) {
  if (!streaming || hasContent) return null;

  return (
    <div className="tg-thinking">
      <span className="tg-thinking-dot" />
      <span className="tg-thinking-text">正在理解任务</span>
    </div>
  );
}
