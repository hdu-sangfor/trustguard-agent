import { CRTerminal } from './components/CRTerminal';

/**
 * 简洁日志工作台。视口由外层容器锁定，不产生页面滚动条。
 */
export function Home() {
  return (
      <div
          className="relative h-[100dvh] max-h-[100dvh] min-h-0 w-full overflow-hidden font-mono"
          style={{
            background: "var(--tg-page-bg)",
            color: "var(--tg-text)",
          }}
      >
        <div
            className="h-full w-full overflow-hidden flex flex-col items-center justify-center pt-[clamp(24px,4vh,48px)] pb-[clamp(8px,2vh,20px)] px-3 box-border">
          <CRTerminal />
        </div>
      </div>
  );
}
