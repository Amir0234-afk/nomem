# nomem docs

| Document | What it covers | Read it when |
|---|---|---|
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | **Current state** — phase status, what works, what's stubbed, deviations from AGENT.md, repo layout, how to run it | You want to know where the project is right now |
| [ROADMAP.md](ROADMAP.md) | **What's next** — Phase 4 (release) and Phase 5 (paid tier) tasks, known technical debt, 10 open design questions | You're planning the next step or reviewing direction |
| [TESTING.md](TESTING.md) | **The test suite** — how it's layered, the fakes, file-by-file breakdown of all 178 tests, coverage gaps | You want to understand what's verified and how |
| [architecture.md](architecture.md) | **How it's built** — module map, the ingest / retrieval / decay pipelines as diagrams, the sync model | You're reading or changing the code |
| [schema.md](schema.md) | **The data contract** — every `Node` / `Edge` / `SubGraph` field, bi-temporal semantics, a worked supersession example | You're working with the models or writing a backend |
| [adapters.md](adapters.md) | **Extending nomem** — how to implement a `BaseBackend`, `BaseEmbedder`, or `BaseLLM`, and how plugins attach; the built-in backend comparison | You're adding a storage/embedding/LLM adapter, or writing a plugin |
| [stability.md](stability.md) | **The public API** — what's stable, what's internal, what `nomem>=0.1,<0.2` guarantees, and the three sanctioned ways to extend | You're depending on nomem, or about to change something in it |

The project brief is [`../AGENT.md`](../AGENT.md) — the design spec, which now describes
the shipped design and the frozen `0.1.0` contract; the remaining deliberate differences
are catalogued in
[PROJECT_STATUS.md §5](PROJECT_STATUS.md#5-deviations-from-agentmd-all-deliberate).
The release plan is [`../phases/PHASE_4.md`](../phases/PHASE_4.md).

## TL;DR for a status review

- **Phases 0–3 of 5 are complete** (schema/interfaces, SQLite end-to-end, Postgres +
  decay + hierarchical retrieval, Neo4j + sync API), and so are **Phase 4 Parts A and B**
  (the frozen extension surface; the dangling config).
- **4,266 lines** of implementation, **178 tests** (all green with services up;
  123 pass / 52 skip without), `ruff` + `mypy --strict` clean.
- All three backends (SQLite, PostgreSQL/pgvector, Neo4j) pass **one identical behavioral
  contract suite** — swapping backends doesn't change results.
- **Not done:** Phase 4 Part C — PyPI release, `0.1.0` version bump, quickstart examples,
  CHANGELOG, CI — and the entire paid tier (Phase 5). Every adapter method is implemented
  and no config field is inert. See [ROADMAP.md](ROADMAP.md).
- **Highest stakes left:** the extension surface is now built (14-method `BaseBackend`,
  `nomem/plugins.py`) but freezes for good when `0.1.0` publishes. The paid tier is a
  *separate package* depending on the public core, so anything it needs must be public
  first, or it forks.
- **Biggest still-open call:** whether v1 needs full transaction-time history or the
  current single-row model is enough (ROADMAP Q1) — a `0.2.0` change either way.