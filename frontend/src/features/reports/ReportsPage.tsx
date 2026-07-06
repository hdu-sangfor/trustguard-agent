import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import {
  listCompletedTasks, getTaskReport, getTaskExecutions, getTaskObservation,
  type ApiTask, type ApiReport, type ApiExecutionRecord, type ApiObservation,
  type ApiReportFinding, type ApiReportRecommendation, type ApiReportArtifact, type ApiSeverity,
} from "@/shared/lib/api";
import { ORBIT_TASKS_UPDATED_EVENT } from "@/shared/constants/orbitTasksStorage";

// Phase order and colour map
const PHASE_COLORS: Record<string, string> = {
  RECON: "#64748b", THREAT_MODEL: "#a78bfa", VULN_SCAN: "#fb923c",
  EXPLOIT: "#f87171", REPORT: "#34d399", DONE: "#22d3ee",
};
const PHASE_ORDER = ["RECON", "THREAT_MODEL", "VULN_SCAN", "EXPLOIT", "REPORT", "DONE"];

// Derive a rough vuln count from execution records
function countVulns(execs: ApiExecutionRecord[]): number {
  return execs.filter((e) => {
    const s = (e.status ?? "").toUpperCase();
    return s === "DONE" || s === "SUCCESS";
  }).length;
}

// Map vulnerability keyword → repair suggestion
const REPAIR_SUGGESTIONS: Array<{ keywords: string[]; suggestion: string; severity: "high" | "medium" | "low" }> = [
  { keywords: ["sql", "sqli", "sql injection", "注入"], suggestion: "使用参数化查询（Prepared Statement）替换字符串拼接；对所有用户输入进行严格类型校验；部署 WAF 并开启 SQL 注入防护规则。", severity: "high" },
  { keywords: ["xss", "cross-site scripting", "跨站脚本"], suggestion: "对输出内容进行 HTML 实体编码；设置 Content-Security-Policy 响应头；启用 HttpOnly 和 SameSite Cookie 标志。", severity: "high" },
  { keywords: ["rce", "remote code execution", "命令执行", "代码执行"], suggestion: "禁止执行用户可控内容；升级存在 RCE 漏洞的框架组件至安全版本；启用应用层沙箱隔离；最小化服务运行权限。", severity: "high" },
  { keywords: ["upload", "文件上传", "webshell"], suggestion: "限制上传文件类型（白名单）；文件存储至非 Web 可访问目录；使用随机文件名并剥离扩展名关联；部署内容扫描。", severity: "high" },
  { keywords: ["unauthorized", "未授权", "authentication bypass", "认证绕过"], suggestion: "确保所有敏感接口强制鉴权；采用 JWT 或 Session 令牌并设置合理过期时间；实施最小权限原则。", severity: "high" },
  { keywords: ["struts", "s2-045", "s2-057", "ognl"], suggestion: "升级 Apache Struts2 至最新稳定版本；禁用不必要的多文件上传功能；在反向代理层过滤异常 Content-Type。", severity: "high" },
  { keywords: ["thinkphp", "5.0.23", "think php"], suggestion: "升级 ThinkPHP 至官方修复版本；关闭调试模式；移除或保护 `.env` 配置文件。", severity: "high" },
  { keywords: ["weblogic", "cve-2023-21839"], suggestion: "应用 Oracle CPU 补丁；限制 T3/IIOP 协议的外网访问；启用 JEP 290 反序列化过滤器。", severity: "high" },
  { keywords: ["tomcat", "cve-2017-12615", "cve-2019-0232"], suggestion: "升级 Tomcat 至修复版本；禁用 DefaultServlet 的 PUT 方法；严格配置 web.xml 中的 servlet 权限。", severity: "medium" },
  { keywords: ["shiro", "cve-2016-4437", "apache shiro"], suggestion: "更换 Shiro 默认密钥（rememberMe）；升级至 Shiro ≥ 1.7.0；限制 Cookie 最大长度并开启异常监控。", severity: "high" },
  { keywords: ["fastjson", "1.2.24", "1.2.47", "反序列化"], suggestion: "升级 Fastjson 至 ≥ 2.0；禁用 autoType 功能；实施 JSON 输入长度限制与类白名单过滤。", severity: "high" },
  { keywords: ["flask", "ssti", "server-side template injection", "模板注入"], suggestion: "避免将用户输入直接传递给模板引擎；使用 `Markup.escape()` 对输出转义；升级至最新 Jinja2 版本。", severity: "high" },
  { keywords: ["open port", "open_port", "端口", "暴露"], suggestion: "关闭非必要Gateway服务端口；在防火墙层限制端口访问来源；Gateway暴露服务启用 TLS 加密。", severity: "low" },
  { keywords: ["directory traversal", "path traversal", "目录遍历"], suggestion: "校验并规范化文件路径；使用白名单限制允许访问的目录；禁止在 Web 根目录外读取文件。", severity: "medium" },
  { keywords: ["weak password", "弱口令", "brute force", "暴力破解"], suggestion: "强制密码复杂度要求；实施账号锁定策略（连续失败次数）；部署登录验证码；启用双因素认证。", severity: "medium" },
  { keywords: ["information disclosure", "信息泄露", "phpinfo", "debug"], suggestion: "关闭生产环境调试模式；移除版本信息响应头（Server, X-Powered-By）；严格限制错误信息显示。", severity: "low" },
  { keywords: ["privilege escalation", "权限提升", "提权"], suggestion: "遵循最小权限原则运行服务；定期审计 sudo 权限；禁用 SUID/SGID 高危二进制文件。", severity: "high" },
];

function generateRepairSuggestions(vulns: string[]): Array<{ vuln: string; suggestion: string; severity: "high" | "medium" | "low" }> {
  const results: Array<{ vuln: string; suggestion: string; severity: "high" | "medium" | "low" }> = [];
  for (const vuln of vulns) {
    const lower = vuln.toLowerCase();
    const match = REPAIR_SUGGESTIONS.find((r) => r.keywords.some((kw) => lower.includes(kw)));
    if (match) {
      results.push({ vuln, suggestion: match.suggestion, severity: match.severity });
    } else {
      results.push({ vuln, suggestion: "升级相关组件至最新稳定版本；参考 CVE 数据库获取官方补丁；在漏洞修复前通过 WAF 规则临时缓解。", severity: "medium" });
    }
  }
  return results;
}

// ─── Report Normalization ────────────────────────────────────────────────────
// 优先使用后端 report.findings / recommendations / artifacts 等新字段；
// 若后端未返回则回退到 observation.context + observation.artifacts_summary（兼容旧数据）。
type SeverityBasic = "high" | "medium" | "low";

type NormalizedReport = {
  findings: ApiReportFinding[];
  recommendations: Array<{ finding: string; suggestion: string; severity: SeverityBasic }>;
  artifacts: ApiReportArtifact[];
  openPorts: Array<number | string>;
  services: string[];
  riskLevel: string;
  severityHistogram: Record<string, number>;
};

const SEV_RANK: Record<string, SeverityBasic> = {
  critical: "high", high: "high", medium: "medium", low: "low", info: "low",
};

function normalizeReport(report: ApiReport | null | undefined, obs: ApiObservation | null | undefined): NormalizedReport {
  const hasBackendFindings = !!(report?.findings && report.findings.length > 0);
  const hasBackendRecs = !!(report?.recommendations && report.recommendations.length > 0);
  const hasBackendArtifacts = !!(report?.artifacts && report.artifacts.length > 0);
  const hasBackendPorts = !!(report?.openPorts && report.openPorts.length > 0);

  if (hasBackendFindings || hasBackendRecs || hasBackendArtifacts || hasBackendPorts) {
    const findings = report?.findings ?? [];
    const recs = (report?.recommendations ?? []).map((r) => ({
      finding: r.finding,
      suggestion: r.suggestion,
      severity: SEV_RANK[(r.severity ?? "medium").toLowerCase()] ?? "medium",
    }));
    return {
      findings,
      recommendations: recs,
      artifacts: report?.artifacts ?? [],
      openPorts: report?.openPorts ?? [],
      services: report?.services ?? [],
      riskLevel: report?.riskLevel ?? "none",
      severityHistogram: report?.severityHistogram ?? {},
    };
  }

  // Fallback: derive from observation.context
  const ctx = (obs?.context ?? {}) as Record<string, unknown>;
  const confirmedVulns = Array.isArray(ctx["confirmed_vulnerabilities"]) ? (ctx["confirmed_vulnerabilities"] as string[]) : [];
  const vulnSvcs = Array.isArray(ctx["vulnerable_services"]) ? (ctx["vulnerable_services"] as string[]) : [];
  const openPorts = Array.isArray(ctx["open_ports"]) ? (ctx["open_ports"] as Array<number | string>) : [];
  const artifactsRaw = obs?.artifacts_summary ?? [];
  const artifacts: ApiReportArtifact[] = artifactsRaw.map((a) => ({ skillId: a.skill_id, summary: a.summary }));

  const findings: ApiReportFinding[] = [
    ...confirmedVulns.map((v) => ({ title: v, severity: "high" as ApiSeverity })),
    ...vulnSvcs.map((s) => ({ title: s, severity: "medium" as ApiSeverity })),
  ];

  const allVulns = [...confirmedVulns, ...vulnSvcs];
  const recs = allVulns.length > 0 ? generateRepairSuggestions(allVulns).map((r) => ({
    finding: r.vuln, suggestion: r.suggestion, severity: r.severity,
  })) : [];

  const riskLevel =
    findings.some((f) => f.severity === "critical" || f.severity === "high") ? "high" :
    findings.some((f) => f.severity === "medium") ? "medium" :
    findings.length > 0 ? "low" : "none";

  const hist: Record<string, number> = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
  for (const f of findings) hist[f.severity] = (hist[f.severity] ?? 0) + 1;

  return { findings, recommendations: recs, artifacts, openPorts, services: vulnSvcs, riskLevel, severityHistogram: hist };
}

const RISK_LABEL: Record<string, string> = {
  critical: "严重", high: "高危", medium: "中危", low: "低危", none: "无",
};

type ReportEntry = {
  task: ApiTask;
  report: ApiReport | null;
  execs: ApiExecutionRecord[] | null;
  observation: ApiObservation | null;
  loading: boolean;
  expanded: boolean;
};

