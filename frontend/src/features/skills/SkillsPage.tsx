/**
 * SkillsPage — 技能注册表
 * 展示平台所有安全技能（Security Skill Containers）的完整目录。
 * 在线时从 GET /api/v1/admin/skills 获取实时数据；
 * 离线时使用内置的 34+ 技能演示注册表。
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Header from "@/shared/components/Header";
import { getSkillRegistry, TRUSTGUARD_PHASES, type ApiSkillEntry } from "@/shared/lib/api";

// ─── Static skill descriptions (indexed by skill_id) ──────────────────────────
const SKILL_META: Record<string, { desc: string; phases: string[]; tags: string[] }> = {
  "nmap":                    { desc: "全端口扫描与服务版本探测，指纹识别开放端口与协议",             phases: ["RECON"], tags: ["NetworkScan", "必备"] },
  "httpx":                   { desc: "高速 HTTP/HTTPS 探测，批量指纹识别 Web 服务技术栈",          phases: ["RECON", "THREAT_MODEL"], tags: ["WebRecon"] },
  "ehole":                   { desc: "红队专用 CMS/框架指纹识别，覆盖 1500+ Web 资产特征",         phases: ["RECON", "THREAT_MODEL"], tags: ["WebRecon"] },
  "dirsearch":               { desc: "目录与路径枚举，发现隐藏接口、备份文件与敏感路径",            phases: ["RECON"], tags: ["WebRecon"] },
  "katana":                  { desc: "现代 Web 爬虫，深度抓取 JavaScript 渲染页面与 API 接口",     phases: ["RECON", "VULN_SCAN"], tags: ["WebRecon"] },
  "http-enum":               { desc: "HTTP 服务枚举，通过脚本探测默认文件、登录页与管理后台",        phases: ["RECON"], tags: ["WebRecon"] },
  "ffuf-dir-enum":           { desc: "高速目录模糊测试，支持自定义字典与过滤规则",                  phases: ["RECON"], tags: ["WebRecon"] },
  "whatweb-fingerprint":     { desc: "Web 应用指纹识别，检测 CMS、框架、服务器与版本信息",          phases: ["RECON", "THREAT_MODEL"], tags: ["WebRecon"] },
  "dispatcher":              { desc: "ETL 工件分片器，将大型扫描结果切片分发给下游技能",            phases: ["THREAT_MODEL", "VULN_SCAN", "EXPLOIT"], tags: ["Pipeline"] },
  "nuclei":                  { desc: "基于模板的漏洞扫描引擎，覆盖 9000+ CVE 与安全检测模板",      phases: ["VULN_SCAN", "EXPLOIT"], tags: ["VulnScan", "核心"] },
  "nikto-scan":              { desc: "经典 Web 服务器漏洞扫描，检测已知安全配置问题",               phases: ["VULN_SCAN"], tags: ["VulnScan"] },
  "fscan":                   { desc: "综合内网扫描工具，支持端口扫描、漏洞检测与弱口令爆破",         phases: ["RECON", "VULN_SCAN", "EXPLOIT"], tags: ["NetworkScan"] },
  "sqlmap":                  { desc: "自动化 SQL 注入检测与利用，支持 5 种注入类型",               phases: ["VULN_SCAN", "EXPLOIT"], tags: ["WebTesting", "Exploit"] },
  "web-vuln-common":         { desc: "通用 Web 漏洞检测：XSS/CSRF/SSRF/路径遍历/文件包含",        phases: ["VULN_SCAN"], tags: ["WebTesting"] },
  "fenjing":                 { desc: "Jinja2/Twig SSTI 模板注入检测与利用，自动绕过常见过滤器",     phases: ["VULN_SCAN", "EXPLOIT"], tags: ["Exploit"] },
  "exploit-struts2":         { desc: "Apache Struts2 多版本 RCE 利用（S2-045/S2-057/S2-061）",    phases: ["EXPLOIT"], tags: ["Exploit", "CVE"] },
  "exploit-thinkphp":        { desc: "ThinkPHP 框架 RCE 利用（5.0.x/5.1.x/6.0.x 全系列）",       phases: ["EXPLOIT"], tags: ["Exploit", "CVE"] },
  "exploit-tomcat":          { desc: "Apache Tomcat CVE-2017-12615 PUT 任意文件上传 RCE",          phases: ["EXPLOIT"], tags: ["Exploit", "CVE"] },
  "exploit-weblogic":        { desc: "Oracle WebLogic T3/IIOP 协议反序列化 RCE（CVE-2023-21839）", phases: ["EXPLOIT"], tags: ["Exploit", "CVE"] },
  "shiro_exploit":           { desc: "Apache Shiro 默认密钥反序列化 RCE（CVE-2016-4437）",         phases: ["EXPLOIT"], tags: ["Exploit", "CVE"] },
  "fastjson-exploit":        { desc: "Fastjson JNDI 注入 RCE（≤1.2.47 autoType 绕过）",           phases: ["EXPLOIT"], tags: ["Exploit", "CVE"] },
  "ysoserial":               { desc: "Java 反序列化载荷生成器，支持 CommonsCollections 等 30+ 链",  phases: ["EXPLOIT"], tags: ["Exploit", "PostExploit"] },
  "jndi_exploit":            { desc: "JNDI 注入服务端，提供 LDAP/RMI 恶意端点用于 RCE 验证",       phases: ["EXPLOIT"], tags: ["Exploit"] },
  "metasploit":              { desc: "世界级渗透框架，集成 2000+ 模块，支持 Meterpreter 会话",      phases: ["EXPLOIT", "REPORT"], tags: ["PostExploit", "核心"] },
  "metasploit-session":      { desc: "Metasploit 持久化会话管理，维持长连接与后渗透操作",           phases: ["EXPLOIT"], tags: ["PostExploit"] },
  "linpeas":                 { desc: "Linux 权限提升枚举脚本，检测 SUID/SGID/Cron/弱配置",         phases: ["EXPLOIT"], tags: ["PostExploit"] },
  "webshell-php":            { desc: "PHP Webshell 生成与管理，支持一句话与蚁剑/哥斯拉协议",        phases: ["EXPLOIT"], tags: ["PostExploit"] },
  "python-sandbox":          { desc: "隔离 Python 代码沙箱，安全执行 PoC 验证脚本",                phases: ["VULN_SCAN", "EXPLOIT"], tags: ["Sandbox"] },
  "curl-raw":                { desc: "底层 HTTP 原始请求发送，用于构造自定义 Payload 和验证漏洞",   phases: ["RECON", "VULN_SCAN", "EXPLOIT"], tags: ["基础"] },
  "baidu-search":            { desc: "百度搜索引擎接口，辅助情报收集与目标资产信息关联",             phases: ["RECON", "THREAT_MODEL"], tags: ["Search", "OSINT"] },
  "read_workspace_artifact": { desc: "工件读取器，从任务工作区加载 artifacts 供 LLM 分析决策",      phases: ["REPORT"], tags: ["Pipeline"] },
  "read_target_list":        { desc: "目标列表读取，批量加载待测资产清单并注入执行上下文",           phases: ["RECON"], tags: ["Pipeline"] },
  "pua":                     { desc: "自定义 payload 组合工具，支持编码变换与绕过规则生成",          phases: ["VULN_SCAN", "EXPLOIT"], tags: ["Exploit"] },
  "v1-micro-sample":         { desc: "V1 微技能样板，展示 Pydantic Schema + S-06 规范 + Agent 摘要", phases: ["RECON"], tags: ["V1"] },
};

const PHASE_LABELS: Record<string, string> = {
  RECON: "情报收集", THREAT_MODEL: "威胁建模", VULN_SCAN: "漏洞扫描", EXPLOIT: "漏洞利用", REPORT: "报告生成", DONE: "已完成",
};
const PHASE_COLORS: Record<string, string> = {
  RECON: "#38bdf8", THREAT_MODEL: "#818cf8", VULN_SCAN: "#fb923c", EXPLOIT: "#f87171", REPORT: "#34d399", DONE: "#4ade80",
};

const TAG_COLORS: Record<string, string> = {
  "核心": "#f87171", "必备": "#fbbf24", "CVE": "#fb923c", "Exploit": "#f87171",
  "PostExploit": "#a78bfa", "WebRecon": "#38bdf8", "VulnScan": "#fb923c",
  "NetworkScan": "#818cf8", "Agent": "#22d3ee", "OSINT": "#34d399",
  "Pipeline": "#64748b", "V1": "#a78bfa",
};

function tagColor(tag: string): string {
  return TAG_COLORS[tag] ?? "#64748b";
}

// Build demo entries from static meta
const DEMO_SKILLS: ApiSkillEntry[] = Object.entries(SKILL_META).map(([id, m]) => ({
  skill_id: id,
  category: m.tags[0] ?? "General",
  description: m.desc,
  phases: m.phases,
  tags: m.tags,
}));

// ─── Sub-components ────────────────────────────────────────────────────────────

function SkillCard({ skill, meta }: { skill: ApiSkillEntry; meta: typeof SKILL_META[string] | undefined }) {
  const phases = meta?.phases ?? [];
  const tags   = meta?.tags ?? [skill.category];
  const desc   = meta?.desc ?? skill.category;

  return (
    <div style={{
      background: "rgba(15,23,42,0.78)", border: "1px solid rgba(71,85,105,0.3)",
      borderRadius: 9, padding: "13px 15px",
      transition: "border-color 0.2s, box-shadow 0.2s",
    }}
    onMouseEnter={e => { e.currentTarget.style.borderColor = "rgba(34,211,238,0.3)"; e.currentTarget.style.boxShadow = "0 0 16px rgba(34,211,238,0.06)"; }}
    onMouseLeave={e => { e.currentTarget.style.borderColor = "rgba(71,85,105,0.3)"; e.currentTarget.style.boxShadow = "none"; }}
    >
      {/* Header row */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 }}>
        <div style={{ fontFamily: "monospace", fontSize: 13, fontWeight: 700, color: "#22d3ee" }}>
          {skill.skill_id}
        </div>
        <div style={{ display: "flex", gap: 3, flexWrap: "wrap", justifyContent: "flex-end" }}>
          {tags.slice(0, 2).map(t => (
            <span key={t} style={{
              fontSize: 9, padding: "1px 5px", borderRadius: 3,
              background: `${tagColor(t)}18`, border: `1px solid ${tagColor(t)}40`,
              color: tagColor(t), fontFamily: "monospace", fontWeight: 700,
            }}>{t}</span>
          ))}
        </div>
      </div>

      {/* Description */}
      <div style={{ fontSize: 11, color: "rgba(148,163,184,0.75)", lineHeight: 1.55, marginBottom: 9, minHeight: 32 }}>
        {desc}
      </div>

      {/* Phase pills */}
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
        {phases.map(ph => (
          <span key={ph} style={{
            fontSize: 9, padding: "2px 7px", borderRadius: 10,
            background: `${PHASE_COLORS[ph] ?? "#64748b"}18`,
            border: `1px solid ${PHASE_COLORS[ph] ?? "#64748b"}35`,
            color: PHASE_COLORS[ph] ?? "#64748b",
            fontFamily: "monospace", fontWeight: 600,
          }}>
            {ph}
          </span>
        ))}
      </div>
    </div>
  );
}

