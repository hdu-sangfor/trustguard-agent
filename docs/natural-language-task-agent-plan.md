# 自然语言渗透测试任务 Agent 改造计划

> 状态：MVP 已实现，后续里程碑待推进
> 分支：`feat/natural-language-task-agent-plan`
> 原则：最小侵入、复用现有任务生命周期、禁止上层 Agent 直连 Executor。

## 1. 目标与非目标

### 目标

让用户可以用自然语言完成以下操作：

1. 描述测试目标、授权范围、测试重点和限制条件；
2. 得到结构化任务草稿，并在缺少关键信息时被追问；
3. 确认草稿后创建、启动、暂停、恢复任务；
4. 查询阶段、进度、Trace、执行记录和报告；
5. 在整个过程中保留现有 Gateway、Orchestrator、Executor、Evidence 的审计链。

### 非目标

- 不重写现有 `state_machine`、Skill 调度器或 Docker 技能容器；
- 不让上层 Agent 生成 Shell 命令、Docker 命令或直接选择 Executor 参数；
- 不把现有 `/v1/agent/run` 演示型 LangGraph 直接扩展成生产任务编排器；
- 第一阶段不引入新的第三方 Agent Runtime；
- 第一阶段不实现复杂的多目标编排、定时任务和自动报告问答。

## 2. 当前系统边界

生产渗透测试链路是：

```text
Gateway.create_task()
  → Orchestrator.create_task()
  → Orchestrator.run_task() / _run_loop()
  → state_machine.tick() 或 tick_manager()
  → call_plan_list_decision_engine()
  → validate_plan_list_business()
  → try_dispatch_pending_plan_list_item()
  → compile_plan_item()
  → ExecutionDispatcher.dispatch()
  → Executor._execute_impl()
  → ExternalScriptSkill.execute()
  → workspace / Evidence / Trace 回流
```

关键实现位置：

- Gateway 任务生命周期：`gateway/app/main.py` 的 `create_task`、`_run_lifecycle`、`run_task`、`stop_task`、`resume_task`；
- Orchestrator 任务生命周期：`orchestrator/app/main.py` 的 `create_task`、`run_task`、`_run_loop`、`tick_task`；
- 状态机：`orchestrator/app/core/state_machine.py` 的 `tick`、`tick_manager`、`_run_actions_and_merge_results`；
- 计划编译：`orchestrator/app/clients/llm_client.py` 的 `call_plan_list_decision_engine`，以及 `orchestrator/app/core/plan_execution_dispatch.py` 的 `try_dispatch_pending_plan_list_item`；
- 执行派发：`orchestrator/app/core/execution_dispatcher.py` 的 `HttpExecutionDispatcher.dispatch` / `MqExecutionDispatcher.dispatch`；
- 执行器安全边界：`executor/app/main.py` 的 `_is_target_allowed`、`_apply_nmap_scope_guard`、`_execute_impl`；
- 证据链：`orchestrator/app/core/agent_tools.py` 的 `apply_execution_result`，以及 `evidence/app/main.py` 的 `ingest_event`、`put_context`、`put_checkpoint`。

`orchestrator/app/trustguard_agent/graph_core.py` 中的 `/v1/agent/run` 当前只是固定 `DEFAULT_PLAN` 的最小 LangGraph 示例，不接入完整的任务状态机、PlanList 和 Evidence，因此不作为新 Agent 的生产基座。

### 2.1 当前 Workflow 是如何定义的

当前没有统一的 `Workflow` 基类、`WorkflowSpec`、运行句柄或可执行 Workflow Registry。生产渗透 Workflow 是由多组 Python 结构共同定义的隐式工作流。

#### 第一层：任务生命周期循环

`orchestrator/app/main.py`：

```text
run_task()
  → 创建后台 _runner
  → _run_loop()
  → 循环调用 tick()/tick_manager()
  → DONE / FAILED / PAUSED / max_ticks / max_duration 时退出
```

`_run_loop()`负责锁、重试、超时、暂停和 checkpoint，但它不知道每个渗透阶段的具体业务。

#### 第二层：阶段状态图

状态定义在 `TaskState` 和 `Phase`，允许路线硬编码在 `phase_transition_guard._PHASE_ROUTE_MATRIX`：

```text
PENDING
  → RECON
  → THREAT_MODEL（可选）
  → VULN_SCAN
  → EXPLOIT（需漏洞确权）
  → REPORT
  → DONE
```

实际允许的边：

```python
{
    RECON: {THREAT_MODEL, VULN_SCAN},
    THREAT_MODEL: {VULN_SCAN},
    VULN_SCAN: {EXPLOIT, REPORT},
    EXPLOIT: {REPORT},
    REPORT: {DONE},
}
```

