# Alert Triage Agent

## 概述

告警研判 Agent 是 TrustGuard 的安全告警自动化研判模块。它将 XDR 数据查询、RAG 知识增强和 LLM 结构化决策组合成可追溯的研判流程。

## 架构

```
Gateway (REST API)
  └─ POST /api/v1/alert-triage/tasks
       GET  /api/v1/alert-triage/tasks/{id}
       POST /api/v1/alert-triage/tasks/{id}/run
       GET  /api/v1/alert-triage/tasks/{id}/events
       GET  /api/v1/alert-triage/tasks/{id}/report

Orchestrator (Workflow Engine)
  └─ alert_triage.graph_core — LangGraph 8 节点状态图
       ├─ load_alert      → XDR API
       ├─ collect_evidence → XDR API (proof/assets/incidents)
       ├─ check_whitelist → XDR API
       ├─ query_rag       → trustguard-rag MCP v1
       ├─ make_decision   → LLM
       ├─ validate_decision
       ├─ persist_result
       └─ recommend_action
```

## 关键设计

- **MVP 安全边界**: 所有建议动作权限强制 `manual_confirm` 或 `forbidden`，不产生 `auto` 级动作
- **证据不足降级**: 缺失证据时置信度上限 0.5，确定性结论降级为 `suspicious`
- **RAG MCP 降级**: MCP 关闭、超时、契约不匹配或无命中时继续基于 XDR 原始证据研判，不阻塞流程
- **LLM 输出解析失败**: 兜底为 `insufficient_evidence`，不抛出异常
- **所有路径可追踪**: 每个节点产生 Trace Event

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `XDR_BASE_URL` | `http://localhost:18900` | XDR API 地址 |
| `XDR_API_KEY` | (空) | XDR API 认证密钥 |
| `XDR_SIGN_SECRET` | (空) | HMAC-SHA256 签名密钥 |
| `XDR_TIMEOUT` | `30.0` | XDR 请求超时 |
| `XDR_MAX_RETRIES` | `2` | XDR 重试次数 |
| `KNOWLEDGE_MCP_ENABLED` | `false` | 是否启用 trustguard-rag MCP |
| `KNOWLEDGE_MCP_URL` | `http://host.docker.internal:18201/mcp` | MCP 服务地址 |
| `KNOWLEDGE_MCP_ACCESS_TOKEN` | 空 | 部署环境注入的短期访问令牌 |

## API 端点

### 创建研判任务
```
POST /api/v1/alert-triage/tasks
{"alert_uuid": "alert-xxx", "enable_rag": true}
```

### 运行研判
```
POST /api/v1/alert-triage/tasks/{task_id}/run
```

### 查看结果
```
GET /api/v1/alert-triage/tasks/{task_id}
```

### 查看过程事件
```
GET /api/v1/alert-triage/tasks/{task_id}/events
```

### 查看研判报告
```
GET /api/v1/alert-triage/tasks/{task_id}/report
```

## 运行测试

```bash
# Schema 模型测试
PYTHONPATH=orchestrator/app pytest tests/unit/alert_triage/test_triage_schema_unit.py -v

# 图节点测试
PYTHONPATH=orchestrator/app pytest tests/unit/alert_triage/test_graph_nodes_unit.py -v --asyncio-mode=auto

# XDR 客户端测试
PYTHONPATH=orchestrator/app pytest tests/unit/alert_triage/test_xdr_client_unit.py -v --asyncio-mode=auto
```

## 完成情况
[✅] Gateway 创建告警研判任务
[✅] Agent 查询告警/proof/事件/资产/白名单
[✅] Agent 通过 trustguard-rag MCP 检索并保留 citation
[✅] 结构化 Schema (Pydantic)
[⚠️] 研判过程写入 Trace 和 Evidence                   ← Trace 有，Evidence 缺
[✅] 不暴露 ground truth
[✅] 证据不足输出 insufficient_evidence
[✅] RAG 不可用可降级
[✅] MVP 不自动执行高风险处置
[❌] 4 个场景端到端测试
[❌] 现有 Agent 冒烟测试不回归                         ← 未执行
[✅] Docker Compose 配置通过
[✅] 前端/API 可查看研判报告
[❌] README/docs 补充说明
