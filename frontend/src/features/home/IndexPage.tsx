import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDown, ArrowRight, Check, ShieldCheck } from "lucide-react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import Header from "@/shared/components/Header";
import mainLogoSrc from "@/shared/assets/main.jpg";
import "./Index.css";

gsap.registerPlugin(ScrollTrigger);

const heroLinks = [
  "任务编排",
  "技能执行",
  "证据链",
  "知识增强",
] as const;

const advantages = [
  { code: "01", title: "从用户意图到授权任务", text: "Agent 先识别测试目标、授权范围、限制条件和证据要求，再生成需要人工确认的任务草稿。" },
  { code: "02", title: "从隔离执行到策略控制", text: "技能在独立容器中运行，执行目标必须与任务授权范围一致；利用验证只有在任务策略允许时才能进入执行流程。" },
  { code: "03", title: "从工具结果到可复核结论", text: "技能输出、执行事件、结果观察、证据判断和报告结论共同组成可以追踪和复核的证据链。" },
] as const;

const capabilityDomains = [
  {
    code: "TASK",
    title: "Task Agent",
    subtitle: "任务理解与动态规划",
    text: "将自然语言转化为包含目标、范围、约束、执行策略和证据要求的安全任务，并在正式启动前进行人工确认。",
    linkLabel: "进入可信卫士",
    href: "/agent",
  },
  {
    code: "SKILL",
    title: "Skill Orchestration",
    subtitle: "安全技能编排",
    text: "根据任务阶段与当前观察，从 30+ 安全技能、专用能力定义和漏洞验证模板中规划执行路径，并在证据不足时重新规划。",
    linkLabel: "查看技能库",
    href: "/skills",
  },
  {
    code: "TRACE",
    title: "Evidence & Reporting",
    subtitle: "证据与报告",
    text: "记录计划、调用、观察、判断和结论，使任务报告可以解释、复核、审计和回放。",
    linkLabel: "查看报告中心",
    href: "/reports",
  },
  {
    code: "RAG",
    title: "Knowledge Intelligence",
    subtitle: "知识增强与经验沉淀",
    text: "将漏洞知识、历史任务和人工审核内容接入 RAG 检索，为 Agent 的计划、执行与证据判断补充可信上下文。",
    linkLabel: "进入知识中心",
    href: "/knowledge",
  },
] as const;

const workflowSteps = [
  {
    code: "01",
    phase: "RECON",
    title: "资产侦察",
    description: "识别授权范围内的目标资产、开放服务、技术指纹和外部暴露面，先建立可以验证的事实基础。",
    metric: "SCOPE",
    metricLabel: "所有发现和执行目标都必须落在任务授权范围内",
  },
  {
    code: "02",
    phase: "THREAT MODEL",
    title: "威胁建模",
    description: "Agent 结合资产信息与 RAG 知识上下文分析潜在风险路径，明确下一阶段需要验证的问题。",
    metric: "PLAN",
    metricLabel: "把观察转化为动态执行计划",
  },
  {
    code: "03",
    phase: "VULN SCAN",
    title: "漏洞扫描",
    description: "按目标特征选择适合的扫描、识别和验证技能，持续回收结构化结果。",
    metric: "30+",
    metricLabel: "覆盖资产侦察、指纹识别、漏洞扫描、利用验证与证据处理的技能和模板能力",
  },
  {
    code: "04",
    phase: "EXPLOIT",
    title: "授权验证",
    description: "只有任务已经确认、策略明确允许且执行目标位于授权范围内，才进行受控验证。",
    metric: "CONFIRMED",
    metricLabel: "任务级人工确认与执行策略共同控制高风险动作",
  },
  {
    code: "05",
    phase: "REPORT",
    title: "证据报告",
    description: "将工具输出、结果观察、证据判断和最终结论组织为可复核的安全报告。",
    metric: "8 STEP TYPES",
    metricLabel: "结构化推理步骤解释从计划到结论的判断过程",
  },
  {
    code: "06",
    phase: "DONE",
    title: "完成交付",
    description: "任务状态、执行轨迹和报告完成归档，经过审核的经验可以继续进入 RAG 知识建设流程。",
    metric: "TRACE",
    metricLabel: "从用户意图到最终结论全程可回放",
  },
] as const;

