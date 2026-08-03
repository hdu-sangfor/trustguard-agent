# Reasoning Trace（CoT）

为 Agent 任务记录可持久化、可回放的结构化推理步骤。本期范围仅后端契约与落库，不含「推理过程」面板与证据深链。

## Language

**Reasoning Trace（推理轨迹）**:
一次 Agent 任务对应的一条完整推理记录，由有序的 Reasoning Step 组成，用唯一 `trace_id` 标识。
_Avoid_: run、session、execution trajectory（后者指仓库里已有的运维级执行轨迹）

**Reasoning Step（推理步骤）**:
推理轨迹中的一个原子步骤，带 `step_id`、步骤类型、时间、耗时与执行状态。
_Avoid_: TraceEvent、log line、span（除非明确说在做 OTEL 适配）

**Execution Trace（执行轨迹）**:
仓库已有的运维级事件流（`TraceEvent` → Evidence `tg_trace_events`），记录技能/工具/编排事件，不是 Issue #136 的八种业务步骤类型。
_Avoid_: 推理轨迹、CoT

**本期切片 A**:
只交付 Reasoning Step 的数据模型、`trace_id`/`step_id`、八种步骤类型、持久化与按任务拉取；不做 SSE、不做前端「推理过程」面板与证据跳转。

**并行落库（已定）**:
Reasoning Step 用独立模型/表/API，与现有 Execution Trace（`TraceEvent` → `tg_trace_events`）并行；不改造、不投影混用。

**trace_id 与 task_id（已定）**:
一次任务对应一条 Reasoning Trace（1:1），且 `trace_id = task_id`。不做按 run 分轨迹（1:N）。
注意：Evidence 错误信封里的 `trace_id`（`err-…`）是排障关联号，与推理轨迹 `trace_id` 不是同一概念。

**落库位置（已定）**:
Reasoning Step 存在 Evidence 新表（及对应 ingest/list API）；Orchestrator 负责写出步骤。不放 Orchestrator 本地库，不做双写。

**写入策略（已定）**:
切片 A 在编排器决策/技能/RAG 等现有路径上挂**最小 emit**，覆盖已有语义能对应上的步骤类型；未接线的类型可暂缺或占位。不做「仅空契约」、也不强求八种类型本期全部硬接线。

**实时推送（已定）**:
切片 A 不做 SSE/WebSocket；只保证落库与按任务/轨迹拉取。推送留给前端面板迭代。

**步骤类型编码（已定）**:
库/API 使用稳定英文枚举码；中文名为展示标签（如 `label_zh`），不把中文当主键类型值。

本期合法集合（可扩展，见下）：
| 码 | 中文 |
|----|------|
| `TASK_UNDERSTANDING` | 任务理解 |
| `TASK_PLANNING` | 任务规划 |
| `RAG_RETRIEVAL` | RAG 检索 |
| `TOOL_CALL` | 工具调用 |
| `RESULT_OBSERVATION` | 结果观察 |
| `EVIDENCE_JUDGMENT` | 证据判断 |
| `REPLANNING` | 重新规划 |
| `FINAL_CONCLUSION` | 最终结论 |

**类型扩展余地（已定）**:
实现时把步骤类型放在**单一注册表/常量模块**，DB 用字符串列而非数据库 ENUM，避免改类型要做破坏性迁移；新增类型以改注册表 + 兼容读旧数据为主。切片 A 写入仍校验当前集合。

**脱敏（已定）**:
切片 A 写入路径即脱敏（emit 前或 ingest 时）；落库内容视为可对外展示的脱敏结果，不存明文敏感字段双轨。

**ReasoningStep 字段（已定，v1）**:
`trace_id`, `task_id`, `step_id`, `step_type`, `status`, `started_at`, `finished_at`, `duration_ms`, `summary`, `payload`。
证据引用本期放在 `payload` 可选键中，不单独建强类型证据表/顶层 `evidence_refs`。

**最小挂钩类型（已定）**:
本期实接线：`TASK_PLANNING`, `TOOL_CALL`, `RAG_RETRIEVAL`, `RESULT_OBSERVATION`, `FINAL_CONCLUSION`。
`REPLANNING` / `TASK_UNDERSTANDING` / `EVIDENCE_JUDGMENT` 本期不硬接（注册表仍保留，便于以后扩展）。

**trace_id 取值（已定）**:
`trace_id = task_id`（字符串相等）。不另建 `trace-*` 映射表。

**读 API（已定）**:
Gateway 代理对外：`GET /api/v1/tasks/{taskId}/reasoning-steps`；内部转 Evidence。写路径：Orchestrator → Evidence ingest。

## 实现备忘（切片 A）

- Evidence 表 `tg_reasoning_steps`；`POST /v1/reasoning-steps`；`GET /internal/tasks/{id}/reasoning-steps`
- Orchestrator：`emit_cot_step` + 五类挂钩；类型注册表 `app/reasoning_steps.py`
- 动态测试：`tests/dynamic/run_cot_reasoning_dynamic.py`
- 个人报告：`local-notes/trustguard-agent/cot-reasoning-dynamic-*.md`
