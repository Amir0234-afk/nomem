# Contributing

Thanks for looking. nomem is a small, opinionated library, so the fastest way to get a
change merged is to know which of its rules are load-bearing before you write code.

## Read first

- [`docs/stability.md`](docs/stability.md) — what is public, what is internal, and what
  `nomem>=0.1,<0.2` guarantees. **A change to anything in the Public table is a breaking
  change**, regardless of how small the diff is.
- [`AGENT.md`](AGENT.md) — the design brief, including "What an Agent Must Not Do".
- [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) — current state, and what is
  deliberately unbuilt.

## Setup

```bash
uv sync --extra postgres --extra neo4j
ollama pull nomic-embed-text && ollama pull llama3.1:8b   # for `live` tests
docker compose up -d                                      # pgvector + neo4j
```

## The gate

Every change must pass all three:

```bash
uv run ruff check .
uv run mypy nomem
uv run pytest -q -m "not live and not postgres and not neo4j"   # fast, hermetic
```

Before opening a PR that touches a backend, run the full suite too:

```bash
export NOMEM_TEST_POSTGRES_DSN=postgresql://nomem:nomem@localhost:5433/nomem
export NOMEM_TEST_NEO4J_URI=bolt://localhost:7688
export NOMEM_TEST_NEO4J_AUTH=neo4j:nomemtest123
uv run pytest -q
```

CI runs the hermetic suite on 3.11–3.13 and the full backend suite against real Postgres
and Neo4j. Ollama-backed `live` tests are manual.

## Rules that are not up for negotiation

These exist for reasons documented in `AGENT.md` and `docs/`. A PR that violates one will
be asked to change, not debated:

- **No hard deletes in the graph API.** Removal is retirement via `valid_to`.
  `BaseBackend.purge_user` is the single gated exception and must never become reachable
  from `MemoryGraph` or `GraphCRUD` — `tests/test_no_hard_delete.py` enforces this.
- **No self-scheduled background work.** Expose a method; let the developer call it.
- **No hardcoded backend, embedder, model, or threshold.** Everything comes from config.
- **No silent behavior.** Ambiguous resolutions, dropped relations, and trimmed retrievals
  are all reported in the returned object. Do not swallow ingest failures.
- **No telemetry, phone-home, or usage tracking.** Not behind a flag, not opt-in.
- **No new mandatory runtime dependency.** The core installs with nothing. New integrations
  go behind an extra, or use the standard library via `nomem/_http.py`.

## Adding a backend, embedder, LLM, or plugin

You do not need to modify this repository to do any of these — that is the whole point of
the adapter and plugin boundaries. See [`docs/adapters.md`](docs/adapters.md).

If you find something that *cannot* be built through an adapter, a plugin, or config, that
is a gap in the public surface and worth an issue. The fix belongs here, in the open core.

## Backend changes

All three bundled backends share one behavioral contract suite
(`tests/test_backend_contract.py`). A change to backend behavior means:

1. Add or change the shared test — it runs against all three automatically.
2. Make all three pass. Divergence between backends is the thing the suite exists to
   prevent.
3. Reuse `nomem/backends/_common.py` (`active_at`, `bfs_subgraph`) and
   `nomem/core/decay.py::score_node` rather than reimplementing the semantics.

## Commits and PRs

- One logical change per PR; keep the diff reviewable.
- Say what changed and why in the description. Link the issue if there is one.
- Update the docs in the same PR. `docs/` is treated as part of the code, and
  `docs/PROJECT_STATUS.md` is the snapshot people trust.
- Add a `CHANGELOG.md` entry under `[Unreleased]` for anything user-visible.

## Reporting bugs

Include the nomem version, the Python version, the backend, and a minimal reproduction —
ideally against `sqlite` with `:memory:`, `CallableEmbedder`, and `CallableLLM`, so it runs
with no external services.

Behavior of anything documented as internal in `docs/stability.md` — `nomem/core/`, any
`_`-prefixed module, prompt text, or a backend's storage layout — is not covered by the
stability guarantee, and a change to it is not a regression.

## Security

Do not open a public issue for a security problem. Contact the maintainer directly.

## Licence

Contributions are accepted under the MIT licence that covers the core
([`LICENSE`](LICENSE)).
