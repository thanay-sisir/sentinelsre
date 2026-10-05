# SentinelSRE — Project Plan

Condensed working plan. Locked decisions: LangGraph (not openai-agents),
xAI Grok first provider (provider-agnostic config), no local Docker
(LocalProcessOpsBackend for dev; Harbor `-e langsmith` remote builds for eval),
scripted-agent offline mode included, git + GitHub Actions CI.

## Milestones

1. **Deterministic platform** — broken service can be created, observed,
   repaired, and independently verified. No LLM involved.
2. **Agentic workflow** — LangGraph performs the same process safely:
   typed tools, policy checks, approval, verification, rollback, reporting.
3. **Evaluation + observability** — Harbor reproduces and scores the incident
   in a LangSmith sandbox; LangSmith traces agent/model/tool activity.

## Phase checklist

- [ ] Phase 0 — scaffold, config, models, lifecycle, unit tests
- [ ] Phase 1 — demo platform + sre-ops + wrong-inventory-endpoint injector
- [ ] Phase 2 — OpsBackend, tools, policy engine, approvals, audit log
- [ ] Phase 3 — LangGraph nodes/graph, budgets, scripted mode, CLI, SQLite
- [ ] Phase 4 — LangSmith tracing + correlation
- [ ] Phase 5 — Harbor task + verifier + external agent + sandbox run
- [ ] Phase 6 — real benchmark run, eval summary, README finalization

Stretch: scenarios 2–3, ATIF trajectory, FastAPI service, LogHub pipeline.

## Risks

- LangSmith free-tier sandbox quota (~5 LCU/mo) → keep trial counts small;
  Daytona/Modal are documented Harbor fallbacks.
- Grok structured-output quirks → json_schema → function_calling → repair
  fallback chain in llm/structured.py; scripted mode covers CI.
- Harbor 0.23 API surface → verify flags/signatures against installed package.
