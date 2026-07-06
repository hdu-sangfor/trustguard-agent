import { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { PieChart, Pie, Cell, Tooltip } from 'recharts';
import Header from "@/shared/components/Header";
import { useAppSession } from "@/shared/context/AppSessionContext";
import {
  getSliSnapshot, getMqStatus, getV1Overview, getSystemHealth, getTaskStats,
  listTasks, createTask, runTask, stopTask, resumeTask, deleteTask,
  bulkStopRunningTasks, cleanupFinishedTasks, getRecentGlobalEvents, getV1HealthOverview, getV1SchedulingObserve, getV1KbObserve, getV1KbFederationObserve, getSkillRegistry, getTaskExecutions,
  listUsers, createUser as apiCreateUser, updateUser as apiUpdateUser, deleteUser as apiDeleteUser, setUserPassword as apiSetUserPassword, getAuditEvents,
  toFrontendStatus,
  type ApiSliSnapshot, type ApiMqStatus, type ApiV1Overview, type ApiHealthStatus, type ApiTaskStats, type ApiTask, type ApiGlobalEvent, type ApiV1HealthOverview, type ApiSchedulingObserve, type ApiV1KbObserve, type ApiV1KbFederation, type ApiSkillRegistry, type ApiExecutionRecord, type ApiUser, type ApiAuditEvent,
} from "@/shared/lib/api";
import { SENTINEL_ORBIT_TASKS_KEY, ORBIT_TASKS_UPDATED_EVENT, readStoredOrbitTasks } from "@/shared/constants/orbitTasksStorage";

function MetricCard({
  title, color, border, children,
}: { title: string; color: string; border: string; children: React.ReactNode }) {
  return (
    <div style={{
      background: "rgba(2,6,23,0.75)",
      border: `1px solid ${border}`,
      borderRadius: 12,
      padding: "18px 20px",
      display: "flex",
      flexDirection: "column",
      gap: 10,
      minWidth: 0,
    }}>
      <div style={{ color, fontWeight: 800, fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase" as const }}>{title}</div>
      {children}
    </div>
  );
}

function MetricRow({ label, value, valueColor = "#e2e8f0" }: { label: string; value: React.ReactNode; valueColor?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
      <span style={{ color: "#64748b", fontSize: 12 }}>{label}</span>
      <span style={{ color: valueColor, fontWeight: 700, fontSize: 13, fontFamily: "monospace" }}>{value}</span>
    </div>
  );
}

function StatusBadge({ ok, label }: { ok: boolean | null; label: string }) {
  const color = ok === null ? "#94a3b8" : ok ? "#34d399" : "#f87171";
  const glow = ok === null ? "none" : ok ? "0 0 6px rgba(52,211,153,0.5)" : "0 0 6px rgba(248,113,113,0.5)";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: color, boxShadow: glow }} />
      <span style={{ color, fontSize: 12, fontWeight: 600 }}>{label}</span>
    </span>
  );
}

const DEMO_USERS: ApiUser[] = [
  { id: 1, userId: "user-demo-admin", username: "admin", displayName: "系统管理员", email: "admin@trustguard.local", role: "ADMIN", status: "ACTIVE", lastLoginAt: new Date().toISOString(), createdAt: "2025-01-01T00:00:00", updatedAt: new Date().toISOString() },
  { id: 2, userId: "user-demo-operator", username: "operator", displayName: "测试操作员", email: "operator@trustguard.local", role: "OPERATOR", status: "ACTIVE", lastLoginAt: new Date().toISOString(), createdAt: "2025-01-01T00:00:00", updatedAt: new Date().toISOString() },
  { id: 3, userId: "user-demo-viewer", username: "viewer", displayName: "只读用户", email: "viewer@trustguard.local", role: "VIEWER", status: "ACTIVE", lastLoginAt: null, createdAt: "2025-01-01T00:00:00", updatedAt: "2025-01-01T00:00:00" },
];

const DEMO_SKILL_REGISTRY: ApiSkillRegistry = {
  skill_ids: [
    "nmap","httpx","ehole","whatweb-fingerprint","http-enum","fscan","baidu-search","curl-raw",
    "katana","dirsearch","ffuf-dir-enum","dispatcher","nuclei","nikto-scan",
    "sqlmap","fenjing","exploit-struts2","exploit-thinkphp","shiro_exploit","fastjson-exploit",
    "ysoserial","jndi_exploit","exploit-weblogic","exploit-tomcat","metasploit","metasploit-session","webshell-php","python-sandbox",
    "linpeas","pua","read_workspace_artifact","read_target_list",
  ],
  skills: [
    { skill_id: "nmap", name: "nmap", category: "RECON", description: "端口扫描与服务版本探测" },
    { skill_id: "httpx", name: "httpx", category: "RECON", description: "快速 HTTP 探针与轻量 Web 指纹" },
    { skill_id: "ehole", name: "ehole", category: "RECON", description: "框架/CMS 指纹识别" },
    { skill_id: "whatweb-fingerprint", name: "whatweb-fingerprint", category: "RECON", description: "WhatWeb 技术栈检测" },
    { skill_id: "http-enum", name: "http-enum", category: "RECON", description: "HTTP 头部与基础枚举" },
    { skill_id: "fscan", name: "fscan", category: "RECON", description: "综合内网扫描" },
    { skill_id: "baidu-search", name: "baidu-search", category: "RECON", description: "情报收集搜索" },
    { skill_id: "curl-raw", name: "curl-raw", category: "RECON/EXPLOIT", description: "自定义 HTTP 请求与 PoC 验证" },
    { skill_id: "katana", name: "katana", category: "VULN_SCAN", description: "深度 Web 爬取" },
    { skill_id: "dirsearch", name: "dirsearch", category: "VULN_SCAN", description: "目录与路径爆破" },
    { skill_id: "ffuf-dir-enum", name: "ffuf-dir-enum", category: "VULN_SCAN", description: "高速 Web 路径枚举" },
    { skill_id: "dispatcher", name: "dispatcher", category: "VULN_SCAN", description: "URL 去重分片管道" },
    { skill_id: "nuclei", name: "nuclei", category: "VULN_SCAN/EXPLOIT", description: "模板化漏洞扫描与 PoC 验证" },
    { skill_id: "nikto-scan", name: "nikto-scan", category: "VULN_SCAN", description: "Web 配置审计" },
    { skill_id: "sqlmap", name: "sqlmap", category: "EXPLOIT", description: "SQL 注入检测与利用" },
    { skill_id: "fenjing", name: "fenjing", category: "EXPLOIT", description: "Jinja2 SSTI 自动化利用" },
    { skill_id: "exploit-struts2", name: "exploit-struts2", category: "EXPLOIT", description: "Struts2 RCE 利用链" },
    { skill_id: "exploit-thinkphp", name: "exploit-thinkphp", category: "EXPLOIT", description: "ThinkPHP 5.x RCE（CVE-2018-20062）" },
    { skill_id: "shiro_exploit", name: "shiro_exploit", category: "EXPLOIT", description: "Shiro 反序列化 RCE（CVE-2016-4437）" },
    { skill_id: "fastjson-exploit", name: "fastjson-exploit", category: "EXPLOIT", description: "Fastjson JNDI RCE（CVE-2019-14540）" },
    { skill_id: "ysoserial", name: "ysoserial", category: "EXPLOIT", description: "Java 反序列化 Payload 生成" },
    { skill_id: "jndi_exploit", name: "jndi_exploit", category: "EXPLOIT", description: "JNDI 注入利用服务端" },
    { skill_id: "exploit-weblogic", name: "exploit-weblogic", category: "EXPLOIT", description: "WebLogic T3 反序列化 RCE（CVE-2023-21839）" },
    { skill_id: "exploit-tomcat", name: "exploit-tomcat", category: "EXPLOIT", description: "Tomcat PUT 上传 RCE（CVE-2017-12615）" },
    { skill_id: "metasploit", name: "metasploit", category: "EXPLOIT", description: "MSF 漏洞利用框架" },
    { skill_id: "metasploit-session", name: "metasploit-session", category: "EXPLOIT", description: "MSF 会话管理" },
    { skill_id: "webshell-php", name: "webshell-php", category: "EXPLOIT", description: "PHP Webshell 上传与执行" },
    { skill_id: "python-sandbox", name: "python-sandbox", category: "EXPLOIT", description: "沙箱化 Payload 调试" },
    { skill_id: "linpeas", name: "linpeas", category: "POST_EXPLOIT", description: "Linux 提权路径自动枚举" },
    { skill_id: "pua", name: "pua", category: "POST_EXPLOIT", description: "持久化访问维持" },
    { skill_id: "read_workspace_artifact", name: "read_workspace_artifact", category: "VERIFY", description: "工作区产物读取与验证" },
    { skill_id: "read_target_list", name: "read_target_list", category: "ALL", description: "多目标批量测试驱动" },
  ],
};

function readSessionRole(): string | null {
  try {
    const raw = localStorage.getItem('sentinel_session_v1');
    if (!raw) return null;
    const p = JSON.parse(raw) as Record<string, unknown>;
    return typeof p.role === 'string' ? p.role : null;
  } catch { return null; }
}

