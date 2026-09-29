# [41] Historical Anchor Backfill

2026-09-28 plan；这是一次**存量数据迁移**：为已有 live 记忆补齐 `metadata.anchors`。分阶段执行——先在 `remember-me` 小批量自动试跑，再放量该 namespace，最后扩展到其余 namespace（含 `atos-next-master`）。不引入在线 anchor 生成，也不设逐条人工审核门槛。

## Goal and decisions

- Migrate existing live memories by backfilling `metadata.anchors`, without adding generation to online MCP/admin write paths.
- Generate anchors with the existing DeepSeek `opencode run` workflow and write valid outputs automatically; no per-record approval or manual review is required.
- Scope is staged, but the migration target is all namespaces: Stage 1 = a small automatic pilot batch in `remember-me`; Stage 2 = the remaining eligible `remember-me` records; Stage 3 = all other live namespaces. Measured baseline: 1074 live records, 1068 without anchors (`atos-next-master` 938, `remember-me` 125, `atos-next` 3, `待报销` 2).
- Eligible records are live records with missing/empty `metadata.anchors`; never overwrite existing non-empty anchors. An explicit empty array (`[]`) is treated the same as missing.
- Extract 0–5 distinctive retrieval cues that could plausibly appear in a future query for this memory. They may be literal names, classes, APIs, error codes, config keys, versions, and identifiers, or grounded aliases, abbreviations, and likely query wording that do not literally occur in the text. Every anchor must be supported by the memory's meaning; do not invent facts or add generic topic labels. Preserve canonical spelling for identifiers. Records with no useful, well-grounded cue remain unchanged.
- Definition of done: every eligible record has a recorded outcome (`applied`, `skip`, or `error`) in retained manifests. This is **not** a requirement that every record ends with anchors.
- Use a small pilot to validate the automated path and inspect aggregate coverage/search behavior before expanding batches; per-record manual acceptance is not a rollout gate.
- Acceptance requires before/after reconciliation. A rollback path is **not** required.
- Keep this maintenance task separate from [40] retrieval benchmark and any future online anchor generation.

## Proposed CLI and data flow

Add `bin/manage migrate anchors` with explicit `draft` and `apply` stages, backed by a new offline CLI module. The manifest is an audit/restart artifact, not a human approval queue:

1. **Draft** selects records without anchors from the read-only SQLite primary. Each LLM call receives only one memory's content (or a deliberately small context); after each response, the migration appends its ID, content hash, and generated anchors to a temporary JSONL file. It loops until all records in this batch are processed. The DB remains unchanged during generation.
2. Each manifest row contains `memory_id`, namespace, content hash, a bounded text preview, `proposed_anchors` (string array), `anchor_notes` (one entry per anchor with a grounding rationale and plausible example query), status (`generated`, `skip`, or `error`), and an optional error. A rationale may point to supporting meaning in the memory without requiring the anchor to be a literal quote. Do not copy complete memory text into the manifest.
3. The operator runs `apply` on the generated manifest; row-by-row edits or approval status are not required. An empty candidate list is recorded as `skip` and not written.
4. **Apply** starts only after generation for the whole batch is complete. Before writing, it rereads every record and checks that it is live, remains in the requested namespace, still has no non-empty anchors, and its content hash matches the manifest. Any conflict aborts the batch before writes; nothing is force-overwritten.
5. Apply writes all generated `metadata.anchors` in one SQLite transaction, preserving each `updated_at`. The transaction updates metadata, anchor relations, Anchor FTS, and dual outbox versions; any failure rolls the whole batch back. After commit, the existing dual sync worker copies the outbox changes to the JSONL replica.

Support `--namespace`, `--manifest`, `--limit`, `--model`, `--cli`, and `--timeout`; default namespace is `remember-me`. Draft supports `--dry-run` to count eligible rows without model calls. Apply requires the service to be stopped and relies on the existing primary writer lock. One manifest is one transaction batch; rollout can use separate manifests for pilot and subsequent scopes. The manifest may contain project memory excerpts and remains local, ignored, and operator-managed.

