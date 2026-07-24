# TrustGuard Agent

## 单租户知识中心

当前版本按“一套部署服务一个组织”的单租户模式运行。知识库和文档在部署内共享：

- `ADMIN`、`OPERATOR`：创建、编辑和删除知识库，上传、改名、删除文档，处理同名文档冲突；
- `VIEWER`：知识问答、检索、文档与分块浏览；
- 浏览器只调用 Agent Gateway，Gateway 再代理到独立的 `trustguard-rag` 服务。

配置 `.env` 后通过根目录 `docker compose` 启动 Agent。知识中心相关配置：

```dotenv
RAG_SERVICE_BASE_URL=http://host.docker.internal:18200
AUTH_TOKEN_SECRET=replace-with-a-random-long-secret
AUTH_TOKEN_TTL_SECONDS=86400
RAG_UPLOAD_MAX_BYTES=52428800
```

生产环境必须替换 `AUTH_TOKEN_SECRET`，并将 RAG 服务限制在内部网络，避免浏览器绕过
Gateway 直接调用知识库和文档写接口。登录令牌格式已经升级为带 HMAC-SHA256 签名和过期时间
的令牌，升级后已有浏览器会话需要重新登录。