`guard_next_phase()`和`guard_finish_phase()`增加信息成熟度、扫描覆盖、框架识别和漏洞确权门禁。

#### 第三层：每次 tick 的业务解释器

`state_machine.tick()`并不是声明式图，而是一个较大的异步解释器：

```text
检查终态/预算
  → REPORT 结算
  → 注入目标画像、记忆、知识
  → 获取当前阶段可用 Skill
  → RECON 确定性预热（Katana + Dirsearch）
  → 若有未完成 PlanItem，编译并执行下一项
  → 否则调用 LLM 生成新 PlanList
  → 业务校验并持久化计划
  → 按 orchestration hint 尝试推进阶段
```

`tick_manager()`是可选变体，在相同步骤外增加 Todo 的生成、选择、注入和持久化；由`ENABLE_MANAGER_AGENT`选择。它不是另一个可注册 Workflow。

#### 第四层：阶段内动态 Plan

LLM 每轮输出`PlanList`，其中包含`PlanItem`和可选`PlanOrchestrationHints`。计划保存在：

```text
TaskState.target_context["_latest_plan_list"]
TaskState.target_context["_plan_dispatch_next_index"]
```

下一 tick 由`try_dispatch_pending_plan_list_item()`读取一个 PlanItem，调用`compile_plan_item()`得到`CompiledInstruction`，然后进入`_run_actions_and_merge_results()`。所以 PlanList 是“阶段内动态战术计划”，不是完整 Workflow 定义。

#### 第五层：执行派发

`ExecutionDispatcher`按环境变量选择 HTTP 或 MQ，把编译后的 Skill 请求交给 Executor。该层只负责执行和回收，不决定 Workflow 路线。

#### 独立的 Demo LangGraph

`trustguard_agent/graph_core.py`使用`StateGraph`显式定义：

```text
plan_next_tool
  → execute_tool
  → plan_next_tool
  → summarize
  → END
```

但其`DEFAULT_PLAN = [http_probe, fingerprint, risk_review]`，没有接入生产`TaskState`、阶段门禁、PlanList、Executor Skill 和 Evidence。它与上面的生产 Workflow 并行存在。

因此目前更准确的描述是：

```text
生产 Pentest Workflow = _run_loop + TaskState + tick + phase guards + PlanList pipeline
Demo Agent Workflow     = graph_core.build_graph()
Agent Registry          = 只读能力元数据，不参与上述 Workflow 执行
knowledge.workflow_type = 知识检索元数据，不是 Workflow 定义
```

## 3. 推荐的分层方案

```text
用户
  ↓ 自然语言
Task Agent（控制面）
  ├─ 意图解析
  ├─ 授权/目标/风险校验
  ├─ 缺字段追问
  ├─ 生成草稿并请求确认
  └─ 只调用 Gateway 任务工具
        ↓
Gateway 现有任务 API
        ↓
Orchestrator 现有 Plan/State Machine
        ↓
Executor + Skill Containers
```

### 设计决策

上层 Agent 应作为独立的 `supervisor` 服务设计，而不是继续塞进 `orchestrator/app/core`。它是控制面 Supervisor；渗透测试编排器和未来告警研判编排器都是它可以调起的异步 Workflow/SubAgent。

这样做虽然新增一个服务目录，但对旧代码是加法式改造：Supervisor 只调用 Gateway 公开任务 API，不直接 import `orchestrator/app`，也不直接连接 Executor。现有渗透链路保持不变，未来增加告警研判时只新增一个 Workflow Adapter。

调用关系：

```text
Gateway / UI / API Client
          ↓
Supervisor Agent（自然语言、路由、确认、权限、观察）
          ├─ PentestWorkflowAdapter → Gateway /api/v1/tasks/*
          └─ AlertTriageWorkflowAdapter → 告警研判服务（未来）
                                      ↓
                    Orchestrator / Executor / Evidence
```

Supervisor 不应该同步等待一次渗透测试完成。`launch()` 只返回 `WorkflowHandle`，之后通过轮询、SSE 或事件订阅观察状态。

### 3.1 Supervisor 的代码目录

建议新增顶层目录 `supervisor/`，保持与 `gateway/`、`orchestrator/`、`executor/` 同级：