// ─── Main ─────────────────────────────────────────────────────────────────────

const ALL_PHASE_FILTER = "ALL";

export default function SkillsPage() {
  const navigate = useNavigate();
  const [skills, setSkills]         = useState<ApiSkillEntry[]>([]);
  const [loading, setLoading]       = useState(true);
  const [online, setOnline]         = useState<boolean | null>(null);
  const [phaseFilter, setPhaseFilter] = useState<string>(ALL_PHASE_FILTER);
  const [search, setSearch]         = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getSkillRegistry();
      if (data.skills && data.skills.length > 0) {
        // Enrich with static meta where available
        const enriched: ApiSkillEntry[] = data.skills.map(s => ({
          ...s,
          description: (SKILL_META[s.skill_id]?.desc) ?? (s.category),
          phases: SKILL_META[s.skill_id]?.phases,
          tags: SKILL_META[s.skill_id]?.tags ?? [s.category],
        }));
        setSkills(enriched);
        setOnline(true);
      } else {
        setSkills(DEMO_SKILLS);
        setOnline(false);
      }
    } catch {
      setSkills(DEMO_SKILLS);
      setOnline(false);
    }
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  // Filter logic
  const filtered = skills.filter(s => {
    const q = search.trim().toLowerCase();
    const matchSearch = !q ||
      s.skill_id.toLowerCase().includes(q) ||
      (s as { description?: string }).description?.toLowerCase().includes(q) ||
      s.category.toLowerCase().includes(q);

    const meta = SKILL_META[s.skill_id];
    const phases: string[] = meta?.phases ?? (s as { phases?: string[] }).phases ?? [];
    const matchPhase = phaseFilter === ALL_PHASE_FILTER || phases.includes(phaseFilter);

    return matchSearch && matchPhase;
  });

  // Phase counts
  const phaseCounts = (TRUSTGUARD_PHASES as readonly string[]).reduce<Record<string, number>>((acc, ph) => {
    acc[ph] = skills.filter(s => {
      const meta = SKILL_META[s.skill_id];
      const phases: string[] = meta?.phases ?? (s as { phases?: string[] }).phases ?? [];
      return phases.includes(ph);
    }).length;
    return acc;
  }, {});

  return (
    <div style={{ minHeight: "100vh", background: "linear-gradient(180deg, #0a0f1e 0%, #0f172a 60%, #0a0f1e 100%)", paddingTop: 60 }}>
      <Header />

      <div style={{ maxWidth: 1400, margin: "0 auto", padding: "24px 20px 60px" }}>

        {/* Page header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 24 }}>
          <div>
            <h1 style={{ margin: 0, fontFamily: "monospace", fontWeight: 900, fontSize: 22, color: "#e2e8f0", letterSpacing: "0.08em" }}>
              技能注册表
            </h1>
            <div style={{ fontSize: 12, color: "rgba(148,163,184,0.55)", marginTop: 4, fontFamily: "monospace" }}>
              SKILL REGISTRY · {loading ? "加载中…" : `${skills.length} 个安全技能容器`}
              {online !== null && (
                <span style={{ marginLeft: 12, color: online ? "rgba(52,211,153,0.8)" : "rgba(251,191,36,0.8)" }}>
                  ● {online ? "实时注册表" : "演示数据"}
                </span>
              )}
            </div>
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <button
              type="button"
              onClick={() => { void load(); }}
              style={{
                background: "rgba(34,211,238,0.08)", border: "1px solid rgba(34,211,238,0.35)",
                color: "#22d3ee", borderRadius: 7, padding: "6px 14px",
                fontSize: 12, cursor: "pointer", fontFamily: "monospace",
              }}
            >⟳ 刷新</button>
            <button
              type="button"
              onClick={() => navigate("/admin")}
              style={{
                background: "rgba(15,23,42,0.5)", border: "1px solid rgba(71,85,105,0.45)",
                color: "#94a3b8", borderRadius: 7, padding: "6px 14px",
                fontSize: 12, cursor: "pointer", fontFamily: "monospace",
              }}
            >平台管理 →</button>
          </div>
        </div>

        {/* ── Stats bar ── */}
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 20 }}>
          {[
            { label: "全部技能", count: skills.length, color: "#64748b" },
            ...((TRUSTGUARD_PHASES as readonly string[]).map(ph => ({
              label: `${ph} · ${PHASE_LABELS[ph] ?? ph}`,
              count: phaseCounts[ph] ?? 0,
              color: PHASE_COLORS[ph] ?? "#64748b",
            }))),
          ].map(({ label, count, color }) => (
            <div key={label} style={{
              padding: "8px 14px", borderRadius: 8,
              background: "rgba(15,23,42,0.7)", border: `1px solid ${color}25`,
              display: "flex", alignItems: "center", gap: 8,
            }}>
              <span style={{ fontFamily: "monospace", fontWeight: 900, fontSize: 18, color }}>{count}</span>
              <span style={{ fontSize: 10, color: "rgba(148,163,184,0.55)", fontFamily: "monospace" }}>{label}</span>
            </div>
          ))}
        </div>

        {/* ── Filter bar ── */}
        <div style={{ display: "flex", gap: 10, marginBottom: 20, flexWrap: "wrap", alignItems: "center" }}>
          {/* Phase filter tabs */}
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {[ALL_PHASE_FILTER, ...(TRUSTGUARD_PHASES as readonly string[])].map(ph => {
              const active = ph === phaseFilter;
              const color  = ph === ALL_PHASE_FILTER ? "#64748b" : (PHASE_COLORS[ph] ?? "#64748b");
              return (
                <button
                  key={ph}
                  type="button"
                  onClick={() => setPhaseFilter(ph)}
                  style={{
                    padding: "5px 12px", borderRadius: 20,
                    border: active ? `1px solid ${color}` : "1px solid rgba(71,85,105,0.35)",
                    background: active ? `${color}22` : "rgba(15,23,42,0.5)",
                    color: active ? color : "rgba(148,163,184,0.5)",
                    fontFamily: "monospace", fontSize: 11, fontWeight: active ? 700 : 400,
                    cursor: "pointer", transition: "all 0.18s",
                  }}
                >
                  {ph === ALL_PHASE_FILTER ? `全部 (${skills.length})` : `${ph} (${phaseCounts[ph] ?? 0})`}
                </button>
              );
            })}
          </div>

          {/* Search */}
          <input
            type="text"
            placeholder="搜索技能…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              flex: "1 1 180px", minWidth: 140, maxWidth: 260,
              padding: "6px 12px", borderRadius: 7,
              background: "rgba(15,23,42,0.7)", border: "1px solid rgba(71,85,105,0.4)",
              color: "#e2e8f0", fontFamily: "monospace", fontSize: 12,
              outline: "none",
            }}
          />
          {search && (
            <span style={{ fontSize: 11, color: "rgba(148,163,184,0.5)", fontFamily: "monospace" }}>
              {filtered.length} 个匹配
            </span>
          )}
        </div>

        {/* ── Skill grid ── */}
        {loading ? (
          <div style={{ textAlign: "center", padding: "60px 20px", color: "rgba(148,163,184,0.35)", fontFamily: "monospace" }}>
            加载中…
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ textAlign: "center", padding: "60px 20px", color: "rgba(148,163,184,0.3)", fontFamily: "monospace" }}>
            没有匹配的技能
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 12 }}>
            {filtered.map(skill => (
              <SkillCard key={skill.skill_id} skill={skill} meta={SKILL_META[skill.skill_id]} />
            ))}
          </div>
        )}

        {/* ── Platform note ── */}
        <div style={{ marginTop: 32, padding: "14px 20px", borderRadius: 10, background: "rgba(15,23,42,0.5)", border: "1px solid rgba(71,85,105,0.2)", fontSize: 11, fontFamily: "monospace", color: "rgba(148,163,184,0.45)" }}>
          <span style={{ color: "#22d3ee", fontWeight: 700 }}>Skill Container 架构：</span>
          每个技能运行于独立 Docker 容器，由 LLM 状态机（Orchestrator）根据目标特征自动调度。
          技能通过 <span style={{ color: "#94a3b8" }}>docker/tools_registry.yaml</span> 注册，输出遵循统一 JSON 契约，
          artifact 写入共享工作区供下游技能复用。
          后端在线时展示来自 Executor 服务的实时注册数据。
        </div>
      </div>
    </div>
  );
}
