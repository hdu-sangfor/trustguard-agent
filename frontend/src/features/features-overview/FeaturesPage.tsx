import { Link } from "react-router-dom";
import {
  ArrowRight,
  BookOpenCheck,
  ClipboardList,
  Database,
  GitBranch,
  ShieldCheck,
  TerminalSquare,
} from "lucide-react";
import Header from "@/shared/components/Header";
import PageTitle from "@/shared/components/PageTitle";
import "@/shared/styles/console-pages.css";
import "./FeaturesPage.css";

const overview = [
  {
    icon: <GitBranch size={17} />,
    label: "动态编排",
    value: "LangGraph",
    description: "管理任务状态、阶段推进、结果观察和证据不足时的重新规划。",
  },
  {
    icon: <TerminalSquare size={17} />,
    label: "隔离执行",
    value: "Containerized Skills",
    description: "技能在独立容器中运行，并受任务授权范围和执行策略约束。",
  },
  {
    icon: <ClipboardList size={17} />,
    label: "证据追踪",
    value: "Evidence Trace",
    description: "统一记录计划、调用、观察、判断和报告结论，支持审计与回放。",
  },
  {
    icon: <BookOpenCheck size={17} />,
    label: "知识增强",
    value: "RAG",
    description: "为任务规划、技能执行和证据判断补充可信的漏洞与历史任务上下文。",
  },
] as const;

const stack = ["React / Vite", "FastAPI", "LangGraph", "RAG", "Docker Skills", "Evidence API"] as const;

const pipeline = [
  ["Task Input", "目标 / 范围 / 策略"],
  ["Gateway", "身份与统一入口"],
  ["Supervisor", "授权与人工确认"],
  ["Orchestrator", "计划与阶段推进"],
  ["Executor", "隔离技能执行"],
  ["RAG", "可信知识增强"],
  ["Evidence", "观察与证据判断"],
  ["报告输出", "结论与修复建议"],
] as const;

const coreFeatures = [
  ["授权范围控制", "任务在执行前明确目标、范围、限制和证据要求；每次技能调用都必须与任务边界一致。"],
  ["动态任务编排", "Agent 根据阶段状态和最新观察调整执行路径，在证据不足或结果冲突时重新规划。"],
  ["隔离技能执行", "安全技能在独立容器中运行，参数、目标和高风险能力都受到任务策略约束。"],
  ["证据优先", "计划、技能输出、事件、观察、判断和结论共同进入证据链，报告能够解释依据。"],
  ["人工确认", "任务启动和受控利用验证设置任务级确认门禁，让高风险动作始终保留明确责任边界。"],
  ["知识增强", "RAG 将漏洞知识、历史任务和人工审核内容带入规划、执行与证据判断，形成经验闭环。"],
] as const;

const technicalRows = [
  ["任务状态", "PENDING / RUNNING / PAUSED / FAILED / DONE", "驱动任务列表、轨迹页轮询和报告生成入口。"],
  ["阶段状态", "TRUSTGUARD_PHASES", "统一任务、轨迹和报告页面的阶段展示与执行进度。"],
  ["知识输入", "Knowledge Base / Crawler Job / Review", "采集、清洗、审核后进入 RAG，增强 Agent 判断。"],
  ["执行记录", "ApiExecutionRecord", "保存技能调用、阶段、耗时、状态和 request_id，构成可复盘日志。"],
  ["观察结果", "ApiObservation", "承载开放端口、服务指纹、漏洞线索和 artifact 摘要。"],
  ["报告结构", "findings / recommendations / artifacts", "支持报告中心展开查看和下载 Markdown / HTML 报告。"],
] as const;

const capabilityGroups = [
  {
    icon: <GitBranch size={15} />,
    title: "编排层",
    items: ["阶段拆解", "任务暂停 / 恢复", "结果驱动重规划", "多任务状态同步"],
  },
  {
    icon: <BookOpenCheck size={15} />,
    title: "知识层",
    items: ["法规知识", "漏洞知识", "工具知识", "审核后入库"],
  },
  {
    icon: <TerminalSquare size={15} />,
    title: "技能层",
    items: ["指纹识别", "漏洞扫描", "授权验证", "报告生成"],
  },
  {
    icon: <ClipboardList size={15} />,
    title: "证据层",
    items: ["执行事件", "技能输出", "观察结果", "报告结论"],
  },
] as const;

