---
name: read_target_list
description: 读取工作区内纯文本 URL 列表文件，返回 urls 数组（不合并 raw 日志，避免截断丢失）。
metadata:
  runtime:
    requires:
      bins: []
---

# read_target_list

参数：`params.rel_path`（相对 `WORKSPACE_ROOT`）、可选 `params.max_urls`（默认 800，上限 5000）。

用于 `katana_urls.txt`、`clustered_targets.txt` 等；与 `read_workspace_artifact` 互补。