function SectionLabel({ number, children }: { number: string; children: string }) {
  return <div className="landing-section-label"><span>{number}</span>{children}</div>;
}

function CapabilityDomains() {
  return (
    <section className="landing-domains" id="capabilities" aria-label="TrustGuard 四大能力">
      <div className="landing-section-head landing-reveal">
        <SectionLabel number="03">CAPABILITY DOMAINS</SectionLabel>
        <p>把复杂的安全自动化拆成四类可以理解、体验和验证的平台能力。</p>
      </div>
      <div className="landing-domains-title landing-reveal">
        <h2>一个 Agent，连接安全任务的完整上下文。</h2>
      </div>
      <div className="landing-domain-grid">
        {capabilityDomains.map((domain) => (
          <article className="landing-domain-card landing-reveal" key={domain.code}>
            <div className="landing-domain-code">{domain.code}</div>
            <div>
              <h3>{domain.title}</h3>
              <strong>{domain.subtitle}</strong>
              <p>{domain.text}</p>
            </div>
            <Link to={domain.href}>{domain.linkLabel}<ArrowRight size={15} /></Link>
          </article>
        ))}
      </div>
    </section>
  );
}

function ScrollWorkflow() {
  const [expandedStep, setExpandedStep] = useState(0);

  return (
    <section className="landing-workflow" id="workflow" aria-label="可信安全任务工作流程" data-active-step="1">
      <div className="landing-workflow-stage">
        <header className="landing-workflow-head">
          <SectionLabel number="04">FROM INTENT TO EVIDENCE</SectionLabel>
          <p>继续滚动，查看一次安全任务如何在同一个可信舞台中推进。</p>
        </header>
        <div className="landing-workflow-grid">
          <ol className="landing-workflow-nav" aria-label="六阶段安全工作流程">
            <i className="landing-workflow-track"><i /></i>
            {workflowSteps.map((step, index) => (
              <li className="landing-workflow-nav-item" data-step={index + 1} key={step.code}>
                <span>{step.code}</span><strong>{step.title}</strong>
              </li>
            ))}
          </ol>
          <div className="landing-workflow-scenes">
            {workflowSteps.map((step, index) => (
              <article className="landing-workflow-scene" key={step.code} data-mobile-expanded={index === expandedStep}>
                <button
                  className="landing-workflow-mobile-toggle"
                  type="button"
                  aria-expanded={index === expandedStep}
                  onClick={() => setExpandedStep(index)}
                >
                  <span>{step.code}</span><strong>{step.title}</strong><i aria-hidden="true">+</i>
                </button>
                <div className="landing-workflow-scene-body">
                  <div className="landing-workflow-copy">
                    <span>{step.phase}</span>
                    <h3>{step.title}</h3>
                    <p>{step.description}</p>
                  </div>
                  <div className="landing-workflow-metric">
                    <strong>{step.metric}</strong>
                    <span>{step.metricLabel}</span>
                  </div>
                </div>
              </article>
            ))}
          </div>
          <div className="landing-workflow-visual" aria-hidden="true">
            <div className="landing-workflow-core"><ShieldCheck size={38} /></div>
            {workflowSteps.map((step, index) => (
              <span className={`landing-workflow-node node-${index + 1}`} data-step={index + 1} key={step.code}>{step.code}</span>
            ))}
            <i className="landing-workflow-orbit" />
          </div>
        </div>
        <footer className="landing-workflow-foot">
          <span>SCROLL</span><i><i /></i><span className="landing-workflow-current">01 / 06</span>
        </footer>
      </div>
    </section>
  );
}

