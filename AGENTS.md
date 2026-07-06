# AGENTS.md

This file is for coding agents working in this repository. Human-facing documentation belongs in `README.md` and `docs/`.

## Project Shape

TrustGuard Agent is Docker-first. Keep the root `docker-compose.yml` as the full-stack entrypoint and preserve one top-level service directory per long-running container.

Main service directories:

- `frontend/`: React/Vite UI, nginx runtime container.
- `gateway/`: Python FastAPI public API.
- `orchestrator/`: Python task orchestration, state machine, and embedded LangGraph runtime.
- `executor/`: Python skill execution API and MQ worker code.
- `evidence/`: Python FastAPI trace/context/checkpoint APIs.
- `skills/`: buildable skill images, not long-running services.

## Operating Rules

- Standard commands should be Linux shell commands.
- Windows helpers must stay under `dev/win/`.
- Do not add additional `README.md` files. Use `AGENTS.md` for coding-agent notes.
- Do not add large AI-generated planning documents without review.
- Do not vendor third-party agent runtimes.
- Keep `.env`, `node_modules`, `dist`, `target`, `__pycache__`, and `.pytest_cache` out of git.

## Verification

Prefer the lightweight checks before broad test runs:

```bash
python3 scripts/smoke-inline.py
python3 scripts/demo-inline-agent.py
python3 -m compileall executor/app orchestrator/app gateway/app evidence/app scripts dev/mq
docker compose config
docker compose --profile skills config
npm --prefix frontend run build
```

Full Python unit suite, when dependencies are available:

```bash
pytest -q -m "not integration and not smoke"
```

Pytest configuration lives at `tests/pytest.ini`; use `pytest -c tests/pytest.ini ...` when a tool does not auto-discover it.

## Agent Runtime Policy

The default stack should use the first-party LangGraph runtime embedded in `orchestrator/` and native TrustGuard skill containers through `executor/`. Do not add compatibility layers that route the default compose path through a vendored third-party agent runtime.