```text
supervisor/
├─ Dockerfile
├─ pyproject.toml
├─ uv.lock
└─ app/
   ├─ main.py                         # FastAPI：chat、draft、confirm、workflow status
   ├─ config.py                       # LLM、Gateway、Redis、确认令牌配置
   ├─ api/
   │  ├─ chat.py                      # 自然语言入口
   │  └─ workflows.py                 # 启动、停止、恢复、查询
   ├─ domain/
   │  ├─ models.py                    # Actor、Conversation、WorkflowHandle、AgentResponse
   │  └─ enums.py                     # Intent、ConversationState、WorkflowState
   ├─ runtime/
   │  ├─ graph.py                     # Supervisor LangGraph
   │  ├─ state.py                     # SupervisorState，不复用 TaskState
   │  ├─ nodes.py                     # classify/clarify/policy/confirm/dispatch/observe
   │  ├─ router.py                    # 按 intent 选择 Workflow Adapter
   │  └─ checkpointer.py              # Redis 会话断点
   ├─ registry/
   │  ├─ models.py                    # WorkflowSpec
   │  ├─ registry.py                  # WorkflowRegistry
   │  └─ builtins.py                  # 注册 pentest、未来 alert-triage
   ├─ workflows/
   │  ├─ base.py                      # WorkflowAdapter 协议
   │  ├─ pentest/
   │  │  ├─ models.py                 # PentestDraft、PentestPolicy
   │  │  ├─ intent.py                 # 渗透意图解析
   │  │  ├─ policy.py                 # 目标/授权/风险校验
   │  │  ├─ adapter.py                # 调 Gateway 任务 API
   │  │  └─ prompts.py                # 渗透领域提示词
   │  └─ alert_triage/                # 仅放 Supervisor 侧调用契约/适配器
   │     ├─ models.py                 # AlertTriageDraft 和通用结果映射
   │     ├─ intent.py                 # 判断用户是否要发起/查询告警研判
   │     └─ adapter.py                # 调独立 alert-triage 服务
   ├─ clients/
   │  ├─ gateway_client.py             # 唯一的渗透任务调用出口
   │  ├─ llm_client.py                # Supervisor 通用结构化 LLM 调用
   │  └─ audit_client.py              # 审计事件/Trace
   ├─ security/
   │  ├─ actor.py                     # 用户身份、角色和任务归属
   │  ├─ confirmation.py              # 草稿签名、过期、一次性消费
   │  └─ scope.py                     # 目标规范化和授权策略
   └─ stores/
      ├─ conversation_store.py         # Redis 会话状态
      └─ workflow_run_store.py         # Supervisor 与下层 task_id 的映射
```

### 3.2 Workflow/SubAgent 协议

各领域 SubAgent 不是直接互相 import，而是实现统一 Adapter：

```python
class WorkflowAdapter(Protocol):
    workflow_id: str

    async def build_draft(self, request: IntentRequest) -> DraftResult: ...
    async def validate(self, draft: BaseModel, actor: Actor) -> PolicyDecision: ...
    async def launch(self, draft: BaseModel, actor: Actor, idem_key: str) -> WorkflowHandle: ...
    async def get_status(self, handle: WorkflowHandle) -> WorkflowStatus: ...
    async def stop(self, handle: WorkflowHandle, actor: Actor) -> None: ...
    async def resume(self, handle: WorkflowHandle, actor: Actor) -> None: ...
    async def get_result(self, handle: WorkflowHandle, actor: Actor) -> WorkflowResult: ...
```

Supervisor 只依赖这个协议。`PentestWorkflowAdapter`内部调用 Gateway 的 create/run/stop/resume/report 接口；`AlertTriageWorkflowAdapter`未来可以调用告警服务或自己的队列。

`V1AgentRegistry` 和 `V1SchedulingPolicy`可以作为设计参考，但不能直接当作 Workflow Registry：当前实现只有 AgentSpec 的只读登记和 capability 候选排序，没有会话、确认、句柄、生命周期和结果协议。

### 3.3 对 `orchestrator/app` 的复用边界

| 现有代码 | 复用方式 | 结论 |
|---|---|---|
| `trustguard_agent/graph_core.py` | 参考 LangGraph 节点/路由写法 | 不直接复用固定 Demo Plan |
| `clients/llm_client.py` | 复用 Provider、重试、JSON 解析的设计 | 首版 Supervisor 自有薄客户端；稳定后再抽通用包 |
| `core/prompt_injection_guard.py` | 适合抽成通用安全组件 | 不从另一个服务目录直接 import |
| `core/correlation_ids.py` | 统一 `conversation_id/workflow_run_id/task_id` 关联字段 | 可形成跨服务契约 |
| `structured_error_envelope.py` | 统一错误 envelope | 可形成跨服务契约 |
| `core/v1_agent_registry.py` | 参考 capability registry | 不具备 Workflow 生命周期，不能原样复用 |
| `core/v1_scheduler_policy.py` | 参考 capability-first 路由 | Supervisor 需额外考虑意图、权限、租户和可用性 |
| `core/state_machine.py` | 只由 Pentest SubAgent 内部使用 | Supervisor 禁止 import/调用内部函数 |
| `core/agent_tools.py` | 只由 Pentest 执行回流使用 | Supervisor 禁止获得 Skill 派发能力 |
| `core/manager_agent.py` | 渗透阶段 Todo 管理 | 不等于跨领域 Supervisor |
| `core/task_store.py` | 渗透任务状态 | 不用于对话和跨 Workflow Handle |

