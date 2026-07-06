---
name: read_workspace_artifact
description: Read raw workspace artifact files by artifact_ref/path for PoC verification and false-positive validation.
metadata:
  runtime:
    emoji: "📄"
    requires:
      bins:
        - python3
---

# Read Workspace Artifact

Read raw files from workspace (for example nuclei `chunk_0001.jsonl`, `raw.out`, `raw.err`) using `artifact_ref` or workspace-relative path.

This skill is intended for evidence inspection when structured summaries are insufficient.