function Architecture() {
  const nodes = [
    { code: "01", name: "Gateway", detail: "身份、权限与统一入口" },
    { code: "02", name: "Supervisor", detail: "授权边界、策略与人工确认" },
    { code: "03", name: "Orchestrator", detail: "规划、重规划与阶段推进" },
    { code: "04", name: "Executor", detail: "隔离技能与参数约束" },
    { code: "05", name: "Evidence / RAG", detail: "证据沉淀、知识检索与经验闭环" },
  ] as const;

  return (
    <section className="landing-architecture" id="architecture" aria-label="TrustGuard 技术架构">
      <div className="landing-section-head landing-reveal">
        <SectionLabel number="05">TRUSTED BY DESIGN</SectionLabel>
        <p>快速编排的背后，是职责清晰的分层系统，而不是一个拥有无限权限的黑盒 Agent。</p>
      </div>
      <div className="landing-architecture-title landing-reveal">
        <h2>每一层只做它应该做的事。</h2>
        <p>用户意图经过统一入口、任务编排和隔离执行，最终进入证据与知识闭环。</p>
      </div>
      <div className="landing-architecture-flow landing-reveal">
        <div className="landing-architecture-intent"><span>USER INTENT</span><strong>安全目标</strong></div>
        {nodes.map((node) => (
          <article key={node.code}>
            <span>{node.code}</span><strong>{node.name}</strong><small>{node.detail}</small>
          </article>
        ))}
      </div>
      <div className="landing-architecture-guards landing-reveal">
        <span><Check size={13} /> 授权范围校验</span>
        <span><Check size={13} /> 隔离技能执行</span>
        <span><Check size={13} /> 任务级人工确认</span>
        <span><Check size={13} /> 全程证据追踪</span>
      </div>
    </section>
  );
}

function Experience() {
  return (
    <section className="landing-experience" aria-label="立即体验 TrustGuard">
      <div>
        <SectionLabel number="06">TRY THE AGENT</SectionLabel>
        <h2>立即体验可信安全 Agent</h2>
        <p>创建一条已授权安全任务，观察 Agent 如何生成计划、调用技能、判断证据并输出可以复核的安全报告。</p>
      </div>
      <div className="landing-experience-actions">
        <Link className="landing-primary-action" to="/agent">开始安全任务 <ArrowRight size={16} /></Link>
        <Link className="landing-secondary-action" to="/features">查看技术特点 <ArrowRight size={15} /></Link>
      </div>
    </section>
  );
}