如果后续至少两个服务确实需要同一套 LLM 传输、错误和关联 ID，可新增：

```text
packages/agent_runtime/
├─ pyproject.toml
└─ trustguard_agent_runtime/
   ├─ llm.py
   ├─ errors.py
   ├─ correlation.py
   └─ input_guard.py
```

抽取时先保留 `orchestrator/app/clients/llm_client.py` 等兼容包装，逐个替换导入并跑回归测试，避免一次性移动旧模块。首版不为了“看起来复用”而提前做这项重构。

### 3.4 Pentest SubAgent 的调起方式

`PentestWorkflowAdapter`把现有完整渗透任务视为一个异步 SubAgent，不进入其内部 tick：

| Adapter 方法 | 现有接口 | 返回/行为 |
|---|---|---|
| `build_draft()` | Supervisor 自己的结构化 LLM | 生成 `PentestDraft`，无副作用 |
| `validate()` | Supervisor 确定性策略 + Gateway 授权数据 | 返回缺字段、拒绝原因或确认要求 |
| `launch()` | `POST /api/v1/tasks`，随后可调用 `POST /api/v1/tasks/{task_id}/run` | 返回含 `task_id` 的 `WorkflowHandle` |
| `get_status()` | `GET /api/v1/tasks/{task_id}` 和 `/run-status` | 映射为通用 `WorkflowStatus` |
| `stop()` | `POST /api/v1/tasks/{task_id}/stop` | 保持现有 checkpoint 语义 |
| `resume()` | `POST /api/v1/tasks/{task_id}/resume` | 恢复现有任务 |
| `get_result()` | `GET /api/v1/tasks/{task_id}/report` 和 `/trace` | 映射为通用 `WorkflowResult` |

`WorkflowHandle`至少包含：

```json
{
  "workflowRunId": "wfr-uuid",
  "workflowId": "pentest",
  "externalTaskId": "task-uuid",
  "actorId": "user-id",
  "state": "RUNNING",
  "createdAt": "..."
}
```

Supervisor 调 Gateway 时使用服务间令牌，并携带已验证的 actor/tenant 上下文。Gateway 必须重新校验任务归属和角色，不能因为请求来自 Supervisor 就跳过授权。

### 3.5 告警研判 Agent 不放入现有 Orchestrator

当前 `orchestrator/app`实际上是 Pentest Orchestrator，而不是通用 Workflow 容器：

- `TaskState` 固定包含 `target/current_phase/target_context`；
- `Phase` 固定为 `RECON/THREAT_MODEL/VULN_SCAN/EXPLOIT/REPORT`；
- 决策输出固定为渗透 `PlanList/PlanItem/Skill`；
- 执行面固定连接 Executor、技能镜像和渗透工件；
- `manager_agent.py` 的 Todo 也是围绕端口、URL、漏洞和 Exploit。

告警研判通常需要的是告警归一化、资产/身份上下文补全、IOC/威胁情报查询、事件关联、误报判断、处置建议和人工审批。把这些节点塞进现有 `orchestrator/app` 会迫使共享一个越来越大的 `TaskState`、提示词和依赖集合，最终两个领域互相影响测试和发布。

理想方案是新增独立顶层服务：

```text
alert-triage/
├─ Dockerfile
├─ pyproject.toml
├─ uv.lock
└─ app/
   ├─ main.py                         # /v1/runs 生命周期 API
   ├─ config.py
   ├─ api/
   │  ├─ runs.py                     # create/start/status/stop/result
   │  └─ events.py                   # 告警输入和增量研判事件
   ├─ domain/
   │  ├─ models.py                   # Alert、Entity、Evidence、Verdict、Recommendation
   │  ├─ enums.py                    # severity/status/verdict/action
   │  └─ errors.py
   ├─ runtime/
   │  ├─ graph.py                    # 告警研判 LangGraph
   │  ├─ state.py                    # AlertTriageState
   │  ├─ nodes.py                    # normalize/enrich/correlate/analyze/decide/report
   │  ├─ router.py
   │  └─ checkpointer.py
   ├─ connectors/
   │  ├─ siem.py                     # SIEM/日志平台
   │  ├─ asset.py                    # CMDB/资产中心
   │  ├─ identity.py                 # IAM/账号上下文
   │  ├─ threat_intel.py             # IOC/威胁情报
   │  └─ case_system.py              # 工单/案件平台
   ├─ policy/
   │  ├─ verdict.py                  # 置信度和结论门禁
   │  └─ response_action.py          # 隔离/封禁等动作必须审批
   ├─ clients/
   │  ├─ llm_client.py
   │  ├─ audit_client.py
   │  └─ supervisor_client.py        # 可选，只用于回报事件，不用于直接调 pentest
   └─ stores/
      ├─ run_store.py
      ├─ evidence_store.py
      └─ case_store.py
```

