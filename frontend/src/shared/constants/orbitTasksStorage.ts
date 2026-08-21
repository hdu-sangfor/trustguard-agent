/** Shared with TasksPage (writes) and logs CRTerminal (reads). */
import { DEMO_FALLBACK_ENABLED } from "@/shared/constants/demoFallback";

export const SENTINEL_ORBIT_TASKS_KEY = "sentinel_orbit_tasks_v1";

/** Same-tab sync (StorageEvent only fires across tabs). */
export const ORBIT_TASKS_UPDATED_EVENT = "sentinel-orbit-tasks-updated";

/** Written by TasksPage when user clicks "查看日志"; read+cleared by CRTerminal on mount. */
export const PENDING_LOG_TASK_KEY = "sentinel_pending_log_task_id";

export type StoredOrbitTask = {
  id: string;
  name: string;
  desc: string;
  url: string;
  /** Optional: logs page shows only these lines. */
  log?: string;
  createdAt: number;
  /** Last backend-synced update time (epoch ms). Populated from ApiTask.updatedAt. */
  updatedAt?: number;
  status: string;
  /** Current execution phase from backend, e.g. RECON / VULN_SCAN / EXPLOIT */
  currentPhase?: string;
};

function demoLog(taskName: string, target: string, phase: string): string {
  const base = Date.now();
  const rows = [
    ["ORCHESTRATOR", `任务接收：${taskName} / ${target}`],
    ["PLANNER", "生成 PTES 六阶段执行计划：RECON → THREAT_MODEL → VULN_SCAN → EXPLOIT → REPORT"],
    ["KB", "RAG 检索命中 6 条漏洞知识：CVE、PoC 边界、误报复核规则、修复建议"],
    ["EXECUTOR", "nmap 完成端口探测，发现 HTTP/SSH/数据库相关服务"],
    ["EXECUTOR", "httpx/whatweb 完成 Web 指纹识别，提取框架、标题、响应头"],
    ["EXECUTOR", "nuclei 完成模板扫描，输出候选漏洞和证据片段"],
    ["AGENT", "根据知识库规则进行二次验证，过滤弱证据命中项"],
    ["REPORT", `当前阶段 ${phase}，已归档执行记录、观察结果和证据链`],
  ];
  return rows
    .map(([source, message], index) => `[${new Date(base - (rows.length - index) * 42000).toISOString()}] [${source}] ${message}`)
    .join("\n");
}

export const DEMO_ORBIT_TASKS: StoredOrbitTask[] = [
  {
    id: "1",
    name: "Web 常规渗透测试",
    desc: "面向 DVWA 靶场的自动化渗透流程，覆盖目录枚举、SQL 注入、XSS、文件上传验证与报告生成。",
    url: "http://192.168.1.100/dvwa/",
    createdAt: Date.now() - 1000 * 60 * 42,
    updatedAt: Date.now() - 1000 * 60 * 2,
    status: "running",
    currentPhase: "EXPLOIT",
    log: demoLog("Web 常规渗透测试", "http://192.168.1.100/dvwa/", "EXPLOIT"),
  },
  {
    id: "2",
    name: "Struts2 S2-045 RCE 检测",
    desc: "检测 Struts2 Jakarta Multipart Content-Type OGNL 注入漏洞，并输出可复核命令执行证据。",
    url: "http://192.168.1.102:8080/struts2-showcase/",
    createdAt: Date.now() - 1000 * 60 * 68,
    updatedAt: Date.now() - 1000 * 60 * 8,
    status: "finished",
    currentPhase: "DONE",
    log: demoLog("Struts2 S2-045 RCE 检测", "http://192.168.1.102:8080/struts2-showcase/", "DONE"),
  },
  {
    id: "3",
    name: "API 接口安全测试",
    desc: "围绕 Spring Boot API 执行未授权访问、Actuator 暴露、SQL 注入与敏感数据泄露检测。",
    url: "http://192.168.1.101:8080/api/v1/",
    createdAt: Date.now() - 1000 * 60 * 25,
    updatedAt: Date.now() - 1000 * 60 * 5,
    status: "paused",
    currentPhase: "VULN_SCAN",
    log: demoLog("API 接口安全测试", "http://192.168.1.101:8080/api/v1/", "VULN_SCAN"),
  },
  {
    id: "4",
    name: "Shiro 默认密钥反序列化",
    desc: "检测 rememberMe Cookie 特征、默认密钥命中情况和反序列化利用链可达性。",
    url: "http://192.168.1.105:8080/shiro/",
    createdAt: Date.now() - 1000 * 60 * 88,
    updatedAt: Date.now() - 1000 * 60 * 31,
    status: "finished",
    currentPhase: "DONE",
    log: demoLog("Shiro 默认密钥反序列化", "http://192.168.1.105:8080/shiro/", "DONE"),
  },
  {
    id: "5",
    name: "Bugku CTF 文件上传题",
    desc: "竞赛题型演示任务：源码泄露识别、上传黑名单绕过、WebShell 验证和 Flag 证据归档。",
    url: "http://bugku.local/upload/",
    createdAt: Date.now() - 1000 * 60 * 12,
    updatedAt: Date.now() - 1000 * 60 * 1,
    status: "running",
    currentPhase: "REPORT",
    log: demoLog("Bugku CTF 文件上传题", "http://bugku.local/upload/", "REPORT"),
  },
  {
    id: "6",
    name: "内网主机综合渗透",
    desc: "多端口内网资产探测、弱口令识别、横向移动路径分析和高危服务利用验证。",
    url: "192.168.10.5",
    createdAt: Date.now() - 1000 * 60 * 95,
    updatedAt: Date.now() - 1000 * 60 * 40,
    status: "failed",
    currentPhase: "EXPLOIT",
    log: demoLog("内网主机综合渗透", "192.168.10.5", "EXPLOIT"),
  },
];

export function readStoredOrbitTasks(): StoredOrbitTask[] {
  try {
    const raw = localStorage.getItem(SENTINEL_ORBIT_TASKS_KEY);
    if (!raw) return DEMO_FALLBACK_ENABLED ? DEMO_ORBIT_TASKS : [];
    const p = JSON.parse(raw) as unknown;
    if (!Array.isArray(p) || p.length === 0) return DEMO_FALLBACK_ENABLED ? DEMO_ORBIT_TASKS : [];
    const stored = p as StoredOrbitTask[];
    return DEMO_FALLBACK_ENABLED ? stored : stored.filter((task) => !/^\d+$/.test(task.id));
  } catch {
    return DEMO_FALLBACK_ENABLED ? DEMO_ORBIT_TASKS : [];
  }
}