// ─── HTML Report Generator ────────────────────────────────────────────────────
function generateHtmlReport(
  t: ApiTask,
  report: ApiReport | null,
  execs: ApiExecutionRecord[] | null,
  obs: ApiObservation | null,
): string {
  const phases = report?.phases ?? [];
  const norm = normalizeReport(report, obs);
  const { findings, recommendations: repairs, artifacts: normArtifacts, openPorts, services: vulnSvcs, riskLevel } = norm;
  const confirmedVulns = findings.map((f) => f.title);
  const artifacts = normArtifacts.map((a) => ({ skill_id: a.skillId, summary: a.summary }));

  const PHASE_COLORS_HTML: Record<string, string> = {
    RECON: "#38bdf8", THREAT_MODEL: "#818cf8", VULN_SCAN: "#fb923c",
    EXPLOIT: "#f87171", REPORT: "#34d399", DONE: "#4ade80",
  };

  const sevColor: Record<string, string> = { high: "#f87171", medium: "#fbbf24", low: "#94a3b8" };
  const sevLabel: Record<string, string> = { high: "高危", medium: "中危", low: "低危" };
  const sevBg: Record<string, string> = { high: "rgba(239,68,68,0.12)", medium: "rgba(251,191,36,0.12)", low: "rgba(148,163,184,0.1)" };

  const phaseRows = phases.map((p) => {
    const c = PHASE_COLORS_HTML[p.phase] ?? "#94a3b8";
    return `<tr><td style="padding:8px 14px;border-bottom:1px solid #1e293b;font-family:monospace;font-size:13px;color:${c}">${p.phase}</td><td style="padding:8px 14px;border-bottom:1px solid #1e293b;color:#34d399;font-family:monospace;font-size:12px">${p.status}</td><td style="padding:8px 14px;border-bottom:1px solid #1e293b;color:#94a3b8;font-size:12px">${p.notes ?? "—"}</td></tr>`;
  }).join("");

  const execRows = (execs ?? []).slice(0, 25).map((e, i) => {
    const s = (e.status ?? "").toUpperCase();
    const col = s === "DONE" || s === "SUCCESS" ? "#34d399" : s === "FAILED" ? "#f87171" : "#fbbf24";
    return `<tr><td style="padding:6px 12px;border-bottom:1px solid #1e293b;color:#64748b;font-size:12px;font-family:monospace">${i + 1}</td><td style="padding:6px 12px;border-bottom:1px solid #1e293b;color:#818cf8;font-size:12px;font-family:monospace">${e.phase ?? "—"}</td><td style="padding:6px 12px;border-bottom:1px solid #1e293b;color:#38bdf8;font-size:12px;font-family:monospace">${e.skill_id ?? "—"}</td><td style="padding:6px 12px;border-bottom:1px solid #1e293b;color:${col};font-size:12px;font-family:monospace">${e.status ?? "—"}</td><td style="padding:6px 12px;border-bottom:1px solid #1e293b;color:#475569;font-size:12px;font-family:monospace">${e.duration_ms != null ? e.duration_ms + "ms" : "—"}</td></tr>`;
  }).join("");

  const vulnItems = findings.map((f) => {
    const sev = (f.severity && ["high", "medium", "low"].includes(f.severity)) ? f.severity : (f.severity === "critical" ? "high" : "medium");
    const badgeColor = sevColor[sev] ?? "#fca5a5";
    const cveBadge = f.cve ? `<span style="margin-left:8px;padding:1px 6px;border-radius:3px;background:rgba(129,140,248,0.15);border:1px solid rgba(129,140,248,0.4);color:#a5b4fc;font-size:10px;font-family:monospace">${f.cve}</span>` : "";
    const evidenceLine = f.evidence ? `<div style="margin-top:4px;padding-left:22px;color:#64748b;font-family:monospace;font-size:11px;line-height:1.5">${f.evidence}</div>` : "";
    const skillLine = f.skill ? `<span style="margin-left:8px;color:#64748b;font-family:monospace;font-size:10px">[${f.skill}]</span>` : "";
    return `<li style="padding:6px 0;border-bottom:1px dashed rgba(71,85,105,0.15);list-style:none">
      <div style="display:flex;align-items:center;flex-wrap:wrap">
        <span style="color:${badgeColor};margin-right:8px">⚠</span>
        <span style="color:#fca5a5;font-family:monospace;font-size:13px">${f.title}</span>
        ${cveBadge}${skillLine}
      </div>
      ${evidenceLine}
    </li>`;
  }).join("");

  const repairItems = repairs.map((r) => `
    <div style="margin-bottom:14px;padding:14px 16px;border-radius:8px;background:${sevBg[r.severity]};border:1px solid ${sevColor[r.severity]}40">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
        <span style="padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;font-family:monospace;background:${sevBg[r.severity]};border:1px solid ${sevColor[r.severity]}60;color:${sevColor[r.severity]}">${sevLabel[r.severity]}</span>
        <span style="font-size:13px;font-weight:600;color:#e2e8f0">${r.finding}</span>
      </div>
      <div style="font-size:12px;color:#94a3b8;line-height:1.65">${r.suggestion}</div>
    </div>`).join("");

  const artifactItems = artifacts.map((a) => `
    <div style="display:flex;gap:12px;padding:6px 0;border-bottom:1px solid #1e293b">
      <span style="min-width:140px;font-family:monospace;font-size:12px;color:#a78bfa;flex-shrink:0">${a.skill_id}</span>
      <span style="font-size:12px;color:#94a3b8">${a.summary}</span>
    </div>`).join("");

  const duration = t.createdAt && t.updatedAt
    ? Math.round((new Date(t.updatedAt).getTime() - new Date(t.createdAt).getTime()) / 60000)
    : null;

  const riskColorMap: Record<string, string> = { critical: "#ef4444", high: "#f87171", medium: "#fbbf24", low: "#94a3b8", none: "#475569" };
  const riskLabelMap: Record<string, string> = { critical: "严重", high: "高危", medium: "中危", low: "低危", none: "无风险" };
  const riskColorHtml = riskColorMap[riskLevel] ?? "#475569";
  const riskLabel = riskLabelMap[riskLevel] ?? riskLevel;

  const hist = norm.severityHistogram ?? {};
  const histItems = ["critical", "high", "medium", "low", "info"].map((k) => {
    const n = hist[k] ?? 0;
    const col = { critical: "#ef4444", high: "#f87171", medium: "#fbbf24", low: "#94a3b8", info: "#64748b" }[k] ?? "#94a3b8";
    const lbl = { critical: "严重", high: "高危", medium: "中危", low: "低危", info: "信息" }[k] ?? k;
    return `<div style="flex:1;min-width:90px;padding:10px 12px;border-radius:6px;background:rgba(2,6,23,0.5);border:1px solid ${col}30;text-align:center">
      <div style="font-family:monospace;font-size:9px;color:rgba(148,163,184,0.6);letter-spacing:0.08em;margin-bottom:4px">${lbl}</div>
      <div style="font-family:monospace;font-size:20px;font-weight:800;color:${col}">${n}</div>
    </div>`;
  }).join("");

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>渗透测试报告 — ${t.name}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #020a12; color: #e2e8f0; font-family: 'Segoe UI', 'PingFang SC', sans-serif; font-size: 14px; line-height: 1.6; }
  .page { max-width: 960px; margin: 0 auto; padding: 40px 32px 60px; }
  .header-banner { background: linear-gradient(135deg, rgba(34,211,238,0.08), rgba(129,140,248,0.08)); border: 1px solid rgba(34,211,238,0.25); border-radius: 12px; padding: 28px 32px; margin-bottom: 28px; }
  .brand { font-family: 'Courier New', monospace; font-size: 11px; letter-spacing: 0.2em; color: rgba(34,211,238,0.6); text-transform: uppercase; margin-bottom: 6px; }
  .report-title { font-size: 26px; font-weight: 800; color: #e2e8f0; letter-spacing: 0.02em; margin-bottom: 6px; }
  .report-meta { font-size: 12px; color: rgba(148,163,184,0.6); font-family: monospace; }
  .section { background: rgba(15,23,42,0.8); border: 1px solid rgba(71,85,105,0.3); border-radius: 12px; padding: 20px 24px; margin-bottom: 18px; }
  .section-title { font-size: 10px; font-weight: 800; color: rgba(148,163,184,0.6); letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 14px; padding-bottom: 8px; border-bottom: 1px solid rgba(71,85,105,0.2); }
  .meta-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0; }
  .meta-row { display: flex; justify-content: space-between; padding: 7px 0; border-bottom: 1px solid rgba(71,85,105,0.1); font-size: 13px; }
  .meta-key { color: rgba(148,163,184,0.5); }
  .meta-val { color: #e2e8f0; font-family: monospace; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; }
  th { padding: 8px 14px; text-align: left; font-size: 10px; font-weight: 700; color: rgba(148,163,184,0.5); letter-spacing: 0.08em; text-transform: uppercase; border-bottom: 1px solid rgba(71,85,105,0.3); }
  .summary-text { color: #94a3b8; font-size: 13px; line-height: 1.7; white-space: pre-wrap; }
  .vuln-list { list-style: none; padding: 0; }
  .port-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; background: rgba(56,189,248,0.12); border: 1px solid rgba(56,189,248,0.3); color: #38bdf8; font-family: monospace; font-size: 11px; font-weight: 700; margin: 3px; }
  .footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid rgba(71,85,105,0.2); text-align: center; font-family: monospace; font-size: 11px; color: rgba(148,163,184,0.3); }
  @media print { body { background: #fff; color: #111; } .header-banner { background: #f8fafc; border-color: #cbd5e1; } .section { background: #fff; border-color: #e2e8f0; } .meta-key, .report-meta, .brand { color: #64748b; } .meta-val, .report-title { color: #111; } .summary-text, th { color: #475569; } }
</style>
</head>
<body>
<div class="page">
  <div class="header-banner">
    <div class="brand">◈ TRUSTGUARD AGENT — 渗透测试报告</div>
    <div class="report-title">${t.name}</div>
    <div class="report-meta">Task ID: ${t.taskId} &nbsp;|&nbsp; 目标: ${t.target} &nbsp;|&nbsp; 生成时间: ${new Date().toLocaleString("zh-CN")}</div>
    <div style="margin-top:10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <span style="padding:4px 12px;border-radius:4px;font-family:monospace;font-size:12px;font-weight:700;background:${riskColorHtml}20;border:1px solid ${riskColorHtml};color:${riskColorHtml}">整体风险：${riskLabel}</span>
      <span style="color:rgba(148,163,184,0.6);font-family:monospace;font-size:11px">共 ${findings.length} 处漏洞 &nbsp;·&nbsp; ${repairs.length} 项修复建议 &nbsp;·&nbsp; ${artifacts.length} 份扫描产物</span>
    </div>
  </div>

  ${findings.length > 0 ? `<div class="section">
    <div class="section-title">严重度分布</div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">${histItems}</div>
  </div>` : ""}

  <div class="section">
    <div class="section-title">任务概览</div>
    <div class="meta-grid">
      ${[
        ["任务名称", t.name],
        ["目标地址", t.target],
        ["测试状态", t.status],
        ["当前阶段", t.currentPhase || "—"],
        ["创建时间", t.createdAt ? new Date(t.createdAt).toLocaleString("zh-CN") : "—"],
        ["完成时间", t.updatedAt ? new Date(t.updatedAt).toLocaleString("zh-CN") : "—"],
        ["Task ID", t.taskId],
        ["测试时长", duration != null ? duration + " 分钟" : "—"],
      ].map(([k, v]) => `<div class="meta-row"><span class="meta-key">${k}</span><span class="meta-val">${v}</span></div>`).join("")}
    </div>
  </div>

  ${report?.summary ? `<div class="section">
    <div class="section-title">测试摘要</div>
    <div class="summary-text">${report.summary}</div>
  </div>` : ""}

  ${phases.length > 0 ? `<div class="section">
    <div class="section-title">阶段覆盖 (${phases.length} 阶段)</div>
    <table><thead><tr><th>阶段</th><th>状态</th><th>备注</th></tr></thead><tbody>${phaseRows}</tbody></table>
  </div>` : ""}

  ${(confirmedVulns.length > 0 || openPorts.length > 0) ? `<div class="section">
    <div class="section-title">漏洞发现</div>
    ${openPorts.length > 0 ? `<div style="margin-bottom:12px"><div style="font-size:11px;color:rgba(148,163,184,0.5);margin-bottom:6px">开放端口</div>${openPorts.map((p) => `<span class="port-badge">${p}</span>`).join("")}</div>` : ""}
    ${vulnSvcs.length > 0 ? `<div style="margin-bottom:12px"><div style="font-size:11px;color:rgba(148,163,184,0.5);margin-bottom:4px">存在漏洞服务</div><div style="color:#fb923c;font-family:monospace;font-size:13px">${vulnSvcs.join(" / ")}</div></div>` : ""}
    ${confirmedVulns.length > 0 ? `<div><div style="font-size:11px;color:rgba(148,163,184,0.5);margin-bottom:6px">确认漏洞 (${confirmedVulns.length})</div><ul class="vuln-list">${vulnItems}</ul></div>` : ""}
  </div>` : ""}

  ${repairs.length > 0 ? `<div class="section">
    <div class="section-title">修复建议 (${repairs.length} 项)</div>
    ${repairItems}
  </div>` : ""}

  ${artifacts.length > 0 ? `<div class="section">
    <div class="section-title">扫描产物摘要</div>
    ${artifactItems}
  </div>` : ""}

  ${execRows ? `<div class="section">
    <div class="section-title">执行轨迹 (${(execs ?? []).length} 条)</div>
    <table><thead><tr><th>#</th><th>阶段</th><th>技能</th><th>状态</th><th>耗时</th></tr></thead><tbody>${execRows}</tbody></table>
  </div>` : ""}

  <div class="footer">
    本报告由 TRUSTGUARD AGENT 自动生成 &nbsp;·&nbsp; 生成时间 ${new Date().toLocaleString("zh-CN")}
    <br>仅供授权渗透测试使用，请勿用于任何非授权活动
  </div>
</div>
</body>
</html>`;
}

const ReportsPage = () => {
  const { loggedIn } = useAppSession();
  const navigate = useNavigate();

  const [entries, setEntries] = useState<ReportEntry[]>([]);
  const [pageLoading, setPageLoading] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [searchFilter, setSearchFilter] = useState("");

  useEffect(() => {
    if (!loggedIn) {
      toast.error("请先登录");
      localStorage.setItem("sentinel_login_redirect", "/reports");
      navigate("/login", { replace: true });
    }
  }, [loggedIn, navigate]);

  const refresh = useCallback(async () => {
    setPageLoading(true);
    try {
      const tasks = await listCompletedTasks(100);
      if (tasks.length > 0) {
        setEntries(tasks.map((t) => ({ task: t, report: null, execs: null, observation: null, loading: false, expanded: false })));
      } else {
        // Backend online but no completed tasks — check if we have local finished tasks as demo data
        const { readStoredOrbitTasks } = await import("@/shared/constants/orbitTasksStorage");
        const local = readStoredOrbitTasks().filter((t) => t.status === "finished");
        if (local.length > 0) {
          const demoTasks: ApiTask[] = local.map((t) => ({
            id: 0,
            taskId: t.id,
            name: t.name,
            target: t.url,
            description: t.desc,
            status: "DONE" as const,
            currentPhase: t.currentPhase ?? "DONE",
            createdAt: new Date(t.createdAt).toISOString(),
            updatedAt: new Date(t.updatedAt ?? t.createdAt).toISOString(),
          }));
          setEntries(demoTasks.map((t) => ({ task: t, report: null, execs: null, observation: null, loading: false, expanded: false })));
        } else {
          setEntries([]);
        }
      }
      setLastRefresh(new Date());
    } catch {
      // Backend offline — fall back to local finished tasks
      try {
        const { readStoredOrbitTasks } = await import("@/shared/constants/orbitTasksStorage");
        const local = readStoredOrbitTasks().filter((t) => t.status === "finished");
        if (local.length > 0) {
          const demoTasks: ApiTask[] = local.map((t) => ({
            id: 0,
            taskId: t.id,
            name: t.name,
            target: t.url,
            description: t.desc,
            status: "DONE" as const,
            currentPhase: t.currentPhase ?? "DONE",
            createdAt: new Date(t.createdAt).toISOString(),
            updatedAt: new Date(t.updatedAt ?? t.createdAt).toISOString(),
          }));
          setEntries(demoTasks.map((t) => ({ task: t, report: null, execs: null, observation: null, loading: false, expanded: false })));
          setLastRefresh(new Date());
        }
      } catch { /* ignore */ }
    } finally {
      setPageLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  // Auto-refresh when a task status changes (e.g., demo simulation finishes)
  useEffect(() => {
    const handler = () => { void refresh(); };
    window.addEventListener(ORBIT_TASKS_UPDATED_EVENT, handler);
    return () => window.removeEventListener(ORBIT_TASKS_UPDATED_EVENT, handler);
  }, [refresh]);

  // Poll every 12s for newly completed tasks (catches backend-driven completions)
  useEffect(() => {
    const iv = window.setInterval(() => { void refresh(); }, 12000);
    return () => window.clearInterval(iv);
  }, [refresh]);

  const toggleExpand = async (idx: number) => {
    const entry = entries[idx];
    if (!entry) return;
    const nowExpanded = !entry.expanded;
    // Toggle collapsed state immediately
    setEntries((prev) => prev.map((e, i) => i === idx ? { ...e, expanded: nowExpanded, loading: nowExpanded && e.report === null } : e));
    if (!nowExpanded || entry.report !== null) return;
    // Fetch report + executions + observation lazily
    const [reportRes, execsRes, obsRes] = await Promise.allSettled([
      getTaskReport(entry.task.taskId),
      getTaskExecutions(entry.task.taskId, 30, 0),
      getTaskObservation(entry.task.taskId),
    ]);
    let report = reportRes.status === "fulfilled" ? reportRes.value : null;
    let execs = execsRes.status === "fulfilled" ? execsRes.value : null;
    let observation = obsRes.status === "fulfilled" ? obsRes.value : null;

    // Demo fallback: numeric IDs are local seeds; provide mock data when backend fails
    if (/^\d+$/.test(entry.task.taskId)) {
      const t = entry.task;
      if (!report) {
        report = {
          taskId: t.taskId, target: t.target, status: "DONE", summary:
            `自动化渗透测试任务已完成全部 6 个阶段（RECON → THREAT_MODEL → VULN_SCAN → EXPLOIT → REPORT → DONE）。目标：${t.target}。测试过程由 LLM 驱动状态机自动推进，结合 33+ 安全技能容器完成侦察、漏洞扫描与利用验证。`,
          phases: PHASE_ORDER.map((ph) => ({ phase: ph, status: "DONE", notes: ph + " 已完成" })),
          createdAt: t.createdAt,
        };
      }
      if (!execs) {
        const nl2 = t.name?.toLowerCase() ?? "";
        const xStruts  = nl2.includes("struts") || nl2.includes("s2-045");
        const xFlask   = nl2.includes("flask")  || nl2.includes("ssti");
        const xThink   = nl2.includes("thinkphp");
        const xShiro   = nl2.includes("shiro");
        const xFast    = nl2.includes("fastjson");
        const xWl      = nl2.includes("weblogic");
        const xTom     = nl2.includes("tomcat")  || nl2.includes("put 上传");
        const skillPlan2: { phase: string; skill: string; dur: number }[] =
          xStruts ? [
            { phase: "RECON",        skill: "nmap",               dur: 10200 },
            { phase: "RECON",        skill: "httpx",              dur: 3100  },
            { phase: "THREAT_MODEL", skill: "ehole",              dur: 2800  },
            { phase: "VULN_SCAN",    skill: "nuclei",             dur: 16400 },
            { phase: "EXPLOIT",      skill: "exploit-struts2",    dur: 2100  },
            { phase: "EXPLOIT",      skill: "linpeas",            dur: 9600  },
            { phase: "REPORT",       skill: "read_workspace_artifact", dur: 840 },
          ] : xFlask ? [
            { phase: "RECON",        skill: "nmap",               dur: 9800  },
            { phase: "RECON",        skill: "whatweb",            dur: 2600  },
            { phase: "THREAT_MODEL", skill: "dirsearch",          dur: 11200 },
            { phase: "VULN_SCAN",    skill: "nuclei",             dur: 13800 },
            { phase: "EXPLOIT",      skill: "exploit-ssti",       dur: 1800  },
            { phase: "REPORT",       skill: "read_workspace_artifact", dur: 720 },
          ] : xThink ? [
            { phase: "RECON",        skill: "nmap",               dur: 10800 },
            { phase: "RECON",        skill: "whatweb",            dur: 4200  },
            { phase: "VULN_SCAN",    skill: "nuclei",             dur: 14600 },
            { phase: "EXPLOIT",      skill: "exploit-thinkphp",   dur: 1900  },
            { phase: "EXPLOIT",      skill: "linpeas",            dur: 9800  },
            { phase: "REPORT",       skill: "read_workspace_artifact", dur: 780 },
          ] : xShiro ? [
            { phase: "RECON",        skill: "nmap",               dur: 12300 },
            { phase: "RECON",        skill: "httpx",              dur: 2800  },
            { phase: "VULN_SCAN",    skill: "nuclei",             dur: 15200 },
            { phase: "EXPLOIT",      skill: "shiro_exploit",      dur: 2400  },
            { phase: "EXPLOIT",      skill: "ysoserial",          dur: 6800  },
            { phase: "EXPLOIT",      skill: "linpeas",            dur: 10400 },
            { phase: "REPORT",       skill: "read_workspace_artifact", dur: 860 },
          ] : xFast ? [
            { phase: "RECON",        skill: "nmap",               dur: 11200 },
            { phase: "RECON",        skill: "httpx",              dur: 2600  },
            { phase: "VULN_SCAN",    skill: "nuclei",             dur: 14900 },
            { phase: "EXPLOIT",      skill: "fastjson-exploit",   dur: 2200  },
            { phase: "EXPLOIT",      skill: "jndi_exploit",       dur: 5600  },
            { phase: "EXPLOIT",      skill: "linpeas",            dur: 9200  },
            { phase: "REPORT",       skill: "read_workspace_artifact", dur: 740 },
          ] : xWl ? [
            { phase: "RECON",        skill: "nmap",               dur: 13500 },
            { phase: "RECON",        skill: "httpx",              dur: 3200  },
            { phase: "VULN_SCAN",    skill: "nuclei",             dur: 17200 },
            { phase: "EXPLOIT",      skill: "exploit-weblogic",   dur: 2800  },
            { phase: "EXPLOIT",      skill: "ysoserial",          dur: 7400  },
            { phase: "EXPLOIT",      skill: "read_workspace_artifact", dur: 2100 },
            { phase: "REPORT",       skill: "read_workspace_artifact", dur: 920 },
          ] : xTom ? [
            { phase: "RECON",        skill: "nmap",               dur: 10900 },
            { phase: "RECON",        skill: "httpx",              dur: 2500  },
            { phase: "VULN_SCAN",    skill: "nuclei",             dur: 13800 },
            { phase: "EXPLOIT",      skill: "exploit-tomcat",     dur: 1700  },
            { phase: "EXPLOIT",      skill: "linpeas",            dur: 8900  },
            { phase: "REPORT",       skill: "read_workspace_artifact", dur: 800 },
          ] : [
            { phase: "RECON",        skill: "nmap",               dur: 10500 },
            { phase: "RECON",        skill: "httpx",              dur: 2900  },
            { phase: "VULN_SCAN",    skill: "nuclei",             dur: 14200 },
            { phase: "EXPLOIT",      skill: "sqlmap",             dur: 8800  },
            { phase: "REPORT",       skill: "read_workspace_artifact", dur: 760 },
          ];
        const base2 = Date.now() - skillPlan2.reduce((a, s) => a + s.dur, 0);
        let cursor2 = base2;
        execs = skillPlan2.map((s, i) => {
          cursor2 += s.dur;
          return {
            request_id: `demo-${t.taskId}-${i}`,
            task_id: t.taskId, phase: s.phase,
            skill_id: s.skill, status: "DONE", duration_ms: s.dur,
            created_at: new Date(cursor2).toISOString(),
          };
        });
      }
      if (!observation) {
        const nl = t.name?.toLowerCase() ?? "";
        const isStruts   = nl.includes("struts") || nl.includes("s2-045") || nl.includes("s2-057");
        const isFlask    = nl.includes("flask") || nl.includes("ssti");
        const isThinkPHP = nl.includes("thinkphp");
        const isShiro    = nl.includes("shiro");
        const isFastjson = nl.includes("fastjson");
        const isWebLogic = nl.includes("weblogic");
        const isTomcat   = nl.includes("tomcat") || nl.includes("put 上传");
        const confirmedVulns =
          isStruts   ? ["Apache Struts2 S2-045 RCE (CVE-2017-5638)", "OGNL 注入利用成功"] :
          isFlask    ? ["Flask Jinja2 SSTI 模板注入任意代码执行", "系统命令执行已验证"] :
          isThinkPHP ? ["ThinkPHP 5.0.23 远程代码执行 (CVE-2018-20062)", "invokefunction 路由利用成功"] :
          isShiro    ? ["Apache Shiro 反序列化 RCE (CVE-2016-4437)", "rememberMe 默认密钥利用，命令执行已验证"] :
          isFastjson ? ["Fastjson 1.2.47 JNDI 反序列化 RCE (CVE-2019-14540)", "autoType 绕过 + LDAP 注入利用成功"] :
          isWebLogic ? ["Oracle WebLogic T3 反序列化 RCE (CVE-2023-21839)", "IIOP 协议恶意序列化 Payload 执行成功"] :
          isTomcat   ? ["Apache Tomcat PUT 任意文件上传 RCE (CVE-2017-12615)", "JSP Webshell 上传与执行已验证"] :
          ["Web 应用远程代码执行漏洞", "信息泄露与权限提升"];
        const vulnService =
          isStruts   ? "Apache Struts2/2.3.30 (port 8080)" :
          isFlask    ? "Flask/2.2.3 + Jinja2/3.0.3 (port 5000)" :
          isThinkPHP ? "ThinkPHP/5.0.23 + PHP/7.2.9 (port 80)" :
          isShiro    ? "Apache Shiro/1.2.4 + Tomcat/8.5 (port 8080)" :
          isFastjson ? "Spring Boot + Fastjson/1.2.47 (port 8080)" :
          isWebLogic ? "Oracle WebLogic Server/14.1.1.0 (port 7001)" :
          isTomcat   ? "Apache Tomcat/8.5.19 (port 8080)" :
          "Web Application (port 80/8080)";
        observation = {
          task_id: t.taskId, target: t.target, status: "DONE",
          current_phase: "DONE", generated_at: new Date().toISOString(),
          context: {
            open_ports: isWebLogic ? [7001, 7002] : isTomcat || isShiro || isFastjson ? [8080, 22] : [80, 443, 8080],
            vulnerable_services: [vulnService],
            confirmed_vulnerabilities: confirmedVulns,
          },
          artifacts_summary: [
            { skill_id: "nmap", summary: `发现开放端口，识别服务：${vulnService}` },
            { skill_id: "nuclei", summary: confirmedVulns[0] + " — PoC 验证通过，CVSS 9.8 (Critical)" },
            { skill_id: isThinkPHP || isFlask ? "dirsearch" : "whatweb", summary: `服务指纹识别完成，确认目标框架与版本信息` },
          ],
        };
      }
    }

    setEntries((prev) => prev.map((e, i) => i === idx ? { ...e, report, execs, observation, loading: false } : e));
  };

  const downloadReport = async (entry: ReportEntry) => {
    const t = entry.task;
    // Fetch missing data on-demand (user may not have expanded the row)
    let report = entry.report;
    let exeList = entry.execs;
    let obs = entry.observation;
    if (report === null || exeList === null || obs === null) {
      toast.loading("正在准备报告数据…", { id: "report-dl" });
      try {
        const [r1, r2, r3] = await Promise.allSettled([
          report === null ? getTaskReport(t.taskId) : Promise.resolve(null),
          exeList === null ? getTaskExecutions(t.taskId, 30, 0) : Promise.resolve(null),
          obs === null ? getTaskObservation(t.taskId) : Promise.resolve(null),
        ]);
        if (r1.status === "fulfilled" && r1.value !== null) report = r1.value;
        if (r2.status === "fulfilled" && r2.value !== null) exeList = r2.value;
        if (r3.status === "fulfilled" && r3.value !== null) obs = r3.value;
      } catch { /* use whatever we have */ }
      // Demo fallback for numeric-ID seeds when backend unreachable
      if (/^\d+$/.test(t.taskId)) {
        if (!report) {
          report = {
            taskId: t.taskId, target: t.target, status: "DONE",
            summary: `自动化渗透测试任务已完成全部 6 个阶段。目标：${t.target}。测试过程由 LLM 驱动状态机自动推进，结合 33+ 安全技能容器完成侦察、漏洞扫描与利用验证。`,
            phases: PHASE_ORDER.map((ph) => ({ phase: ph, status: "DONE", notes: ph + " 已完成" })),
          };
        }
        if (!obs) {
          const nl = t.name?.toLowerCase() ?? "";
          const isStruts   = nl.includes("struts") || nl.includes("s2-045") || nl.includes("s2-057");
          const isFlask    = nl.includes("flask") || nl.includes("ssti");
          const isThinkPHP = nl.includes("thinkphp");
          const isShiro    = nl.includes("shiro");
          const isFastjson = nl.includes("fastjson");
          const isWebLogic = nl.includes("weblogic");
          const isTomcat   = nl.includes("tomcat") || nl.includes("put 上传");
          const confirmedVulns =
            isStruts   ? ["Apache Struts2 S2-045 RCE (CVE-2017-5638)", "OGNL 注入利用成功"] :
            isFlask    ? ["Flask Jinja2 SSTI 模板注入任意代码执行", "系统命令执行已验证"] :
            isThinkPHP ? ["ThinkPHP 5.0.23 远程代码执行 (CVE-2018-20062)", "invokefunction 路由利用成功"] :
            isShiro    ? ["Apache Shiro 反序列化 RCE (CVE-2016-4437)", "rememberMe 默认密钥利用，命令执行已验证"] :
            isFastjson ? ["Fastjson 1.2.47 JNDI 反序列化 RCE (CVE-2019-14540)", "autoType 绕过 + LDAP 注入利用成功"] :
            isWebLogic ? ["Oracle WebLogic T3 反序列化 RCE (CVE-2023-21839)", "IIOP 协议恶意序列化 Payload 执行成功"] :
            isTomcat   ? ["Apache Tomcat PUT 任意文件上传 RCE (CVE-2017-12615)", "JSP Webshell 上传与执行已验证"] :
            ["Web 应用远程代码执行漏洞", "信息泄露与权限提升"];
          const vulnService =
            isStruts   ? "Apache Struts2/2.3.30 (port 8080)" :
            isFlask    ? "Flask/2.2.3 + Jinja2/3.0.3 (port 5000)" :
            isThinkPHP ? "ThinkPHP/5.0.23 + PHP/7.2.9 (port 80)" :
            isShiro    ? "Apache Shiro/1.2.4 + Tomcat/8.5 (port 8080)" :
            isFastjson ? "Spring Boot + Fastjson/1.2.47 (port 8080)" :
            isWebLogic ? "Oracle WebLogic Server/14.1.1.0 (port 7001)" :
            isTomcat   ? "Apache Tomcat/8.5.19 (port 8080)" :
            "Web Application (port 80/8080)";
          obs = {
            task_id: t.taskId, target: t.target, status: "DONE",
            current_phase: "DONE", generated_at: new Date().toISOString(),
            context: {
              open_ports: isWebLogic ? [7001, 7002] : isTomcat || isShiro || isFastjson ? [8080, 22] : [80, 443, 8080],
              vulnerable_services: [vulnService],
              confirmed_vulnerabilities: confirmedVulns,
            },
            artifacts_summary: [
              { skill_id: "nmap", summary: `发现开放端口，识别服务：${vulnService}` },
              { skill_id: "nuclei", summary: confirmedVulns[0] + " — PoC 验证通过，CVSS 9.8 (Critical)" },
              { skill_id: isThinkPHP || isFlask ? "dirsearch" : "whatweb", summary: "服务指纹识别完成，确认目标框架与版本信息" },
            ],
          };
        }
        if (!exeList) {
          // Target-specific skill execution records matching TasksPage demoSkills
          const nl = t.name?.toLowerCase() ?? "";
          const isStruts2  = nl.includes("struts") || nl.includes("s2-045");
          const isFlaskEx  = nl.includes("flask") || nl.includes("ssti");
          const isThinkEx  = nl.includes("thinkphp");
          const isShiroEx  = nl.includes("shiro");
          const isFastEx   = nl.includes("fastjson");
          const isWlEx     = nl.includes("weblogic");
          const isTomEx    = nl.includes("tomcat") || nl.includes("put 上传");
          type SkillEntry = { phase: string; skill: string; dur: number };
          const skillPlan: SkillEntry[] =
            isStruts2 ? [
              { phase: "RECON",        skill: "nmap",               dur: 10200 },
              { phase: "RECON",        skill: "httpx",              dur: 3100  },
              { phase: "THREAT_MODEL", skill: "ehole",              dur: 2800  },
              { phase: "VULN_SCAN",    skill: "nuclei",             dur: 16400 },
              { phase: "EXPLOIT",      skill: "exploit-struts2",    dur: 2100  },
              { phase: "EXPLOIT",      skill: "linpeas",            dur: 9600  },
              { phase: "REPORT",       skill: "read_workspace_artifact", dur: 840 },
            ] : isFlaskEx ? [
              { phase: "RECON",        skill: "nmap",               dur: 9800  },
              { phase: "RECON",        skill: "whatweb",            dur: 2600  },
              { phase: "THREAT_MODEL", skill: "dirsearch",          dur: 11200 },
              { phase: "VULN_SCAN",    skill: "nuclei",             dur: 13800 },
              { phase: "EXPLOIT",      skill: "exploit-ssti",       dur: 1800  },
              { phase: "REPORT",       skill: "read_workspace_artifact", dur: 720 },
            ] : isThinkEx ? [
              { phase: "RECON",        skill: "nmap",               dur: 10800 },
              { phase: "RECON",        skill: "whatweb",            dur: 4200  },
              { phase: "VULN_SCAN",    skill: "nuclei",             dur: 14600 },
              { phase: "EXPLOIT",      skill: "exploit-thinkphp",   dur: 1900  },
              { phase: "EXPLOIT",      skill: "linpeas",            dur: 9800  },
              { phase: "REPORT",       skill: "read_workspace_artifact", dur: 780 },
            ] : isShiroEx ? [
              { phase: "RECON",        skill: "nmap",               dur: 12300 },
              { phase: "RECON",        skill: "httpx",              dur: 2800  },
              { phase: "VULN_SCAN",    skill: "nuclei",             dur: 15200 },
              { phase: "EXPLOIT",      skill: "shiro_exploit",      dur: 2400  },
              { phase: "EXPLOIT",      skill: "ysoserial",          dur: 6800  },
              { phase: "EXPLOIT",      skill: "linpeas",            dur: 10400 },
              { phase: "REPORT",       skill: "read_workspace_artifact", dur: 860 },
            ] : isFastEx ? [
              { phase: "RECON",        skill: "nmap",               dur: 11200 },
              { phase: "RECON",        skill: "httpx",              dur: 2600  },
              { phase: "VULN_SCAN",    skill: "nuclei",             dur: 14900 },
              { phase: "EXPLOIT",      skill: "fastjson-exploit",   dur: 2200  },
              { phase: "EXPLOIT",      skill: "jndi_exploit",       dur: 5600  },
              { phase: "EXPLOIT",      skill: "linpeas",            dur: 9200  },
              { phase: "REPORT",       skill: "read_workspace_artifact", dur: 740 },
            ] : isWlEx ? [
              { phase: "RECON",        skill: "nmap",               dur: 13500 },
              { phase: "RECON",        skill: "httpx",              dur: 3200  },
              { phase: "VULN_SCAN",    skill: "nuclei",             dur: 17200 },
              { phase: "EXPLOIT",      skill: "exploit-weblogic",   dur: 2800  },
              { phase: "EXPLOIT",      skill: "ysoserial",          dur: 7400  },
              { phase: "EXPLOIT",      skill: "read_workspace_artifact", dur: 2100 },
              { phase: "REPORT",       skill: "read_workspace_artifact", dur: 920 },
            ] : isTomEx ? [
              { phase: "RECON",        skill: "nmap",               dur: 10900 },
              { phase: "RECON",        skill: "httpx",              dur: 2500  },
              { phase: "VULN_SCAN",    skill: "nuclei",             dur: 13800 },
              { phase: "EXPLOIT",      skill: "exploit-tomcat",     dur: 1700  },
              { phase: "EXPLOIT",      skill: "linpeas",            dur: 8900  },
              { phase: "REPORT",       skill: "read_workspace_artifact", dur: 800 },
            ] : [
              { phase: "RECON",        skill: "nmap",               dur: 10500 },
              { phase: "RECON",        skill: "httpx",              dur: 2900  },
              { phase: "VULN_SCAN",    skill: "nuclei",             dur: 14200 },
              { phase: "EXPLOIT",      skill: "sqlmap",             dur: 8800  },
              { phase: "REPORT",       skill: "read_workspace_artifact", dur: 760 },
            ];
          const base = Date.now() - skillPlan.reduce((a, s) => a + s.dur, 0);
          let cursor = base;
          exeList = skillPlan.map((s, i) => {
            cursor += s.dur;
            return {
              request_id: `demo-${t.taskId}-${i}`,
              task_id: t.taskId, phase: s.phase,
              skill_id: s.skill, status: "DONE", duration_ms: s.dur,
              created_at: new Date(cursor).toISOString(),
            };
          });
        }
      }
      toast.dismiss("report-dl");
    }

    const phases = report?.phases ?? [];
    const phaseRows = phases.map((p) => `| ${p.phase ?? "—"} | ${p.status ?? "—"} | ${p.notes ?? ""} |`).join("\n");
    const execs = exeList ?? [];
    const execRows = execs.slice(0, 20).map((e, i) =>
      `| ${i + 1} | ${e.phase ?? "—"} | ${e.skill_id ?? "—"} | ${e.status ?? "—"} | ${e.duration_ms ?? "—"}ms |`
    ).join("\n");

    const norm = normalizeReport(report, obs);
    const { findings, recommendations: repairs, artifacts: normArtifacts, openPorts, services: vulnSvcs, riskLevel, severityHistogram } = norm;
    const openPortsStr = openPorts.join(", ");
    const vulnServicesStr = vulnSvcs.join(", ");
    const findingLines = findings.map((f) => {
      const cvePart = f.cve ? ` [${f.cve}]` : "";
      const sevPart = f.severity ? ` (${f.severity})` : "";
      const skillPart = f.skill ? ` — ${f.skill}` : "";
      const evidencePart = f.evidence ? `\n  - 证据：${f.evidence}` : "";
      return `- **${f.title}**${cvePart}${sevPart}${skillPart}${evidencePart}`;
    }).join("\n");
    const artifactRows = normArtifacts.map((a) => `| ${a.skillId} | ${a.summary} |`).join("\n");

    const histSummary = Object.entries(severityHistogram ?? {})
      .filter(([, n]) => (n as number) > 0)
      .map(([k, n]) => `${k}=${n}`).join(", ");

    const vulnSection = (findings.length > 0 || vulnServicesStr || openPortsStr) ? [
      `## 漏洞发现`,
      ``,
      `**整体风险等级**: ${RISK_LABEL[riskLevel] ?? riskLevel}`,
      histSummary ? `**严重度分布**: ${histSummary}` : "",
      openPortsStr ? `**开放端口**: ${openPortsStr}` : "",
      vulnServicesStr ? `**存在漏洞服务**: ${vulnServicesStr}` : "",
      findingLines ? `\n**确认漏洞 (${findings.length})**:\n${findingLines}` : "",
    ].filter(Boolean).join("\n") : "";

    const artifactsSection = artifactRows ? [
      `## 扫描产物摘要`,
      ``,
      `| 技能 | 摘要 |`,
      `|---|---|`,
      artifactRows,
    ].join("\n") : "";

    const severityLabel: Record<string, string> = { high: "⚠ 高危", medium: "◆ 中危", low: "● 低危" };
    const repairRows = repairs.map((r, i) =>
      `### ${i + 1}. ${r.finding}\n**风险等级**: ${severityLabel[r.severity] ?? r.severity}\n\n**修复建议**: ${r.suggestion}`
    ).join("\n\n");
    const repairSection = repairRows ? `## 修复建议\n\n${repairRows}` : "";

    const reportSummary = report?.summary ?? "";

    const md = [
      `# 渗透测试报告`,
      ``,
      `**任务名称**: ${t.name}`,
      `**目标地址**: ${t.target}`,
      `**Task ID**: ${t.taskId}`,
      `**测试状态**: ${t.status}`,
      `**整体风险**: ${RISK_LABEL[riskLevel] ?? riskLevel}`,
      `**创建时间**: ${t.createdAt ? new Date(t.createdAt).toLocaleString("zh-CN") : "—"}`,
      `**完成时间**: ${t.updatedAt ? new Date(t.updatedAt).toLocaleString("zh-CN") : "—"}`,
      ``,
      reportSummary ? `## 摘要\n\n${reportSummary}` : "",
      reportSummary ? `` : "",
      `## 阶段覆盖`,
      ``,
      phaseRows ? `| 阶段 | 状态 | 备注 |\n|---|---|---|\n${phaseRows}` : "无阶段数据",
      ``,
      vulnSection,
      vulnSection ? `` : "",
      repairSection,
      repairSection ? `` : "",
      artifactsSection,
      artifactsSection ? `` : "",
      execRows ? `## 执行轨迹 (${execs.length} 条)\n\n| # | 阶段 | 技能 | 状态 | 耗时 |\n|---|---|---|---|---|\n${execRows}` : "",
      ``,
      `---`,
      `*本报告由 TRUSTGUARD AGENT 自动生成*`,
    ].filter((l) => l !== null && l !== undefined).join("\n");

    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `trustguard-report-${t.taskId}.md`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success("Markdown 报告已下载");
  };

  const downloadHtmlReport = async (entry: ReportEntry) => {
    const t = entry.task;
    let report = entry.report;
    let exeList = entry.execs;
    let obs = entry.observation;
    if (report === null || exeList === null || obs === null) {
      toast.loading("正在准备报告数据…", { id: "report-html-dl" });
      try {
        const [r1, r2, r3] = await Promise.allSettled([
          report === null ? getTaskReport(t.taskId) : Promise.resolve(null),
          exeList === null ? getTaskExecutions(t.taskId, 30, 0) : Promise.resolve(null),
          obs === null ? getTaskObservation(t.taskId) : Promise.resolve(null),
        ]);
        if (r1.status === "fulfilled" && r1.value !== null) report = r1.value;
        if (r2.status === "fulfilled" && r2.value !== null) exeList = r2.value;
        if (r3.status === "fulfilled" && r3.value !== null) obs = r3.value;
      } catch { /* use what we have */ }
      // Demo fallback for numeric-ID seeds (same profiles as downloadReport)
      if (/^\d+$/.test(t.taskId)) {
        if (!report) {
          report = {
            taskId: t.taskId, target: t.target, status: "DONE",
            summary: `自动化渗透测试任务已完成全部 6 个阶段（RECON → THREAT_MODEL → VULN_SCAN → EXPLOIT → REPORT → DONE）。目标：${t.target}。测试过程由 LLM 驱动状态机自动推进，结合 34+ 安全技能容器完成侦察、漏洞扫描与利用验证。`,
            phases: PHASE_ORDER.map((ph) => ({ phase: ph, status: "DONE", notes: ph + " 已完成" })),
          };
        }
        if (!obs) {
          const nl = t.name?.toLowerCase() ?? "";
          const isStruts   = nl.includes("struts") || nl.includes("s2-045");
          const isFlask    = nl.includes("flask") || nl.includes("ssti");
          const isThinkPHP = nl.includes("thinkphp");
          const isShiro    = nl.includes("shiro");
          const isFastjson = nl.includes("fastjson");
          const isWebLogic = nl.includes("weblogic");
          const isTomcat   = nl.includes("tomcat") || nl.includes("put 上传");
          const confirmedVulns =
            isStruts   ? ["Apache Struts2 S2-045 RCE (CVE-2017-5638)", "OGNL 注入利用成功"] :
            isFlask    ? ["Flask Jinja2 SSTI 模板注入任意代码执行", "系统命令执行已验证"] :
            isThinkPHP ? ["ThinkPHP 5.0.23 远程代码执行 (CVE-2018-20062)", "invokefunction 路由利用成功"] :
            isShiro    ? ["Apache Shiro 反序列化 RCE (CVE-2016-4437)", "rememberMe 默认密钥利用，命令执行已验证"] :
            isFastjson ? ["Fastjson 1.2.47 JNDI 反序列化 RCE (CVE-2019-14540)", "autoType 绕过 + LDAP 注入利用成功"] :
            isWebLogic ? ["Oracle WebLogic T3 反序列化 RCE (CVE-2023-21839)", "IIOP 协议恶意序列化 Payload 执行成功"] :
            isTomcat   ? ["Apache Tomcat PUT 任意文件上传 RCE (CVE-2017-12615)", "JSP Webshell 上传与执行已验证"] :
            ["Web 应用远程代码执行漏洞", "信息泄露与权限提升"];
          const vulnService =
            isStruts   ? "Apache Struts2/2.3.30 (port 8080)" :
            isFlask    ? "Flask/2.2.3 + Jinja2/3.0.3 (port 5000)" :
            isThinkPHP ? "ThinkPHP/5.0.23 + PHP/7.2.9 (port 80)" :
            isShiro    ? "Apache Shiro/1.2.4 + Tomcat/8.5 (port 8080)" :
            isFastjson ? "Spring Boot + Fastjson/1.2.47 (port 8080)" :
            isWebLogic ? "Oracle WebLogic Server/14.1.1.0 (port 7001)" :
            isTomcat   ? "Apache Tomcat/8.5.19 (port 8080)" :
            "Web Application (port 80/8080)";
          obs = {
            task_id: t.taskId, target: t.target, status: "DONE",
            current_phase: "DONE", generated_at: new Date().toISOString(),
            context: {
              open_ports: isWebLogic ? [7001, 7002] : isTomcat || isShiro || isFastjson ? [8080, 22] : [80, 443, 8080],
              vulnerable_services: [vulnService],
              confirmed_vulnerabilities: confirmedVulns,
            },
            artifacts_summary: [
              { skill_id: "nmap", summary: `发现开放端口，识别服务：${vulnService}` },
              { skill_id: "nuclei", summary: confirmedVulns[0] + " — PoC 验证通过，CVSS 9.8 (Critical)" },
              { skill_id: "whatweb", summary: "服务指纹识别完成，确认目标框架与版本信息" },
            ],
          };
        }
      }
      toast.dismiss("report-html-dl");
    }
    const html = generateHtmlReport(t, report, exeList, obs);
    const blob = new Blob([html], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `trustguard-report-${t.taskId}.html`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success("HTML 报告已下载");
  };

  if (!loggedIn) return null;

  return (
    <div style={{ minHeight: "100vh", background: "linear-gradient(180deg, #020a12 0%, #0f172a 60%, #020a12 100%)" }}>
      <Header />
      <div style={{ paddingTop: 80, paddingBottom: 40, maxWidth: 1100, margin: "0 auto", padding: "80px 24px 40px" }}>

        {/* Title row */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24, flexWrap: "wrap", gap: 10 }}>
          <div>
            <div style={{ color: "#22d3ee", fontFamily: "monospace", fontSize: 11, letterSpacing: "0.18em", textTransform: "uppercase", marginBottom: 4 }}>
              ◈ REPORTS GALLERY
            </div>
            <h1 style={{ color: "#e2e8f0", fontFamily: "monospace", fontSize: 22, fontWeight: 800, margin: 0, letterSpacing: "0.04em" }}>
              渗透测试报告管理
            </h1>
            <div style={{ color: "#475569", fontSize: 12, marginTop: 4 }}>
              已完成任务的测试报告汇总 · 点击行展开查看详情
            </div>
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            {lastRefresh && (
              <span style={{ color: "#334155", fontFamily: "monospace", fontSize: 10 }}>
                {lastRefresh.toLocaleTimeString("zh-CN")} 刷新
              </span>
            )}
            <button
              type="button"
              onClick={() => { void refresh(); }}
              disabled={pageLoading}
              style={{
                padding: "6px 16px", borderRadius: 6, border: "1px solid rgba(34,211,238,0.35)",
                background: "rgba(34,211,238,0.07)", color: pageLoading ? "#475569" : "#22d3ee",
                fontFamily: "monospace", fontSize: 12, cursor: pageLoading ? "wait" : "pointer", fontWeight: 700,
              }}
            >{pageLoading ? "加载中…" : "↻ 刷新"}</button>
          </div>
        </div>

        {/* Stats KPI bar */}
        {entries.length > 0 && (() => {
          const durations = entries
            .filter((e) => e.task.createdAt && e.task.updatedAt)
            .map((e) => (new Date(e.task.updatedAt!).getTime() - new Date(e.task.createdAt!).getTime()) / 60000);
          const avgDur = durations.length > 0 ? Math.round(durations.reduce((a, b) => a + b, 0) / durations.length) : null;
          const minDur = durations.length > 0 ? Math.round(Math.min(...durations)) : null;
          const dates = entries.map((e) => e.task.updatedAt).filter(Boolean).map((d) => new Date(d!).getTime());
          const latest = dates.length > 0 ? new Date(Math.max(...dates)) : null;
          const kpis = [
            { label: "已完成测试", value: String(entries.length), color: "#22d3ee", sub: "份报告" },
            { label: "平均测试时长", value: avgDur !== null ? `${avgDur} 分钟` : "—", color: "#a78bfa", sub: "全任务均值" },
            { label: "最短测试时长", value: minDur !== null ? `${minDur} 分钟` : "—", color: "#34d399", sub: "最快完成" },
            { label: "最近完成", value: latest ? latest.toLocaleDateString("zh-CN") : "—", color: "#fb923c", sub: latest ? latest.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : "" },
          ];
          return (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 12, marginBottom: 20 }}>
              {kpis.map((k) => (
                <div key={k.label} style={{
                  background: "rgba(2,6,23,0.75)", border: `1px solid ${k.color}20`,
                  borderRadius: 10, padding: "12px 16px",
                }}>
                  <div style={{ color: "#475569", fontSize: 10, marginBottom: 4, letterSpacing: "0.04em" }}>{k.label}</div>
                  <div style={{ color: k.color, fontFamily: "monospace", fontSize: 20, fontWeight: 800 }}>{k.value}</div>
                  {k.sub && <div style={{ color: "#334155", fontSize: 10, marginTop: 2 }}>{k.sub}</div>}
                </div>
              ))}
            </div>
          );
        })()}

        {/* Empty state */}
        {!pageLoading && entries.length === 0 && (
          <div style={{
            padding: "60px 20px", textAlign: "center",
            background: "rgba(2,6,23,0.6)", border: "1px solid rgba(51,65,85,0.4)", borderRadius: 12,
          }}>
            <div style={{ color: "#334155", fontSize: 40, marginBottom: 12 }}>◇</div>
            <div style={{ color: "#475569", fontFamily: "monospace", fontSize: 13 }}>暂无已完成的渗透测试报告</div>
            <div style={{ color: "#334155", fontSize: 11, marginTop: 6 }}>完成一次渗透测试任务后，报告将在此处展示</div>
            <button
              type="button"
              onClick={() => navigate("/tasks")}
              style={{
                marginTop: 16, padding: "8px 20px", borderRadius: 6,
                border: "1px solid rgba(34,211,238,0.35)", background: "rgba(34,211,238,0.07)",
                color: "#22d3ee", fontSize: 12, cursor: "pointer", fontFamily: "monospace", fontWeight: 700,
              }}
            >前往任务管理 →</button>
          </div>
        )}

        {/* Report cards */}
        {entries.length > 0 && (
          <div style={{
            background: "rgba(2,6,23,0.75)", border: "1px solid rgba(51,65,85,0.4)",
            borderRadius: 12, overflow: "hidden",
          }}>
            <div style={{ padding: "10px 18px", borderBottom: "1px solid rgba(51,65,85,0.3)", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span style={{ color: "#34d399", fontWeight: 800, fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase" }}>
                已完成任务 ({entries.length})
              </span>
              <span style={{ color: "#475569", fontSize: 10, fontFamily: "monospace" }}>点击行展开报告详情</span>
              <input
                type="text"
                placeholder="搜索任务名称/目标…"
                value={searchFilter}
                onChange={(e) => setSearchFilter(e.target.value)}
                style={{
                  marginLeft: "auto", width: 200, padding: "4px 10px",
                  borderRadius: 5, border: "1px solid rgba(51,65,85,0.5)",
                  background: "rgba(2,6,23,0.8)", color: "#94a3b8",
                  fontFamily: "monospace", fontSize: 11, outline: "none",
                }}
              />
            </div>

            {entries
              .filter((e) => {
                if (!searchFilter.trim()) return true;
                const q = searchFilter.toLowerCase();
                return (e.task.name ?? "").toLowerCase().includes(q) || (e.task.target ?? "").toLowerCase().includes(q) || (e.task.taskId ?? "").toLowerCase().includes(q);
              })
              .map((entry, idx, filtered) => {
              const t = entry.task;
              const phaseColor = PHASE_COLORS[t.currentPhase] ?? "#94a3b8";
              const curPhaseIdx = PHASE_ORDER.indexOf(t.currentPhase);
              const successExecs = entry.execs ? countVulns(entry.execs) : null;
              const duration = t.createdAt && t.updatedAt
                ? Math.round((new Date(t.updatedAt).getTime() - new Date(t.createdAt).getTime()) / 60000)
                : null;

              const realIdx = entries.indexOf(entry);
              return (
                <div key={t.taskId} style={{ borderBottom: idx < filtered.length - 1 ? "1px solid rgba(51,65,85,0.2)" : "none" }}>
                  {/* Summary row — clickable */}
                  <div
                    onClick={() => { void toggleExpand(realIdx); }}
                    style={{
                      padding: "12px 18px", cursor: "pointer", display: "flex", gap: 12,
                      alignItems: "center", flexWrap: "wrap",
                      background: entry.expanded ? "rgba(52,211,153,0.03)" : "transparent",
                      transition: "background 0.15s",
                    }}
                  >
                    {/* Expand indicator */}
                    <span style={{ color: entry.expanded ? "#34d399" : "#475569", fontSize: 10, minWidth: 10 }}>
                      {entry.expanded ? "▲" : "▼"}
                    </span>

                    {/* Task name + target */}
                    <div style={{ flex: "1 1 200px", minWidth: 0 }}>
                      <div style={{ color: "#e2e8f0", fontWeight: 700, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={t.name}>
                        {t.name}
                      </div>
                      <div style={{ color: "#64748b", fontSize: 11, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={t.target}>
                        {t.target}
                      </div>
                    </div>

                    {/* Final phase badge */}
                    <span style={{
                      padding: "2px 9px", borderRadius: 4, fontSize: 10, fontFamily: "monospace", fontWeight: 700,
                      background: `${phaseColor}18`, border: `1px solid ${phaseColor}50`, color: phaseColor,
                      whiteSpace: "nowrap",
                    }}>{t.currentPhase || "—"}</span>

                    {/* Stats pills */}
                    {duration !== null && (
                      <span style={{ color: "#64748b", fontSize: 10, fontFamily: "monospace", whiteSpace: "nowrap" }}>
                        ⏱ {duration}m
                      </span>
                    )}
                    {successExecs !== null && (
                      <span style={{ color: "#34d399", fontSize: 10, fontFamily: "monospace", whiteSpace: "nowrap" }}>
                        ✓ {successExecs} 执行
                      </span>
                    )}

                    {/* Completion date */}
                    <span style={{ color: "#334155", fontSize: 10, fontFamily: "monospace", whiteSpace: "nowrap", marginLeft: "auto" }}>
                      {t.updatedAt ? new Date(t.updatedAt).toLocaleDateString("zh-CN") : "—"}
                    </span>

                    {/* Actions — stop propagation */}
                    <div
                      onClick={(e) => e.stopPropagation()}
                      style={{ display: "flex", gap: 6, flexShrink: 0 }}
                    >
                      <button
                        type="button"
                        onClick={() => navigate(`/tasks?taskId=${t.taskId}`)}
                        style={{
                          padding: "3px 10px", borderRadius: 4, fontSize: 11, fontWeight: 600,
                          border: "1px solid rgba(34,211,238,0.3)", background: "rgba(34,211,238,0.05)",
                          color: "#67e8f9", cursor: "pointer",
                        }}
                      >详情</button>
                      <button
                        type="button"
                        onClick={() => { void downloadReport(entry); }}
                        style={{
                          padding: "3px 10px", borderRadius: 4, fontSize: 11, fontWeight: 600,
                          border: "1px solid rgba(52,211,153,0.35)", background: "rgba(52,211,153,0.06)",
                          color: "#34d399", cursor: "pointer",
                        }}
                      >↓ MD</button>
                      <button
                        type="button"
                        onClick={() => { void downloadHtmlReport(entry); }}
                        style={{
                          padding: "3px 10px", borderRadius: 4, fontSize: 11, fontWeight: 600,
                          border: "1px solid rgba(251,191,36,0.35)", background: "rgba(251,191,36,0.06)",
                          color: "#fbbf24", cursor: "pointer",
                        }}
                      >↓ HTML</button>
                    </div>
                  </div>

                  {/* Expanded detail */}
                  {entry.expanded && (
                    <div style={{ padding: "12px 18px 18px 36px", background: "rgba(52,211,153,0.02)", borderTop: "1px solid rgba(51,65,85,0.15)" }}>
                      {entry.loading && (
                        <div style={{ color: "#475569", fontFamily: "monospace", fontSize: 11 }}>加载报告数据…</div>
                      )}
                      {!entry.loading && (
                        <>
                          {/* Phase progress bar */}
                          <div style={{ display: "flex", gap: 4, alignItems: "center", marginBottom: 14, flexWrap: "wrap" }}>
                            {PHASE_ORDER.map((ph, phIdx) => {
                              const done = curPhaseIdx > phIdx;
                              const active = ph === t.currentPhase;
                              const color = PHASE_COLORS[ph] ?? "#94a3b8";
                              return (
                                <div key={ph} style={{ display: "flex", alignItems: "center", gap: 3 }}>
                                  <span style={{
                                    padding: "1px 7px", borderRadius: 3, fontSize: 9, fontFamily: "monospace", fontWeight: 700,
                                    background: active ? `${color}20` : done ? `${color}0d` : "rgba(51,65,85,0.12)",
                                    border: `1px solid ${active ? color : done ? `${color}40` : "rgba(51,65,85,0.25)"}`,
                                    color: active ? color : done ? `${color}99` : "#334155",
                                    boxShadow: active ? `0 0 5px ${color}40` : "none",
                                  }}>{ph}</span>
                                  {phIdx < PHASE_ORDER.length - 1 && (
                                    <span style={{ color: "#1e293b", fontSize: 9 }}>→</span>
                                  )}
                                </div>
                              );
                            })}
                          </div>

                          {/* Metadata grid */}
                          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: "8px 24px", marginBottom: 14 }}>
                            <div>
                              <div style={{ color: "#475569", fontSize: 10 }}>Task ID</div>
                              <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
                                <span style={{ color: "#64748b", fontFamily: "monospace", fontSize: 11 }}>{t.taskId}</span>
                                <button
                                  type="button"
                                  onClick={() => { void navigator.clipboard.writeText(t.taskId); toast.success("已复制"); }}
                                  style={{ padding: "1px 6px", borderRadius: 3, border: "1px solid rgba(51,65,85,0.4)", background: "transparent", color: "#475569", fontSize: 10, cursor: "pointer" }}
                                >复制</button>
                                <button
                                  type="button"
                                  onClick={() => navigate(`/trace/${t.taskId}`)}
                                  style={{ padding: "1px 7px", borderRadius: 3, border: "1px solid rgba(129,140,248,0.4)", background: "rgba(129,140,248,0.07)", color: "#a5b4fc", fontSize: 10, cursor: "pointer" }}
                                >查看轨迹 →</button>
                              </div>
                            </div>
                            <div>
                              <div style={{ color: "#475569", fontSize: 10 }}>描述</div>
                              <div style={{ color: "#64748b", fontSize: 11, marginTop: 2 }} title={t.description || "—"}>{t.description || "—"}</div>
                            </div>
                            <div>
                              <div style={{ color: "#475569", fontSize: 10 }}>创建时间</div>
                              <div style={{ color: "#64748b", fontFamily: "monospace", fontSize: 11, marginTop: 2 }}>
                                {t.createdAt ? new Date(t.createdAt).toLocaleString("zh-CN") : "—"}
                              </div>
                            </div>
                            <div>
                              <div style={{ color: "#475569", fontSize: 10 }}>完成时间</div>
                              <div style={{ color: "#34d399", fontFamily: "monospace", fontSize: 11, marginTop: 2 }}>
                                {t.updatedAt ? new Date(t.updatedAt).toLocaleString("zh-CN") : "—"}
                              </div>
                            </div>
                            {duration !== null && (
                              <div>
                                <div style={{ color: "#475569", fontSize: 10 }}>测试时长</div>
                                <div style={{ color: "#22d3ee", fontFamily: "monospace", fontSize: 11, marginTop: 2 }}>{duration} 分钟</div>
                              </div>
                            )}
                          </div>

                          {/* Report summary */}
                          {entry.report?.summary && (
                            <div style={{ marginBottom: 14, padding: "10px 14px", borderRadius: 7, background: "rgba(34,211,238,0.04)", border: "1px solid rgba(34,211,238,0.12)" }}>
                              <div style={{ color: "#22d3ee", fontSize: 10, marginBottom: 5, textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "monospace" }}>报告摘要</div>
                              <div style={{ color: "#94a3b8", fontSize: 11, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{entry.report.summary}</div>
                            </div>
                          )}

                          {/* Findings + artifacts (from backend report or observation fallback) */}
                          {(() => {
                            const norm = normalizeReport(entry.report, entry.observation);
                            const { findings, artifacts, openPorts, services, riskLevel, severityHistogram } = norm;
                            if (findings.length === 0 && artifacts.length === 0 && openPorts.length === 0 && services.length === 0) return null;
                            const riskColorMap: Record<string, string> = { critical: "#ef4444", high: "#f87171", medium: "#fbbf24", low: "#94a3b8", none: "#475569" };
                            const riskCol = riskColorMap[riskLevel] ?? "#475569";
                            const findingSevColor: Record<string, string> = { critical: "#ef4444", high: "#f87171", medium: "#fbbf24", low: "#94a3b8", info: "#64748b" };
                            return (
                              <div style={{ marginBottom: 14 }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
                                  <div style={{ color: "#fb923c", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "monospace" }}>
                                    漏洞发现 / 扫描摘要
                                  </div>
                                  <span style={{
                                    padding: "1px 8px", borderRadius: 3, fontFamily: "monospace", fontSize: 9, fontWeight: 700,
                                    background: `${riskCol}18`, border: `1px solid ${riskCol}`, color: riskCol,
                                  }}>风险：{RISK_LABEL[riskLevel] ?? riskLevel}</span>
                                  {findings.length > 0 && (
                                    <span style={{ color: "#64748b", fontSize: 10, fontFamily: "monospace" }}>
                                      共 {findings.length} 处漏洞
                                      {severityHistogram && Object.keys(severityHistogram).some((k) => (severityHistogram[k] ?? 0) > 0) && (
                                        <> · {["critical", "high", "medium", "low"].filter((k) => (severityHistogram[k] ?? 0) > 0).map((k) => `${k}=${severityHistogram[k]}`).join(", ")}</>
                                      )}
                                    </span>
                                  )}
                                </div>

                                {(openPorts.length > 0 || services.length > 0) && (
                                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "6px 16px", marginBottom: findings.length > 0 ? 10 : 0 }}>
                                    {openPorts.length > 0 && (
                                      <div>
                                        <div style={{ color: "#475569", fontSize: 9, marginBottom: 2 }}>开放端口</div>
                                        <div style={{ color: "#fbbf24", fontFamily: "monospace", fontSize: 10 }}>{openPorts.join(", ")}</div>
                                      </div>
                                    )}
                                    {services.length > 0 && (
                                      <div>
                                        <div style={{ color: "#475569", fontSize: 9, marginBottom: 2 }}>存在漏洞服务</div>
                                        <div style={{ color: "#fb923c", fontFamily: "monospace", fontSize: 10 }}>{services.join(", ")}</div>
                                      </div>
                                    )}
                                  </div>
                                )}

                                {findings.length > 0 && (
                                  <div style={{ marginBottom: artifacts.length > 0 ? 10 : 0 }}>
                                    <div style={{ color: "#475569", fontSize: 9, marginBottom: 3 }}>确认漏洞 ({findings.length})</div>
                                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                                      {findings.map((f, vi) => {
                                        const col = findingSevColor[f.severity] ?? "#fca5a5";
                                        return (
                                          <div key={vi} style={{ padding: "4px 0", borderBottom: "1px dashed rgba(71,85,105,0.15)", fontFamily: "monospace", fontSize: 10 }}>
                                            <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                                              <span style={{ color: col, minWidth: 10 }}>!</span>
                                              <span style={{
                                                padding: "0 5px", borderRadius: 3, fontSize: 8, fontWeight: 700,
                                                background: `${col}15`, border: `1px solid ${col}40`, color: col,
                                              }}>{(f.severity ?? "medium").toUpperCase()}</span>
                                              <span style={{ color: "#fca5a5" }}>{f.title}</span>
                                              {f.cve && (
                                                <span style={{ padding: "0 5px", borderRadius: 3, fontSize: 8, background: "rgba(129,140,248,0.1)", border: "1px solid rgba(129,140,248,0.3)", color: "#a5b4fc" }}>{f.cve}</span>
                                              )}
                                              {f.skill && <span style={{ color: "#64748b", fontSize: 9 }}>[{f.skill}]</span>}
                                            </div>
                                            {f.evidence && (
                                              <div style={{ marginTop: 2, paddingLeft: 20, color: "#64748b", fontSize: 9, lineHeight: 1.4 }}>
                                                证据：{f.evidence}
                                              </div>
                                            )}
                                          </div>
                                        );
                                      })}
                                    </div>
                                  </div>
                                )}

                                {artifacts.length > 0 && (
                                  <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                                    <div style={{ color: "#475569", fontSize: 9, marginBottom: 2 }}>扫描产物 ({artifacts.length})</div>
                                    {artifacts.map((a, ai) => (
                                      <div key={ai} style={{ display: "flex", gap: 8, fontFamily: "monospace", fontSize: 10 }}>
                                        <span style={{ color: "#a78bfa", minWidth: 120, flexShrink: 0 }}>{a.skillId}</span>
                                        <span style={{ color: "#64748b" }}>{a.summary}</span>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            );
                          })()}

                          {/* Execution summary */}
                          {entry.execs && entry.execs.length > 0 && (
                            <div style={{ marginBottom: 14 }}>
                              <div style={{ color: "#475569", fontSize: 10, marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.06em" }}>
                                执行记录 ({entry.execs.length} 条)
                              </div>
                              <div style={{ display: "flex", flexDirection: "column", gap: 3, maxHeight: 180, overflowY: "auto" }}>
                                {entry.execs.map((ex, i) => {
                                  const s = (ex.status ?? "").toUpperCase();
                                  const col = s === "DONE" || s === "SUCCESS" ? "#34d399" : s === "FAILED" || s === "ERROR" ? "#f87171" : "#fbbf24";
                                  return (
                                    <div key={ex.request_id ?? i} style={{ display: "flex", gap: 8, alignItems: "center", fontFamily: "monospace", fontSize: 10 }}>
                                      <span style={{ color: col, minWidth: 8 }}>●</span>
                                      <span style={{ color: "#64748b", minWidth: 100 }}>{ex.skill_id ?? ex.phase ?? "—"}</span>
                                      <span style={{ color: col, minWidth: 60 }}>{ex.status ?? "—"}</span>
                                      {ex.duration_ms !== undefined && <span style={{ color: "#475569" }}>{ex.duration_ms}ms</span>}
                                      {ex.created_at && <span style={{ color: "#334155" }}>{new Date(ex.created_at).toLocaleTimeString("zh-CN")}</span>}
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          )}

                          {/* Repair suggestions (from backend report or observation fallback) */}
                          {(() => {
                            const norm = normalizeReport(entry.report, entry.observation);
                            const repairs = norm.recommendations;
                            if (repairs.length === 0) return null;
                            const sevColor: Record<string, string> = { high: "#f87171", medium: "#fbbf24", low: "#94a3b8" };
                            const sevLabel: Record<string, string> = { high: "高危", medium: "中危", low: "低危" };
                            return (
                              <div style={{ marginBottom: 14 }}>
                                <div style={{ color: "#34d399", fontSize: 10, marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "monospace" }}>
                                  修复建议 ({repairs.length})
                                </div>
                                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                  {repairs.map((r, ri) => (
                                    <div key={ri} style={{
                                      padding: "8px 12px", borderRadius: 6,
                                      background: "rgba(2,6,23,0.6)",
                                      border: `1px solid ${sevColor[r.severity]}28`,
                                    }}>
                                      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
                                        <span style={{
                                          padding: "1px 6px", borderRadius: 3, fontSize: 9, fontFamily: "monospace", fontWeight: 700,
                                          background: `${sevColor[r.severity]}15`, border: `1px solid ${sevColor[r.severity]}40`,
                                          color: sevColor[r.severity],
                                        }}>{sevLabel[r.severity]}</span>
                                        <span style={{ color: "#e2e8f0", fontSize: 11, fontWeight: 600 }}>{r.finding}</span>
                                      </div>
                                      <div style={{ color: "#94a3b8", fontSize: 10, lineHeight: 1.5 }}>{r.suggestion}</div>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            );
                          })()}

                          {/* Action bar */}
                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                            <button
                              type="button"
                              onClick={() => navigate(`/tasks?taskId=${t.taskId}`)}
                              style={{
                                padding: "5px 14px", borderRadius: 5, fontSize: 12, fontWeight: 700,
                                border: "1px solid rgba(34,211,238,0.3)", background: "rgba(34,211,238,0.06)",
                                color: "#67e8f9", cursor: "pointer",
                              }}
                            >查看任务详情</button>
                            <button
                              type="button"
                              onClick={() => navigate(`/logs?taskId=${t.taskId}`)}
                              style={{
                                padding: "5px 14px", borderRadius: 5, fontSize: 12, fontWeight: 700,
                                border: "1px solid rgba(167,139,250,0.35)", background: "rgba(167,139,250,0.06)",
                                color: "#a78bfa", cursor: "pointer",
                              }}
                            >查看日志</button>
                            <button
                              type="button"
                              onClick={() => { void downloadReport(entry); }}
                              style={{
                                padding: "5px 14px", borderRadius: 5, fontSize: 12, fontWeight: 700,
                                border: "1px solid rgba(52,211,153,0.4)", background: "rgba(52,211,153,0.08)",
                                color: "#34d399", cursor: "pointer",
                              }}
                            >↓ Markdown 报告</button>
                            <button
                              type="button"
                              onClick={() => { void downloadHtmlReport(entry); }}
                              style={{
                                padding: "5px 14px", borderRadius: 5, fontSize: 12, fontWeight: 700,
                                border: "1px solid rgba(251,191,36,0.4)", background: "rgba(251,191,36,0.08)",
                                color: "#fbbf24", cursor: "pointer",
                              }}
                            >↓ HTML 报告</button>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Stats bar (if entries loaded) */}
        {entries.length > 0 && (
          <div style={{
            marginTop: 14, padding: "10px 16px", borderRadius: 8,
            background: "rgba(2,6,23,0.5)", border: "1px solid rgba(51,65,85,0.35)",
            display: "flex", gap: 20, alignItems: "center", flexWrap: "wrap",
          }}>
            <span style={{ color: "#475569", fontSize: 11, fontFamily: "monospace" }}>统计：</span>
            <span style={{ color: "#34d399", fontSize: 11, fontFamily: "monospace" }}>
              ✓ {entries.length} 份完成报告
            </span>
            {entries.filter((e) => e.execs).length > 0 && (
              <span style={{ color: "#22d3ee", fontSize: 11, fontFamily: "monospace" }}>
                总执行 {entries.reduce((acc, e) => acc + (e.execs?.length ?? 0), 0)} 条记录
              </span>
            )}
            <button
              type="button"
              onClick={() => navigate("/dashboard")}
              style={{
                marginLeft: "auto", padding: "4px 12px", borderRadius: 5,
                border: "1px solid rgba(34,211,238,0.25)", background: "transparent",
                color: "#22d3ee", fontSize: 11, cursor: "pointer",
              }}
            >管理中心 →</button>
            <button
              type="button"
              onClick={() => navigate("/admin")}
              style={{
                padding: "4px 12px", borderRadius: 5,
                border: "1px solid rgba(51,65,85,0.4)", background: "transparent",
                color: "#475569", fontSize: 11, cursor: "pointer",
              }}
            >← 平台管理</button>
          </div>
        )}
      </div>
    </div>
  );
};

export default ReportsPage;
