# TrustGuard Agent

## 单租户知识中心

当前版本按“一套部署服务一个组织”的单租户模式运行。知识库和文档在部署内共享：

- `ADMIN`、`OPERATOR`：创建、编辑和删除知识库，上传、改名、删除文档，处理同名文档冲突，创建或控制 RAG 数据采集任务，并管理 Knowledge Scope 与 Experience；
- `VIEWER`：知识问答、检索、文档与分块浏览，以及只读查看采集预置、任务和 Knowledge Scope；
- 浏览器只调用 Agent Gateway，Gateway 再代理到独立的 `trustguard-rag` 服务。

登录后可通过顶部“数据采集”进入 `/knowledge/collect`，选择 9 类 Agent 知识库预置或自定义采集。
Agent 发起的采集默认启用人工审核闸门：清洗结果先进入 RAG 暂存区，任务完成后由全局弹窗通知
`ADMIN` 或 `OPERATOR`，审核页面通过的数据才会进入受控入库队列，驳回数据不会写入知识库。

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
不会转发给 RAG；Gateway 会为所有 RAG 业务请求注入统一服务身份。Knowledge Scope 和
Experience 不需要额外的 JSON 令牌映射，也不应复用 MCP Access Token。

生产环境必须替换 `AUTH_TOKEN_SECRET`，并将 RAG 服务限制在内部网络。登录令牌格式已经升级为
带 HMAC-SHA256 签名和过期时间的令牌，升级后已有浏览器会话需要重新登录。

Gateway 中的知识能力按职责拆分：

- `app/security/auth.py`：登录令牌、当前用户和角色依赖；
- `app/api/knowledge.py`：知识库、文档、入库、问答、Scope 和 Experience 代理路由；
- `app/clients/rag_client.py`：RAG HTTP 调用、超时和错误映射；
- `app/schemas/knowledge.py`：知识接口请求模型；
- `app/db.py`、`app/audit.py`：数据库访问与审计记录。

Knowledge Scope 配置保存在 RAG 数据库，通过
`/api/v1/knowledge/scopes` 管理；Experience 通过
`/api/v1/knowledge/experiences` 访问。Agent Gateway 先校验登录 JWT 和角色，再使用固定的
Gateway 服务身份请求 RAG，并仅按白名单透传 `Idempotency-Key` 等必要元数据。

## RAG MCP Knowledge Search

Agent Orchestrator 通过协议无关的 `KnowledgeGateway` 调用 RAG MCP。默认关闭；可先以
Shadow 模式观察结果，也可读取 MCP Resource、写入任务本地 `chk-*`，再将知识命中注入
决策上下文：

```dotenv
KNOWLEDGE_MCP_ENABLED=true
KNOWLEDGE_MCP_SHADOW_MODE=false
KNOWLEDGE_MCP_MATERIALIZE_ENABLED=true
KNOWLEDGE_MCP_INJECT_ENABLED=true
KNOWLEDGE_MCP_MATERIALIZE_LIMIT=3
KNOWLEDGE_MCP_URL=http://host.docker.internal:18201/mcp
KNOWLEDGE_MCP_ACCESS_TOKEN=
KNOWLEDGE_MCP_PENETRATION_SCOPE=penetration
KNOWLEDGE_MCP_ALERT_TRIAGE_SCOPE=alert-triage
LEGACY_STATIC_KB_READ_ENABLED=false
LEGACY_EXPERIENCE_READ_ENABLED=true
```

Phase 3A 只支持由部署环境注入 `KNOWLEDGE_MCP_ACCESS_TOKEN`；Client Credentials
自动取 Token 和刷新尚未接入，因此当前不应直接启用生产流量。该 Token 必须是外部
身份服务签发的短期 JWT，不能复用 `RAG_GATEWAY_SERVICE_TOKEN` 或
`RAG_INTERNAL_SERVICE_TOKEN`。Shadow 成功时 Evidence Trace 会出现
`KNOWLEDGE_TRIGGERED`、`MCP_TOOL_CALLED`；
启用本地化和注入后还会出现 `KNOWLEDGE_MATERIALIZED`、`KNOWLEDGE_INJECTED`。
MCP Search 或 Resource Read 失败时保持 fail-open，不影响原渗透 Workflow。

`LEGACY_STATIC_KB_READ_ENABLED=false` 只停止 Agent 原静态知识检索，不会关闭
Experience 检索或执行后经验沉淀；`KB_ENABLED=false` 才会整体关闭旧 KB 客户端。