export default function FeaturesPage() {
  return (
    <div className="features-page tg-console-page">
      <Header />
      <main className="features-shell tg-console-shell">
        <PageTitle
          eyebrow={<><ShieldCheck size={14} /> TRUSTGUARD AGENT ARCHITECTURE</>}
          title="技术特点"
          description="TrustGuard 通过任务级授权、动态编排、隔离技能执行和证据链追踪，将安全测试转化为有边界、可解释、可复盘的 Agent 工作流。"
          actions={(
            <div className="features-hero-actions">
              <Link to="/agent">开始安全任务 <ArrowRight size={13} /></Link>
              <Link to="/skills">查看技能库</Link>
              <Link to="/system">系统状态</Link>
            </div>
          )}
        />

        <section className="features-metrics" aria-label="技术能力概览">
          {overview.map((item) => (
            <div key={item.label}>
              {item.icon}
              <span>{item.label}</span>
              <strong>{item.value}</strong>
              <small>{item.description}</small>
            </div>
          ))}
        </section>

        <div className="features-stack" aria-label="技术栈">
          <span><Database size={14} /> IMPLEMENTATION STACK</span>
          {stack.map((item) => <code key={item}>{item}</code>)}
        </div>

        <section className="features-section features-architecture-section">
          <div className="features-section-heading">
            <div>
              <span>01</span>
              <div>
                <h2>系统流程</h2>
                <p>任务经统一入口和监督层确认授权边界，由编排器调度隔离技能，并结合 RAG 与执行观察沉淀证据和报告。</p>
              </div>
            </div>
          </div>
          <div className="features-architecture">
            <svg className="features-flow-svg" viewBox="0 0 980 220" role="img" aria-label="TrustGuard 技术流程图">
              <defs>
                <marker id="featuresArrow" viewBox="0 0 10 10" refX="0" refY="5" markerWidth="5" markerHeight="5" orient="auto">
                  <path d="M 0 0 L 10 5 L 0 10 z" />
                </marker>
              </defs>
              {pipeline.map(([title, description], index) => {
                const x = 16 + index * 120;
                return (
                  <g key={title}>
                    {index < pipeline.length - 1 && <path d={`M ${x + 104} 110 L ${x + 116} 110`} className="features-flow-arrow" />}
                    <rect x={x} y="66" width="104" height="88" rx="8" className="features-flow-node" />
                    <text x={x + 12} y="101" className="features-flow-title">{title}</text>
                    <text x={x + 12} y="128" className="features-flow-desc">{description}</text>
                  </g>
                );
              })}
            </svg>
            <div className="features-flow" aria-label="TrustGuard 移动端技术流程">
              {pipeline.map(([title, description], index) => (
                <div className="features-flow-row" key={title}>
                  <code>{String(index + 1).padStart(2, "0")}</code>
                  <strong>{title}</strong>
                  <span>{description}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="features-section" aria-label="核心技术特点">
          <div className="features-section-heading">
            <div>
              <span>02</span>
              <div>
                <h2>核心技术特点</h2>
                <p>这些机制共同保证 Agent 有明确授权边界、能够根据观察行动，并让每个结论都有可复核依据。</p>
              </div>
            </div>
          </div>
          <div className="features-core-grid">
            {coreFeatures.map(([title, description], index) => (
              <article key={title}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <h3>{title}</h3>
                <p>{description}</p>
              </article>
            ))}
          </div>
          <details className="features-developer-reference">
            <summary>开发者实现参考</summary>
            <p>以下对象用于连接页面、接口、执行轨迹和报告结构，便于开发与联调时快速定位数据边界。</p>
            <div className="features-tech-table">
              {technicalRows.map(([name, object, description]) => (
                <div className="features-tech-row" key={name}>
                  <strong>{name}</strong>
                  <code>{object}</code>
                  <span>{description}</span>
                </div>
              ))}
            </div>
          </details>
        </section>

        <section className="features-section">
          <div className="features-section-heading">
            <div>
              <span>03</span>
              <div>
                <h2>能力分层</h2>
                <p>编排、知识、技能和证据四层保持职责清晰，同时围绕同一条任务证据链协作。</p>
              </div>
            </div>
          </div>
          <div className="features-layer-list">
            {capabilityGroups.map((group) => (
              <div className="features-layer" key={group.title}>
                <div>{group.icon}<strong>{group.title}</strong></div>
                <ul>
                  {group.items.map((item) => <li key={item}>{item}</li>)}
                </ul>
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
