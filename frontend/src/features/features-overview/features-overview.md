#### 系统概述

    本系统是一套基于 **LLM-driven Agent** 范式的下一代自动化渗透测试平台。通过将大语言模型的推理规划能力与底层渗透工具链深度融合，构建了一个覆盖 **资产侦察 → 漏洞推理 → 利用验证 → 权限维持 → 报告归因** 全链路的自主安全评估系统。

    区别于传统的规则扫描器或单一功能的自动化脚本，本系统的核心创新在于实现了 **”上下文感知的智能编排”** 与 **”确定性技能执行”** 的分离架构。系统通过结构化状态机约束模型行为，利用 **Tick 驱动机制** 模拟人工渗透的”尝试-观察-决策”循环，在保证操作可控性与合规性的前提下，提供接近人类专家的漏洞挖掘深度与广度。

    系统原生支持多主流大模型接入，通过统一的 LLM 提供商抽象层，可对接 **OpenAI-compatible**、**Anthropic**、**Gemini**、**DeepSeek** 及本地部署模型，无需修改核心编排逻辑即可切换推理后端。

#### 系统数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│                         用户 / 前端管控界面                           │
│   React 19 + Vite · 任务创建 · 3D轨道视图 · CRT日志 · 报告下载        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ REST /api/v1/*
┌──────────────────────────────▼──────────────────────────────────────┐
│               Gateway API (Python FastAPI · :18080)                  │
│          API 网关 · 任务 CRUD · 聚合 Observation/Events/Trace         │
└───────┬──────────────────────────────────────────────────┬──────────┘
        │ HTTP                                             │ HTTP
┌───────▼────────────────────────┐        ┌───────────────▼────────────┐
│  Orchestrator (Python · :18081)│        │ Evidence (Python · :18103) │
│  LLM 状态机 · Tick驱动 · Plan  │◄──────►│ 事件流 · 断点 · Artifact   │
│  RECON→THREAT→VULN→EXPLOIT     │        │ MySQL × WorkSpace 文件层   │
│  →REPORT→DONE                  │        └────────────────────────────┘
└───────┬────────────────────────┘
        │ HTTP / RabbitMQ
┌───────▼────────────────────────────────────────────────────────────┐
│               Executor Service (Python · :18102)                    │
│   技能动态加载 · 容器调度 · Artifact落盘 · Worker并发管理            │
├────────────────────────────────────────────────────────────────────┤
│  nmap │ httpx │ nuclei │ dirsearch │ sqlmap │ metasploit │ …(34+)  │
└────────────────────────────────────────────────────────────────────┘
```

#### 核心架构设计与技术实现

    系统严格遵循 **模块解耦原则** 与 **单一事实来源原则**，将感知、决策、执行、存储彻底分离，形成高内聚、低耦合的四层架构。

| 层级 | 核心组件 | 职责定义 | 关键技术实现 |
| :--- | :--- | :--- | :--- |
| **接入与交互层** | Gateway Backend | 统一 API 网关，负责鉴权、任务生命周期管理、视图聚合与响应标准化。 | RESTful API (`/api/v1/tasks`)，聚合Evidence数据形成 `Observation` 与 `Events` 流。 |
| **编排与决策层** | Orchestrator | **系统大脑**。维护任务状态机，基于历史上下文与可用技能集生成下一步行动意图，管理断点与恢复。 | **状态机 + Tick 驱动**；**TaskStore 抽象层**（适配 Redis/内存）；分布式锁与幂等控制。 |
| **执行与适配层** | Executor + MQ Worker | **技能执行单元**。将编排器的抽象意图转化为具体的工具调用与参数落地，管理执行产物落盘。 | **Skill 规范**（`SKILL.md` + `_meta.json` + `execute.py`）；Artifact 引用机制；并发限流。 |
| **数据与基础层** | Evidence + Infra | **事实数据中枢**。负责持久化任务元数据、执行快照、原始日志，提供工作区管理与断点重建能力。 | MySQL（结构化元数据） + Redis（热状态缓存） + **WorkSpace（事实文件层）**。 |

     为避免大模型在自主决策中产生不可控的幻觉或越权行为，系统设计了一套 **受约束的状态机决策流程**：

- **阶段化管理**：任务被划分为 `RECON`（侦查）、`VULN_SCAN`（漏洞扫描）、`EXPLOIT`（利用）、`POST_EXPLOIT`（后渗透）等固件阶段。每个阶段拥有独立的 **可用技能集（Skill Set）** 与 **LLM Prompt 模板**。
- **单一动作约束**：编排器在每一轮 `Tick` 中，通过 **Pydantic Model** 严格约束 LLM 的输出格式，强制模型仅能选择**一个 Skill**、生成**一个目标（Target）** 和**一组参数**。这从根本上消除了模型一次生成多个复杂危险指令的风险。
- **闭环反馈回路**：`Tick` 执行完毕后，执行器的结构化输出（Stdout/Stderr/Exit Code/Artifact Ref）会作为新的上下文注入下一轮 LLM 决策，形成 **“决策-执行-观察-再决策”** 的强化循环。

    系统针对渗透测试中常见的任务中断、服务抖动、长时间运行等痛点，设计了企业级的容错与恢复机制。

- **Checkpoint 与状态重建**：Evidence与 WorkSpace 共同维护任务的完整运行时快照。当收到 `Stop` 指令或发生异常中断时，编排器会保存当前的**状态机位置、上下文摘要、Todos 列表快照**。`Resume` 时，系统优先从 Checkpoint 恢复，若无 Checkpoint 则通过 `Restore` 接口利用落盘的事件流**重放重建**最小状态。
- **Execution 幂等与去重**：在 MQ 异步派发模式下，通过 `SETNX` 原子操作注册 `request_id`。即使消息因网络问题重复投递，系统也能基于执行记录存储识别出重复请求并直接丢弃，**避免重复攻击导致目标日志污染或服务崩溃**。
- **Artifact 竞态处理**：针对执行完成但文件尚未完全刷盘的竞态窗口，执行器返回的是 **`artifact_ref` 指针**而非完整数据。读取端采用带超时的指数退避重试机制访问 `/v1/artifacts/{ref}`，确保数据可见性的最终一致。

#### 渗透技能生态与集成规范

    系统定义了一套严格的 Skill 规范，使得新工具的集成无需修改 Python 服务核心代码，仅需遵循目录规范即可实现“热插拔”。

```
skills/
└── <skill_id>/
    ├── SKILL.md          # 供 LLM 阅读的技能描述、适用场景、参数说明（Prompt 上下文关键来源）
    ├── _meta.json        # 结构化元数据：版本、作者、依赖、阶段标签、授权限制
    └── scripts/
        └── execute.py    # 统一的入口函数 def run(target, params, workspace)
```

- **LLM 感知增强**：`SKILL.md` 不仅用于人类阅读，更是 **LLM 的工具选择知识库**。编排器会将该任务当前阶段所有可用技能的 `SKILL.md` 摘要注入 Prompt，引导模型做出精准的工具选型。
- **执行环境隔离**：每个 Skill 调用均在独立的工作子目录中执行，输入参数经过严格清洗与转义，防止命令注入。

    系统内置 **34+** 种渗透技能容器，涵盖网络探测、Web 指纹、目录爆破、漏洞扫描、漏洞利用、后渗透多个维度。每个 Skill 按 TrustGuard 规范封装为独立 Docker 镜像，支持热插拔注册；底层调用的安全工具链（Nuclei 模板库、Metasploit 模块等）覆盖 50+ 种检测能力。

| 技能 ID | 阶段 | 简介 |
| :--- | :--- | :--- |
| **nmap** | RECON | 端口扫描与服务版本探测，生成攻击面地图 |
| **httpx** | RECON | 快速 HTTP 探针与轻量 Web 指纹（ProjectDiscovery） |
| **ehole** | RECON | EHole 框架/CMS 指纹识别 |
| **whatweb-fingerprint** | RECON | WhatWeb 技术栈检测，快速 Web 剖面 |
| **http-enum** | RECON | 基于 curl 的 HTTP 头部与基础枚举 |
| **fscan** | RECON | 综合内网扫描：端口 + 服务 + 漏洞快速摸排 |
| **baidu-search** | RECON | 百度 AI 在线搜索，用于目标情报收集 |
| **curl-raw** | RECON / EXPLOIT | 自定义 HTTP 请求，验证 PoC 与边界探测 |
| **katana** | VULN_SCAN | 深度 Web 爬取，构建 URL 候选池 |
| **dirsearch** | VULN_SCAN | 多线程目录与路径爆破，发现隐藏端点 |
| **ffuf-dir-enum** | VULN_SCAN | ffuf 内容发现，高速 Web 路径枚举 |
| **dispatcher** | VULN_SCAN | URL 去重 + 分片 + manifest 管道，驱动 nuclei 分批扫描 |
| **nuclei** | VULN_SCAN / EXPLOIT | 基于模板的漏洞扫描与 PoC 验证（safe-poc / exploit 双标签） |
| **nikto-scan** | VULN_SCAN | Nikto Web 配置审计，检测常见服务端错误配置 |
| **sqlmap** | EXPLOIT | 自动化 SQL 注入检测与数据提取 |
| **fenjing** | EXPLOIT | Jinja2 SSTI 自动化利用，含 WAF 绕过与 CTF 特化模式 |
| **exploit-struts2** | EXPLOIT | Apache Struts2 经典 RCE 漏洞利用链（S2-045/S2-057 等） |
| **metasploit** | EXPLOIT | MSF 漏洞利用框架：exploit + post 模块统一调度 |
| **metasploit-session** | EXPLOIT | MSF 会话管理与交互式模块执行 |
| **webshell-php** | EXPLOIT | PHP Webshell 上传与远程命令执行 |
| **python-sandbox** | EXPLOIT | 沙箱化 Python 执行环境，用于自定义 payload 调试 |
| **linpeas** | POST_EXPLOIT | linPEAS 自动化 Linux 提权路径枚举 |
| **pua** | POST_EXPLOIT | 持久化访问与后门维持 |
| **read_workspace_artifact** | VERIFY | 工作区产物读取，用于 PoC 验证与假阳性排查 |
| **read_target_list** | ALL | 读取靶标 URL 列表，驱动多目标批量测试 |
| **exploit-thinkphp** | EXPLOIT | ThinkPHP 5.x invokefunction RCE 全流程利用（CVE-2018-20062） |
| **shiro_exploit** | EXPLOIT | Apache Shiro rememberMe 反序列化 RCE，支持 CC/CB 利用链（CVE-2016-4437） |
| **fastjson-exploit** | EXPLOIT | Fastjson 1.2.47 JNDI 注入 RCE，autoType 绕过 + 回显 chain（CVE-2019-14540） |
| **ysoserial** | EXPLOIT | Java 反序列化 Payload 生成，覆盖 CC1-CC6 / CB1 / 多种 gadget chain |
| **jndi_exploit** | EXPLOIT | JNDI 注入服务端，LDAP/RMI 双模式，配合 fastjson/log4j/weblogic 等利用 |
| **exploit-weblogic** | EXPLOIT | Oracle WebLogic T3/IIOP 反序列化 RCE，支持 CVE-2023-21839 / CVE-2020-14882 |
| **exploit-tomcat** | EXPLOIT | Apache Tomcat PUT 任意文件上传 RCE，JSP Webshell 上传（CVE-2017-12615） |

- **动态适配层**：执行器内部包含针对 Linux/Windows 双平台的适配逻辑，目标类型识别后自动调整底层调用方式（如 PowerShell 封装）。
- **漏洞靶场支持**：内置 **Vulhub**（Struts2、Spring、ThinkPHP 等 CVE 环境）与 **Bugku PAR** 平台的特定解析器，能识别靶场指纹并调用精准利用链。

#### 可观测性与审计追溯

   系统不只是一个执行黑盒，它提供了三个维度的透明化视图：

 **事件流**：按时间顺序记录的每一次编排决策、执行派发、结果回调的原始日志，用于技术复盘。
 **观测视图**：面向安全分析师的聚合视图，将分散的执行结果（如开放的端口、发现的子域、爆出的漏洞ID）按资产聚合，形成可视化的 **攻击面概览**。
 **Todo 列表**：展示 Agent 当前规划但尚未执行的任务队列，反映了模型的实时“思维路径”。

    所有测试过程的原始产出（Nmap XML 结果、Dirsearch 日志、SQLMap 输出）**不以数据库 BLOB 字段存储**，而是直接落盘至 **WorkSpace 文件事实层**。数据库仅存储指向该文件的 `artifact_ref` 索引。

- **优势**：避免数据库膨胀，保证了证据的原始性与法律效力。报告生成时，系统直接从 WorkSpace 提取原始数据渲染图表与详情，**杜绝中间层数据加工带来的篡改风险**。

#### 跨平台支持与靶场兼容

    系统内置针对 Linux / Windows 双平台目标的适配层，目标类型识别后自动调整工具调用路径（Linux 走 bash/sh，Windows 走 PowerShell/cmd），实现同一 Skill 在两类环境下的无缝切换。

| 维度 | Linux 目标 | Windows 目标 |
| :--- | :--- | :--- |
| **Shell 适配** | bash/sh 原生调用 | PowerShell / cmd 封装 |
| **提权路径** | linPEAS / sudo 滥用 / SUID 枚举 | WinPEAS / Token 滥用 / UAC 绕过 |
| **持久化** | Crontab / systemd service | 计划任务 / 注册表 / 服务安装 |
| **横向移动** | SSH 密钥复用 / NFS 挂载 | Pass-the-Hash / SMB 共享 |

    靶场兼容层内置 **Vulhub**（Struts2/Spring/ThinkPHP/Shiro 等主流 CVE 环境）与 **Bugku PAR** 平台解析器，能自动识别靶场指纹并调用精准利用链，支持竞赛题目的端到端自动化解题。

#### 量化能力指标

| 指标项 | 系统能力 | 基础要求 | 进阶要求 |
| :--- | :--- | :--- | :--- |
| **漏洞检测率** | 基于模板的精准验证，误报过滤 | ≥90% | ≥95% |
| **误报率** | PoC 二次确认机制消除误报 | ≤10% | ≤5% |
| **CVE 覆盖** | nuclei 模板库 + 专用利用脚本 | ≥1% | ≥5% |
| **目标系统** | Linux + Windows 双平台适配 | Linux 或 Windows | **Linux + Windows** ✓ |
| **靶场兼容** | Vulhub + Bugku PAR 双平台解析 | Vulnhub/Vulhub | **Vulhub + Bugku PAR** ✓ |
| **工具数量** | 34+ Skill 容器 × 热插拔注册，底层工具链覆盖 50+ 检测能力 | ≥30 个 | ≥50 个 ✓ |
| **单目标时间** | Tick 驱动并发执行，分钟级完成 | ≤30 分钟 | **≤15 分钟** ✓ |
| **并发测试** | 多任务独立 WorkSpace 隔离 | ≥1 个 | **≥3 个** ✓ |
| **多阶段攻击** | RECON→THREAT_MODEL→VULN_SCAN→EXPLOIT→REPORT→DONE 六阶段链式推进 | 单阶段 | **多阶段链式** ✓ |
| **自动报告** | MD + HTML 双格式报告，含漏洞清单 + 修复建议 + CVSS 评级 | 基础报告 | **详细报告 + 修复建议** ✓ |

#### 支持靶机环境

| 平台 | 靶机 | 类型 |
| :--- | :--- | :--- |
| **Vulhub** | Apache Struts2 S2-045 / S2-057 | RCE |
| **Vulhub** | ThinkPHP 5.0.23-RCE | RCE |
| **Vulhub** | CVE-2023-21839（WebLogic） | RCE |
| **Vulhub** | CVE-2017-12615（Tomcat PUT） | 文件上传 |
| **Vulhub** | CVE-2019-11043（PHP-FPM） | RCE |
| **Vulhub** | CVE-2022-41678（ActiveMQ） | RCE |
| **Vulhub** | CVE-2017-7504（JBoss） | RCE |
| **Vulhub** | Tomcat8 弱口令 | 认证绕过 |
| **Vulhub** | CVE-2016-4437（Shiro 反序列化） | RCE |
| **Vulhub** | Fastjson 1.2.24 / 1.2.47 RCE | 反序列化 |
| **Vulhub** | CVE-2022-34265（Django） | SQL 注入 |
| **Vulhub** | Flask SSTI | 模板注入 |
| **Vulhub** | CVE-2024-36401（GeoServer） | RCE |
| **Vulnhub** | Tomato / Earth / Jangow / Phineas / Odin | 综合渗透 |
| **Bugku PAR** | Web 综合题自动化解题 | CTF 端到端 |

#### 前端管控界面

前端基于 **React 19 + Vite 7 + TypeScript** 构建，通过 `/api/v1/*` REST 接口与后端实时交互，提供完整的任务管控体验。

| 功能模块 | 说明 |
| :--- | :--- |
| **3D 轨道视图** | 球形任务拓扑，节点颜色/光晕实时反映运行状态（运行/暂停/完成/失败）；支持列表视图切换，列标题可点击排序 |
| **任务全生命周期管控** | 创建 → 执行 → 暂停 → 续跑 → 单步推进，所有操作均通过 API 实时同步至编排器；支持**一键并发启动全部任务**，直接演示 ≥3 并发能力 |
| **多维度可视化面板** | 任务详情弹窗集成：结构化观测快照（阶段/状态/上下文键值/Artifacts 列表）/ 测试计划 Todo / 执行轨迹（含 LLM 推理） / 策略计划（LLM Plan）/ 编译段 / 分页执行记录（点击展开完整推理文本与工具原始输出，自动刷新）/ 结构化报告在线预览（含漏洞清单与修复建议）|
| **6 阶段进度可视化** | 列表视图为每行任务渲染 6 段迷你进度条（RECON→THREAT_MODEL→VULN_SCAN→EXPLOIT→REPORT→DONE），当前阶段高亮，直观展示多阶段链式推进 |
| **实时日志终端** | CRT 风格日志终端，按 20+ 事件类型精细渲染（阶段切换/技能执行/LLM 决策/框架识别/内存更新/预算超限/错误），支持关键词过滤与日志导出；顶部实时 **Todo 任务条**（每 5 秒刷新，DONE/IN_PROGRESS/FAILED/PENDING 四色高亮，精确反映 PTES 各阶段推进进度）；支持 URL 深链接 `?taskId=` 直达指定任务日志（无论从任务管理还是平台管理中心跳转）；后端离线时展示缓存日志并显示提示，保证演示连贯性 |
| **自动化报告生成** | 双格式报告下载：**Markdown** 格式（含阶段详情、执行轨迹、漏洞清单、修复建议）与**HTML** 格式（深色主题 + 专业排版，支持打印转 PDF）；满足进阶报告要求；弹窗内在线预览支持**展开/收起**切换（⊞/⊟），展开后占 65% 视口高度；后端不可达时自动从本地缓存生成离线报告 |
| **报告管理中心** | 独立报告汇总页（`/reports`），列出所有已完成渗透测试任务；点击行**内联展开**查看：6 阶段进度、Task ID（一键复制）、测试目标与描述、完成时间及测试时长、**漏洞发现/扫描摘要**（开放端口、存在漏洞服务、确认漏洞清单）、**按漏洞类型生成修复建议**（含风险等级）、全部执行记录明细；支持**双格式一键下载**（MD + HTML 报告，HTML 含专业暗色排版，可直接打印为 PDF）；后端新增 `GET /api/v1/admin/reports` 聚合接口，专用于报告清单索引 |
| **平台管理中心** | 独立管理页面（`/admin`），实时展示：SLI 快照（Tick 总数/错误率/活跃任务）、MQ 队列状态（消息数/消费者/调度模式）、V1 调度配置（Plan 开关/KB/Agent 注册数）、知识库 KB 状态（后端/集合/向量维度/知识块数/联邦存储节点）、任务统计概览（含 Donut 图表）、**快速创建任务表单**（支持"创建"与"创建并运行"双模式，Enter 键提交）、**待启动任务列表**（PENDING 状态任务一键启动）、活跃任务表（点击行**内联展开**：6 阶段进度条 + Task ID 一键复制 + 描述/时间戳 + 近 6 条执行记录详情；含日志直链 / 暂停与续跑操作）、近期完成/失败任务表（同样支持**内联展开**查看任务详情，含逐条删除按钮）、全局活动流（跨任务事件流）、编排器健康诊断（degraded 告警）、**技能注册表面板**（按阶段过滤，卡片式展示所有已注册技能及 category，代理 Executor /v1/skills）、调度诊断（支持输入 task_id 精准查询 Agent 候选评分）、**用户管理面板**（CRUD：新建/禁用/删除用户，角色 ADMIN/OPERATOR/VIEWER）；自动每 10 秒刷新，localStorage 同步确保 Header 徽章与日志终端实时对齐；全对接 Admin API 端点 |
| **运营管理中心** | 统一运营仪表盘（`/dashboard`），6 维 KPI 卡（总任务/运行中/完成/暂停/失败/待启动 + 完成率）、活跃任务实时表格（点击跳转轨迹）、最近完成任务列表、管控操作栏（**一键停止全部运行中任务**、**清理完成/失败记录**，含二次确认防误操作）、实时事件流（色标分类）、快捷操作网格（6 入口）、竞赛能力指标自查面板、服务连通状态；数据来源 `GET /api/v1/admin/dashboard/summary`（DB-only < 200ms），15 秒自动刷新 |
| **系统状态仪表盘** | 独立系统状态页（`/system`），实时展示：6 维 KPI 卡片（累计任务/运行中/已完成/失败/平台用户/活跃用户）、5 服务健康状态表（Gateway/Orchestrator/Executor/Evidence/MQ）、平台信息（版本/运行时/OS/运行时长）、**平台能力矩阵**（技能容器数/阶段列表/并发/调度模式/LLM 提供商 + 竞赛指标符合度自查清单）、用户账号概览（角色分布 + 用户表）；每 15 秒自动刷新；后端断线时各区块单独降级显示"离线"而非整页崩溃；`GET /api/v1/system/info` 由 Gateway FastAPI 接口提供，合并 TaskStats/服务拓扑/运行时元数据 |
| **系统健康监控** | Header 实时显示后端连通状态；任务中心气泡聚合 SLI 快照（总 Tick / 错误率）、MQ 队列状态与 V1 调度模式；并发运行数 ≥3 时触发醒目的"并发"角标 |
| **状态变化通知** | 后台轮询自动检测任务完成/失败并弹出 Toast 通知，无需手动刷新 |
| **漏洞数据库** | 漏洞聚合页（`/vulns`），跨任务 CVE 汇总；按严重级别（严重/高危/中危/低危/信息）分类统计与筛选；CVE 详情表格（CVSS 评分 + 受影响服务 + 任务归属 + 修复建议）；数据来源 `GET /api/v1/admin/vulns/summary` + 演示回退 |
| **批量调度中心** | 批量任务页（`/batch`），多目标一次性提交；三种预设模板（Web三连/内网扫描/靶场组合）；目标解析预览 + 自动启动开关；提交后展示逐条创建结果 + 任务跳转链接；`POST /api/v1/admin/tasks/batch` 后端接口支持 ≤20 目标并行创建与可选自动启动 |
| **监控大屏** | 独立监控页（`/monitor`），实时任务状态矩阵、并发槽可视化、SLI 趋势、活跃任务卡片群、全局事件流；直观展示 ≥3 并发能力 |

| **任务执行轨迹** | 独立轨迹页面（`/trace/:taskId`），三标签页：① **执行事件流** — 按事件类型着色的完整原始事件序列；② **AI 编排轨迹** — LLM 每次决策明细（阶段/技能/状态/推理摘要/耗时）+ 执行计划汇总条；③ **上下文快照** — AI 发现的攻击面聚合（已确认漏洞 CVE 列表、开放端口矩阵、技术栈指纹、OS 猜测、逐技能分析摘要）；任务完成后展示"查看报告"快捷跳转 |
| **统计分析中心** | 独立统计页（`/stats`），四维汇总卡（累计任务/发现漏洞/技能执行次数/活动事件）、任务状态环形图、漏洞严重级别分布进度条（严重/高危/中危/低危）、技能使用排行 TOP-10 条形图、高危 CVE 列表（CVSS 徽章）、事件类型分布卡片群；后端接口 `GET /api/v1/admin/analytics/overview` |
| **个人中心** | 独立用户页（`/profile`），账户信息/角色展示、任务统计卡、近期任务列表（点击直达轨迹）、密码与昵称修改、审计操作日志 |
| **配置中心** | 独立配置页（`/config`），四组运行时配置块（AI 引擎/执行引擎/部署环境/功能开关），接口 `GET /api/v1/admin/config/runtime` |

#### 安全合规与运行约束

- **双重授权校验**：任务创建时声明的 `allowed_target` 会在编排器生成指令时作为 **必传约束** 下发给执行器。执行器在调用底层工具前，会通过正则/IP 段匹配进行**二次强校验**，拦截任何越界请求。
- **最小权限执行容器**：Docker 镜像默认以非 Root 用户运行，挂载目录严格控制读写权限，限制容器的 Linux Capabilities，降低逃逸风险。
