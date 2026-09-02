# nomem schema spec

The canonical data models live in [`nomem/models.py`](../nomem/models.py). They carry no
behavior — they are the contract between the core pipelines, the backend adapters, and
application code.

## `Node`

| Field | Type | Meaning |
|---|---|---|
| `id` | `str` | UUID, assigned on CREATE |
| `user_id` | `str` | Owning user; backends must never leak across users |
| `type` | `str` | `"entity" | "event" | "emotion" | "fact"` or any custom string |
| `label` | `str` | Human-readable name of the thing |
| `embedding` | `list[float]` | Vector; length == embedder `dimensions` |
| `importance` | `float` | Decay score; updated only by `run_decay()` |
| `access_count` | `int` | Increments on each `retrieve()` that returns the node (not on ingest, unless `count_on_ingest`) |
| `last_accessed_at` | `datetime` | Updated on the same trigger as `access_count` |
| `created_at` | `datetime` | **Transaction time** — when nomem wrote this record |
| `valid_from` | `datetime` | **Event time** — when this version became true in the world |
| `valid_to` | `datetime | None` | `None` == currently active; set on RETIRE |
| `superseded_by` | `str | None` | Id of the node that replaced this one, if retired |
| `resolution_source` | `str` | `"llm" | "string_match" | "embedding" | "manual"` |
| `metadata` | `dict` | Dev-defined, stored as JSON |

## `Edge`

| Field | Type | Meaning |
|---|---|---|
| `id` | `str` | UUID |
| `user_id` | `str` | Owning user |
| `source_id` / `target_id` | `str` | Node ids (directed) |
| `relation` | `str` | `"caused_by" | "involves" | "follows"` or custom |
| `weight` | `float` | Reinforced on repeated `upsert_edge` |
| `created_at` | `datetime` | Transaction time |
| `valid_from` / `valid_to` | `datetime` / `datetime | None` | Event-time validity window |
| `superseded_by` | `str | None` | Replacement edge id, if retired |
| `metadata` | `dict` | Dev-defined |

## `SubGraph`

Returned by `retrieve()`. `nodes` / `edges` contain **only active records** by default;
pass `as_of=` to `retrieve()` for historical state. `metadata` carries at least
`hop_depth`, `retrieved_at`, `seed_node_ids`, `decay_scores`, `retrieval_mode`, and —
in hierarchical mode — `sub_index_used`.

## Bi-temporal semantics

nomem tracks two timelines:

* **Event time** (`valid_from` / `valid_to`) — when a fact was true in the world.
* **Transaction time** (`created_at`) — when nomem recorded it.

`retrieve(..., as_of=T)` returns the graph **as it was known at `T`**: records with
`created_at <= T` and whose validity window contains `T`.

**Hard deletes never happen.** Removal is always retirement: set `valid_to = now`, and
`superseded_by` when a replacement exists.

### Worked example: a supersession chain

```
2026-01-10  ingest: "Kira lives in Berlin"
    → CREATE node K  (label="Kira lives in Berlin", valid_from=2026-01-10, valid_to=None)

2026-03-02  ingest: "Kira moved to Lisbon"
    → RETIRE node K  (valid_to=2026-03-02, superseded_by=K2)
    → CREATE node K2 (label="Kira lives in Lisbon", valid_from=2026-03-02, valid_to=None)

retrieve("where does Kira live")                  → K2  (Lisbon)
retrieve("where does Kira live", as_of=2026-02-01) → K   (Berlin)
```

The Berlin fact is never lost — it is still queryable through `as_of`, and the chain
`K → superseded_by → K2` records the transition.