建议的告警研判图：

```text
ingest_alert
  → normalize
  → enrich_asset_and_identity
  → query_threat_intel
  → correlate_events
  → analyze
  → decide_verdict
  → recommend_response
  → approval_gate（仅高风险处置）
  → report
```

`supervisor/app/workflows/alert_triage/`只存放自然语言入口需要的 Draft、结果映射和 HTTP Adapter；真正的研判状态机、连接器和证据存储放在 `alert-triage/`。

### 3.6 理想的仓库目录

```text
trustguard-agent/
├─ frontend/                          # UI
├─ gateway/                           # 统一公共 API、认证、任务归属
├─ supervisor/                        # 上层对话 Agent 和 Workflow 路由
├─ orchestrator/                      # 现有 Pentest SubAgent（名称暂不改，避免大迁移）
├─ executor/                          # Pentest Skill 执行面
├─ evidence/                          # 现有 Pentest Trace/Checkpoint
├─ alert-triage/                      # 未来独立告警研判 SubAgent
├─ skills/                            # Pentest 原生技能镜像
├─ contracts/
│  └─ workflows/
│     └─ v1/
│        ├─ common/                   # WorkflowHandle/Status/Event/Error
│        ├─ pentest/                  # PentestDraft/Result
│        └─ alert_triage/             # AlertTriageDraft/Verdict/Result
├─ packages/
│  └─ agent_runtime/                  # 达到复用阈值后再抽取的极小通用包
├─ tests/
│  ├─ contracts/                      # 服务之间的契约一致性
│  ├─ unit/supervisor/
│  └─ unit/alert_triage/
└─ docker-compose.yml                 # 完整栈唯一入口
```

不要为了目录“统一”立即把现有 `orchestrator/app`搬到 `workflows/pentest/`。可以在文档和注册表中把它标识为 `workflow_id=pentest`，等接口稳定、测试覆盖充分后再考虑服务改名。

### 3.7 告警研判升级为渗透测试

告警研判 Agent 如果发现需要主动验证，不应直接调用 Executor 或 Pentest Orchestrator。正确流程是：

```text
Alert Triage 输出 escalation proposal
  → Supervisor 接收 proposal
  → 转换为 PentestDraft（带 alert_run_id/evidence_refs）
  → 重新执行目标授权和风险策略
  → 用户确认
  → PentestWorkflowAdapter.launch()
```

这样告警证据只能作为渗透计划的输入引用，不能绕过 `allow_exploit`、目标范围和人工确认。跨 Workflow 关联使用：

```text
conversation_id
workflow_run_id
parent_workflow_run_id
external_task_id
evidence_refs
```

## 4. 第一阶段 API 设计

### 4.1 生成任务草稿（无副作用）

```http
POST /api/v1/task-agent/draft
```

请求：

```json
{
  "message": "对 https://test.example.com 做非破坏性 Web 渗透，重点检查未授权和 Struts2，最多运行 20 分钟",
  "conversationId": "optional-id"
}
```

响应：

```json
{
  "status": "NEEDS_CONFIRMATION",
  "draftId": "draft-uuid",
  "draft": {
    "name": "test.example.com Web 渗透测试",
    "target": "https://test.example.com",
    "description": "对测试环境执行 Web 安全评估",
    "businessBackground": "该目标为已获授权的测试环境",
    "extraUserRequirements": "仅执行非破坏性验证；重点检查未授权访问与 Struts2",
    "testProfile": "safe",
    "allowExploit": false,
    "maxDurationSeconds": 1200
  },
  "missingFields": [],
  "warnings": [],
  "confirmationToken": "server-signed-short-lived-token"
}
```

`status` 至少包括：

- `NEEDS_CLARIFICATION`：缺少目标、授权或关键限制；
- `NEEDS_CONFIRMATION`：草稿完整，等待用户确认；
- `REJECTED`：目标或要求违反策略；
- `READY`：兼容内部调用，表示可以进入确认流程。

该接口不能创建任务，不能启动执行，也不能返回任意 Skill 参数。这里的“无副作用”是指不产生渗透任务或执行动作；允许写入不含敏感原文的安全审计记录。

### 4.2 确认并创建任务

```http
POST /api/v1/task-agent/confirm
```

请求只携带 `draftId`、`confirmationToken`、`start` 和客户端幂等键。服务端必须从签名内容或 Redis 草稿记录中恢复原始结构化草稿，不能信任客户端重新提交的 `target`、`testProfile` 或 `allowExploit`。

