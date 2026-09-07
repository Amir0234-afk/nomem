# Contributing

Thanks for looking. nomem is a small, opinionated library, so the fastest way to get a
change merged is to know which of its rules are load-bearing before you write code.

## Read first

- [`docs/stability.md`](docs/stability.md) — what is public, what is internal, and what
  `nomem>=0.1,<0.2` guarantees. **A change to anything in the Public table is a breaking
  change**, regardless of how small the diff is.
- [`AGENT.md`](AGENT.md) — the design brief, including "What an Agent Must Not Do".
- [`docs/roadmap.md`](docs/roadmap.md) — what is deliberately unbuilt, the known
  technical debt, and which design questions are still open.

## Setup

```bash
uv sync --extra postgres --extra neo4j
ollama pull nomic-embed-text && ollama pull llama3.1:8b   # for `live` tests
docker compose up -d                                      # pgvector + neo4j
```

### Environment

| | |
|---|---|
| Python | `>=3.11` (developed on 3.11; CI covers 3.11–3.13) |
| Package manager | `uv` |
| Build backend | `hatchling` |
| Core runtime deps | **none** |
| Optional extras | `postgres` (`asyncpg>=0.29`, `pgvector>=0.3`), `neo4j` (`neo4j>=5.20,<7` — the cap admits the 6.x the suite runs against and stops 7.x arriving unannounced) |
| Dev deps | `pytest>=8.2`, `pytest-asyncio>=0.23`, `ruff>=0.5`, `mypy>=1.10` |
| Lint | `ruff` — E, F, I, UP, B, SIM, RUF; line length 100 |
| Types | `mypy --strict` + `warn_unreachable`; `asyncpg.*`/`pgvector.*`/`neo4j.*` set `ignore_missing_imports` |

### Test containers (dedicated, throwaway)

`docker compose up -d` starts two containers on **non-default ports**, so they cannot
collide with any Postgres or Neo4j already on your machine:

- **`nomem-postgres`** — `pgvector/pgvector:pg16` on `localhost:5433`, `nomem/nomem`,
  ephemeral (tmpfs). DSN: `postgresql://nomem:nomem@localhost:5433/nomem`
- **`nomem-neo4j`** — `neo4j:5.26`, bolt on `localhost:7688`, HTTP on `localhost:7475`,
  auth `neo4j/nomemtest123`, ephemeral (tmpfs)

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

## The test suite

Four tiers. Everything gated by an env var **skips cleanly** when the service is absent,
and the markers let you deselect explicitly (`-m "not postgres and not neo4j"`).

| Layer | Needs | Marker |
|---|---|---|
| **Hermetic** — fakes for embedder + LLM, SQLite `:memory:` | nothing | *(none)* |
| **Live (Ollama)** — real `nomic-embed-text` + `llama3.1:8b` | local Ollama with both models | `live` |
| **Postgres** — real `pgvector` | `NOMEM_TEST_POSTGRES_DSN` + `asyncpg`/`pgvector` importable | `postgres` |
| **Neo4j** — real Neo4j 5 | `NOMEM_TEST_NEO4J_URI` (+ `NOMEM_TEST_NEO4J_AUTH`) + `neo4j` importable | `neo4j` |

```bash
uv run pytest -q -m "not live and not postgres and not neo4j"   # hermetic only
uv run pytest -q -m "not postgres and not neo4j"                # + live Ollama
uv run pytest -q                                                # everything
uv run pytest -q tests/test_backend_contract.py -k neo4j        # one backend
```

### The fakes (`tests/conftest.py`)

| Fake | Behavior |
|---|---|
| `FakeEmbedder(dims=24)` | Deterministic bag-of-words hash → 24-dim unit vector. Identical text → cosine 1.0; shared tokens → high cosine; disjoint → ~0. Enough structure for resolution and routing assertions without a model. |
| `FakeLLM(response)` | A canned `dict` **or** a `prompt -> dict` callable. Records every prompt in `.calls`, so a test can script exactly what "the model extracted". |
| `make_graph(...)` fixture | In-memory `MemoryGraph` wired to both fakes with `backend_options={"path": ":memory:"}`. Accepts `llm_response=`, `decay=`, `retrieval_config=`, and so on. |

Live tests build a real `MemoryGraph(embedder="nomic", llm="ollama")` instead.

**A scripted fake proves less than it looks.** Negation was covered only by handing the
pipeline `negated: True` through `FakeLLM`, which proved the CRUD layer retires correctly
and nothing about whether extraction ever asks it to — and it did not, on the default
model, until the prompt was fixed. If you change extraction behavior, add a `live` test.

### Coverage gaps, known and accepted

- No load or performance test; the SQLite linear vector scan is untested at scale.
- `_http.py` network error paths are marked `# pragma: no cover`. The OpenAI embedder is
  tested through a stubbed transport, so its own transport is exercised only as far as the
  arguments it passes.
- The `live` tier does not run in CI — it needs a local model server.
- The concurrency test is 20 writes; there is no sustained read/write interleaving stress.
- Nothing exercises a *third-party* backend implementing the ABC from scratch, so a gap
  between "the contract as documented" and "as the bundled three happen to behave" would
  go unnoticed.

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
- **Do not add to the MIT core a feature the commercial tier is meant to sell.**
  `list_nodes` / `list_edges` are the deliberate exception: the plugin model cannot work
  without enumeration, and the consequence — a DIY graph dump is about thirty lines — was
  accepted knowingly, not overlooked. See
  [the honest tension](docs/roadmap.md#the-honest-tension-in-shipping-export-first).

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
- Update the docs in the same PR. `docs/` is treated as part of the code.
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