## Draft stage specification (frozen 2026-09-28)

Frozen contract for `bin/manage migrate anchors draft`. It is read-only: no SQLite writes, no primary writer lock, no embeddings, no NDJSON. It is safe to run while the service is up.

**Command surface.** `bin/manage migrate anchors draft [options]`; `bin/manage` injects `--db "$db"` when absent, matching `migrate keywords` / `migrate categories`. Generated candidates are applied with `bin/manage migrate anchors apply --manifest <path>`; manual manifest editing is optional, not required.

| Option | Default | Notes |
|---|---|---|
| `--namespace` | `remember-me` | Target namespace. |
| `--limit` | unset (all eligible) | Max eligible records to draft. |
| `--batch-size` | `10` | Memories per `opencode run` call. The CLI pays a large fixed startup cost per call, so batching amortizes it (~8x faster); results are matched back by id. |
| `--manifest` | `data/anchor-backfill/draft-<UTC>.jsonl` | Output path. |
| `--exclude-manifest` | unset (repeatable) | Skip memory_ids already recorded in the given manifests. The manifest is the ledger for disjoint batching: the next batch is eligible DB ids minus already-drafted ids, so apply can be deferred without overlap. |
| `--model` | `opencode-go/deepseek-v4-flash` | Reuses the existing migration default. |
| `--cli` | `opencode` | Reuses the existing migration default. |
| `--timeout` | `120.0` | Per-record model call timeout. |
| `--dry-run` | off | Counts eligible rows only; no model calls, no manifest. |
| `--db` | injected by `bin/manage` | SQLite primary path. |

**Record selection.** `deleted=0`, `namespace == --namespace`, `metadata.anchors` missing / `null` / `[]` (empty array is eligible). Order by `updated_at DESC` (most recently updated first), take the first `--limit`. Records with non-empty anchors are never selected.

**Model calls.** Strictly serial, but batched: one `opencode run` call handles up to `--batch-size` (default 10) memories and returns one JSON object per memory (`{"id", "anchors", "anchor_notes"}`), matched back by id. Rationale: each call pays a fixed ~30s startup cost, so batching 10 memories per call is ~8x faster (measured). Prompt contract: propose 0–5 distinctive terms a future user might use to find this memory. Literal identifiers are useful, but grounded aliases, abbreviations, and natural query wording are allowed even when absent verbatim from the text. For each candidate return the anchor, a concise rationale tied to this memory, and one plausible future query. Do not invent facts or return generic topics/keywords; preserve canonical spelling of proper identifiers. After each call, append and flush that call's rows to the temporary manifest before starting the next call. A per-call failure (timeout, non-zero exit, unparseable output) marks every memory in that call `error`; an id absent from a successful response is also `error`. The batch cannot be applied until every selected memory has a successful `generated` or `skip` result.

**Candidate validation.** Validate anchor strings with `normalize_anchors` (type, non-empty, ≤128 chars, dedupe, ≤5). Do not require a literal substring match: an anchor is a retrieval cue and may be a grounded synonym or likely query term. Retain the model's rationale and example query as audit context; the model prompt asks it to avoid unsupported guesses and generic terms. If no candidate remains, the draft sets `status=skip` (no useful anchors); otherwise `status=generated`.

**content_hash.** `sha256` of the memory `text` as stored (UTF-8 bytes), hex; `content_hash_algo = "sha256/text-utf8"`. Text only — namespace and metadata are excluded. Apply uses it to detect a text change between generation and write.

**Manifest.** JSONL, UTF-8, one record per line, fixed field order:
`type, schema_version, memory_id, namespace, content_hash, content_hash_algo, text_preview, proposed_anchors, anchor_notes, status, error, model, cli, drafted_at`.
`status` ∈ `{generated, error, skip}`. Rows are appended and flushed incrementally. The manifest starts with a header containing selected count and a digest of selected IDs, then one row per selected memory, and ends with a completion trailer only if every row is `generated` or `skip`. Any `error` or interruption leaves it incomplete and not applicable. `text_preview` is a bounded normalized prefix (≤500 chars); full memory text is never stored.

