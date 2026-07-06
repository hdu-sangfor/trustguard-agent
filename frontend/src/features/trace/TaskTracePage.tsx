/**
 * TaskTracePage — 任务执行轨迹与详情
 * 路由: /trace/:taskId
 * - 展示任务元信息、状态、阶段流水线
 * - 展示实时事件流（轮询 4s，仅 RUNNING 时）
 * - 提供运行 / 暂停 / 继续 操作
 * - 后端离线时从 localStorage 读取演示数据
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import {
  getTask, getTaskEvents, runTask, stopTask, resumeTask,
  getTaskTrace, getTracePlan, getTaskObservation,
  TRUSTGUARD_PHASES,
  type ApiTask, type ApiEvent, type ApiTraceExecution, type ApiObservation,
} from "@/shared/lib/api";
import { readStoredOrbitTasks } from "@/shared/constants/orbitTasksStorage";

// ─── Phase config ─────────────────────────────────────────────────────────────
const PHASE_LABELS: Record<string, string> = {
  RECON: "情报收集", THREAT_MODEL: "威胁建模", VULN_SCAN: "漏洞扫描",
  EXPLOIT: "漏洞利用", REPORT: "报告生成", DONE: "已完成",
};
const PHASE_COLORS: Record<string, string> = {
  RECON: "#38bdf8", THREAT_MODEL: "#818cf8", VULN_SCAN: "#fb923c",
  EXPLOIT: "#f87171", REPORT: "#34d399", DONE: "#4ade80",
};

const TRACE_SURFACE = "var(--tg-panel-bg)";
const TRACE_SURFACE_MUTED = "var(--tg-panel-muted)";
const TRACE_BORDER = "var(--tg-panel-border)";
const TRACE_TEXT = "var(--tg-text)";
const TRACE_MUTED = "var(--tg-text-muted)";
const TRACE_FAINT = "var(--tg-text-faint)";
const TRACE_HOVER = "var(--tg-hover-bg)";

const phaseIndex = (phase: string | null | undefined): number => {
  if (!phase) return -1;
  const idx = (TRUSTGUARD_PHASES as readonly string[]).indexOf(phase.toUpperCase());
  return idx >= 0 ? idx : -1;
};

// ─── Event type colors ────────────────────────────────────────────────────────
const EVENT_COLOR_MAP: Record<string, string> = {
  PHASE_TRANSITION: "#818cf8", SKILL_STARTED: "#38bdf8", SKILL_COMPLETED: "#34d399",
  SKILL_FAILED: "#f87171", ORCHESTRATOR_TICK: "#94a3b8", TASK_CREATED: "#a78bfa",
  TASK_COMPLETED: "#4ade80", TASK_FAILED: "#f87171", TASK_PAUSED: "#fbbf24",
};

function eventColor(type: string): string {
  const key = Object.keys(EVENT_COLOR_MAP).find(k => type?.toUpperCase().includes(k));
  return key ? EVENT_COLOR_MAP[key] : "#64748b";
}

// ─── Demo data builders ───────────────────────────────────────────────────────
function buildDemoTask(taskId: string): ApiTask | null {
  const stored = readStoredOrbitTasks().find(t => t.id === taskId);
  if (!stored) return null;
  const statusMap: Record<string, ApiTask["status"]> = {
    running: "RUNNING", paused: "PAUSED", finished: "DONE", failed: "FAILED", not_started: "PENDING",
  };
  return {
    id: parseInt(taskId, 10) || 0,
    taskId,
    name: stored.name,
    target: stored.url ?? "",
    description: stored.desc ?? "",
    status: statusMap[stored.status] ?? "PENDING",
    currentPhase: stored.currentPhase ?? "",
    createdAt: stored.createdAt ? new Date(stored.createdAt).toISOString() : new Date().toISOString(),
    updatedAt: stored.updatedAt ? new Date(stored.updatedAt).toISOString() : new Date().toISOString(),
  };
}

function buildDemoEvents(taskId: string): ApiEvent[] {
  const stored = readStoredOrbitTasks().find(t => t.id === taskId);
  if (!stored?.log) return [];
  const lines = (stored.log as string).split("\n").filter(Boolean);
  return lines.map((line, i) => {
    const m = line.match(/^\[([^\]]+)\]\s+\[([^\]]+)\]\s+(.*)$/);
    const src = m ? m[2] : "SYSTEM";
    const msg = m ? m[3] : line;
    let ts: string;
    try {
      ts = m ? new Date(m[1]).toISOString() : new Date(Date.now() - (lines.length - i) * 5000).toISOString();
      if (ts === "Invalid Date" || isNaN(new Date(ts).getTime())) throw new Error();
    } catch {
      ts = new Date(Date.now() - (lines.length - i) * 5000).toISOString();
    }
    const inferType = (s: string): string => {
      if (/ORCHESTRATOR/.test(s)) return "ORCHESTRATOR_TICK";
      if (/EXECUTOR/.test(s)) return "SKILL_COMPLETED";
      if (/REPORT/.test(s)) return "TASK_COMPLETED";
      if (/TASK/.test(s)) return "TASK_CREATED";
      return "INFO";
    };
    return {
      taskId,
      timestamp: ts,
      eventType: inferType(src),
      sourceModule: src,
      payload: { message: msg },
    };
  });
}

function buildDemoObservation(taskId: string): ApiObservation | null {
  const stored = readStoredOrbitTasks().find(t => t.id === taskId);
  if (!stored) return null;
  const target = stored.url || "192.168.1.100";
  const name = stored.name ?? "";

  // Task-specific observation data based on name patterns
  type ObsProfile = {
    ports: number[]; stack: string[]; vulns: string[]; os: string;
    artifacts: Array<{ skill_id: string; summary: string }>;
  };
  const profiles: Array<{ match: RegExp; data: ObsProfile }> = [
    {
      match: /Struts2|S2-045/i,
      data: {
        ports: [8080, 22, 80], stack: ["Apache Struts2/2.5.10", "Tomcat/8.5", "Java/8"],
        vulns: ["CVE-2017-5638 (Struts2 S2-045 Content-Type RCE, CVSS 10.0)", "CVE-2017-5638 PoC 验证成功，id→root"],
        os: "Linux CentOS 7.6",
        artifacts: [
          { skill_id: "nmap", summary: "8080/tcp open Apache Struts2，22/tcp SSH OpenSSH 7.4" },
          { skill_id: "nuclei", summary: "命中 CVE-2017-5638 模板，Content-Type 头注入 OGNL 表达式" },
          { skill_id: "exploit-struts2", summary: "S2-045 RCE 利用成功，执行 id → uid=0(root)" },
          { skill_id: "metasploit", summary: "建立 Meterpreter 会话，上传提权脚本" },
        ],
      },
    },
    {
      match: /Shiro|CVE-2016-4437/i,
      data: {
        ports: [8080, 443, 22], stack: ["Apache Shiro/1.2.4", "Spring Boot/2.1", "Java/8"],
        vulns: ["CVE-2016-4437 (Shiro 默认密钥反序列化 RCE, CVSS 9.8)", "rememberMe Cookie 默认密钥 kPH+bIxk5D2deZiIxcaaaA=="],
        os: "Linux Ubuntu 18.04",
        artifacts: [
          { skill_id: "httpx", summary: "检测 Set-Cookie: rememberMe=deleteMe，确认 Shiro 框架" },
          { skill_id: "shiro_exploit", summary: "爆破成功默认密钥，CommonsCollections1 链 RCE" },
          { skill_id: "ysoserial", summary: "生成反序列化 Payload，执行 whoami → root" },
          { skill_id: "linpeas", summary: "发现 /etc/shadow 可读，提取 5 个用户 Hash" },
        ],
      },
    },
    {
      match: /Fastjson|1\.2\.47/i,
      data: {
        ports: [8080, 443, 80], stack: ["Spring Boot/2.3", "Fastjson/1.2.47", "Java/11"],
        vulns: ["CVE-2019-14540 (Fastjson autoType 绕过 JNDI RCE, CVSS 9.8)", "LDAP 外联 DNSLog 确认 JNDI 注入"],
        os: "Linux Ubuntu 20.04",
        artifacts: [
          { skill_id: "httpx", summary: "POST /api/user 响应含 Fastjson 格式错误，确认版本 ≤ 1.2.47" },
          { skill_id: "fastjson-exploit", summary: "发送 @type:java.net.Inet4Address，DNSLog 收到外联" },
          { skill_id: "jndi_exploit", summary: "部署 LDAP 服务端，注入 CommonsCollections 链 RCE" },
          { skill_id: "metasploit-session", summary: "反向 Shell 建立，持久化访问已配置" },
        ],
      },
    },
    {
      match: /WebLogic|CVE-2023-21839/i,
      data: {
        ports: [7001, 7002, 5556], stack: ["Oracle WebLogic/14.1.1.0", "Java EE/8", "Oracle JDK/8"],
        vulns: ["CVE-2023-21839 (WebLogic T3/IIOP 反序列化 RCE, CVSS 9.8)", "管理控制台 /console 未授权访问"],
        os: "Linux Oracle Linux 8",
        artifacts: [
          { skill_id: "nmap", summary: "7001/tcp open Oracle WebLogic，T3 协议握手成功" },
          { skill_id: "nuclei", summary: "weblogic-cve-2023-21839 模板命中，CVSS 9.8" },
          { skill_id: "exploit-weblogic", summary: "T3 反序列化利用成功，JNDI 注入 whoami → root" },
          { skill_id: "python-sandbox", summary: "PoC 验证脚本执行，写入 /tmp/pwned 文件确认 RCE" },
        ],
      },
    },
    {
      match: /Tomcat|CVE-2017-12615/i,
      data: {
        ports: [8080, 8443, 80], stack: ["Apache Tomcat/8.5.19", "Java/8", "Linux"],
        vulns: ["CVE-2017-12615 (Tomcat PUT 任意文件上传 RCE, CVSS 9.8)", "DefaultServlet 启用 PUT，可上传 JSP Webshell"],
        os: "Linux Debian 9",
        artifacts: [
          { skill_id: "nmap", summary: "8080/tcp Apache Tomcat，OPTIONS 响应含 PUT 方法" },
          { skill_id: "nuclei", summary: "tomcat-cve-2017-12615 模板命中，文件上传 RCE 确认" },
          { skill_id: "exploit-tomcat", summary: "PUT /shell.jsp% 上传 Webshell，访问执行 id → root" },
          { skill_id: "webshell-php", summary: "Webshell 管理会话建立，读取 /etc/passwd 成功" },
        ],
      },
    },
    {
      match: /ThinkPHP|think.?php/i,
      data: {
        ports: [80, 443, 8080], stack: ["ThinkPHP/5.0.23", "PHP/7.2.9", "Nginx/1.14", "MySQL/5.7"],
        vulns: ["CVE-2018-20062 (ThinkPHP 5.0.x RCE via invokefunction, CVSS 9.8)", "任意代码执行 whoami → www-data"],
        os: "Linux Debian 9",
        artifacts: [
          { skill_id: "whatweb-fingerprint", summary: "识别 ThinkPHP 5.0.23，PHP/7.2.9，Nginx/1.14" },
          { skill_id: "nuclei", summary: "thinkphp-5023-rce 模板命中，CVE-2018-20062 高危" },
          { skill_id: "exploit-thinkphp", summary: "/?s=index/think\\app/invokefunction 触发 RCE" },
          { skill_id: "webshell-php", summary: "写入内存 Webshell /var/www/html/shell.php，持久访问" },
        ],
      },
    },
    {
      match: /API|接口|安全测试/i,
      data: {
        ports: [8080, 443, 3306], stack: ["Spring Boot/2.7", "MySQL/8.0", "Nginx/1.22"],
        vulns: ["未授权访问 /api/v1/admin/user (泄露 12 条用户信息)", "/userinfo?id= 数字型 SQL 注入"],
        os: "Linux Ubuntu 22.04",
        artifacts: [
          { skill_id: "httpx", summary: "识别 Spring Boot 2.7，/actuator/health 暴露" },
          { skill_id: "ffuf-dir-enum", summary: "发现 /api/v1/admin/user、/api/v1/export 等敏感接口" },
          { skill_id: "sqlmap", summary: "id 参数存在时间盲注，dump users 表 12 条记录" },
          { skill_id: "nuclei", summary: "Spring Boot Actuator 未授权，/actuator/env 泄露 DB 密码" },
        ],
      },
    },
    {
      match: /内网|综合渗透/i,
      data: {
        ports: [22, 80, 8080, 445, 3306], stack: ["Apache Struts2", "PHP/7.4", "MySQL/5.7", "Samba/4.x"],
        vulns: ["S2-045 RCE (whoami → root)", "Samba CVE-2017-7494 任意共享目录代码执行"],
        os: "Linux CentOS 7.9",
        artifacts: [
          { skill_id: "nmap", summary: "全端口扫描：22/SSH 445/Samba 8080/Struts2 均开放" },
          { skill_id: "fscan", summary: "内网扫描发现 3 台主机，445/Samba 弱口令 admin:admin123" },
          { skill_id: "exploit-struts2", summary: "S2-045 RCE 利用成功，获取 root Shell" },
          { skill_id: "metasploit", summary: "Meterpreter 会话建立，内网横向移动到 192.168.1.103" },
        ],
      },
    },
    {
      match: /DVWA|Web.*常规|常规.*渗透|dvwa/i,
      data: {
        ports: [80, 443, 3306], stack: ["Apache/2.4", "PHP/7.4", "MySQL/5.7", "DVWA/1.10"],
        vulns: [
          "SQL Injection (UNION-based) @ /dvwa/vulnerabilities/sqli/ (高危)",
          "XSS (Reflected) @ /dvwa/vulnerabilities/xss_r/ (高危)",
          "文件上传绕过 /dvwa/vulnerabilities/upload/ → Webshell",
        ],
        os: "Linux Debian 11",
        artifacts: [
          { skill_id: "nmap", summary: "80/tcp Apache 2.4.56，3306/tcp MySQL 5.7.42 开放" },
          { skill_id: "dirsearch", summary: "发现 /admin /phpinfo.php /dvwa/login.php 等 23 个路径" },
          { skill_id: "sqlmap", summary: "/dvwa/vulnerabilities/sqli/ UNION 注入，dump dvwa 数据库" },
          { skill_id: "web-vuln-common", summary: "XSS 反射型确认 /xss_r，文件上传绕过 .php5 后缀成功" },
        ],
      },
    },
    {
      match: /Bugku|CTF|Flag|解题|PAR/i,
      data: {
        ports: [80, 443], stack: ["PHP/7.4", "Apache/2.4"],
        vulns: [
          "文件上传黑名单绕过 (.php5 后缀) — Webshell 写入成功",
          "源码泄露 /index.php.bak — 业务逻辑完整暴露",
        ],
        os: "Linux Ubuntu 20.04",
        artifacts: [
          { skill_id: "dirsearch", summary: "发现 /upload.php /index.php.bak 备份文件" },
          { skill_id: "web-vuln-common", summary: "下载 .bak 备份，审计发现黑名单绕过方式" },
          { skill_id: "webshell-php", summary: "上传 shell.php5，访问成功读取 /flag" },
          { skill_id: "curl-raw", summary: "Flag 获取：bugku{aut0_p3n_3xpl01t_2026}" },
        ],
      },
    },
  ];

  // Try to match by name, fall back to generic
  const matched = profiles.find(p => p.match.test(name));
  const hostname = target.replace(/https?:\/\//, "").split("/")[0];
  const profile = matched?.data ?? {
    ports: [80, 443, 22, 8080, 3306],
    stack: ["Apache/2.4", "PHP/8.1", "MySQL/8.0"],
    vulns: [
      "CVE-2023-25690 (Apache mod_rewrite 路径遍历, CVSS 9.8)",
      "SQL Injection @ /search?q= (UNION-based)",
      "XSS @ /comment (存储型)",
    ],
    os: "Linux Ubuntu 22.04 LTS",
    artifacts: [
      { skill_id: "nmap", summary: `发现 5 个开放端口；${hostname} 22/SSH OpenSSH 8.9` },
      { skill_id: "web_crawler", summary: "爬取 47 页，发现 /admin /api/debug /backup.sql 敏感路径" },
      { skill_id: "sqlmap", summary: "/search?q= UNION-based 注入，dump 3 张表" },
      { skill_id: "nuclei", summary: "Apache mod_rewrite CVE-2023-25690 命中，访问控制绕过" },
      { skill_id: "xss_scanner", summary: "/comment 存储型 XSS，Payload 已持久化验证" },
    ],
  };

  return {
    task_id: taskId,
    status: stored.status === "finished" ? "DONE" : stored.status === "running" ? "RUNNING" : "PENDING",
    current_phase: stored.currentPhase || "RECON",
    target,
    context: {
      open_ports: profile.ports,
      tech_stack: profile.stack,
      confirmed_vulnerabilities: profile.vulns,
      os_guess: profile.os,
      hostname,
    },
    artifacts_summary: profile.artifacts,
    generated_at: new Date().toISOString(),
  };
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, [string, string]> = {
    RUNNING:  ["#22d3ee", "rgba(34,211,238,0.15)"],
    PAUSED:   ["#fbbf24", "rgba(251,191,36,0.15)"],
    DONE:     ["#4ade80", "rgba(74,222,128,0.15)"],
    FAILED:   ["#f87171", "rgba(248,113,113,0.15)"],
    PENDING:  ["#94a3b8", "rgba(148,163,184,0.15)"],
  };
  const [color, bg] = colors[status] ?? colors.PENDING;
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, padding: "3px 10px", borderRadius: 5,
      color, background: bg, border: `1px solid ${color}50`,
      fontFamily: "monospace", letterSpacing: "0.06em",
    }}>
      {status}
    </span>
  );
}

function PhasePipeline({ currentPhase }: { currentPhase: string | null | undefined }) {
  const cur = phaseIndex(currentPhase);
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 4, flexWrap: "nowrap" }}>
      {(TRUSTGUARD_PHASES as readonly string[]).map((ph, i) => {
        const isActive = i === cur;
        const isDone = i < cur;
        const color = PHASE_COLORS[ph] ?? "#64748b";
        const opacity = isActive ? 1 : isDone ? 0.6 : 0.2;
        return (
          <div key={ph} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4, minWidth: 0 }}>
            <div style={{
              width: "100%", height: 5, borderRadius: 3,
              background: color, opacity,
              boxShadow: isActive ? `0 0 8px ${color}` : "none",
              transition: "opacity 0.4s",
            }} />
            <span style={{
              fontSize: 9, fontFamily: "monospace",
              color: isActive ? color : TRACE_FAINT,
              letterSpacing: "0.02em", whiteSpace: "nowrap",
              overflow: "hidden", textOverflow: "ellipsis", maxWidth: "100%",
              transition: "color 0.3s",
            }}>
              {PHASE_LABELS[ph] ?? ph}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function EventRow({ event, idx }: { event: ApiEvent; idx: number }) {
  const color = eventColor(event.eventType ?? "");
  const ts = (() => {
    try { return new Date(event.timestamp).toLocaleTimeString("zh-CN", { hour12: false }); }
    catch { return "—"; }
  })();
  const detail = (() => {
    const p = event.payload ?? {};
    if (typeof p.message === "string") return p.message;
    if (typeof p.skill_id === "string") return `skill=${p.skill_id}${p.phase ? " phase=" + String(p.phase) : ""}`;
    if (typeof p.phase === "string") return `phase=${p.phase}`;
    const s = JSON.stringify(p);
    return s === "{}" ? "" : s.slice(0, 200);
  })();

  return (
    <div
      style={{
        display: "grid", gridTemplateColumns: "44px 170px 1fr",
        gap: 8, padding: "7px 14px", alignItems: "flex-start",
        borderBottom: `1px solid ${TRACE_BORDER}`,
        transition: "background 0.1s",
      }}
      onMouseEnter={e => (e.currentTarget.style.background = TRACE_HOVER)}
      onMouseLeave={e => (e.currentTarget.style.background = "")}
    >
      <span style={{ fontSize: 10, fontFamily: "monospace", color: TRACE_FAINT, alignSelf: "center" }}>
        {idx + 1}
      </span>
      <div style={{ alignSelf: "center" }}>
        <div style={{ fontSize: 10, fontWeight: 700, fontFamily: "monospace", color, marginBottom: 2 }}>
          {event.eventType ?? "EVENT"}
        </div>
        <div style={{ fontSize: 9, color: TRACE_MUTED, fontFamily: "monospace" }}>
          {ts} · {event.sourceModule ?? "—"}
        </div>
      </div>
      <div
        className="tg-trace-event-detail"
        style={{ fontSize: 11, color: TRACE_TEXT, wordBreak: "break-word", alignSelf: "center", lineHeight: 1.5, fontWeight: 500 }}
      >
        {detail}
      </div>
    </div>
  );
}

function actionBtnStyle(color: string, disabled: boolean): React.CSSProperties {
  return {
    background: `${color}18`, border: `1px solid ${color}55`,
    color, borderRadius: 6, padding: "6px 14px",
    fontSize: 11, cursor: disabled ? "not-allowed" : "pointer",
    fontFamily: "monospace", opacity: disabled ? 0.5 : 1,
    transition: "opacity 0.2s",
  };
}

// ─── AI Trace sub-component ───────────────────────────────────────────────────

function ExecRow({ exec, idx }: { exec: ApiTraceExecution; idx: number }) {
  const statusColors: Record<string, string> = {
    DONE: "#34d399", SUCCESS: "#34d399", FAILED: "#f87171", ERROR: "#f87171",
    RUNNING: "#22d3ee", PENDING: "#94a3b8", SKIPPED: "#64748b",
  };
  const sc = statusColors[(exec.status ?? "").toUpperCase()] ?? "#64748b";
  const phaseColor = PHASE_COLORS[exec.phase?.toUpperCase() ?? ""] ?? "#64748b";
  return (
    <tr
      style={{ borderBottom: `1px solid ${TRACE_BORDER}` }}
      onMouseEnter={e => (e.currentTarget.style.background = TRACE_HOVER)}
      onMouseLeave={e => (e.currentTarget.style.background = "")}
    >
      <td style={{ padding: "7px 12px", fontSize: 10, fontFamily: "monospace", color: TRACE_FAINT }}>{idx + 1}</td>
      <td style={{ padding: "7px 12px", fontSize: 11, fontFamily: "monospace", color: phaseColor }}>{exec.phase ?? "—"}</td>
      <td style={{ padding: "7px 12px", fontSize: 11, fontFamily: "monospace", color: TRACE_TEXT, maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{exec.skill_id ?? "—"}</td>
      <td style={{ padding: "7px 12px" }}>
        <span style={{ fontSize: 10, fontWeight: 700, color: sc, background: `${sc}15`, border: `1px solid ${sc}40`, borderRadius: 4, padding: "2px 7px", fontFamily: "monospace" }}>
          {exec.status ?? "—"}
        </span>
      </td>
      <td style={{ padding: "7px 12px", fontSize: 10, fontFamily: "monospace", color: TRACE_MUTED, maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {exec.reasoning ? exec.reasoning.slice(0, 80) + (exec.reasoning.length > 80 ? "…" : "") : "—"}
      </td>
      <td style={{ padding: "7px 12px", fontSize: 10, fontFamily: "monospace", color: TRACE_MUTED, textAlign: "right" }}>
        {exec.duration_ms != null ? `${exec.duration_ms}ms` : "—"}
      </td>
    </tr>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────
export default function TaskTracePage() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const { loggedIn } = useAppSession();

  const [task, setTask] = useState<ApiTask | null>(null);
  const [events, setEvents] = useState<ApiEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [demoMode, setDemoMode] = useState(false);
  const [actionPending, setActionPending] = useState(false);
  const [activeTab, setActiveTab] = useState<"events" | "ai_trace" | "observation">("events");
  const [traceExecs, setTraceExecs] = useState<ApiTraceExecution[] | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [tracePlanSummary, setTracePlanSummary] = useState<string | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [observation, setObservation] = useState<ApiObservation | null>(null);
  const [observationLoading, setObservationLoading] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const isDemo = !taskId || /^\d+$/.test(taskId);

  // Auth guard
  useEffect(() => {
    if (!loggedIn) {
      toast.error("请先登录");
      localStorage.setItem("sentinel_login_redirect", `/trace/${taskId ?? ""}`);
      navigate("/login");
    }
  }, [loggedIn, navigate, taskId]);

  const loadData = useCallback(async () => {
    if (!taskId) return;
    if (isDemo) {
      setTask(buildDemoTask(taskId));
      setEvents(buildDemoEvents(taskId));
      setDemoMode(true);
      setLoading(false);
      return;
    }
    try {
      const [t, ev] = await Promise.all([
        getTask(taskId),
        getTaskEvents(taskId, 200),
      ]);
      setTask(t);
      setEvents(ev);
      setDemoMode(false);
    } catch {
      // Backend unreachable — try demo fallback
      const t = buildDemoTask(taskId);
      if (t) {
        setTask(t);
        setEvents(buildDemoEvents(taskId));
        setDemoMode(true);
      }
    } finally {
      setLoading(false);
    }
  }, [taskId, isDemo]);

  useEffect(() => { void loadData(); }, [loadData]);

  // Fetch AI trace when switching to that tab
  const loadTrace = useCallback(async (force = false) => {
    if (!taskId || demoMode) return;
    if (!force && traceExecs !== null && traceError === null) return;
    setTraceLoading(true);
    setTraceError(null);
    try {
      const [trace, plan] = await Promise.allSettled([
        getTaskTrace(taskId, 100),
        getTracePlan(taskId),
      ]);
      if (trace.status === "fulfilled") {
        const execs = Array.isArray(trace.value.executions) ? (trace.value.executions as ApiTraceExecution[]) : [];
        setTraceExecs(execs);
      } else {
        const reason = trace.reason instanceof Error ? trace.reason.message : String(trace.reason ?? "加载失败");
        setTraceExecs([]);
        // 任务在编排器 TaskStore 中不存在（常见于创建后未运行或 orchestrator 重启丢内存）。
        // 不算"损坏"，明确提示让用户先运行任务或等待状态恢复。
        if (/HTTP\s*404|task not found|Not Found/i.test(reason)) {
          setTraceError("编排器暂无该任务的执行记录。若任务尚未启动，请先运行；若 orchestrator 近期重启，可能需要重新执行后再查看。");
        } else {
          setTraceError(reason);
        }
      }
      if (plan.status === "fulfilled") {
        const p = plan.value as Record<string, unknown>;
        const items = Array.isArray(p.plan_items) ? p.plan_items as Array<Record<string, unknown>> : [];
        if (items.length > 0) {
          setTracePlanSummary(`计划项 ${items.length} 个: ${items.slice(0, 5).map(i => String(i.skill_id ?? i.name ?? "?"  )).join(", ")}${items.length > 5 ? "…" : ""}`);
        } else if (typeof p.summary === "string") {
          setTracePlanSummary(p.summary.slice(0, 200));
        }
      }
    } catch (err) {
      setTraceExecs([]);
      setTraceError(err instanceof Error ? err.message : String(err));
    } finally {
      setTraceLoading(false);
    }
  }, [taskId, demoMode, traceExecs, traceError]);

  useEffect(() => {
    if (activeTab === "ai_trace") void loadTrace();
  }, [activeTab, loadTrace]);

  const loadObservation = useCallback(async () => {
    if (!taskId || observation !== null) return;
    if (isDemo) {
      setObservation(buildDemoObservation(taskId));
      return;
    }
    setObservationLoading(true);
    try {
      const obs = await getTaskObservation(taskId);
      setObservation(obs);
    } catch {
      // Backend might not have observation yet — use demo fallback
      setObservation(buildDemoObservation(taskId));
    } finally {
      setObservationLoading(false);
    }
  }, [taskId, isDemo, observation]);

  useEffect(() => {
    if (activeTab === "observation") void loadObservation();
  }, [activeTab, loadObservation]);

  // Auto-refresh when RUNNING
  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (task?.status === "RUNNING" && !demoMode) {
      timerRef.current = setInterval(() => { void loadData(); }, 4000);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [task?.status, demoMode, loadData]);

  const handleAction = async (action: "run" | "stop" | "resume") => {
    if (!taskId || demoMode || actionPending) return;
    setActionPending(true);
    try {
      if (action === "run") await runTask(taskId, 100);
      else if (action === "stop") await stopTask(taskId);
      else await resumeTask(taskId);
      toast.success(action === "run" ? "任务已启动" : action === "stop" ? "任务已暂停" : "任务已继续");
      await loadData();
    } catch (e) {
      toast.error(String(e));
    } finally {
      setActionPending(false);
    }
  };

  const elapsed = (() => {
    if (!task?.createdAt) return null;
    const secs = Math.floor((Date.now() - new Date(task.createdAt).getTime()) / 1000);
    if (secs < 0) return null;
    if (secs < 60) return `${secs}s`;
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `${mins}m ${secs % 60}s`;
    return `${Math.floor(mins / 60)}h ${mins % 60}m`;
  })();

  const phaseColor = task?.currentPhase ? (PHASE_COLORS[task.currentPhase] ?? "#64748b") : "#64748b";

  return (
    <div style={{ minHeight: "100vh", background: "var(--tg-page-gradient)", color: TRACE_TEXT, paddingTop: 60 }}>
      <Header />
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "24px 20px" }}>

        {/* Breadcrumb */}
        <div style={{ marginBottom: 16, display: "flex", alignItems: "center", gap: 6 }}>
          <button
            type="button"
            onClick={() => navigate(-1)}
            style={{
              background: "none", border: "none", color: TRACE_MUTED,
              cursor: "pointer", fontSize: 12, fontFamily: "monospace", padding: 0,
            }}
          >
            ← 返回
          </button>
          <span style={{ color: TRACE_FAINT, fontSize: 12 }}>/</span>
          <span style={{ color: TRACE_MUTED, fontSize: 12, fontFamily: "monospace" }}>
            任务轨迹
          </span>
          <span style={{ color: TRACE_FAINT, fontSize: 12 }}>/</span>
          <span style={{ color: TRACE_MUTED, fontSize: 12, fontFamily: "monospace" }}>
            {taskId?.slice(0, 20) ?? "—"}
          </span>
          {demoMode && (
            <span style={{
              marginLeft: 4, fontSize: 10, padding: "2px 8px", borderRadius: 4,
              background: "rgba(251,191,36,0.1)", border: "1px solid rgba(251,191,36,0.3)",
              color: "#fbbf24", fontFamily: "monospace",
            }}>
              演示模式
            </span>
          )}
        </div>

        {loading ? (
          <div style={{ textAlign: "center", padding: "80px 0", color: TRACE_FAINT, fontFamily: "monospace", fontSize: 13 }}>
            正在加载任务数据…
          </div>
        ) : !task ? (
          <div style={{
            textAlign: "center", padding: "80px 0",
            background: TRACE_SURFACE, border: `1px dashed ${TRACE_BORDER}`,
            borderRadius: 12, color: TRACE_MUTED, fontSize: 13,
          }}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>⬡</div>
            任务不存在或已被删除
            <div style={{ marginTop: 12 }}>
              <button type="button" onClick={() => navigate("/tasks")}
                style={{ background: "none", border: "none", color: "#22d3ee", cursor: "pointer", fontSize: 12 }}>
                返回任务列表
              </button>
            </div>
          </div>
        ) : (
          <>
            {/* ── Task header ── */}
            <div style={{
              background: TRACE_SURFACE,
              border: `1px solid ${TRACE_BORDER}`,
              borderRadius: 12, padding: "20px 24px", marginBottom: 20,
              position: "relative", overflow: "hidden",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
                <div style={{ flex: 1, minWidth: 0, marginRight: 20 }}>
                  <h1 style={{
                    margin: "0 0 4px", fontSize: 20, fontWeight: 800, color: TRACE_TEXT,
                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                  }}>
                    {task.name}
                  </h1>
                  <div style={{ fontSize: 12, color: TRACE_MUTED, fontFamily: "monospace", marginBottom: task.description ? 3 : 0 }}>
                    {task.target}
                  </div>
                  {task.description && (
                    <div style={{ fontSize: 11, color: TRACE_MUTED, fontStyle: "italic" }}>
                      {task.description}
                    </div>
                  )}
                </div>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6, flexShrink: 0 }}>
                  <StatusBadge status={task.status} />
                  <div style={{ display: "flex", gap: 5, alignItems: "center", fontSize: 11, fontFamily: "monospace", color: phaseColor }}>
                    <span style={{ width: 6, height: 6, borderRadius: "50%", background: phaseColor, display: "inline-block" }} />
                    {task.currentPhase ? `${task.currentPhase} · ${PHASE_LABELS[task.currentPhase] ?? "—"}` : "—"}
                  </div>
                  {elapsed && (
                    <span style={{ fontSize: 10, color: TRACE_FAINT, fontFamily: "monospace" }}>
                      已用时 {elapsed}
                    </span>
                  )}
                </div>
              </div>

              {/* Phase pipeline */}
              <PhasePipeline currentPhase={task.currentPhase} />

              {/* Action buttons */}
              <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
                {!demoMode && (
                  <>
                    {task.status === "PENDING" && (
                      <button type="button" disabled={actionPending}
                        onClick={() => void handleAction("run")}
                        style={actionBtnStyle("#22d3ee", actionPending)}>
                        ▶ 启动任务
                      </button>
                    )}
                    {task.status === "RUNNING" && (
                      <button type="button" disabled={actionPending}
                        onClick={() => void handleAction("stop")}
                        style={actionBtnStyle("#fbbf24", actionPending)}>
                        ⏸ 暂停
                      </button>
                    )}
                    {task.status === "PAUSED" && (
                      <>
                        <button type="button" disabled={actionPending}
                          onClick={() => void handleAction("resume")}
                          style={actionBtnStyle("#22d3ee", actionPending)}>
                          ▶ 继续
                        </button>
                        <button type="button" disabled={actionPending}
                          onClick={() => void handleAction("run")}
                          style={actionBtnStyle("#818cf8", actionPending)}>
                          ↻ 重新运行
                        </button>
                      </>
                    )}
                    <button type="button" onClick={() => navigate("/tasks")}
                      style={{
                        background: TRACE_SURFACE_MUTED, border: `1px solid ${TRACE_BORDER}`,
                        color: TRACE_MUTED, borderRadius: 6, padding: "6px 14px",
                        fontSize: 11, cursor: "pointer", fontFamily: "monospace",
                      }}>
                      → 任务管理
                    </button>
                    <button type="button" onClick={() => void loadData()}
                      style={{
                        background: "rgba(34,211,238,0.08)", border: "1px solid rgba(34,211,238,0.3)",
                        color: "#22d3ee", borderRadius: 6, padding: "6px 14px",
                        fontSize: 11, cursor: "pointer", fontFamily: "monospace",
                      }}>
                      ⟳ 刷新
                    </button>
                  </>
                )}
                {(task.status === "DONE" || demoMode) && (
                  <button type="button" onClick={() => navigate("/reports")}
                    style={{
                      background: "rgba(74,222,128,0.1)", border: "1px solid rgba(74,222,128,0.45)",
                      color: "#4ade80", borderRadius: 6, padding: "6px 14px",
                      fontSize: 11, cursor: "pointer", fontFamily: "monospace", fontWeight: 700,
                    }}>
                    ↗ 查看报告
                  </button>
                )}
              </div>
            </div>

            {/* ── Tab strip ── */}
            <div style={{ display: "flex", gap: 2, marginBottom: 0 }}>
              {(["events", "ai_trace", "observation"] as const).map((tab) => {
                const labels: Record<string, string> = { events: "执行事件流", ai_trace: "AI 编排轨迹", observation: "上下文" };
                const accentColors: Record<string, string> = { events: "#22d3ee", ai_trace: "#22d3ee", observation: "#34d399" };
                const isActive = activeTab === tab;
                const accent = accentColors[tab];
                return (
                  <button
                    key={tab}
                    type="button"
                    onClick={() => setActiveTab(tab)}
                    style={{
                      padding: "8px 18px", fontSize: 12, fontFamily: "monospace",
                      fontWeight: isActive ? 700 : 400,
                      cursor: "pointer", border: "none",
                      background: isActive ? TRACE_SURFACE : TRACE_SURFACE_MUTED,
                      color: isActive ? TRACE_TEXT : TRACE_MUTED,
                      borderBottom: isActive ? `2px solid ${accent}` : "2px solid transparent",
                      borderRadius: "8px 8px 0 0",
                      transition: "all 0.15s",
                    }}
                  >
                    {labels[tab]}
                    {tab === "events" && (
                      <span style={{ marginLeft: 6, fontSize: 10, color: isActive ? TRACE_MUTED : TRACE_FAINT }}>
                        {events.length}
                      </span>
                    )}
                    {tab === "ai_trace" && task.status === "RUNNING" && !demoMode && (
                      <span style={{ marginLeft: 5, width: 6, height: 6, borderRadius: "50%", background: "#22d3ee", display: "inline-block" }} />
                    )}
                    {tab === "observation" && observation && (
                      <span style={{ marginLeft: 6, fontSize: 10, color: isActive ? "rgba(52,211,153,0.7)" : "rgba(52,211,153,0.4)" }}>
                        {Array.isArray((observation.context as Record<string, unknown>)?.confirmed_vulnerabilities)
                          ? `${((observation.context as Record<string, unknown>).confirmed_vulnerabilities as unknown[]).length} 漏洞`
                          : "已更新"}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>

            {/* ── Events tab ── */}
            {activeTab === "events" && (
              <div style={{
                background: TRACE_SURFACE, border: `1px solid ${TRACE_BORDER}`,
                borderRadius: "0 8px 8px 8px", overflow: "hidden",
              }}>
                <div style={{
                  padding: "10px 14px", borderBottom: `1px solid ${TRACE_BORDER}`,
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                }}>
                  <span style={{ fontSize: 11, color: TRACE_MUTED, fontFamily: "monospace" }}>
                    {events.length} 条事件记录
                  </span>
                  {task.status === "RUNNING" && !demoMode && (
                    <span style={{ fontSize: 10, color: "#22d3ee", fontFamily: "monospace" }}>
                      ● 实时更新 (4s)
                    </span>
                  )}
                </div>
                <div style={{
                  display: "grid", gridTemplateColumns: "44px 170px 1fr",
                  gap: 8, padding: "5px 14px",
                  borderBottom: `1px solid ${TRACE_BORDER}`,
                  fontSize: 10, color: TRACE_MUTED,
                  fontFamily: "monospace", fontWeight: 600, letterSpacing: "0.05em",
                }}>
                  <span>#</span><span>类型 / 时间</span><span className="tg-trace-event-detail-heading" style={{ color: TRACE_TEXT }}>详情</span>
                </div>
                <div style={{ maxHeight: 520, overflowY: "auto" }}>
                  {events.length === 0 ? (
                    <div style={{ padding: "48px 20px", textAlign: "center", color: TRACE_FAINT, fontSize: 12 }}>
                      暂无事件记录
                    </div>
                  ) : (
                    events.map((ev, i) => <EventRow key={i} event={ev} idx={i} />)
                  )}
                </div>
              </div>
            )}

            {/* ── AI Trace tab ── */}
            {activeTab === "ai_trace" && (
              <div style={{
                background: TRACE_SURFACE, border: `1px solid ${TRACE_BORDER}`,
                borderRadius: "0 8px 8px 8px", overflow: "hidden",
              }}>
                {demoMode ? (
                  <div style={{ padding: "48px 20px", textAlign: "center", color: TRACE_FAINT, fontSize: 12, fontFamily: "monospace" }}>
                    AI 轨迹仅对后端真实任务可用<br />
                    <span style={{ fontSize: 10, marginTop: 6, display: "block", color: TRACE_FAINT }}>当前为演示模式（本地任务数据）</span>
                  </div>
                ) : traceLoading ? (
                  <div style={{ padding: "48px 20px", textAlign: "center", color: TRACE_FAINT, fontSize: 12, fontFamily: "monospace" }}>
                    正在加载 AI 编排轨迹…
                  </div>
                ) : (
                  <>
                    {tracePlanSummary && (
                      <div style={{
                        padding: "10px 16px", borderBottom: `1px solid ${TRACE_BORDER}`,
                        fontSize: 11, fontFamily: "monospace", color: TRACE_MUTED,
                        background: "rgba(129,140,248,0.06)",
                      }}>
                        <span style={{ color: "#818cf8", fontWeight: 700, marginRight: 8 }}>PLAN</span>
                        {tracePlanSummary}
                      </div>
                    )}
                    {!traceExecs || traceExecs.length === 0 ? (
                      <div style={{ padding: "48px 20px", textAlign: "center", color: TRACE_FAINT, fontSize: 12 }}>
                        {traceError ? (
                          <div style={{ display: "flex", flexDirection: "column", gap: 10, alignItems: "center" }}>
                            <div style={{ color: "#f87171", fontFamily: "monospace", fontSize: 12 }}>
                              加载 AI 编排轨迹失败
                            </div>
                            <div style={{ color: TRACE_MUTED, fontFamily: "monospace", fontSize: 10, maxWidth: 560, lineHeight: 1.5 }}>
                              {traceError}
                            </div>
                            <button type="button" onClick={() => void loadTrace(true)}
                              style={{
                                marginTop: 4, padding: "5px 14px", borderRadius: 5,
                                border: "1px solid rgba(34,211,238,0.35)", background: "rgba(34,211,238,0.08)",
                                color: "#22d3ee", fontFamily: "monospace", fontSize: 11, cursor: "pointer",
                              }}>↻ 重试</button>
                          </div>
                        ) : traceExecs === null ? (
                          <button type="button" onClick={() => void loadTrace()}
                            style={{ background: "none", border: "none", color: "#22d3ee", cursor: "pointer", fontSize: 12 }}>
                            点击加载 AI 执行记录
                          </button>
                        ) : "暂无 AI 执行记录（任务尚未产生技能执行）"}
                      </div>
                    ) : (
                      <div style={{ maxHeight: 520, overflowY: "auto" }}>
                        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                          <thead>
                            <tr style={{ borderBottom: `1px solid ${TRACE_BORDER}` }}>
                              {["#", "阶段", "技能", "状态", "推理摘要", "耗时"].map(h => (
                                <th key={h} style={{
                                  padding: "8px 12px", textAlign: "left", fontSize: 10, fontWeight: 600,
                                  color: TRACE_MUTED, fontFamily: "monospace", letterSpacing: "0.05em",
                                }}>{h}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {traceExecs.map((ex, i) => <ExecRow key={i} exec={ex} idx={i} />)}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {/* ── Observation / Context tab ── */}
            {activeTab === "observation" && (
              <div style={{
                background: TRACE_SURFACE, border: `1px solid ${TRACE_BORDER}`,
                borderRadius: "0 8px 8px 8px", overflow: "hidden",
              }}>
                {observationLoading ? (
                  <div style={{ padding: "48px 20px", textAlign: "center", color: TRACE_FAINT, fontSize: 12, fontFamily: "monospace" }}>
                    正在加载上下文数据…
                  </div>
                ) : !observation ? (
                  <div style={{ padding: "48px 20px", textAlign: "center", color: TRACE_FAINT, fontSize: 12 }}>
                    暂无上下文数据
                    <div style={{ marginTop: 10 }}>
                      <button type="button" onClick={() => { setObservation(null); void loadObservation(); }}
                        style={{ background: "none", border: "none", color: "#22d3ee", cursor: "pointer", fontSize: 12 }}>
                        点击重试
                      </button>
                    </div>
                  </div>
                ) : (
                  <div style={{ padding: "0 0 16px" }}>
                    {/* Header summary bar */}
                    <div style={{
                      padding: "10px 16px", borderBottom: `1px solid ${TRACE_BORDER}`,
                      background: "rgba(52,211,153,0.05)",
                      display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap",
                    }}>
                      <span style={{ fontSize: 11, fontFamily: "monospace", color: "#34d399", fontWeight: 700 }}>上下文快照</span>
                      <span style={{ fontSize: 10, fontFamily: "monospace", color: TRACE_MUTED }}>
                        目标: {observation.target}
                      </span>
                      <span style={{ fontSize: 10, fontFamily: "monospace", color: TRACE_MUTED }}>
                        阶段: {observation.current_phase}
                      </span>
                      <span style={{ fontSize: 10, fontFamily: "monospace", color: TRACE_FAINT }}>
                        {new Date(observation.generated_at).toLocaleString("zh-CN")}
                      </span>
                      {demoMode && (
                        <span style={{ fontSize: 10, padding: "1px 7px", borderRadius: 3, background: "rgba(251,191,36,0.1)", border: "1px solid rgba(251,191,36,0.3)", color: "#fbbf24", fontFamily: "monospace" }}>
                          演示数据
                        </span>
                      )}
                    </div>

                    <div style={{ padding: "16px 20px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>

                      {/* Confirmed vulnerabilities */}
                      {Array.isArray((observation.context as Record<string, unknown>)?.confirmed_vulnerabilities) && (
                        <div style={{
                          background: "rgba(248,113,113,0.05)", border: "1px solid rgba(248,113,113,0.2)",
                          borderRadius: 8, padding: "12px 14px",
                        }}>
                          <div style={{ fontSize: 11, fontWeight: 700, fontFamily: "monospace", color: "#f87171", marginBottom: 10, letterSpacing: "0.05em" }}>
                            已确认漏洞 ({((observation.context as Record<string, unknown>).confirmed_vulnerabilities as unknown[]).length})
                          </div>
                          {(((observation.context as Record<string, unknown>).confirmed_vulnerabilities) as string[]).map((v, i) => (
                            <div key={i} style={{
                              fontSize: 11, color: TRACE_TEXT, padding: "5px 0",
                              borderBottom: `1px solid ${TRACE_BORDER}`, fontFamily: "monospace",
                              display: "flex", alignItems: "flex-start", gap: 6,
                            }}>
                              <span style={{ color: "#f87171", flexShrink: 0, marginTop: 1 }}>⚠</span>
                              <span style={{ wordBreak: "break-word", lineHeight: 1.4 }}>{v}</span>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Open ports + tech stack */}
                      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                        {Array.isArray((observation.context as Record<string, unknown>)?.open_ports) && (
                          <div style={{
                            background: "rgba(56,189,248,0.05)", border: "1px solid rgba(56,189,248,0.2)",
                            borderRadius: 8, padding: "12px 14px",
                          }}>
                            <div style={{ fontSize: 11, fontWeight: 700, fontFamily: "monospace", color: "#38bdf8", marginBottom: 8, letterSpacing: "0.05em" }}>
                              开放端口
                            </div>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                              {(((observation.context as Record<string, unknown>).open_ports) as number[]).map((port) => (
                                <span key={port} style={{
                                  fontSize: 11, padding: "2px 10px", borderRadius: 4,
                                  background: "rgba(56,189,248,0.1)", border: "1px solid rgba(56,189,248,0.3)",
                                  color: "#7dd3fc", fontFamily: "monospace", fontWeight: 700,
                                }}>
                                  {port}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}

                        {Array.isArray((observation.context as Record<string, unknown>)?.tech_stack) && (
                          <div style={{
                            background: "rgba(129,140,248,0.05)", border: "1px solid rgba(129,140,248,0.2)",
                            borderRadius: 8, padding: "12px 14px",
                          }}>
                            <div style={{ fontSize: 11, fontWeight: 700, fontFamily: "monospace", color: "#818cf8", marginBottom: 8, letterSpacing: "0.05em" }}>
                              技术栈
                            </div>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                              {(((observation.context as Record<string, unknown>).tech_stack) as string[]).map((tech) => (
                                <span key={tech} style={{
                                  fontSize: 10, padding: "2px 9px", borderRadius: 4,
                                  background: "rgba(129,140,248,0.1)", border: "1px solid rgba(129,140,248,0.25)",
                                  color: "#a5b4fc", fontFamily: "monospace",
                                }}>
                                  {tech}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}

                        {typeof (observation.context as Record<string, unknown>)?.os_guess === "string" && (
                          <div style={{
                            background: "rgba(74,222,128,0.04)", border: "1px solid rgba(74,222,128,0.18)",
                            borderRadius: 8, padding: "10px 14px",
                            display: "flex", alignItems: "center", gap: 10,
                          }}>
                            <span style={{ fontSize: 10, fontWeight: 700, fontFamily: "monospace", color: "#4ade80", letterSpacing: "0.05em" }}>OS</span>
                            <span style={{ fontSize: 11, fontFamily: "monospace", color: TRACE_TEXT }}>
                              {String((observation.context as Record<string, unknown>).os_guess)}
                            </span>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Artifacts summary */}
                    {observation.artifacts_summary && observation.artifacts_summary.length > 0 && (
                      <div style={{ padding: "0 20px" }}>
                        <div style={{
                          background: TRACE_SURFACE_MUTED, border: `1px solid ${TRACE_BORDER}`,
                          borderRadius: 8, overflow: "hidden",
                        }}>
                          <div style={{
                            padding: "10px 14px", borderBottom: `1px solid ${TRACE_BORDER}`,
                            fontSize: 11, fontWeight: 700, fontFamily: "monospace", color: TRACE_MUTED,
                            letterSpacing: "0.05em",
                          }}>
                            技能分析摘要 ({observation.artifacts_summary.length})
                          </div>
                          {observation.artifacts_summary.map((a, i) => (
                            <div key={i} style={{
                              display: "grid", gridTemplateColumns: "160px 1fr",
                              gap: 12, padding: "9px 14px", alignItems: "flex-start",
                              borderBottom: i < observation.artifacts_summary.length - 1 ? `1px solid ${TRACE_BORDER}` : "none",
                            }}
                              onMouseEnter={e => (e.currentTarget.style.background = TRACE_HOVER)}
                              onMouseLeave={e => (e.currentTarget.style.background = "")}
                            >
                              <span style={{ fontSize: 11, fontFamily: "monospace", color: "#22d3ee", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {a.skill_id}
                              </span>
                              <span style={{ fontSize: 11, color: TRACE_TEXT, lineHeight: 1.5, wordBreak: "break-word" }}>
                                {a.summary}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* ── Meta footer ── */}
            <div style={{
              marginTop: 14, padding: "11px 16px",
              background: TRACE_SURFACE_MUTED, border: `1px solid ${TRACE_BORDER}`,
              borderRadius: 10, display: "flex", gap: 24, flexWrap: "wrap",
              fontSize: 11, fontFamily: "monospace", color: TRACE_FAINT,
            }}>
              <span>
                taskId:{" "}
                <span style={{ color: TRACE_MUTED }}>{task.taskId}</span>
              </span>
              <span>
                创建:{" "}
                <span style={{ color: TRACE_MUTED }}>
                  {task.createdAt ? new Date(task.createdAt).toLocaleString("zh-CN") : "—"}
                </span>
              </span>
              {task.updatedAt && (
                <span>
                  更新:{" "}
                  <span style={{ color: TRACE_MUTED }}>
                    {new Date(task.updatedAt).toLocaleString("zh-CN")}
                  </span>
                </span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