确认令牌要求：

- 绑定当前用户 ID、`draftId`、草稿哈希和过期时间；
- 使用服务端密钥签名，默认 10 分钟失效；
- 确认成功后标记为已消费；
- 同一幂等键重试时返回同一个 `taskId`；
- 用户修改草稿后必须签发新的令牌。

响应返回现有 `taskId`，并可根据 `start=true` 调用已有的 `/api/v1/tasks/{task_id}/run`。

### 4.3 生命周期和查询

不新增重复接口，复用：

- `POST /api/v1/tasks/{task_id}/run`
- `POST /api/v1/tasks/{task_id}/stop`
- `POST /api/v1/tasks/{task_id}/resume`
- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/tasks/{task_id}/run-status`
- `GET /api/v1/tasks/{task_id}/trace`
- `GET /api/v1/tasks/{task_id}/report`

上层 Agent 只需要把这些接口封装成工具，不需要理解 Skill、MQ 或 workspace 细节。

## 5. 结构化模型

渗透领域模型建议放在 `supervisor/app/workflows/pentest/models.py`，Supervisor 通用模型放在 `supervisor/app/domain/models.py`，不要修改现有 `LLMDecisionResponse`。

```python
class TaskIntentDraft(BaseModel):
    name: str
    target: str
    description: str | None = None
    business_background: str | None = None
    extra_user_requirements: str | None = None
    test_profile: Literal["safe", "standard", "aggressive"] = "safe"
    allow_exploit: bool = False
    allow_destructive_actions: bool = False
    max_duration_seconds: int = Field(default=900, ge=60, le=86400)

class TaskIntentResponse(BaseModel):
    status: Literal["NEEDS_CLARIFICATION", "NEEDS_CONFIRMATION", "REJECTED", "READY"]
    draft: TaskIntentDraft | None = None
    missing_fields: list[str] = []
    warnings: list[str] = []
    explanation: str = ""