**Manifest wire format (v2, frozen 2026-09-28).** `schema_version = "anchor-manifest/2"`. Every line is tagged with `type`: exactly one `header` (first line), one `row` per selected memory, one `trailer` (last line, present only when complete). `ids_digest = "sha256:" + sha256("\n".join(sorted(selected_memory_ids))).hexdigest()`. Each `generated` row carries one `{anchor, rationale, query}` note per anchor.

**Idempotency.** Draft may resume an incomplete manifest by skipping IDs already completed and retrying error rows; it writes the completion trailer only when selected IDs are all accounted for. A different `--manifest` is a new batch.

**Batching.** Because apply is deferred, the eligible set does not shrink on its own. Draft disjoint batches by passing prior manifests via `--exclude-manifest`; the next batch is `eligible DB ids minus already-drafted ids`. Each batch keeps its own manifest and stays uniformly "not yet applied" at apply time.

**Output and exit.** stdout reports eligible / drafted / error / skip counts and the manifest path. Exit 0 on success; non-zero if every processed record errored (e.g. the model is unavailable). `--dry-run` prints the eligible count without model calls or manifest writes.

**Draft acceptance.** Zero SQLite writes (`updated_at` and DB content unchanged, no writer lock acquired); manifest row count equals processed eligible count; `proposed_anchors` passes `normalize_anchors`, and `anchor_notes` has exactly one rationale and plausible query for each anchor; records with existing non-empty anchors are untouched.

## Apply stage specification (frozen 2026-09-28)

Frozen contract for `bin/manage migrate anchors apply`. It is the only writing stage: it acquires the primary writer lock and therefore requires the service to be stopped. It never calls the model.

**Command surface.** `bin/manage migrate anchors apply --manifest <path> [--namespace remember-me] [--dry-run] [--db ...]`.

**Preconditions (fail fast, zero writes).** The manifest exists and is readable; validate the entire file first — `schema_version` must equal `anchor-manifest/2`, each line must carry a `type` of `header|row|trailer`, the completion trailer and selected-ID digest/count must match, all selected IDs must have exactly one row, every row must carry required fields, statuses must be `generated|skip`, and IDs must be unique. Any incomplete/malformed manifest aborts before writes. The primary writer lock must be free (acquired for the run); a held lock fails immediately. `--dry-run` performs validation and stale-record checks but writes nothing and takes no lock.

**Processing.** All `generated` rows form one transaction batch; `skip` rows remain unchanged. Before beginning the transaction, validate all anchor arrays and re-read every generated record. If any record is missing/deleted, its namespace or content hash changed, or it already has non-empty anchors, abort the entire batch with zero writes. Identical anchors already present are an idempotent no-op only when every generated row in the batch is already applied; mixed already-applied/new state is a conflict and aborts. Then update every row in a single SQLite transaction. Any exception rolls back all rows.

**Write semantics.** Add a storage-level batch operation for anchors; do not loop over `update_metadata`, because it commits each row independently. The operation preserves `updated_at`, updates relation indexes and Anchor FTS, marks outbox versions, and commits once. No anchor vectors are created. Existing dual sync then copies the committed outbox rows to JSONL; sync failure remains pending for the existing retry/health path.

**Output and reconciliation.** Per-row generation outcomes are in the draft manifest; apply writes one batch outcome record to `data/anchor-backfill/apply-<UTC>.jsonl`. Stdout reports generated/skipped counts, applied row count, transaction outcome, before/after `anchor_total` and `memories_with_anchors`, and dual replica health. A manifest is fully applied or not applied; partial DB application is forbidden.

**Exit code.** Non-zero if the manifest is invalid, the writer lock is unavailable, or any `failed:*` occurs; skipped conflicts alone still exit 0 (they are reported).

## Rollout and acceptance

