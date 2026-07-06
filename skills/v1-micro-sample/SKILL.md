---
name: v1-micro-sample
description: V1 样板 Micro-Skill：Pydantic 入参、micro_executor SDK、落盘 parsed.json、stderr S-06、stdout Agent 摘要 JSON（嵌入 raw_stdout）。
metadata:
  runtime:
    requires:
      bins:
        - python3
---

# v1-micro-sample

演示 **S-01 / S-02 / S-03 / S-06**：无宿主机高危命令拼接；容器内仅写工作区与标准协议行。

## 构建镜像（仓库根目录）

```bash
docker build -f skills/v1-micro-sample/Dockerfile -t trustguard-skill-v1-micro-sample:latest .
```

或 `docker compose --profile skills build skill-v1-micro-sample`。

## 参数（`params`）

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| note | str | ok | 简短标记，写入工件与摘要 |
