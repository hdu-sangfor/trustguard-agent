# TrustGuard Agent

## 单租户知识中心

当前版本按“一套部署服务一个组织”的单租户模式运行。知识库和文档在部署内共享：

- `ADMIN`、`OPERATOR`：创建、编辑和删除知识库，上传、改名、删除文档，处理同名文档冲突；
- `VIEWER`：知识问答、检索、文档与分块浏览；
- 浏览器只调用 Agent Gateway，Gateway 再代理到独立的 `trustguard-rag` 服务。

配置 `.env` 后通过根目录 `docker compose` 启动 Agent。知识中心相关配置：

```dotenv
RAG_SERVICE_BASE_URL=http://host.docker.internal:18200
RAG_GATEWAY_SERVICE_TOKEN=replace-with-a-different-long-random-service-token
AUTH_TOKEN_SECRET=replace-with-a-random-long-secret
AUTH_TOKEN_TTL_SECONDS=86400
RAG_UPLOAD_MAX_BYTES=52428800
```

`RAG_GATEWAY_SERVICE_TOKEN` 必须与 RAG 仓库中的同名配置一致，并与
`RAG_INTERNAL_SERVICE_TOKEN` 使用不同随机值。浏览器登录 Token 只在 Agent Gateway 校验，
不会转发给 RAG；Gateway 会为所有 RAG 请求注入独立服务身份。

生产环境必须替换 `AUTH_TOKEN_SECRET`，并将 RAG 服务限制在内部网络。登录令牌格式已经升级为
带 HMAC-SHA256 签名和过期时间的令牌，升级后已有浏览器会话需要重新登录。

Gateway 中的知识能力按职责拆分：

- `app/security/auth.py`：登录令牌、当前用户和角色依赖；
- `app/api/knowledge.py`：知识库、文档、入库和问答路由；
- `app/clients/rag_client.py`：RAG HTTP 调用、超时和错误映射；
- `app/schemas/knowledge.py`：知识接口请求模型；
- `app/db.py`、`app/audit.py`：数据库访问与审计记录。

## RAG MCP Shadow Search

Agent Orchestrator 通过协议无关的 `KnowledgeGateway` 调用 RAG MCP。Phase 3A
默认关闭，开启后与原 Agent KB 并行检索，只记录 Trace，不把 MCP 命中注入 Plan：

```dotenv
KNOWLEDGE_MCP_ENABLED=true
KNOWLEDGE_MCP_SHADOW_MODE=true
KNOWLEDGE_MCP_URL=http://host.docker.internal:18201/mcp
KNOWLEDGE_MCP_ACCESS_TOKEN=
KNOWLEDGE_MCP_SCOPE=penetration
```

Phase 3A 只支持由部署环境注入 `KNOWLEDGE_MCP_ACCESS_TOKEN`；Client Credentials
自动取 Token 和刷新尚未接入，因此当前不应直接启用生产流量。该 Token 必须是外部
身份服务签发的短期 JWT，不能复用 `RAG_GATEWAY_SERVICE_TOKEN` 或
`RAG_INTERNAL_SERVICE_TOKEN`。Shadow 成功时 Evidence Trace 会出现
`KNOWLEDGE_TRIGGERED`、`MCP_TOOL_CALLED`；失败时记录 `MCP_TOOL_FAILED` 并
fail-open，不影响原渗透 Workflow。