- Stage 1 (pilot): dry-run, draft, and automatically apply a small batch in `remember-me`; retain draft and apply manifests as audit artifacts.
- Run apply against each completed manifest with the service stopped. Record eligible, generated, skipped, failed, transaction, and replica-sync outcomes.
- Reconcile before/after: eligible count, processed count by outcome, applied rows matched against the manifest, and coverage delta (`anchor_total` / `memories_with_anchors`). Reconciliation is the acceptance evidence; rollback is not required.
- Verify `updated_at` is unchanged, Anchor Search finds generated anchors, no anchor vectors are created, and SQLite/NDJSON dual health is clean after restart.
- Stage 2: after pilot reconciliation and aggregate search behavior are acceptable, process the remaining eligible `remember-me` records with the same automated flow.
- Stage 3: expand to all other live namespaces, including `atos-next-master`. Scope is already decided; execute each namespace as its own batch with its own reconciliation.

## Tests

- Candidate parsing/validation: malformed model output, non-string values, duplicates, empty/over-limit values, literal identifiers, and grounded aliases absent verbatim from the source. Prompt examples cover avoiding unsupported guesses and generic low-value terms.
- Automatic apply behavior: pending rows write; skip/error rows do not; manifest tampering or invalid status fails safely.
- Staleness and idempotency: changed text, changed namespace, deleted records, newly populated anchors, already-applied whole batch, and interrupted/resumed generation.
- Storage integration: full-batch rollback on injected failure, `updated_at` preservation, transactional Anchor FTS/relation/outbox updates, no `index_value_embeddings` rows for anchors, dual outbox synchronization.
- CLI behavior: read-only draft/dry-run, writer lock enforcement, model failures recorded per record, and deterministic JSONL manifest handling.
- Reconciliation: before/after eligible and outcome counts, manifest-to-applied row matching, and coverage-delta reporting.

## Pilot timing log (2026-09-28)

- v1 flow (10 records, `remember-me`): 162.5s total ≈ 16.3s/record.
- v2 flow (10 records, `remember-me`, manifest `pilot-v2-10.jsonl`): header→trailer 910s wall across an interrupted run plus a 2-record resume; the resume did 2 model calls in 92.8s ≈ 46.4s/record.
- Per-record model latency observed: mostly 38–57s, median ≈ 46s, with one ≈180s outlier.
- Estimate for the remaining 115 `remember-me` records at serial pace: ≈ 90–110 minutes (allow ~2h).
- Manifest timestamps (`header.created_at`, row `drafted_at`, `trailer.completed_at`) give exact per-record timing without extra instrumentation.
- Full `remember-me` batch (115 records, manifest `remember-me-full-v2.jsonl`): first pass 5823.7s ≈ 97.1 min (112 generated, 1 skip, 2 error); a resume retried the 2 errors in 101.1s → 114 generated / 1 skip / 0 error, complete. Total ≈ 98.8 min for 115 records ≈ 51.5s/record.

## Execution status (2026-09-29)

Stage 1 and most of Stage 3 are done and verified. The implementation is committed (`af0cf58`); this note records the rollout results.

- Implementation: `bin/manage migrate anchors draft/apply`, `SQLiteStorage.update_anchors_batch` (single-transaction batch), batched extraction, and `--exclude-manifest` ledger batching. Tests: 458 passed.
- `remember-me`: v1 pilot 10 + v2 full 115 (114 generated / 1 skip) applied in single transactions; `live 145`, `has_anchors 141`.
- `atos-next-master`: 941 drafted across 95 manifests (all `generated`, 0 skip/error), applied manifest-by-manifest (941 rows, 0 failures, `updated_at` unchanged). After restart: dual `in_sync=true`, `pending=0`, replica integrity 0; namespace `live 956`, `needs_anchors 0`.
- Global: `anchor_total=3166`, `memories_with_anchors=1097`, anchor vectors 0; `search_anchor` hits.
- Remaining (optional): the few `remember-me` records added during the run, `待报销` (2), and any future writes — same flow (draft, then apply with the service stopped).
- Reproduce a call from its server-side Request ID (= opencode assistant message id): `/tmp/opencode/inspect_call.py <msg_...|ses_...>`.