```

LLM 输出必须先经过 Pydantic 校验，再经过确定性策略校验。自然语言模型不得直接返回 `ActionItem`、`params`、Shell 参数或 `execution_kind`。

## 6. 最小代码改造面

### 阶段 A：策略和认证前置

预计改动：

1. Gateway 任务创建、启动、停止、恢复接口增加 `get_current_user` 和角色限制；至少要求 `OPERATOR`/`ADMIN`。
2. 增加一次性确认字段，避免自然语言 Agent 无确认直接启动高风险测试。
3. 对 `target` 做 URL/host/CIDR 规范化和显式授权校验。
4. 生产环境将用户字段注入策略从默认 `tag` 调整为 `reject` 或增加更严格的 Agent 专用清洗策略。
5. 所有查询、停止、恢复和报告工具都校验任务归属或管理员权限，不能只凭 `taskId` 操作他人任务。

现有底层目标校验仍保留：

- 编排器 `_is_target_allowed`；
- Executor `_is_target_allowed`；
- Nmap 的 `_apply_nmap_scope_guard`；
- 阶段门禁 `guard_next_phase`。

### 阶段 B：独立 Supervisor 和 Pentest Adapter

新增：

- 顶层 `supervisor/` 服务及其 Docker/Compose 配置；
- `supervisor/app/runtime/` 的 Supervisor LangGraph；
- `supervisor/app/workflows/base.py` 的 WorkflowAdapter 协议；
- `supervisor/app/workflows/pentest/` 的 PentestWorkflowAdapter；
- `supervisor/app/clients/gateway_client.py`；
- Gateway 到 Supervisor 的 `/api/v1/task-agent/*` 代理接口。

复用：

- `orchestrator/app` 的 LangGraph 依赖和状态图设计方式，但不直接复用 `trustguard_agent/graph_core.py` 的固定 Demo Plan；
- `orchestrator/app/core/prompt_injection_guard.py`、`correlation_ids.py`、`structured_error_envelope.py` 的设计，必要时抽取到共享包；
- Gateway 现有 `_orch()`、`_get_task_row()`、任务数据库写入逻辑；
- Gateway 现有 `_run_lifecycle()`。

不修改：

- `state_machine.py` 的规划和执行主循环；
- `execution_dispatcher.py`；
- Executor API；
- MQ Worker；
- Skill 镜像和 `docker/tools_registry.yaml`。

不建议直接 import：

- `orchestrator/app/core/state_machine.py`：它是渗透任务内部状态机，不是通用 Agent Runtime；
- `orchestrator/app/core/agent_tools.py`：包含 Skill 派发和工件回流副作用；
- `orchestrator/app/core/manager_agent.py`：是渗透 Todo，不是跨 Workflow Supervisor；
- `orchestrator/app/trustguard_agent/graph_core.py`：固定演示流程；
- `orchestrator/app/core/task_store.py`：持久化的是 pentest task，不是 Supervisor conversation/workflow run。

跨服务复用必须通过 HTTP/事件契约，或后续抽取小型 `packages/agent_runtime`；不要把 `orchestrator/app` 目录挂载到 Supervisor 镜像里运行。

### 阶段 C：风险策略进入状态机

如果需要严格保证 `scan_only` 不进入 EXPLOIT，应增加持久化的执行策略，而不能只把限制写进 `extraUserRequirements`。

建议把策略写入 `target_context` 并随 checkpoint 持久化：

```json
{
  "execution_policy": {
    "test_profile": "safe",
    "allow_exploit": false,
    "allow_destructive_actions": false
  }
}
```

然后在 `phase_transition_guard.guard_next_phase()` 的 EXPLOIT 分支增加：

```text
confirmed vulnerability
AND execution_policy.allow_exploit == true
```

为了兼容已有手工任务，建议先使用显式版本字段或配置开关，不直接改变所有旧任务的 EXPLOIT 语义。

### 阶段 D：前端自然语言入口

第一版只增加一个“自然语言创建任务”面板：

1. 输入自然语言；
2. 展示结构化草稿；
3. 展示授权、风险模式和目标范围警告；
4. 用户确认；
5. 调用现有任务列表和运行状态页面。

不重写现有 Dashboard、任务详情、Trace 和 Report 页面。

## 7. Supervisor LangGraph 和异步 SubAgent 生命周期

Supervisor 图建议固定为：

```text
classify_intent
  → resolve_workflow
  → collect_missing_fields
  → policy_check
  → request_confirmation
  → dispatch_workflow
  → observe_workflow
  → summarize
```

其中 `dispatch_workflow` 只调用 Adapter 的 `launch()`，不得在图节点内运行 15 分钟的渗透循环。`observe_workflow`可以由前端主动查询，也可以由后台事件消费者更新 `workflow_run_store`。

Supervisor 自己的会话状态和 pentest 的 `TaskState` 必须分开：

```text
SupervisorState
  conversation_id
  actor_id
  intent
  selected_workflow
  draft
  confirmation_state
  workflow_handle
  last_user_message
  response

Pentest TaskState
  task_id
  current_phase
  target_context
  plan / execution / artifacts
```

多轮会话存储：

```text
conversation_id
  → draft
  → missing_fields
  → warnings
  → confirmation_state
  → workflow_id
  → external_task_id
```

上层 Agent 不应把整个 pentest `target_context`塞进对话上下文，只读取下层返回的状态摘要、报告和必要的 Trace 片段。

会话 Agent 的工具只能是 Gateway 任务工具：

- `create_pentest_task`
- `start_pentest_task`
- `get_pentest_task`
- `stop_pentest_task`
- `resume_pentest_task`
- `get_pentest_trace`
- `get_pentest_report`

禁止开放：

- 任意 HTTP 请求工具；
- Docker Socket；
- Executor `/v1/execute`；
- 任意 Skill ID 和参数注入；
- 任意 workspace 文件写入。

## 8. 批量目标策略

当前 Executor 和任务状态主要围绕单个 `target` 设计，且 `_is_target_allowed()`按主机范围比较。

因此第一阶段对“扫描整个网段/一批 URL”的处理建议是：

- 解析出明确的授权目标列表；
- 每个主机创建一个独立任务；
- 上层 Agent 返回任务组 ID 和子任务列表；
- 不把 CIDR 直接作为普通 Web Skill 的 `target`。

只有在单目标流程稳定后，才考虑增加 `task_group`/批量调度模型。

## 9. 安全和审计要求

1. 草稿接口不得创建或启动任务；使用 Gateway 审计记录保存 `draftId`、用户、策略结果和草稿哈希，不写尚不存在的任务 Trace。
2. 确认接口记录确认人、时间、原始自然语言、结构化草稿哈希。
3. 所有 Agent 生成内容进入 `extraUserRequirements` 前必须经过长度限制和注入检测。
4. 上层 Agent 不得覆盖 `allowed_target`；该字段只能由服务端根据任务授权目标生成。
5. 进入 EXPLOIT 前必须同时满足漏洞确权和风险策略允许。
6. 任务停止、恢复和失败仍使用现有 checkpoint、TaskStore 和 Evidence 机制。
7. 报告中应区分“用户声明的授权信息”和“系统实际执行过的动作”。
8. 确认请求必须使用短期签名令牌和幂等键，防止草稿篡改与重复创建。
9. Agent 不得回显 API Key、认证头、环境变量和完整内部异常；日志中的自然语言原文应按长度截断并支持脱敏。
10. 目标规范化后再进行授权判断；域名任务应记录解析结果，防止确认后因 DNS 变化扩大到未授权地址。

## 10. 测试计划

### 单元测试

- 自然语言输出 JSON/Pydantic 解析；
- 缺少 target、授权、风险级别时的追问；
- URL、host、CIDR 规范化；
- 注入文本拒绝和截断；
- `safe` 模式禁止 EXPLOIT；
- 确认接口幂等；
- 草稿内容被篡改或令牌过期时确认失败；
- 不能从草稿注入 Skill 参数；
- 无权限用户不能创建或启动任务。

### 集成测试

1. draft 不产生 `tg_task` 记录；
2. confirm 只创建一次任务；
3. start 复用现有 `_run_lifecycle`；
4. 完整任务仍能走 `tick → PlanList → compile → Executor → Evidence`；
5. stop/resume/report 与已有接口行为一致。

### 回归验证

```bash
python3 scripts/smoke-inline.py
python3 scripts/demo-inline-agent.py
python3 -m compileall supervisor/app executor/app orchestrator/app gateway/app evidence/app scripts dev/mq
docker compose config
docker compose --profile skills config
pytest -q -c tests/pytest.ini tests/unit/supervisor tests/unit/orchestrator tests/unit/gateway
```

## 11. 实施顺序和验收标准

### Milestone 1：安全前置

- 任务 API 有认证和角色控制；
- 新增执行策略字段或兼容开关；
- 现有 UI 创建任务行为不回归。

### Milestone 2：自然语言草稿

- 用户输入自然语言可以得到稳定的结构化草稿；
- 缺少必要信息时不会创建任务；
- 草稿接口无执行副作用。

### Milestone 3：确认创建和启动

- 确认后复用现有 Gateway 任务创建和运行链路；
- 任务可在现有 UI 中查看、停止、恢复和出报告；
- Trace 能区分 Agent 草稿、用户确认和底层渗透执行。

### Milestone 4：Workflow Registry 和未来告警研判

- 注册表可以同时暴露 `pentest` 和 `alert-triage`；
- 告警研判拥有自己的 Draft、Policy、Adapter、结果模型；
- Supervisor 只根据 capability/intent 路由，不感知告警内部步骤；
- 不允许为了新增 Workflow 改写 pentest 状态机。

## 12. 建议的首批文件变更清单

首批实现只允许涉及以下文件或新增文件：

```text
supervisor/                                  # 新增独立控制面服务
docker-compose.yml                           # 新增 supervisor 服务和内网配置
gateway/app/main.py                          # 新增对 supervisor 的代理接口
gateway/app/security/auth.py                 # 复用/补充角色依赖
gateway/app/db.py 或 docker/mysql-init.d/    # 仅在执行策略需要持久化时修改
frontend/src/shared/lib/api.ts               # 新增 API 封装
frontend/src/features/...                    # 新增最小自然语言创建面板
contracts/workflows/                         # 可选：跨服务 JSON 契约
tests/unit/supervisor/...                    # Supervisor/Adapter 单元测试
tests/contracts/...                           # Workflow 契约测试
tests/unit/gateway/...                       # 确认、权限和幂等测试
```

以下目录在第一阶段禁止修改：

```text
executor/
skills/
orchestrator/app/core/state_machine.py      # 除非 Milestone 1 引入硬风险门禁
orchestrator/app/core/execution_dispatcher.py
evidence/
```

## 13. 当前 MVP 实现状态

截至当前分支，第一阶段已经完成：

- 新增独立 `supervisor/` 服务，使用内嵌 LangGraph 完成意图解析、字段校验和草稿生成；
- 新增 HMAC 草稿确认令牌，并绑定操作者、有效期和草稿摘要；
- Gateway 新增 draft/confirm 接口，确认后复用现有任务创建与 `_run_lifecycle`；
- 前端新增 `/agent` 对话页，展示可审计的推理摘要、阶段进度、工具活动和门禁事件；
- 权限相关字段只接受用户显式表述，LLM 不能自行确认授权、利用或破坏性权限；
- 默认 Pentest Workflow、Executor 和 Evidence 链路保持不变。

当前仍属于 MVP，后续迭代项：

- Conversation、Draft 和 Gateway 幂等映射当前为进程内存，服务重启后不会保留；
- `allow_exploit` 当前用于草稿风险提示和规则说明，尚未形成 Orchestrator 的独立持久化硬门禁；
- 破坏性权限不会下发执行器；
- 尚未实现 Redis 会话存储、Workflow Registry 和未来的 `alert-triage/` 服务。

前端所称“执行轨迹”仅包括结构化意图摘要、安全检查、阶段切换、工具调用和结果事件，
不保存或展示模型私有逐字思维链。