const AdminPage = () => {
  const { loggedIn } = useAppSession();
  const navigate = useNavigate();
  const sessionRole = readSessionRole();

  const [sli, setSli] = useState<ApiSliSnapshot | null>(null);
  const [mq, setMq] = useState<ApiMqStatus | null>(null);
  const [v1, setV1] = useState<ApiV1Overview | null>(null);
  const [health, setHealth] = useState<ApiHealthStatus | null>(null);
  const [taskStats, setTaskStats] = useState<ApiTaskStats | null>(null);
  const [orchHealth, setOrchHealth] = useState<ApiV1HealthOverview | null>(null);
  const [schedObserve, setSchedObserve] = useState<ApiSchedulingObserve | null>(null);
  const [schedObserving, setSchedObserving] = useState(false);
  const [schedTaskId, setSchedTaskId] = useState('');
  const [kbObserve, setKbObserve] = useState<ApiV1KbObserve | null>(null);
  const [kbFederation, setKbFederation] = useState<ApiV1KbFederation | null>(null);
  const [activeTasks, setActiveTasks] = useState<ApiTask[]>([]);
  const [recentTasks, setRecentTasks] = useState<ApiTask[]>([]);
  const [globalEvents, setGlobalEvents] = useState<ApiGlobalEvent[]>([]);
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);
  const [taskExecutions, setTaskExecutions] = useState<Record<string, ApiExecutionRecord[]>>({});
  const [bulkStopping, setBulkStopping] = useState(false);
  const [cleaningUp, setCleaningUp] = useState(false);
  const [loading, setLoading] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  // Skill registry (on-demand)
  const [skillRegistry, setSkillRegistry] = useState<ApiSkillRegistry | null>(null);
  const [skillRegistryLoading, setSkillRegistryLoading] = useState(false);
  const [skillPhaseFilter, setSkillPhaseFilter] = useState<string>('');
  // Quick-create task form
  const [qcName, setQcName] = useState('');
  const [qcTarget, setQcTarget] = useState('');
  const [qcDesc, setQcDesc] = useState('');
  const [qcSubmitting, setQcSubmitting] = useState(false);
  const [qcPendingTasks, setQcPendingTasks] = useState<ApiTask[]>([]);
  // User management
  const [users, setUsers] = useState<ApiUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [usersPanelOpen, setUsersPanelOpen] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [newDisplayName, setNewDisplayName] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [newRole, setNewRole] = useState<'ADMIN' | 'OPERATOR' | 'VIEWER'>('VIEWER');
  const [newInitialPassword, setNewInitialPassword] = useState('');
  const [userSubmitting, setUserSubmitting] = useState(false);
  const [pwdTargetUser, setPwdTargetUser] = useState<ApiUser | null>(null);
  const [pwdNewValue, setPwdNewValue] = useState('');
  const [pwdSubmitting, setPwdSubmitting] = useState(false);

  type AdminTab = 'monitor' | 'tasks' | 'skills' | 'config' | 'users' | 'audit';
  const [activeTab, setActiveTab] = useState<AdminTab>('monitor');
  const [auditEvents, setAuditEvents] = useState<ApiAuditEvent[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);
  // Ref tracks which error keys we've already toasted — avoids putting `errors`
  // in the refresh callback deps (which would cause an infinite re-render loop).
  const toastedErrorKeysRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!loggedIn) {
      toast.error("请先登录");
      localStorage.setItem("sentinel_login_redirect", "/admin");
      navigate("/login", { replace: true });
    }
  }, [loggedIn, navigate]);

  // Lazy-fetch execution records when a task row is expanded
  useEffect(() => {
    if (!expandedTaskId) return;
    if (taskExecutions[expandedTaskId]) return; // already loaded
    getTaskExecutions(expandedTaskId, 10, 0)
      .then((recs) => setTaskExecutions((prev) => ({ ...prev, [expandedTaskId]: recs })))
      .catch(() => setTaskExecutions((prev) => ({ ...prev, [expandedTaskId]: [] })));
  }, [expandedTaskId, taskExecutions]);

  const refresh = useCallback(async () => {
    setLoading(true);
    const errs: Record<string, string> = {};
    const [sliRes, mqRes, v1Res, healthRes, statsRes, tasksRes, eventsRes, orchHealthRes, kbObserveRes, kbFedRes] = await Promise.allSettled([
      getSliSnapshot(true),
      getMqStatus(),
      getV1Overview(),
      getSystemHealth(),
      getTaskStats(),
      listTasks(),
      getRecentGlobalEvents(40),
      getV1HealthOverview(),
      getV1KbObserve(),
      getV1KbFederationObserve(),
    ]);
    if (sliRes.status === "fulfilled") setSli(sliRes.value);
    else errs.sli = (sliRes.reason as Error).message;
    if (mqRes.status === "fulfilled") setMq(mqRes.value);
    else errs.mq = (mqRes.reason as Error).message;
    if (v1Res.status === "fulfilled") setV1(v1Res.value);
    else errs.v1 = (v1Res.reason as Error).message;
    if (healthRes.status === "fulfilled") setHealth(healthRes.value);
    else errs.health = (healthRes.reason as Error).message;
    if (statsRes.status === "fulfilled") setTaskStats(statsRes.value);
    else errs.tasks = (statsRes.reason as Error).message;
    if (eventsRes.status === "fulfilled") setGlobalEvents(eventsRes.value);
    // global events error is non-critical
    if (orchHealthRes.status === "fulfilled") setOrchHealth(orchHealthRes.value);
    // orch health error is non-critical
    if (kbObserveRes.status === "fulfilled") setKbObserve(kbObserveRes.value);
    // kb observe error is non-critical
    if (kbFedRes.status === "fulfilled") setKbFederation(kbFedRes.value);
    // kb federation error is non-critical
    if (tasksRes.status === "fulfilled") {
      const all = tasksRes.value;
      setActiveTasks(all.filter((t) => t.status === "RUNNING" || t.status === "PAUSED"));
      setQcPendingTasks(all.filter((t) => t.status === "PENDING"));
      setRecentTasks(
        all
          .filter((t) => t.status === "DONE" || t.status === "FAILED" || t.status === "CANCELLED")
          .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
          .slice(0, 8),
      );
      // Sync localStorage so CRTerminal tabs and Header badge stay current
      // even when user hasn't visited TasksPage.
      if (all.length > 0) {
        try {
          const existing = readStoredOrbitTasks();
          const existingById = new Map(existing.map((t) => [t.id, t]));
          const merged = [
            // Keep truly local-only tasks: not a numeric demo seed AND not a backend-assigned ID.
            // Backend tasks (task-uuid) that are missing from `all` were deleted — don't preserve them.
            ...existing.filter((t) => !/^\d+$/.test(t.id) && !t.id.startsWith('task-') && !all.some((at) => at.taskId === t.id)),
            // All backend tasks (update status + phase; preserve local log/desc fields)
            ...all.map((at) => {
              const local = existingById.get(at.taskId);
              return {
                id: at.taskId,
                name: at.name ?? '未命名任务',
                desc: local?.desc ?? at.description ?? '',
                url: at.target ?? '',
                log: local?.log ?? '',
                createdAt: local?.createdAt ?? (new Date(at.createdAt).getTime() || Date.now()),
                updatedAt: at.updatedAt ? (new Date(at.updatedAt).getTime() || undefined) : undefined,
                status: toFrontendStatus(at.status),
                currentPhase: at.currentPhase,
              };
            }),
          ];
          localStorage.setItem(SENTINEL_ORBIT_TASKS_KEY, JSON.stringify(merged));
          window.dispatchEvent(new Event(ORBIT_TASKS_UPDATED_EVENT));
        } catch { /* quota */ }
      }
    }
    // Toast only new errors; use ref to avoid putting `errors` in deps (would cause infinite loop)
    const newErrKeys = new Set(Object.keys(errs));
    Object.entries(errs).forEach(([k, v]) => {
      if (!toastedErrorKeysRef.current.has(k)) toast.error(`[${k}] ${v.slice(0, 80)}`);
    });
    toastedErrorKeysRef.current = newErrKeys;
    setErrors(errs);
    setLastRefresh(new Date());
    setLoading(false);
  }, []);

  const handleBulkStop = useCallback(async () => {
    setBulkStopping(true);
    try {
      const r = await bulkStopRunningTasks();
      toast.success(`已发送停止请求：${r.stopped} 个运行中任务`);
      void refresh();
    } catch (e: unknown) {
      toast.error(`批量停止失败: ${(e as Error).message}`);
    } finally {
      setBulkStopping(false);
    }
  }, [refresh]);

  const handleCleanup = useCallback(async () => {
    setCleaningUp(true);
    try {
      const r = await cleanupFinishedTasks();
      toast.success(`已清理 ${r.deleted} 条完成/失败记录`);
      void refresh();
    } catch (e: unknown) {
      toast.error(`清理失败: ${(e as Error).message}`);
    } finally {
      setCleaningUp(false);
    }
  }, [refresh]);

  const fetchSkillRegistry = useCallback(async (phase?: string) => {
    setSkillRegistryLoading(true);
    try {
      const data = await getSkillRegistry(phase || undefined);
      setSkillRegistry(data);
    } catch {
      // Backend offline — use static demo registry (client-side phase filter)
      if (phase) {
        const filtered = DEMO_SKILL_REGISTRY.skills.filter((s) =>
          (s.category || "").toUpperCase().includes(phase.toUpperCase())
        );
        setSkillRegistry({ ...DEMO_SKILL_REGISTRY, skills: filtered, skill_ids: filtered.map((s) => s.skill_id) });
      } else {
        setSkillRegistry(DEMO_SKILL_REGISTRY);
      }
    } finally {
      setSkillRegistryLoading(false);
    }
  }, []);

  const handleQuickCreate = useCallback(async (autoRun = false) => {
    const name = qcName.trim();
    const target = qcTarget.trim();
    if (!name || !target) { toast.error('任务名称和目标 URL 不能为空'); return; }
    setQcSubmitting(true);
    try {
      const created = await createTask({ name, target, description: qcDesc.trim() });
      if (autoRun) {
        try { await runTask(created.taskId); } catch { /* start error — still created */ }
        toast.success(`任务已创建并启动: ${name}`);
      } else {
        toast.success(`任务已创建: ${name}`);
      }
      setQcName(''); setQcTarget(''); setQcDesc('');
      void refresh();
    } catch (e: unknown) {
      toast.error(`创建失败: ${(e as Error).message}`);
    } finally {
      setQcSubmitting(false);
    }
  }, [qcName, qcTarget, qcDesc, refresh]);

  const loadUsers = useCallback(async () => {
    setUsersLoading(true);
    try {
      const list = await listUsers();
      setUsers(list);
    } catch {
      // Backend offline — fall back to demo user list so panel isn't empty
      setUsers(DEMO_USERS);
    } finally {
      setUsersLoading(false);
    }
  }, []);

  const handleCreateUser = useCallback(async () => {
    if (!newUsername.trim()) { toast.error('用户名不能为空'); return; }
    if (newInitialPassword && newInitialPassword.length < 6) { toast.error('初始密码长度至少为6位'); return; }
    setUserSubmitting(true);
    try {
      const created = await apiCreateUser({ username: newUsername.trim(), displayName: newDisplayName.trim() || undefined, email: newEmail.trim() || undefined, role: newRole });
      if (newInitialPassword) {
        try { await apiSetUserPassword(created.userId, newInitialPassword); } catch { /* non-fatal */ }
      }
      toast.success(`用户 ${newUsername} 已创建${newInitialPassword ? '（含初始密码）' : ''}`);
      setNewUsername(''); setNewDisplayName(''); setNewEmail(''); setNewRole('VIEWER'); setNewInitialPassword('');
      void loadUsers();
    } catch (e: unknown) {
      toast.error(`创建失败: ${(e as Error).message}`);
    } finally {
      setUserSubmitting(false);
    }
  }, [newUsername, newDisplayName, newEmail, newRole, newInitialPassword, loadUsers]);

  const handleToggleUserStatus = useCallback(async (user: ApiUser) => {
    try {
      await apiUpdateUser(user.userId, { status: user.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE' });
      toast.success(`用户 ${user.username} 状态已更新`);
      void loadUsers();
    } catch (e: unknown) {
      toast.error(`更新失败: ${(e as Error).message}`);
    }
  }, [loadUsers]);

  const handleDeleteUser = useCallback(async (user: ApiUser) => {
    try {
      await apiDeleteUser(user.userId);
      toast.success(`用户 ${user.username} 已删除`);
      void loadUsers();
    } catch (e: unknown) {
      toast.error(`删除失败: ${(e as Error).message}`);
    }
  }, [loadUsers]);

  const loadAuditEvents = useCallback(async () => {
    setAuditLoading(true);
    try {
      const events = await getAuditEvents(100);
      setAuditEvents(events);
    } catch {
      // Backend offline — keep current list
    } finally {
      setAuditLoading(false);
    }
  }, []);

  const handleSetPassword = useCallback(async () => {
    if (!pwdTargetUser) return;
    if (pwdNewValue.length < 6) { toast.error('密码长度至少为6位'); return; }
    setPwdSubmitting(true);
    try {
      await apiSetUserPassword(pwdTargetUser.userId, pwdNewValue);
      toast.success(`用户 ${pwdTargetUser.username} 密码已更新`);
      setPwdTargetUser(null);
      setPwdNewValue('');
    } catch (e: unknown) {
      toast.error(`密码设置失败: ${(e as Error).message}`);
    } finally {
      setPwdSubmitting(false);
    }
  }, [pwdTargetUser, pwdNewValue]);

  useEffect(() => {
    if (!loggedIn) return;
    void refresh();
    void loadUsers(); // pre-fetch users so the panel renders instantly when opened
    void fetchSkillRegistry(); // auto-load skill registry (falls back to demo if backend offline)
    void loadAuditEvents(); // pre-fetch audit log
    const iv = window.setInterval(() => { void refresh(); }, 10000);
    return () => window.clearInterval(iv);
  }, [loggedIn, refresh, loadUsers, fetchSkillRegistry, loadAuditEvents]);

  if (!loggedIn) return null;

  const errorRate = sli?.tick_error_rate != null ? (sli.tick_error_rate * 100).toFixed(2) + "%" : "—";
  const errorRateColor = sli?.tick_error_rate != null
    ? sli.tick_error_rate > 0.1 ? "#f87171" : sli.tick_error_rate > 0.05 ? "#fbbf24" : "#34d399"
    : "#94a3b8";

  const mqReady = mq?.messages_ready ?? 0;
  const mqReadyColor = mqReady > 50 ? "#f87171" : mqReady > 10 ? "#fbbf24" : "#34d399";

  const backendOk = health?.status === "ok" || health?.status === "UP" || (health != null && !errors.health);
  const planEnabled = v1?.v1_scheduling?.plan_item_dispatch_enabled;
  const mqDispatch = v1?.v1_mq_lanes?.execution_dispatch_mode ?? mq?.mode;
  const kbEnabled = v1?.v1_kb?.enabled;
  const kbApiKey = v1?.v1_kb?.has_embed_api_key;
  const agentTotal = v1?.v1_agent_registry?.total;
  const agentEnabled = v1?.v1_agent_registry?.enabled;
  const schedActiveTasks = v1?.v1_scheduling?.active_tasks;

  return (
    <div style={{ minHeight: "100vh", background: "#020a12", paddingBottom: 60 }}>
      <Header />
      <div style={{ paddingTop: 80, maxWidth: 1100, margin: "0 auto", padding: "80px 24px 60px" }}>

        {/* Title row */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 28 }}>
          <div>
            <h1 style={{
              fontFamily: "'Courier New', monospace",
              fontSize: "clamp(1.3rem, 3vw, 2rem)",
              fontWeight: 800,
              color: "#22d3ee",
              letterSpacing: "0.12em",
              textShadow: "0 0 20px rgba(34,211,238,0.4)",
              margin: 0,
            }}>
              平台管理中心
            </h1>
            <div style={{ color: "#475569", fontFamily: "monospace", fontSize: 11, marginTop: 4, letterSpacing: "0.05em" }}>
              TRUSTGUARD AGENT — ADMIN DASHBOARD
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {lastRefresh && (
              <span style={{ color: "#475569", fontFamily: "monospace", fontSize: 11 }}>
                刷新于 {lastRefresh.toLocaleTimeString("zh-CN")}
              </span>
            )}
            <button
              type="button"
              disabled={loading}
              onClick={() => { void refresh(); }}
              style={{
                padding: "8px 16px", borderRadius: 8,
                border: "1px solid rgba(34,211,238,0.5)",
                background: loading ? "rgba(34,211,238,0.05)" : "rgba(34,211,238,0.1)",
                color: "#22d3ee", fontFamily: "monospace", fontSize: 12, fontWeight: 700,
                cursor: loading ? "wait" : "pointer",
                letterSpacing: "0.05em",
              }}
            >
              {loading ? "刷新中…" : "↻ 刷新"}
            </button>
          </div>
        </div>

        {/* System health banner */}
        <div style={{
          marginBottom: 24, padding: "12px 18px", borderRadius: 8,
          background: backendOk ? "rgba(52,211,153,0.07)" : "rgba(248,113,113,0.07)",
          border: `1px solid ${backendOk ? "rgba(52,211,153,0.25)" : "rgba(248,113,113,0.25)"}`,
          display: "flex", alignItems: "center", gap: 16,
        }}>
          <StatusBadge ok={errors.health ? false : health ? backendOk : null} label={errors.health ? "后端离线" : health ? "后端在线" : "检查中…"} />
          {health?.status && (
            <span style={{ color: "#64748b", fontFamily: "monospace", fontSize: 11 }}>
              status: <span style={{ color: "#94a3b8" }}>{String(health.status)}</span>
            </span>
          )}
          {orchHealth?.health?.status != null && (
            <StatusBadge
              ok={orchHealth.health.status === "ok" || orchHealth.health.status === "healthy"}
              label={`编排器: ${String(orchHealth.health.status)}`}
            />
          )}
          {orchHealth?.health?.status === "degraded" && orchHealth.health.message && (
            <span style={{ color: "#fbbf24", fontFamily: "monospace", fontSize: 11 }}>
              {String(orchHealth.health.message).slice(0, 60)}
            </span>
          )}
          {Object.entries(errors).map(([k, v]) => (
            <span key={k} style={{ color: "#f87171", fontFamily: "monospace", fontSize: 11 }}>
              [{k}] {v.slice(0, 60)}
            </span>
          ))}
        </div>

        {/* Tab navigation */}
        {(() => {
          const tabs: { id: AdminTab; label: string; color: string }[] = [
            { id: 'monitor', label: '系统监控', color: '#22d3ee' },
            { id: 'tasks',   label: '任务管理', color: '#34d399' },
            { id: 'skills',  label: '技能注册表', color: '#a78bfa' },
            { id: 'config',  label: '配置管理', color: '#fb923c' },
            { id: 'users',   label: '用户管理', color: '#6366f1' },
            { id: 'audit',   label: '审计日志', color: '#f472b6' },
          ];
          return (
            <div style={{ display: "flex", gap: 4, marginBottom: 20, borderBottom: "1px solid rgba(51,65,85,0.5)", paddingBottom: 0 }}>
              {tabs.map((tab) => {
                const isActive = activeTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => {
                      setActiveTab(tab.id);
                      if (tab.id === 'audit') void loadAuditEvents();
                      if (tab.id === 'users') { void loadUsers(); setUsersPanelOpen(true); }
                    }}
                    style={{
                      padding: "8px 14px", border: "none", background: "none",
                      cursor: "pointer", fontFamily: "monospace", fontSize: 11,
                      fontWeight: isActive ? 800 : 600, letterSpacing: "0.06em",
                      color: isActive ? tab.color : "#475569",
                      borderBottom: isActive ? `2px solid ${tab.color}` : "2px solid transparent",
                      marginBottom: -1, transition: "color 0.15s, border-color 0.15s",
                      textShadow: isActive ? `0 0 8px ${tab.color}60` : "none",
                    }}
                  >{tab.label}</button>
                );
              })}
            </div>
          );
        })()}

        {/* ── MONITOR TAB ─────────────────────────────────────────────────────── */}
        {activeTab === 'monitor' && (<>

        {/* Metrics grid */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 16, marginBottom: 20 }}>

          {/* SLI Card */}
          <MetricCard title="SLI 快照" color="#22d3ee" border="rgba(34,211,238,0.25)">
            <MetricRow label="Tick 总数" value={sli?.total_ticks ?? "—"} />
            <MetricRow label="失败 Tick" value={sli?.failed_ticks ?? "—"} />
            <MetricRow label="错误率" value={errorRate} valueColor={errorRateColor} />
            <MetricRow
              label="活跃任务"
              value={sli?.active_tasks ?? "—"}
              valueColor={(sli?.active_tasks ?? 0) > 0 ? "#22d3ee" : "#64748b"}
            />
            {sli?.total_ticks != null && sli.total_ticks > 0 && (
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#475569", marginBottom: 3 }}>
                  <span>成功率</span>
                  <span style={{ fontFamily: "monospace", color: errorRateColor }}>
                    {(((sli.total_ticks - (sli.failed_ticks ?? 0)) / sli.total_ticks) * 100).toFixed(1)}%
                  </span>
                </div>
                <div style={{ height: 4, borderRadius: 2, background: "rgba(51,65,85,0.5)", overflow: "hidden" }}>
                  <div style={{
                    height: "100%", borderRadius: 2,
                    width: `${Math.max(0, ((sli.total_ticks - (sli.failed_ticks ?? 0)) / sli.total_ticks) * 100).toFixed(1)}%`,
                    background: (sli.tick_error_rate ?? 0) > 0.1 ? "#f87171" : (sli.tick_error_rate ?? 0) > 0.05 ? "#fbbf24" : "#34d399",
                    transition: "width 0.5s ease",
                  }} />
                </div>
              </div>
            )}
            {errors.sli && (
              <div style={{ color: "#f87171", fontSize: 10, fontFamily: "monospace" }}>{errors.sli.slice(0, 80)}</div>
            )}
          </MetricCard>

          {/* MQ Card */}
          <MetricCard title="消息队列" color="#a78bfa" border="rgba(167,139,250,0.25)">
            <MetricRow label="调度模式" value={mqDispatch ?? "—"} valueColor="#a78bfa" />
            <MetricRow label="队列名称" value={mq?.queue ? String(mq.queue).slice(0, 28) : "—"} />
            <MetricRow label="就绪消息" value={mqReady} valueColor={mqReadyColor} />
            <MetricRow label="消费者数" value={mq?.consumers ?? "—"} />
            {errors.mq && (
              <div style={{ color: "#f87171", fontSize: 10, fontFamily: "monospace" }}>{errors.mq.slice(0, 80)}</div>
            )}
          </MetricCard>

          {/* V1 Card */}
          <MetricCard title="V1 调度" color="#fb923c" border="rgba(249,115,22,0.25)">
            <MetricRow
              label="Plan 调度"
              value={planEnabled == null ? "—" : planEnabled ? "开启" : "关闭"}
              valueColor={planEnabled ? "#34d399" : planEnabled === false ? "#f87171" : "#94a3b8"}
            />
            <MetricRow
              label="编排活跃任务"
              value={schedActiveTasks ?? "—"}
              valueColor={(schedActiveTasks ?? 0) > 0 ? "#22d3ee" : "#64748b"}
            />
            <MetricRow
              label="MQ 派发"
              value={v1?.v1_mq_lanes?.mq_dispatch_ready == null ? "—" : v1.v1_mq_lanes.mq_dispatch_ready ? "就绪" : "未就绪"}
              valueColor={v1?.v1_mq_lanes?.mq_dispatch_ready ? "#34d399" : "#94a3b8"}
            />
            <MetricRow
              label="知识库 KB"
              value={kbEnabled == null ? "—" : kbEnabled ? "开启" : "关闭"}
              valueColor={kbEnabled ? "#34d399" : "#64748b"}
            />
            {kbEnabled && (
              <MetricRow
                label="KB Embed Key"
                value={kbApiKey == null ? "—" : kbApiKey ? "已配置" : "未配置"}
                valueColor={kbApiKey ? "#34d399" : "#f87171"}
              />
            )}
            {agentTotal != null && (
              <MetricRow
                label="Agent 注册数"
                value={`${agentEnabled ?? "?"} / ${agentTotal}`}
                valueColor={(agentEnabled ?? 0) > 0 ? "#fb923c" : "#64748b"}
              />
            )}
            {errors.v1 && (
              <div style={{ color: "#f87171", fontSize: 10, fontFamily: "monospace" }}>{errors.v1.slice(0, 80)}</div>
            )}
          </MetricCard>

          {/* KB Observe Card — only show if KB has any data */}
          {(kbObserve != null || kbEnabled) && (
            <MetricCard title="知识库 KB" color="#818cf8" border="rgba(99,102,241,0.25)">
              <MetricRow
                label="状态"
                value={kbObserve?.enabled != null ? (kbObserve.enabled ? "已启用" : "已禁用") : (kbEnabled ? "启用" : "禁用")}
                valueColor={kbObserve?.enabled || kbEnabled ? "#a5b4fc" : "#64748b"}
              />
              {kbObserve?.kb_backend && (
                <MetricRow label="后端" value={String(kbObserve.kb_backend)} valueColor="#818cf8" />
              )}
              {kbObserve?.collection_name && (
                <MetricRow label="集合" value={String(kbObserve.collection_name).slice(0, 24)} />
              )}
              {kbObserve?.total_chunks != null && (
                <MetricRow
                  label="知识块数"
                  value={String(kbObserve.total_chunks)}
                  valueColor={(kbObserve.total_chunks as number) > 0 ? "#a5b4fc" : "#64748b"}
                />
              )}
              {kbObserve?.vector_size != null && (
                <MetricRow label="向量维度" value={String(kbObserve.vector_size)} />
              )}
              <MetricRow
                label="Embed Key"
                value={kbObserve?.has_embed_api_key != null ? (kbObserve.has_embed_api_key ? "已配置" : "未配置") : (kbApiKey ? "已配置" : "未配置")}
                valueColor={(kbObserve?.has_embed_api_key ?? kbApiKey) ? "#34d399" : "#f87171"}
              />
              {kbObserve?.federation_enabled != null && (
                <MetricRow
                  label="联邦 KB"
                  value={kbObserve.federation_enabled ? "开启" : "关闭"}
                  valueColor={kbObserve.federation_enabled ? "#a5b4fc" : "#64748b"}
                />
              )}
              {Array.isArray(kbObserve?.skill_ids) && (kbObserve.skill_ids as string[]).length > 0 && (
                <div style={{ fontSize: 10, color: "#6366f1", fontFamily: "monospace", marginTop: 2 }}>
                  技能: {(kbObserve.skill_ids as string[]).slice(0, 4).join(", ")}{(kbObserve.skill_ids as string[]).length > 4 ? ` +${(kbObserve.skill_ids as string[]).length - 4}` : ""}
                </div>
              )}
              {kbFederation != null && (
                <>
                  <div style={{ height: 1, background: "rgba(99,102,241,0.15)", margin: "4px 0" }} />
                  <MetricRow
                    label="联邦存储"
                    value={kbFederation.enabled ? "已启用" : "已禁用"}
                    valueColor={kbFederation.enabled ? "#a5b4fc" : "#64748b"}
                  />
                  {kbFederation.store_type && (
                    <MetricRow label="存储类型" value={String(kbFederation.store_type)} valueColor="#818cf8" />
                  )}
                  {kbFederation.federation_provider && (
                    <MetricRow label="联邦提供商" value={String(kbFederation.federation_provider).slice(0, 20)} />
                  )}
                  {Array.isArray(kbFederation.stores) && kbFederation.stores.length > 0 && (
                    <div style={{ fontSize: 10, color: "#6366f1", fontFamily: "monospace", marginTop: 2 }}>
                      存储节点: {kbFederation.stores.slice(0, 3).map((s) => String(s.id ?? s.type ?? "?"+ (s.active ? "✓" : "○"))).join(", ")}{kbFederation.stores.length > 3 ? ` +${kbFederation.stores.length - 3}` : ""}
                    </div>
                  )}
                </>
              )}
            </MetricCard>
          )}

          {/* Task Stats Card */}
          <MetricCard title="任务统计" color="#34d399" border="rgba(52,211,153,0.25)">
            <MetricRow label="任务总数" value={taskStats?.total ?? "—"} />
            <MetricRow
              label="运行中"
              value={taskStats?.running ?? "—"}
              valueColor={(taskStats?.running ?? 0) > 0 ? "#22d3ee" : "#64748b"}
            />
            <MetricRow label="已暂停" value={taskStats?.paused ?? "—"} valueColor="#fbbf24" />
            <MetricRow
              label="已完成"
              value={taskStats?.done ?? "—"}
              valueColor={(taskStats?.done ?? 0) > 0 ? "#34d399" : "#64748b"}
            />
            <MetricRow
              label="失败"
              value={taskStats?.failed ?? "—"}
              valueColor={(taskStats?.failed ?? 0) > 0 ? "#f87171" : "#64748b"}
            />
            <MetricRow label="已取消" value={taskStats?.cancelled ?? "—"} />
            {errors.tasks && (
              <div style={{ color: "#f87171", fontSize: 10, fontFamily: "monospace" }}>{errors.tasks.slice(0, 80)}</div>
            )}
            {(() => {
              if (!taskStats) return null;
              const slices = [
                { name: "运行中", value: taskStats.running ?? 0, color: "#22d3ee" },
                { name: "已完成", value: taskStats.done ?? 0, color: "#34d399" },
                { name: "已暂停", value: taskStats.paused ?? 0, color: "#fbbf24" },
                { name: "失败", value: taskStats.failed ?? 0, color: "#f87171" },
                { name: "待运行", value: taskStats.pending ?? 0, color: "#475569" },
                { name: "已取消", value: taskStats.cancelled ?? 0, color: "#64748b" },
              ].filter((s) => s.value > 0);
              if (slices.length === 0) return null;
              const total = taskStats.total ?? slices.reduce((a, s) => a + s.value, 0);
              return (
                <div style={{ display: "flex", justifyContent: "center", marginTop: 4, position: "relative" }}>
                  <PieChart width={120} height={120}>
                    <Pie
                      data={slices}
                      cx={55}
                      cy={55}
                      innerRadius={30}
                      outerRadius={50}
                      dataKey="value"
                      strokeWidth={0}
                    >
                      {slices.map((s, i) => (
                        <Cell key={i} fill={s.color} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 6, fontSize: 11 }}
                      itemStyle={{ color: "#e2e8f0" }}
                    />
                  </PieChart>
                  {/* Center label overlay */}
                  <div style={{
                    position: "absolute", top: "50%", left: "50%",
                    transform: "translate(-50%, -50%)",
                    pointerEvents: "none", textAlign: "center",
                    lineHeight: 1.2,
                  }}>
                    <div style={{ color: "#e2e8f0", fontWeight: 800, fontSize: 15, fontFamily: "monospace" }}>{total}</div>
                    <div style={{ color: "#475569", fontSize: 8, fontFamily: "monospace" }}>total</div>
                  </div>
                </div>
              );
            })()}
          </MetricCard>
        </div>

        {/* Active Tasks */}
        {activeTasks.length > 0 && (
          <div style={{
            marginBottom: 16, borderRadius: 10,
            background: "rgba(2,6,23,0.75)", border: "1px solid rgba(34,211,238,0.2)",
            overflow: "hidden",
          }}>
            <div style={{
              padding: "10px 16px", borderBottom: "1px solid rgba(34,211,238,0.12)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ color: "#22d3ee", fontWeight: 800, fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase" as const }}>
                  活跃任务 ({activeTasks.length})
                </span>
                {activeTasks.filter((t) => t.status === "RUNNING").length > 0 && (
                  <span style={{ color: "#22d3ee", fontFamily: "monospace", fontSize: 10 }}>
                    {activeTasks.filter((t) => t.status === "RUNNING").length} 运行中
                  </span>
                )}
                {activeTasks.filter((t) => t.status === "PAUSED").length > 0 && (
                  <span style={{ color: "#fbbf24", fontFamily: "monospace", fontSize: 10 }}>
                    {activeTasks.filter((t) => t.status === "PAUSED").length} 已暂停
                  </span>
                )}
              </div>
              <span style={{ color: "#475569", fontFamily: "monospace", fontSize: 10 }}>点击"日志"查看实时输出</span>
            </div>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid rgba(51,65,85,0.4)" }}>
                    {["任务名称", "目标", "当前阶段", "状态", "操作"].map((h) => (
                      <th key={h} style={{ padding: "6px 14px", textAlign: "left" as const, color: "#475569", fontWeight: 600, fontSize: 10, letterSpacing: "0.06em", whiteSpace: "nowrap" as const }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {activeTasks.map((t) => {
                    const isRunning = t.status === "RUNNING";
                    const statusColor = isRunning ? "#22d3ee" : "#fbbf24";
                    const phaseColors: Record<string, string> = {
                      RECON: "#64748b", THREAT_MODEL: "#a78bfa", VULN_SCAN: "#fb923c",
                      EXPLOIT: "#f87171", REPORT: "#34d399", DONE: "#22d3ee",
                    };
                    const phaseOrder = ["RECON", "THREAT_MODEL", "VULN_SCAN", "EXPLOIT", "REPORT", "DONE"];
                    const phaseColor = phaseColors[t.currentPhase] ?? "#94a3b8";
                    const isExpanded = expandedTaskId === t.taskId;
                    return (
                      <>
                        <tr
                          key={t.taskId}
                          onClick={() => setExpandedTaskId(isExpanded ? null : t.taskId)}
                          style={{ borderBottom: isExpanded ? "none" : "1px solid rgba(51,65,85,0.2)", cursor: "pointer", background: isExpanded ? "rgba(34,211,238,0.03)" : "transparent" }}
                        >
                          <td style={{ padding: "8px 14px", color: "#e2e8f0", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }} title={t.name}>
                            <span style={{ marginRight: 5, color: isExpanded ? "#22d3ee" : "#475569", fontSize: 9 }}>{isExpanded ? "▲" : "▼"}</span>
                            {t.name}
                          </td>
                          <td style={{ padding: "8px 14px", color: "#64748b", fontFamily: "monospace", fontSize: 11, maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }} title={t.target}>{t.target}</td>
                          <td style={{ padding: "8px 14px" }}>
                            <span style={{ color: phaseColor, fontFamily: "monospace", fontSize: 11, fontWeight: 700 }}>{t.currentPhase || "—"}</span>
                          </td>
                          <td style={{ padding: "8px 14px" }}>
                            <span style={{
                              display: "inline-flex", alignItems: "center", gap: 4,
                              padding: "2px 8px", borderRadius: 4,
                              border: `1px solid ${statusColor}40`,
                              background: `${statusColor}10`,
                              color: statusColor, fontSize: 10, fontWeight: 700, fontFamily: "monospace",
                            }}>
                              <span style={{ width: 5, height: 5, borderRadius: "50%", background: statusColor, boxShadow: isRunning ? `0 0 5px ${statusColor}` : "none" }} />
                              {isRunning ? "运行中" : "已暂停"}
                            </span>
                          </td>
                          <td style={{ padding: "8px 14px" }} onClick={(e) => e.stopPropagation()}>
                            <div style={{ display: "flex", gap: 6 }}>
                              <button
                                type="button"
                                onClick={() => navigate(`/tasks?taskId=${t.taskId}`)}
                                style={{
                                  padding: "3px 10px", borderRadius: 4,
                                  border: "1px solid rgba(34,211,238,0.35)",
                                  background: "rgba(34,211,238,0.06)",
                                  color: "#67e8f9", fontSize: 11, cursor: "pointer", fontWeight: 600,
                                }}
                              >
                                详情
                              </button>
                              <button
                                type="button"
                                onClick={() => navigate(`/logs?taskId=${t.taskId}`)}
                                style={{
                                  padding: "3px 10px", borderRadius: 4,
                                  border: "1px solid rgba(167,139,250,0.4)",
                                  background: "rgba(167,139,250,0.08)",
                                  color: "#a78bfa", fontSize: 11, cursor: "pointer", fontWeight: 600,
                                }}
                              >
                                日志
                              </button>
                              {isRunning && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    stopTask(t.taskId)
                                      .then(() => { toast.success(`已暂停: ${t.name}`); void refresh(); })
                                      .catch((e: Error) => toast.error(`暂停失败: ${e.message}`));
                                  }}
                                  style={{
                                    padding: "3px 10px", borderRadius: 4,
                                    border: "1px solid rgba(251,191,36,0.4)",
                                    background: "rgba(251,191,36,0.08)",
                                    color: "#fbbf24", fontSize: 11, cursor: "pointer", fontWeight: 600,
                                  }}
                                >
                                  暂停
                                </button>
                              )}
                              {t.status === "PAUSED" && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    resumeTask(t.taskId)
                                      .then(() => { toast.success(`已续跑: ${t.name}`); void refresh(); })
                                      .catch((e: Error) => toast.error(`续跑失败: ${e.message}`));
                                  }}
                                  style={{
                                    padding: "3px 10px", borderRadius: 4,
                                    border: "1px solid rgba(52,211,153,0.4)",
                                    background: "rgba(52,211,153,0.08)",
                                    color: "#34d399", fontSize: 11, cursor: "pointer", fontWeight: 600,
                                  }}
                                >
                                  续跑
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                        {isExpanded && (
                          <tr key={`${t.taskId}-detail`} style={{ borderBottom: "1px solid rgba(51,65,85,0.2)" }}>
                            <td colSpan={5} style={{ padding: "10px 20px 14px", background: "rgba(34,211,238,0.03)" }}>
                              {/* Phase progress bar */}
                              <div style={{ display: "flex", gap: 4, marginBottom: 10, alignItems: "center" }}>
                                {phaseOrder.map((ph) => {
                                  const phIdx = phaseOrder.indexOf(ph);
                                  const curIdx = phaseOrder.indexOf(t.currentPhase);
                                  const done = curIdx > phIdx;
                                  const active = ph === t.currentPhase;
                                  const color = phaseColors[ph] ?? "#94a3b8";
                                  return (
                                    <div key={ph} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                                      <span style={{
                                        padding: "1px 7px", borderRadius: 3, fontSize: 9, fontFamily: "monospace", fontWeight: 700,
                                        background: active ? `${color}20` : done ? `${color}0d` : "rgba(51,65,85,0.15)",
                                        border: `1px solid ${active ? color : done ? `${color}50` : "rgba(51,65,85,0.3)"}`,
                                        color: active ? color : done ? `${color}99` : "#475569",
                                        boxShadow: active ? `0 0 6px ${color}40` : "none",
                                      }}>{ph}</span>
                                      {phIdx < phaseOrder.length - 1 && (
                                        <span style={{ color: done ? "#334155" : "#1e293b", fontSize: 9 }}>→</span>
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                              {/* Detail fields */}
                              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 24px" }}>
                                <div>
                                  <span style={{ color: "#475569", fontSize: 10 }}>Task ID</span>
                                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
                                    <span style={{ color: "#94a3b8", fontFamily: "monospace", fontSize: 11 }}>{t.taskId}</span>
                                    <button
                                      type="button"
                                      onClick={() => { void navigator.clipboard.writeText(t.taskId); toast.success("已复制"); }}
                                      style={{ padding: "1px 6px", borderRadius: 3, border: "1px solid rgba(51,65,85,0.4)", background: "transparent", color: "#475569", fontSize: 10, cursor: "pointer" }}
                                    >复制</button>
                                  </div>
                                </div>
                                <div>
                                  <span style={{ color: "#475569", fontSize: 10 }}>描述</span>
                                  <div style={{ color: "#64748b", fontSize: 11, marginTop: 2, maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }} title={t.description || "—"}>{t.description || "—"}</div>
                                </div>
                                <div>
                                  <span style={{ color: "#475569", fontSize: 10 }}>创建时间</span>
                                  <div style={{ color: "#64748b", fontFamily: "monospace", fontSize: 11, marginTop: 2 }}>{t.createdAt ? new Date(t.createdAt).toLocaleString("zh-CN") : "—"}</div>
                                </div>
                                <div>
                                  <span style={{ color: "#475569", fontSize: 10 }}>最后更新</span>
                                  <div style={{ color: "#64748b", fontFamily: "monospace", fontSize: 11, marginTop: 2 }}>{t.updatedAt ? new Date(t.updatedAt).toLocaleString("zh-CN") : "—"}</div>
                                </div>
                              </div>
                              {/* Execution records */}
                              {(() => {
                                const execs = taskExecutions[t.taskId];
                                if (!execs) return (
                                  <div style={{ marginTop: 10, color: "#475569", fontFamily: "monospace", fontSize: 10 }}>加载执行记录…</div>
                                );
                                if (execs.length === 0) return (
                                  <div style={{ marginTop: 10, color: "#334155", fontFamily: "monospace", fontSize: 10 }}>暂无执行记录</div>
                                );
                                return (
                                  <div style={{ marginTop: 10 }}>
                                    <div style={{ color: "#475569", fontSize: 10, marginBottom: 5, letterSpacing: "0.06em", textTransform: "uppercase" as const }}>近期执行记录</div>
                                    <div style={{ display: "flex", flexDirection: "column" as const, gap: 3 }}>
                                      {execs.slice(0, 6).map((ex, idx) => {
                                        const exStatus = ex.status ?? "UNKNOWN";
                                        const exColor = exStatus === "DONE" || exStatus === "SUCCESS" ? "#34d399" : exStatus === "FAILED" || exStatus === "ERROR" ? "#f87171" : "#fbbf24";
                                        return (
                                          <div key={ex.request_id ?? idx} style={{ display: "flex", gap: 8, alignItems: "center", fontFamily: "monospace", fontSize: 10 }}>
                                            <span style={{ color: exColor, minWidth: 12 }}>●</span>
                                            <span style={{ color: "#64748b", minWidth: 90 }}>{ex.skill_id ?? ex.phase ?? "—"}</span>
                                            <span style={{ color: exColor, minWidth: 50 }}>{exStatus}</span>
                                            {ex.duration_ms !== undefined && (
                                              <span style={{ color: "#475569" }}>{ex.duration_ms}ms</span>
                                            )}
                                            {ex.created_at && (
                                              <span style={{ color: "#334155" }}>{new Date(ex.created_at).toLocaleTimeString("zh-CN")}</span>
                                            )}
                                          </div>
                                        );
                                      })}
                                    </div>
                                  </div>
                                );
                              })()}
                            </td>
                          </tr>
                        )}
                      </>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Recent completed/failed tasks */}
        {recentTasks.length > 0 && (
          <div style={{
            marginBottom: 16, borderRadius: 10,
            background: "rgba(2,6,23,0.75)", border: "1px solid rgba(51,65,85,0.4)",
            overflow: "hidden",
          }}>
            <div style={{
              padding: "10px 16px", borderBottom: "1px solid rgba(51,65,85,0.3)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <span style={{ color: "#94a3b8", fontWeight: 800, fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase" as const }}>
                近期完成 / 失败 ({recentTasks.length})
              </span>
              <button
                type="button"
                disabled={cleaningUp}
                onClick={() => { void handleCleanup(); }}
                style={{
                  padding: "3px 12px", borderRadius: 5, fontSize: 11, fontWeight: 700,
                  border: "1px solid rgba(248,113,113,0.4)",
                  background: "rgba(248,113,113,0.07)",
                  color: cleaningUp ? "#475569" : "#fca5a5",
                  cursor: cleaningUp ? "wait" : "pointer",
                }}
              >{cleaningUp ? "清理中…" : "清理记录"}</button>
            </div>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid rgba(51,65,85,0.3)" }}>
                    {["任务名称", "目标", "最终阶段", "状态", "操作"].map((h) => (
                      <th key={h} style={{ padding: "5px 14px", textAlign: "left" as const, color: "#475569", fontWeight: 600, fontSize: 10, letterSpacing: "0.06em", whiteSpace: "nowrap" as const }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {recentTasks.map((t) => {
                    const isDone = t.status === "DONE";
                    const isCancelled = t.status === "CANCELLED";
                    const statusColor = isDone ? "#34d399" : isCancelled ? "#64748b" : "#f87171";
                    const phaseColors: Record<string, string> = {
                      RECON: "#64748b", THREAT_MODEL: "#a78bfa", VULN_SCAN: "#fb923c",
                      EXPLOIT: "#f87171", REPORT: "#34d399", DONE: "#22d3ee",
                    };
                    const phaseColor = phaseColors[t.currentPhase] ?? "#94a3b8";
                    const isExpanded = expandedTaskId === t.taskId;
                    return (
                      <>
                        <tr
                          key={t.taskId}
                          onClick={() => setExpandedTaskId(isExpanded ? null : t.taskId)}
                          style={{ borderBottom: isExpanded ? "none" : "1px solid rgba(51,65,85,0.15)", cursor: "pointer", background: isExpanded ? "rgba(52,211,153,0.02)" : "transparent" }}
                        >
                          <td style={{ padding: "7px 14px", color: "#94a3b8", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }} title={t.name}>
                            <span style={{ marginRight: 5, color: isExpanded ? "#34d399" : "#475569", fontSize: 9 }}>{isExpanded ? "▲" : "▼"}</span>
                            {t.name}
                          </td>
                          <td style={{ padding: "7px 14px", color: "#475569", fontFamily: "monospace", fontSize: 11, maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }} title={t.target}>{t.target}</td>
                          <td style={{ padding: "7px 14px" }}>
                            <span style={{ color: phaseColor, fontFamily: "monospace", fontSize: 11 }}>{t.currentPhase || "—"}</span>
                          </td>
                          <td style={{ padding: "7px 14px" }}>
                            <span style={{
                              display: "inline-flex", alignItems: "center", gap: 4,
                              padding: "2px 8px", borderRadius: 4,
                              border: `1px solid ${statusColor}30`,
                              background: `${statusColor}0a`,
                              color: statusColor, fontSize: 10, fontWeight: 700, fontFamily: "monospace",
                            }}>
                              {isDone ? "✓ 完成" : isCancelled ? "○ 已取消" : "✗ 失败"}
                            </span>
                          </td>
                          <td style={{ padding: "7px 14px" }} onClick={(e) => e.stopPropagation()}>
                            <div style={{ display: "flex", gap: 6 }}>
                              <button
                                type="button"
                                onClick={() => navigate(`/tasks?taskId=${t.taskId}`)}
                                style={{
                                  padding: "3px 10px", borderRadius: 4,
                                  border: "1px solid rgba(34,211,238,0.3)",
                                  background: "rgba(34,211,238,0.05)",
                                  color: "#67e8f9", fontSize: 11, cursor: "pointer", fontWeight: 600,
                                }}
                              >详情</button>
                              <button
                                type="button"
                                onClick={() => navigate(`/logs?taskId=${t.taskId}`)}
                                style={{
                                  padding: "3px 10px", borderRadius: 4,
                                  border: "1px solid rgba(167,139,250,0.35)",
                                  background: "rgba(167,139,250,0.06)",
                                  color: "#a78bfa", fontSize: 11, cursor: "pointer", fontWeight: 600,
                                }}
                              >日志</button>
                              {isDone && (
                                <button
                                  type="button"
                                  onClick={() => navigate("/reports")}
                                  style={{
                                    padding: "3px 10px", borderRadius: 4,
                                    border: "1px solid rgba(52,211,153,0.35)",
                                    background: "rgba(52,211,153,0.06)",
                                    color: "#34d399", fontSize: 11, cursor: "pointer", fontWeight: 600,
                                  }}
                                >报告</button>
                              )}
                              <button
                                type="button"
                                onClick={() => {
                                  deleteTask(t.taskId)
                                    .then(() => { toast.success(`已删除: ${t.name}`); void refresh(); })
                                    .catch((e: Error) => toast.error(`删除失败: ${e.message}`));
                                }}
                                style={{
                                  padding: "3px 10px", borderRadius: 4,
                                  border: "1px solid rgba(248,113,113,0.3)",
                                  background: "rgba(248,113,113,0.05)",
                                  color: "#fca5a5", fontSize: 11, cursor: "pointer", fontWeight: 600,
                                }}
                              >删除</button>
                            </div>
                          </td>
                        </tr>
                        {isExpanded && (
                          <tr key={`${t.taskId}-detail`} style={{ borderBottom: "1px solid rgba(51,65,85,0.15)" }}>
                            <td colSpan={5} style={{ padding: "10px 20px 14px", background: "rgba(52,211,153,0.02)" }}>
                              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 24px" }}>
                                <div>
                                  <span style={{ color: "#475569", fontSize: 10 }}>Task ID</span>
                                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
                                    <span style={{ color: "#64748b", fontFamily: "monospace", fontSize: 11 }}>{t.taskId}</span>
                                    <button
                                      type="button"
                                      onClick={() => { void navigator.clipboard.writeText(t.taskId); toast.success("已复制"); }}
                                      style={{ padding: "1px 6px", borderRadius: 3, border: "1px solid rgba(51,65,85,0.4)", background: "transparent", color: "#475569", fontSize: 10, cursor: "pointer" }}
                                    >复制</button>
                                  </div>
                                </div>
                                <div>
                                  <span style={{ color: "#475569", fontSize: 10 }}>描述</span>
                                  <div style={{ color: "#64748b", fontSize: 11, marginTop: 2, maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }} title={t.description || "—"}>{t.description || "—"}</div>
                                </div>
                                <div>
                                  <span style={{ color: "#475569", fontSize: 10 }}>创建时间</span>
                                  <div style={{ color: "#64748b", fontFamily: "monospace", fontSize: 11, marginTop: 2 }}>{t.createdAt ? new Date(t.createdAt).toLocaleString("zh-CN") : "—"}</div>
                                </div>
                                <div>
                                  <span style={{ color: "#475569", fontSize: 10 }}>完成时间</span>
                                  <div style={{ color: "#64748b", fontFamily: "monospace", fontSize: 11, marginTop: 2 }}>{t.updatedAt ? new Date(t.updatedAt).toLocaleString("zh-CN") : "—"}</div>
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        </>)} {/* end monitor tab */}

        {/* ── TASKS TAB ───────────────────────────────────────────────────────── */}
        {activeTab === 'tasks' && (<>

        {/* Quick-create task */}
        <div style={{
          marginBottom: 16, padding: "16px 20px", borderRadius: 10,
          background: "rgba(2,6,23,0.75)", border: "1px solid rgba(34,211,238,0.2)",
        }}>
          <div style={{ color: "#22d3ee", fontWeight: 800, fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase" as const, marginBottom: 12 }}>
            快速创建任务
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" as const, alignItems: "flex-end" }}>
            <div style={{ display: "flex", flexDirection: "column" as const, gap: 4, flex: "1 1 180px", minWidth: 150 }}>
              <label style={{ color: "#64748b", fontSize: 10, fontFamily: "monospace" }}>任务名称 *</label>
              <input
                type="text"
                value={qcName}
                onChange={(e) => setQcName(e.target.value)}
                placeholder="e.g. Web 常规渗透"
                style={{
                  background: "rgba(15,23,42,0.8)", border: "1px solid rgba(34,211,238,0.25)",
                  borderRadius: 6, padding: "6px 10px", color: "#e2e8f0",
                  fontFamily: "monospace", fontSize: 12, outline: "none",
                }}
                onKeyDown={(e) => { if (e.key === 'Enter') void handleQuickCreate(); }}
              />
            </div>
            <div style={{ display: "flex", flexDirection: "column" as const, gap: 4, flex: "2 1 240px", minWidth: 180 }}>
              <label style={{ color: "#64748b", fontSize: 10, fontFamily: "monospace" }}>目标 URL *</label>
              <input
                type="text"
                value={qcTarget}
                onChange={(e) => setQcTarget(e.target.value)}
                placeholder="http://192.168.1.100"
                style={{
                  background: "rgba(15,23,42,0.8)", border: "1px solid rgba(34,211,238,0.25)",
                  borderRadius: 6, padding: "6px 10px", color: "#e2e8f0",
                  fontFamily: "monospace", fontSize: 12, outline: "none",
                }}
                onKeyDown={(e) => { if (e.key === 'Enter') void handleQuickCreate(); }}
              />
            </div>
            <div style={{ display: "flex", flexDirection: "column" as const, gap: 4, flex: "2 1 220px", minWidth: 160 }}>
              <label style={{ color: "#64748b", fontSize: 10, fontFamily: "monospace" }}>描述（可选）</label>
              <input
                type="text"
                value={qcDesc}
                onChange={(e) => setQcDesc(e.target.value)}
                placeholder="任务描述…"
                style={{
                  background: "rgba(15,23,42,0.8)", border: "1px solid rgba(51,65,85,0.4)",
                  borderRadius: 6, padding: "6px 10px", color: "#94a3b8",
                  fontFamily: "monospace", fontSize: 12, outline: "none",
                }}
                onKeyDown={(e) => { if (e.key === 'Enter') void handleQuickCreate(); }}
              />
            </div>
          </div>
          {/* Quick presets */}
          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 10, color: "#334155", marginBottom: 5, fontFamily: "monospace" }}>快速预设靶场</div>
            <div style={{ display: "flex", gap: 5, flexWrap: "wrap" as const }}>
              {([
                { label: "Struts2 S2-045", name: "Struts2 S2-045 RCE 渗透测试", desc: "测试 Apache Struts2 S2-045 RCE（CVE-2017-5638），验证 OGNL 注入利用链。", url: "http://host.docker.internal:8080/" },
                { label: "ThinkPHP RCE", name: "ThinkPHP 5.0.23 RCE", desc: "对 ThinkPHP 5.0.23 框架进行远程代码执行漏洞自动化渗透测试。", url: "http://host.docker.internal:8080/" },
                { label: "Shiro 反序列化", name: "Shiro CVE-2016-4437", desc: "测试 Apache Shiro RememberMe 反序列化 RCE 漏洞（CVE-2016-4437）。", url: "http://host.docker.internal:8080/" },
                { label: "Flask SSTI", name: "Flask SSTI 模板注入", desc: "Flask Jinja2 服务端模板注入漏洞自动化验证。", url: "http://host.docker.internal:8080/" },
                { label: "FastJSON 1.2.24", name: "FastJSON 1.2.24 反序列化", desc: "FastJSON 1.2.24 反序列化 RCE 漏洞，JNDI 注入利用。", url: "http://host.docker.internal:8080/" },
                { label: "WebLogic RCE", name: "WebLogic CVE-2023-21839", desc: "Oracle WebLogic Server CVE-2023-21839 T3/IIOP RCE。", url: "http://host.docker.internal:8080/" },
              ] as { label: string; name: string; desc: string; url: string }[]).map((p) => (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => { setQcName(p.name); setQcTarget(p.url); setQcDesc(p.desc); }}
                  style={{
                    padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 600,
                    border: "1px solid rgba(71,85,105,0.45)", background: "rgba(2,6,23,0.5)",
                    color: "#475569", cursor: "pointer", fontFamily: "monospace",
                    transition: "color 0.12s, border-color 0.12s",
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = "#a5f3fc"; e.currentTarget.style.borderColor = "rgba(34,211,238,0.5)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = "#475569"; e.currentTarget.style.borderColor = "rgba(71,85,105,0.45)"; }}
                >{p.label}</button>
              ))}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexShrink: 0, marginTop: 10 }}>
              <button
                type="button"
                disabled={qcSubmitting || !qcName.trim() || !qcTarget.trim()}
                onClick={() => { void handleQuickCreate(false); }}
                style={{
                  padding: "7px 16px", borderRadius: 6, fontSize: 12, fontWeight: 800,
                  border: "1px solid rgba(34,211,238,0.4)",
                  background: qcSubmitting ? "rgba(34,211,238,0.03)" : "rgba(34,211,238,0.08)",
                  color: qcSubmitting || !qcName.trim() || !qcTarget.trim() ? "#475569" : "#22d3ee",
                  cursor: qcSubmitting || !qcName.trim() || !qcTarget.trim() ? "default" : "pointer",
                  fontFamily: "monospace", letterSpacing: "0.04em", whiteSpace: "nowrap" as const,
                }}
              >
                {qcSubmitting ? "提交中…" : "+ 创建"}
              </button>
              <button
                type="button"
                disabled={qcSubmitting || !qcName.trim() || !qcTarget.trim()}
                onClick={() => { void handleQuickCreate(true); }}
                style={{
                  padding: "7px 16px", borderRadius: 6, fontSize: 12, fontWeight: 800,
                  border: "1px solid rgba(52,211,153,0.5)",
                  background: qcSubmitting ? "rgba(52,211,153,0.03)" : "rgba(52,211,153,0.12)",
                  color: qcSubmitting || !qcName.trim() || !qcTarget.trim() ? "#475569" : "#34d399",
                  cursor: qcSubmitting || !qcName.trim() || !qcTarget.trim() ? "default" : "pointer",
                  fontFamily: "monospace", letterSpacing: "0.04em", whiteSpace: "nowrap" as const,
                }}
              >
                ▶ 创建并运行
              </button>
            </div>
          </div>

        {/* Pending tasks */}
        {qcPendingTasks.length > 0 && (
          <div style={{
            marginBottom: 16, borderRadius: 10,
            background: "rgba(2,6,23,0.75)", border: "1px solid rgba(251,191,36,0.2)",
            overflow: "hidden",
          }}>
            <div style={{
              padding: "10px 16px", borderBottom: "1px solid rgba(251,191,36,0.12)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <span style={{ color: "#fbbf24", fontWeight: 800, fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase" as const }}>
                待启动任务 ({qcPendingTasks.length})
              </span>
              <span style={{ color: "#475569", fontFamily: "monospace", fontSize: 10 }}>已创建但尚未运行</span>
            </div>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid rgba(51,65,85,0.4)" }}>
                    {["任务名称", "目标", "操作"].map((h) => (
                      <th key={h} style={{ padding: "6px 14px", textAlign: "left" as const, color: "#475569", fontWeight: 600, fontSize: 10, letterSpacing: "0.06em", whiteSpace: "nowrap" as const }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {qcPendingTasks.map((t) => (
                    <tr key={t.taskId} style={{ borderBottom: "1px solid rgba(51,65,85,0.15)" }}>
                      <td style={{ padding: "8px 14px", color: "#e2e8f0", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }} title={t.name}>{t.name}</td>
                      <td style={{ padding: "8px 14px", color: "#64748b", fontFamily: "monospace", fontSize: 11, maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }} title={t.target}>{t.target}</td>
                      <td style={{ padding: "8px 14px" }}>
                        <div style={{ display: "flex", gap: 6 }}>
                          <button
                            type="button"
                            onClick={() => {
                              runTask(t.taskId)
                                .then(() => { toast.success(`已启动: ${t.name}`); void refresh(); })
                                .catch((e: Error) => toast.error(`启动失败: ${e.message}`));
                            }}
                            style={{
                              padding: "3px 10px", borderRadius: 4,
                              border: "1px solid rgba(52,211,153,0.45)",
                              background: "rgba(52,211,153,0.09)",
                              color: "#34d399", fontSize: 11, cursor: "pointer", fontWeight: 600,
                            }}
                          >▶ 启动</button>
                          <button
                            type="button"
                            onClick={() => navigate(`/tasks?taskId=${t.taskId}`)}
                            style={{
                              padding: "3px 10px", borderRadius: 4,
                              border: "1px solid rgba(34,211,238,0.3)",
                              background: "rgba(34,211,238,0.05)",
                              color: "#67e8f9", fontSize: 11, cursor: "pointer", fontWeight: 600,
                            }}
                          >详情</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Bulk actions + Quick nav */}
        <div style={{
          marginBottom: 16, padding: "14px 18px", borderRadius: 8,
          background: "rgba(2,6,23,0.6)", border: "1px solid rgba(51,65,85,0.5)",
          display: "flex", gap: 10, flexWrap: "wrap" as const, alignItems: "center",
        }}>
          <span style={{ color: "#475569", fontFamily: "monospace", fontSize: 11 }}>批量操作：</span>
          <button
            type="button"
            disabled={bulkStopping || activeTasks.filter((t) => t.status === "RUNNING").length === 0}
            onClick={() => { void handleBulkStop(); }}
            style={{
              padding: "6px 14px", borderRadius: 6, fontSize: 12, fontWeight: 700,
              border: "1px solid rgba(251,191,36,0.4)",
              background: "rgba(251,191,36,0.07)",
              color: bulkStopping || activeTasks.filter((t) => t.status === "RUNNING").length === 0 ? "#475569" : "#fbbf24",
              cursor: bulkStopping || activeTasks.filter((t) => t.status === "RUNNING").length === 0 ? "default" : "pointer",
            }}
          >{bulkStopping ? "停止中…" : `⏹ 全部停止${activeTasks.filter((t) => t.status === "RUNNING").length > 0 ? ` (${activeTasks.filter((t) => t.status === "RUNNING").length})` : ""}`}</button>
          <button
            type="button"
            disabled={cleaningUp || recentTasks.length === 0}
            onClick={() => { void handleCleanup(); }}
            style={{
              padding: "6px 14px", borderRadius: 6, fontSize: 12, fontWeight: 700,
              border: "1px solid rgba(248,113,113,0.35)",
              background: "rgba(248,113,113,0.06)",
              color: cleaningUp || recentTasks.length === 0 ? "#475569" : "#fca5a5",
              cursor: cleaningUp || recentTasks.length === 0 ? "default" : "pointer",
            }}
          >{cleaningUp ? "清理中…" : `🗑 清理完成记录${recentTasks.length > 0 ? ` (${recentTasks.length})` : ""}`}</button>
        </div>

        {/* Quick nav */}
        <div style={{
          marginTop: 0, padding: "14px 18px", borderRadius: 8,
          background: "rgba(2,6,23,0.6)", border: "1px solid rgba(51,65,85,0.5)",
          display: "flex", gap: 12, flexWrap: "wrap" as const, alignItems: "center",
        }}>
          <span style={{ color: "#475569", fontFamily: "monospace", fontSize: 11 }}>快速导航：</span>
          {[
            { label: "管理中心", path: "/dashboard", color: "#22d3ee" },
            { label: "审计日志", path: "/audit",    color: "#818cf8" },
            { label: "监控大屏", path: "/monitor",  color: "#34d399" },
            { label: "任务管理", path: "/tasks",    color: "#38bdf8" },
            { label: "统计分析", path: "/stats",    color: "#f87171" },
            { label: "漏洞库",   path: "/vulns",    color: "#ef4444" },
            { label: "批量调度", path: "/batch",    color: "#818cf8" },
            { label: "配置中心", path: "/config",   color: "#a78bfa" },
            { label: "技能库",   path: "/skills",   color: "#fbbf24" },
            { label: "报告中心", path: "/reports",  color: "#fb923c" },
            { label: "运行日志", path: "/logs",     color: "#64748b" },
          ].map((item) => (
            <button
              key={item.path}
              type="button"
              onClick={() => navigate(item.path)}
              style={{
                padding: "6px 14px", borderRadius: 6,
                border: `1px solid ${item.color}40`,
                background: `${item.color}0d`,
                color: item.color, fontFamily: "monospace", fontSize: 12,
                cursor: "pointer", fontWeight: 600,
              }}
            >
              {item.label}
            </button>
          ))}
        </div>

        </>)} {/* end tasks tab */}

        {/* ── SKILLS TAB ──────────────────────────────────────────────────────── */}
        {activeTab === 'skills' && (<>

        {/* Skill Registry */}
        <div style={{
          marginTop: 16, borderRadius: 10,
          background: "rgba(2,6,23,0.75)", border: "1px solid rgba(52,211,153,0.2)",
          overflow: "hidden",
        }}>
          <div style={{
            padding: "10px 16px", borderBottom: "1px solid rgba(52,211,153,0.12)",
            display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" as const,
          }}>
            <span style={{ color: "#34d399", fontWeight: 800, fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase" as const }}>
              技能注册表{skillRegistry ? ` (${skillRegistry.skill_ids.length} 个技能)` : ""}
            </span>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" as const }}>
              {["", "RECON", "VULN_SCAN", "EXPLOIT", "POST_EXPLOIT"].map((ph) => (
                <button
                  key={ph}
                  type="button"
                  onClick={() => {
                    setSkillPhaseFilter(ph);
                    void fetchSkillRegistry(ph || undefined);
                  }}
                  style={{
                    padding: "3px 10px", borderRadius: 4, fontSize: 10, fontWeight: 700,
                    border: skillPhaseFilter === ph && skillRegistry
                      ? "1px solid rgba(52,211,153,0.7)"
                      : "1px solid rgba(52,211,153,0.25)",
                    background: skillPhaseFilter === ph && skillRegistry
                      ? "rgba(52,211,153,0.15)" : "rgba(52,211,153,0.04)",
                    color: "#34d399", cursor: "pointer", fontFamily: "monospace",
                  }}
                >
                  {ph || "全部"}
                </button>
              ))}
              {!skillRegistry && (
                <button
                  type="button"
                  disabled={skillRegistryLoading}
                  onClick={() => { setSkillPhaseFilter(''); void fetchSkillRegistry(); }}
                  style={{
                    padding: "4px 14px", borderRadius: 5, fontSize: 11, fontWeight: 700,
                    border: "1px solid rgba(52,211,153,0.5)",
                    background: "rgba(52,211,153,0.1)",
                    color: skillRegistryLoading ? "#475569" : "#34d399",
                    cursor: skillRegistryLoading ? "wait" : "pointer",
                    fontFamily: "monospace",
                  }}
                >{skillRegistryLoading ? "加载中…" : "查询技能列表"}</button>
              )}
            </div>
          </div>
          {skillRegistry && (
            <div style={{ padding: "12px 16px" }}>
              {skillRegistry.error && (
                <div style={{ color: "#f87171", fontFamily: "monospace", fontSize: 11, marginBottom: 8 }}>
                  执行器错误: {skillRegistry.error}
                </div>
              )}
              {skillRegistry.skills.length > 0 ? (
                <>
                  {/* Phase breakdown summary */}
                  {(() => {
                    const byPhase: Record<string, number> = {};
                    for (const s of skillRegistry.skills) {
                      const ph = (s.category || "").toUpperCase().split("/")[0] || "OTHER";
                      byPhase[ph] = (byPhase[ph] || 0) + 1;
                    }
                    return (
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" as const, marginBottom: 12 }}>
                        {Object.entries(byPhase).map(([ph, cnt]) => {
                          const colors: Record<string, string> = {
                            RECON: "#64748b", THREAT_MODEL: "#a78bfa", VULN_SCAN: "#fb923c",
                            EXPLOIT: "#f87171", POST_EXPLOIT: "#e879f9", REPORT: "#34d399", VERIFY: "#38bdf8", OTHER: "#475569",
                          };
                          const c = colors[ph] ?? "#94a3b8";
                          return (
                            <span key={ph} style={{
                              padding: "2px 10px", borderRadius: 12, fontSize: 10, fontWeight: 700,
                              border: `1px solid ${c}40`, background: `${c}12`,
                              color: c, fontFamily: "monospace",
                            }}>{ph}: {cnt}</span>
                          );
                        })}
                      </div>
                    );
                  })()}
                  {/* Skill grid */}
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 6 }}>
                    {skillRegistry.skills.map((s) => {
                      const ph = (s.category || "").toUpperCase().split("/")[0] || "OTHER";
                      const colors: Record<string, string> = {
                        RECON: "#64748b", THREAT_MODEL: "#a78bfa", VULN_SCAN: "#fb923c",
                        EXPLOIT: "#f87171", POST_EXPLOIT: "#e879f9", REPORT: "#34d399", VERIFY: "#38bdf8", OTHER: "#475569",
                      };
                      const c = colors[ph] ?? "#94a3b8";
                      return (
                        <div key={s.skill_id} style={{
                          padding: "6px 10px", borderRadius: 6,
                          border: `1px solid ${c}25`,
                          background: `${c}08`,
                        }}>
                          <div style={{ color: "#e2e8f0", fontFamily: "monospace", fontSize: 11, fontWeight: 700, whiteSpace: "nowrap" as const, overflow: "hidden", textOverflow: "ellipsis" }}>{s.skill_id}</div>
                          <div style={{ color: c, fontSize: 9, fontFamily: "monospace", marginTop: 2, letterSpacing: "0.04em" }}>{s.category || "—"}</div>
                        </div>
                      );
                    })}
                  </div>
                </>
              ) : (
                <div style={{ color: "#475569", fontFamily: "monospace", fontSize: 11 }}>
                  {skillRegistryLoading ? "加载中…" : "暂无技能数据（执行器可能未连接）"}
                </div>
              )}
            </div>
          )}
        </div>

        </>)} {/* end skills tab */}

        {/* ── CONFIG TAB ──────────────────────────────────────────────────────── */}
        {activeTab === 'config' && (<>

        {/* Global Activity Feed */}
        {globalEvents.length > 0 && (
          <div style={{
            marginTop: 16, borderRadius: 10,
            background: "rgba(2,6,23,0.75)", border: "1px solid rgba(51,65,85,0.4)",
            overflow: "hidden",
          }}>
            <div style={{
              padding: "10px 16px", borderBottom: "1px solid rgba(51,65,85,0.3)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <span style={{ color: "#94a3b8", fontWeight: 800, fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase" as const }}>
                全局活动日志 (最近 {globalEvents.length} 条)
              </span>
              <span style={{ color: "#334155", fontFamily: "monospace", fontSize: 10 }}>跨任务实时事件流</span>
            </div>
            <div style={{ maxHeight: 220, overflowY: "auto", padding: "6px 0" }}>
              {globalEvents.map((ev, idx) => {
                const ts = ev.ts ? (() => {
                  try { return new Date(ev.ts).toLocaleTimeString("zh-CN"); }
                  catch { return ev.ts.slice(0, 19); }
                })() : "—";
                const evtColor: Record<string, string> = {
                  SKILL_COMPLETED: "#34d399", SKILL_INVOKED: "#22d3ee",
                  PHASE_START: "#a78bfa", PHASE_COMPLETE: "#34d399",
                  TASK_STARTED: "#22d3ee", TASK_COMPLETED: "#34d399",
                  TASK_FAILED: "#f87171", TASK_PAUSED: "#fbbf24",
                  TASK_RESUMED: "#34d399", ERROR: "#f87171",
                };
                const color = evtColor[ev.eventType ?? ""] ?? "#64748b";
                const taskShort = ev.taskId ? ev.taskId.slice(-8) : "—";
                return (
                  <div key={idx} style={{
                    display: "flex", gap: 8, alignItems: "center",
                    padding: "3px 16px", fontSize: 11, fontFamily: "monospace",
                    borderBottom: idx < globalEvents.length - 1 ? "1px solid rgba(51,65,85,0.12)" : "none",
                  }}>
                    <span style={{ color: "#334155", minWidth: 70, flexShrink: 0 }}>{ts}</span>
                    <span style={{ color, fontWeight: 700, minWidth: 160, flexShrink: 0 }}>{ev.eventType ?? "EVENT"}</span>
                    <span style={{ color: "#475569", minWidth: 90, flexShrink: 0, fontSize: 10 }}>…{taskShort}</span>
                    {ev.sourceModule && (
                      <span style={{ color: "#334155", fontSize: 10 }}>{ev.sourceModule}</span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Scheduling Observe — on-demand */}
        <div style={{
          marginTop: 16, padding: "14px 18px", borderRadius: 8,
          background: "rgba(2,6,23,0.6)", border: "1px solid rgba(51,65,85,0.5)",
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: schedObserve ? 12 : 8, flexWrap: "wrap" as const, gap: 8 }}>
            <span style={{ color: "#94a3b8", fontFamily: "monospace", fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase" as const }}>
              调度诊断
            </span>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" as const }}>
              <input
                type="text"
                value={schedTaskId}
                onChange={(e) => setSchedTaskId(e.target.value)}
                placeholder="task_id（可选，精准诊断）"
                style={{
                  background: "rgba(15,23,42,0.8)", border: "1px solid rgba(99,102,241,0.3)",
                  borderRadius: 5, padding: "4px 10px", color: "#94a3b8",
                  fontFamily: "monospace", fontSize: 11, outline: "none", width: 200,
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    void (async () => {
                      setSchedObserving(true);
                      try {
                        const obs = await getV1SchedulingObserve(undefined, schedTaskId.trim() || undefined);
                        setSchedObserve(obs);
                      } catch (err: unknown) {
                        toast.error(`调度诊断失败: ${(err as Error).message}`);
                      } finally {
                        setSchedObserving(false);
                      }
                    })();
                  }
                }}
              />
              <button
                type="button"
                disabled={schedObserving}
                onClick={async () => {
                  setSchedObserving(true);
                  try {
                    const obs = await getV1SchedulingObserve(undefined, schedTaskId.trim() || undefined);
                    setSchedObserve(obs);
                  } catch (e: unknown) {
                    toast.error(`调度诊断失败: ${(e as Error).message}`);
                  } finally {
                    setSchedObserving(false);
                  }
                }}
                style={{
                  padding: "4px 12px", borderRadius: 5, fontSize: 11, fontWeight: 700,
                  border: "1px solid rgba(99,102,241,0.4)",
                  background: "rgba(99,102,241,0.07)",
                  color: schedObserving ? "#475569" : "#a5b4fc",
                  cursor: schedObserving ? "wait" : "pointer",
                }}
              >{schedObserving ? "查询中…" : "查询调度状态"}</button>
              {schedObserve && (
                <button
                  type="button"
                  onClick={() => setSchedObserve(null)}
                  style={{
                    padding: "4px 10px", borderRadius: 5, fontSize: 11,
                    border: "1px solid rgba(71,85,105,0.4)",
                    background: "transparent", color: "#475569", cursor: "pointer",
                  }}
                >关闭</button>
              )}
            </div>
          </div>
          {schedObserve && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: "4px 16px" }}>
              {Object.entries(schedObserve)
                .filter(([k]) => k !== 'agent_candidates')
                .flatMap(([k, v]) => {
                  if (v == null || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v as object).length === 0)) return [];
                  const display = typeof v === 'object' ? JSON.stringify(v).slice(0, 80) : String(v);
                  return [(
                    <div key={k} style={{ display: "flex", gap: 6, fontSize: 11, fontFamily: "monospace", padding: "2px 0" }}>
                      <span style={{ color: "#475569", flexShrink: 0 }}>{k}:</span>
                      <span style={{ color: "#64748b" }}>{display}</span>
                    </div>
                  )];
                })}
              {Array.isArray(schedObserve.agent_candidates) && schedObserve.agent_candidates.length > 0 && (
                <div style={{ gridColumn: "1 / -1" }}>
                  <div style={{ color: "#475569", fontSize: 10, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 4, marginTop: 4 }}>Agent 候选</div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {schedObserve.agent_candidates.slice(0, 8).map((a, i) => (
                      <span key={i} style={{
                        padding: "2px 8px", borderRadius: 4, fontSize: 11, fontFamily: "monospace",
                        background: "rgba(99,102,241,0.1)", border: "1px solid rgba(99,102,241,0.25)",
                        color: "#a5b4fc",
                      }}>
                        {String(a.agent_id ?? `agent-${i}`)}
                        {a.score != null && <span style={{ color: "#6366f1", marginLeft: 4 }}>({typeof a.score === 'number' ? a.score.toFixed(2) : String(a.score)})</span>}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        </>)} {/* end config tab */}

        {/* ── USERS TAB ───────────────────────────────────────────────────────── */}
        {activeTab === 'users' && (<>

        {/* ═══ User Management Panel ═══ */}
        <div style={{
          marginTop: 20, borderRadius: 10,
          background: "rgba(2,6,23,0.7)", border: "1px solid rgba(99,102,241,0.3)",
          overflow: "hidden",
        }}>
          {/* Panel header / toggle */}
          <button
            type="button"
            onClick={() => {
              setUsersPanelOpen((o) => !o);
              if (!usersPanelOpen && users.length === 0) void loadUsers();
            }}
            style={{
              width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "12px 18px", background: "none", border: "none", cursor: "pointer",
            }}
          >
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ color: "#a5b4fc", fontFamily: "monospace", fontSize: 11, fontWeight: 800, letterSpacing: "0.1em", textTransform: "uppercase" as const }}>
                用户管理 · 平台账号
              </span>
              {sessionRole && sessionRole !== 'ADMIN' && (
                <span style={{ color: "#fbbf24", fontSize: 9, fontFamily: "monospace", border: "1px solid rgba(251,191,36,0.4)", borderRadius: 3, padding: "1px 5px" }}>
                  只读 · 需 ADMIN 权限
                </span>
              )}
            </span>
            <span style={{ color: "#475569", fontSize: 12 }}>{usersPanelOpen ? "▲" : "▼"}</span>
          </button>

          {usersPanelOpen && (
            <div style={{ padding: "0 18px 18px" }}>
              {/* Add user form — admin only */}
              {(!sessionRole || sessionRole === 'ADMIN') && (
              <div style={{
                marginBottom: 14, padding: "10px 14px", borderRadius: 7,
                background: "rgba(99,102,241,0.05)", border: "1px solid rgba(99,102,241,0.2)",
              }}>
                <div style={{ fontSize: 10, color: "#6366f1", fontFamily: "monospace", marginBottom: 8, letterSpacing: "0.07em" }}>＋ 新增用户</div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" as const, alignItems: "flex-end" }}>
                  <div style={{ display: "flex", flexDirection: "column" as const, gap: 3 }}>
                    <span style={{ fontSize: 9, color: "#475569", fontFamily: "monospace" }}>用户名 *</span>
                    <input
                      type="text" value={newUsername}
                      onChange={(e) => setNewUsername(e.target.value)}
                      placeholder="username"
                      style={{
                        background: "rgba(15,23,42,0.8)", border: "1px solid rgba(99,102,241,0.35)",
                        borderRadius: 5, padding: "5px 9px", color: "#e2e8f0",
                        fontFamily: "monospace", fontSize: 12, outline: "none", width: 120,
                      }}
                    />
                  </div>
                  <div style={{ display: "flex", flexDirection: "column" as const, gap: 3 }}>
                    <span style={{ fontSize: 9, color: "#475569", fontFamily: "monospace" }}>显示名</span>
                    <input
                      type="text" value={newDisplayName}
                      onChange={(e) => setNewDisplayName(e.target.value)}
                      placeholder="显示名称"
                      style={{
                        background: "rgba(15,23,42,0.8)", border: "1px solid rgba(51,65,85,0.4)",
                        borderRadius: 5, padding: "5px 9px", color: "#e2e8f0",
                        fontFamily: "monospace", fontSize: 12, outline: "none", width: 110,
                      }}
                    />
                  </div>
                  <div style={{ display: "flex", flexDirection: "column" as const, gap: 3 }}>
                    <span style={{ fontSize: 9, color: "#475569", fontFamily: "monospace" }}>邮箱</span>
                    <input
                      type="email" value={newEmail}
                      onChange={(e) => setNewEmail(e.target.value)}
                      placeholder="email@example.com"
                      style={{
                        background: "rgba(15,23,42,0.8)", border: "1px solid rgba(51,65,85,0.4)",
                        borderRadius: 5, padding: "5px 9px", color: "#e2e8f0",
                        fontFamily: "monospace", fontSize: 12, outline: "none", width: 160,
                      }}
                    />
                  </div>
                  <div style={{ display: "flex", flexDirection: "column" as const, gap: 3 }}>
                    <span style={{ fontSize: 9, color: "#475569", fontFamily: "monospace" }}>角色</span>
                    <select
                      value={newRole}
                      onChange={(e) => setNewRole(e.target.value as 'ADMIN' | 'OPERATOR' | 'VIEWER')}
                      style={{
                        background: "rgba(15,23,42,0.8)", border: "1px solid rgba(99,102,241,0.35)",
                        borderRadius: 5, padding: "5px 9px", color: "#a5b4fc",
                        fontFamily: "monospace", fontSize: 12, outline: "none",
                      }}
                    >
                      <option value="VIEWER">VIEWER</option>
                      <option value="OPERATOR">OPERATOR</option>
                      <option value="ADMIN">ADMIN</option>
                    </select>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column" as const, gap: 3 }}>
                    <span style={{ fontSize: 9, color: "#475569", fontFamily: "monospace" }}>初始密码（可选）</span>
                    <input
                      type="password" value={newInitialPassword}
                      onChange={(e) => setNewInitialPassword(e.target.value)}
                      placeholder="≥6位"
                      style={{
                        background: "rgba(15,23,42,0.8)", border: "1px solid rgba(51,65,85,0.4)",
                        borderRadius: 5, padding: "5px 9px", color: "#e2e8f0",
                        fontFamily: "monospace", fontSize: 12, outline: "none", width: 100,
                      }}
                    />
                  </div>
                  <button
                    type="button"
                    disabled={userSubmitting || !newUsername.trim()}
                    onClick={() => { void handleCreateUser(); }}
                    style={{
                      padding: "6px 16px", borderRadius: 6, fontFamily: "monospace", fontSize: 12, fontWeight: 700,
                      border: "1px solid rgba(99,102,241,0.5)", background: "rgba(99,102,241,0.12)",
                      color: userSubmitting || !newUsername.trim() ? "#475569" : "#a5b4fc",
                      cursor: userSubmitting || !newUsername.trim() ? "default" : "pointer",
                    }}
                  >{userSubmitting ? "创建中…" : "创建"}</button>
                </div>
              </div>
              )}

              {/* User list */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <span style={{ color: "#64748b", fontFamily: "monospace", fontSize: 10 }}>
                  {usersLoading ? "加载中…" : `共 ${users.length} 个账号`}
                </span>
                <button
                  type="button"
                  onClick={() => { void loadUsers(); }}
                  disabled={usersLoading}
                  style={{
                    padding: "3px 10px", borderRadius: 4, fontSize: 10, fontFamily: "monospace",
                    border: "1px solid rgba(51,65,85,0.4)", background: "rgba(2,6,23,0.5)",
                    color: "#475569", cursor: usersLoading ? "default" : "pointer",
                  }}
                >刷新</button>
              </div>
              {users.length === 0 && !usersLoading ? (
                <div style={{ color: "#334155", fontFamily: "monospace", fontSize: 11, textAlign: "center" as const, padding: "16px 0" }}>
                  暂无用户数据
                </div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse" as const, fontSize: 11, fontFamily: "monospace" }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid rgba(51,65,85,0.4)" }}>
                      {["用户名", "显示名", "邮箱", "角色", "状态", "最近登录", "操作"].map((h) => (
                        <th key={h} style={{ padding: "5px 8px", textAlign: "left" as const, color: "#475569", fontWeight: 600, fontSize: 10 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((u) => {
                      const roleColor = u.role === 'ADMIN' ? "#f87171" : u.role === 'OPERATOR' ? "#fbbf24" : "#64748b";
                      const statusColor = u.status === 'ACTIVE' ? "#34d399" : "#f87171";
                      return (
                        <tr key={u.userId} style={{ borderBottom: "1px solid rgba(51,65,85,0.12)" }}>
                          <td style={{ padding: "6px 8px", color: "#e2e8f0" }}>{u.username}</td>
                          <td style={{ padding: "6px 8px", color: "#94a3b8" }}>{u.displayName ?? "-"}</td>
                          <td style={{ padding: "6px 8px", color: "#64748b" }}>{u.email ?? "-"}</td>
                          <td style={{ padding: "6px 8px" }}>
                            <span style={{ color: roleColor, fontWeight: 700 }}>{u.role}</span>
                          </td>
                          <td style={{ padding: "6px 8px" }}>
                            <span style={{ color: statusColor }}>{u.status}</span>
                          </td>
                          <td style={{ padding: "6px 8px", color: "#334155" }}>
                            {u.lastLoginAt ? new Date(u.lastLoginAt).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"}
                          </td>
                          <td style={{ padding: "6px 8px" }}>
                            {(!sessionRole || sessionRole === 'ADMIN') ? (
                            <span style={{ display: "inline-flex", gap: 5 }}>
                              <button
                                type="button"
                                onClick={() => { void handleToggleUserStatus(u); }}
                                style={{
                                  padding: "2px 7px", borderRadius: 3, fontSize: 10, cursor: "pointer", fontFamily: "monospace",
                                  border: u.status === 'ACTIVE' ? "1px solid rgba(251,191,36,0.5)" : "1px solid rgba(52,211,153,0.5)",
                                  background: "transparent",
                                  color: u.status === 'ACTIVE' ? "#fbbf24" : "#34d399",
                                }}
                              >{u.status === 'ACTIVE' ? "禁用" : "启用"}</button>
                              <button
                                type="button"
                                onClick={() => { setPwdTargetUser(u); setPwdNewValue(''); }}
                                style={{
                                  padding: "2px 7px", borderRadius: 3, fontSize: 10, cursor: "pointer", fontFamily: "monospace",
                                  border: "1px solid rgba(99,102,241,0.4)", background: "transparent", color: "#a5b4fc",
                                }}
                              >密码</button>
                              <button
                                type="button"
                                onClick={() => { void handleDeleteUser(u); }}
                                style={{
                                  padding: "2px 7px", borderRadius: 3, fontSize: 10, cursor: "pointer", fontFamily: "monospace",
                                  border: "1px solid rgba(248,113,113,0.4)", background: "transparent", color: "#f87171",
                                }}
                              >删除</button>
                            </span>
                            ) : (
                              <span style={{ color: "#334155", fontSize: 10, fontFamily: "monospace" }}>—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>

        </>)} {/* end users tab */}

        {/* ── AUDIT TAB ───────────────────────────────────────────────────────── */}
        {activeTab === 'audit' && (
          <div style={{ marginBottom: 20 }}>
            <div style={{
              marginBottom: 12, padding: "12px 18px", borderRadius: 8,
              background: "rgba(2,6,23,0.7)", border: "1px solid rgba(244,114,182,0.3)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <span style={{ color: "#f472b6", fontFamily: "monospace", fontSize: 11, fontWeight: 800, letterSpacing: "0.1em", textTransform: "uppercase" as const }}>
                平台审计日志 · 最近 {auditEvents.length} 条
              </span>
              <button
                type="button"
                onClick={() => { void loadAuditEvents(); }}
                disabled={auditLoading}
                style={{
                  padding: "3px 10px", borderRadius: 4, fontSize: 10, fontFamily: "monospace",
                  border: "1px solid rgba(244,114,182,0.4)", background: "rgba(244,114,182,0.08)",
                  color: "#f472b6", cursor: auditLoading ? "default" : "pointer",
                }}
              >{auditLoading ? "加载中…" : "↻ 刷新"}</button>
            </div>

            {auditEvents.length === 0 && !auditLoading ? (
              <div style={{ textAlign: "center", color: "#334155", fontFamily: "monospace", fontSize: 11, padding: "32px 0" }}>
                暂无审计记录 · 执行登录或用户操作后将在此显示
              </div>
            ) : (
              <div style={{
                borderRadius: 8, overflow: "hidden",
                border: "1px solid rgba(51,65,85,0.4)",
                background: "rgba(2,6,23,0.6)",
              }}>
                <table style={{ width: "100%", borderCollapse: "collapse" as const, fontSize: 11, fontFamily: "monospace" }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid rgba(51,65,85,0.5)" }}>
                      {["时间", "事件类型", "操作者", "目标", "详情"].map((h) => (
                        <th key={h} style={{ padding: "8px 10px", textAlign: "left" as const, color: "#475569", fontSize: 10, fontWeight: 600 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {auditEvents.map((ev, idx) => {
                      const typeColors: Record<string, string> = {
                        LOGIN_SUCCESS: "#34d399", LOGIN_FAILED: "#f87171", LOGOUT: "#94a3b8",
                        REGISTER: "#22d3ee", USER_CREATED: "#a5b4fc", USER_UPDATED: "#fbbf24",
                        USER_DELETED: "#f87171", PASSWORD_CHANGED: "#fb923c",
                        TASK_CREATED: "#34d399", TASK_DELETED: "#f87171",
                        TASK_STARTED: "#22d3ee", TASK_STOPPED: "#fbbf24",
                      };
                      const color = typeColors[ev.type] ?? "#94a3b8";
                      const ts = (() => { try { return new Date(ev.timestamp).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }); } catch { return ev.timestamp.slice(0, 19); } })();
                      return (
                        <tr key={idx} style={{ borderBottom: "1px solid rgba(51,65,85,0.15)" }}>
                          <td style={{ padding: "6px 10px", color: "#475569", whiteSpace: "nowrap" as const }}>{ts}</td>
                          <td style={{ padding: "6px 10px" }}>
                            <span style={{ color, fontWeight: 700, fontSize: 10, letterSpacing: "0.04em" }}>{ev.type}</span>
                          </td>
                          <td style={{ padding: "6px 10px", color: "#e2e8f0" }}>{ev.actor}</td>
                          <td style={{ padding: "6px 10px", color: "#64748b" }}>{ev.target || "—"}</td>
                          <td style={{ padding: "6px 10px", color: "#94a3b8", maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }}>{ev.detail || "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )} {/* end audit tab */}

        {/* Auto-refresh notice */}
        <div style={{ marginTop: 16, textAlign: "center" as const, color: "#334155", fontFamily: "monospace", fontSize: 10 }}>
          数据每 10 秒自动刷新 · 后端 API 代理至 :18080
        </div>
      </div>

      {/* ── Password-reset modal ── */}
      {pwdTargetUser && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 200,
          background: "rgba(2,6,23,0.75)", backdropFilter: "blur(4px)",
          display: "flex", alignItems: "center", justifyContent: "center",
        }}
          onClick={(e) => { if (e.target === e.currentTarget) { setPwdTargetUser(null); setPwdNewValue(''); } }}
        >
          <div style={{
            background: "rgba(15,23,42,0.96)", border: "1px solid rgba(99,102,241,0.4)",
            borderRadius: 12, padding: "24px 28px", width: "100%", maxWidth: 360,
            boxShadow: "0 24px 48px rgba(0,0,0,0.5)",
          }}>
            <div style={{ color: "#a5b4fc", fontWeight: 800, fontSize: 13, fontFamily: "monospace", marginBottom: 4 }}>
              重置密码
            </div>
            <div style={{ color: "#475569", fontSize: 11, fontFamily: "monospace", marginBottom: 16 }}>
              用户：<span style={{ color: "#e2e8f0" }}>{pwdTargetUser.username}</span>
            </div>
            <label style={{ display: "block", color: "#64748b", fontSize: 11, fontFamily: "monospace", marginBottom: 6 }}>
              新密码（至少6位）
            </label>
            <input
              type="password"
              value={pwdNewValue}
              onChange={(e) => setPwdNewValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void handleSetPassword(); }}
              autoFocus
              placeholder="请输入新密码"
              style={{
                width: "100%", boxSizing: "border-box" as const,
                padding: "10px 12px", borderRadius: 8, marginBottom: 16,
                border: "1px solid rgba(99,102,241,0.35)",
                background: "rgba(2,6,23,0.6)", color: "#e0f2fe", fontSize: 13,
              }}
            />
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                onClick={() => void handleSetPassword()}
                disabled={pwdSubmitting || pwdNewValue.length < 6}
                style={{
                  flex: 1, padding: "9px 0", borderRadius: 7, fontSize: 12, fontWeight: 700,
                  cursor: pwdSubmitting || pwdNewValue.length < 6 ? "default" : "pointer",
                  border: "1px solid rgba(99,102,241,0.5)", background: "rgba(99,102,241,0.15)",
                  color: pwdSubmitting || pwdNewValue.length < 6 ? "#475569" : "#a5b4fc",
                  fontFamily: "monospace",
                }}
              >{pwdSubmitting ? "更新中…" : "确认更新"}</button>
              <button
                type="button"
                onClick={() => { setPwdTargetUser(null); setPwdNewValue(''); }}
                style={{
                  flex: 1, padding: "9px 0", borderRadius: 7, fontSize: 12,
                  cursor: "pointer", border: "1px solid rgba(51,65,85,0.4)",
                  background: "transparent", color: "#64748b", fontFamily: "monospace",
                }}
              >取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default AdminPage;