export default function IndexPage() {
  const pageRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = pageRef.current;
    if (!root) return undefined;
    const context = gsap.context(() => {
      const media = gsap.matchMedia();
      media.add("(min-width: 901px) and (prefers-reduced-motion: no-preference)", () => {
        gsap.timeline({ scrollTrigger: { trigger: ".landing-hero", start: "top top", end: "bottom top", scrub: 0.9 } })
          .to(".landing-hero-copy", { yPercent: -9, opacity: 0.32, ease: "none" }, 0);

        gsap.utils.toArray<HTMLElement>(".landing-reveal").forEach((item) => {
          gsap.from(item, {
            autoAlpha: 0, y: 54, duration: 0.8, ease: "power3.out",
            scrollTrigger: { trigger: item, start: "top 88%", toggleActions: "play none none reverse" },
          });
        });

        const workflow = root.querySelector<HTMLElement>(".landing-workflow");
        const workflowStage = root.querySelector<HTMLElement>(".landing-workflow-stage");
        const scenes = gsap.utils.toArray<HTMLElement>(".landing-workflow-scene");
        const current = root.querySelector<HTMLElement>(".landing-workflow-current");
        if (!workflow || !workflowStage || scenes.length === 0) return undefined;

        gsap.set(scenes.slice(1), { autoAlpha: 0, y: 48 });
        scenes.forEach((scene, index) => scene.setAttribute("aria-hidden", index === 0 ? "false" : "true"));
        let activeIndex = 0;
        const showStep = (nextIndex: number) => {
          if (nextIndex === activeIndex) return;
          const direction = nextIndex > activeIndex ? 1 : -1;
          gsap.killTweensOf(scenes);
          scenes.forEach((scene, index) => {
            scene.setAttribute("aria-hidden", index === nextIndex ? "false" : "true");
            if (index !== nextIndex) {
              gsap.set(scene, { autoAlpha: 0, y: -36 * direction });
            }
          });
          gsap.fromTo(
            scenes[nextIndex],
            { autoAlpha: 0, y: 44 * direction },
            { autoAlpha: 1, y: 0, duration: 0.36, ease: "power3.out", overwrite: true },
          );
          activeIndex = nextIndex;
          workflow.dataset.activeStep = String(nextIndex + 1);
          if (current) current.textContent = `${String(nextIndex + 1).padStart(2, "0")} / 06`;
        };

        const workflowTrigger = ScrollTrigger.create({
          trigger: workflow,
          start: "top 60px",
          end: () => `+=${window.innerHeight * 5}`,
          pin: workflowStage,
          pinSpacing: true,
          anticipatePin: 1,
          invalidateOnRefresh: true,
          onUpdate: (self) => showStep(Math.min(workflowSteps.length - 1, Math.floor(self.progress * workflowSteps.length))),
        });
        return () => workflowTrigger.kill();
      });
      return () => media.revert();
    }, root);
    return () => context.revert();
  }, []);

  return (
    <div className="landing-page" ref={pageRef}>
      <Header />
      <main>
        <section className="landing-hero" aria-labelledby="landing-title">
          <div className="landing-hero-stage">
            <div className="landing-hero-topline">
              <div className="landing-eyebrow"><span /> AUTHORIZED SECURITY AUTOMATION · AUDITABLE BY DESIGN</div>
              <nav aria-label="首页能力快捷入口">
                {heroLinks.map((item) => <span key={item}>{item}</span>)}
              </nav>
            </div>
            <div className="landing-hero-copy">
              <h1 id="landing-title">
                <span>让每一次安全测试，</span>
                <span>都有计划、有边界、</span>
                <span>有证据。</span>
              </h1>
              <div className="landing-hero-meta">
                <p>TrustGuard 将已授权的安全目标转化为可规划、可执行、可追溯的 Agent 任务，并记录每一次技能调用、结果判断与最终结论。</p>
                <div className="landing-actions">
                  <Link className="landing-primary-action" to="/agent">开始安全任务 <ArrowRight size={16} /></Link>
                  <a className="landing-secondary-action" href="#workflow">查看工作流程 <ArrowDown size={15} /></a>
                </div>
              </div>
            </div>
            <div className="landing-hero-visual" aria-hidden="true">
              <img src={mainLogoSrc} alt="" />
            </div>
          </div>
        </section>

        <section className="landing-advantages" aria-labelledby="landing-advantages-title">
          <div className="landing-section-head landing-reveal">
            <SectionLabel number="02">WHY TRUSTGUARD</SectionLabel>
            <p>安全自动化不是把更多命令塞进一个对话框，而是建立工具、判断与责任之间的连接。</p>
          </div>
          <div className="landing-advantages-title landing-reveal">
            <h2 id="landing-advantages-title">不是黑盒答案。<br /><span>是一条可信执行闭环。</span></h2>
          </div>
          <div className="landing-advantage-grid">
            {advantages.map((item) => (
              <article className="landing-advantage-card landing-reveal" key={item.code}>
                <span>{item.code}</span><h3>{item.title}</h3><p>{item.text}</p>
              </article>
            ))}
          </div>
        </section>

        <CapabilityDomains />
        <ScrollWorkflow />
        <Architecture />
        <Experience />
      </main>
    </div>
  );
}
