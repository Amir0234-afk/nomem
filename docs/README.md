# nomem docs

| Document | What it covers | Read it when |
|---|---|---|
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | **Current state** — phase status, what works, what's stubbed, deviations from AGENT.md, repo layout, how to run it | You want to know where the project is right now |
| [ROADMAP.md](ROADMAP.md) | **What's next** — Phase 4 (release) and Phase 5 (paid tier) tasks, known technical debt, 10 open design questions | You're planning the next step or reviewing direction |
| [TESTING.md](TESTING.md) | **The test suite** — how it's layered, the fakes, file-by-file breakdown of all 140 tests, coverage gaps | You want to understand what's verified and how |
| [architecture.md](architecture.md) | **How it's built** — module map, the ingest / retrieval / decay pipelines as diagrams, the sync model | You're reading or changing the code |
| [schema.md](schema.md) | **The data contract** — every `Node` / `Edge` / `SubGraph` field, bi-temporal semantics, a worked supersession example | You're working with the models or writing a backend |
| [adapters.md](adapters.md) | **Extending nomem** — how to implement a `BaseBackend`, `BaseEmbedder`, or `BaseLLM`; the built-in backend comparison | You're adding a storage/embedding/LLM adapter |

The project brief is [`../AGENT.md`](../AGENT.md) — the original design spec. Deviations
from it are catalogued in [PROJECT_STATUS.md §5](PROJECT_STATUS.md#5-deviations-from-agentmd-all-deliberate).

## TL;DR for a status review

- **Phases 0–3 of 5 are complete** (schema/interfaces, SQLite end-to-end, Postgres +
  decay + hierarchical retrieval, Neo4j + sync API).
- **3,761 lines** of implementation, **140 tests** (all green with services up;
  95 pass / 42 skip without), `ruff` + `mypy --strict` clean.
- All three backends (SQLite, PostgreSQL/pgvector, Neo4j) pass **one identical behavioral
  contract suite** — swapping backends doesn't change results.
- **Not done:** PyPI release + docs site + CI (Phase 4); the entire paid/Cloud tier
  (Phase 5); `OpenAIEmbedder` is the one stubbed adapter method; a few `IngestConfig`
  fields are declared but not wired.
- **Biggest open call:** whether v1 needs full transaction-time history or the current
  single-row model is enough (ROADMAP Q1).
