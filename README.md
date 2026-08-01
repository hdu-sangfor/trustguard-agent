# TrustGuard Agent

## 单租户知识中心

当前版本按“一套部署服务一个组织”的单租户模式运行。知识库和文档在部署内共享：

- `ADMIN`、`OPERATOR`：创建、编辑和删除知识库，上传、改名、删除文档，处理同名文档冲突，并创建或控制 RAG 数据采集任务；
- `VIEWER`：知识问答、检索、文档与分块浏览，以及只读查看采集预置和任务；
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
不会转发给 RAG；Gateway 会为所有 RAG 请求注入独立服务身份。

生产环境必须替换 `AUTH_TOKEN_SECRET`，并将 RAG 服务限制在内部网络。登录令牌格式已经升级为
带 HMAC-SHA256 签名和过期时间的令牌，升级后已有浏览器会话需要重新登录。

Gateway 中的知识能力按职责拆分：

- `app/security/auth.py`：登录令牌、当前用户和角色依赖；
- `app/api/knowledge.py`：知识库、文档、入库和问答路由；
- `app/clients/rag_client.py`：RAG HTTP 调用、超时和错误映射；
- `app/schemas/knowledge.py`：知识接口请求模型；
- `app/db.py`、`app/audit.py`：数据库访问与审计记录。
