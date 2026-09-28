# [41] Historical Anchor Backfill

2026-09-28 plan；这是一次**存量数据迁移**：为已有 live 记忆补齐 `metadata.anchors`。分阶段执行——先在 `remember-me` 小批量全人工校准，再放量该 namespace，最后扩展到其余 namespace（含 `atos-next-master`）。不引入在线 anchor 生成。

## Goal and decisions

- Migrate existing live memories by backfilling `metadata.anchors`, without adding generation to online MCP/admin write paths.
- Generate candidates with the existing DeepSeek `opencode run` workflow; require human review before any database write.
- Scope is staged, but the migration target is all namespaces: Stage 1 = a small pilot batch in `remember-me` with full per-row manual review; Stage 2 = the remaining eligible `remember-me` records; Stage 3 = all other live namespaces. Measured baseline: 1074 live records, 1068 without anchors (`atos-next-master` 938, `remember-me` 125, `atos-next` 3, `待报销` 2).
- Eligible records are live records with missing/empty `metadata.anchors`; never overwrite existing non-empty anchors.
- Extract 0–5 distinctive names, classes, APIs, error codes, config keys, versions, or identifiers that appear in the memory text. Preserve spelling; do not invent terms or copy generic topics/keywords. Records with no suitable candidate remain unchanged.
- Definition of done: every eligible record has been judged (`applied`, `rejected`, or `skip`) against a retained manifest. This is **not** a requirement that every record ends with anchors.
- Review intensity: full manual review of the pilot batch to calibrate; later stages use the calibrated intensity.
- Acceptance requires before/after reconciliation. A rollback path is **not** required.
- Keep this maintenance task separate from [40] retrieval benchmark and any future online anchor generation.

## Proposed CLI and data flow

Add `bin/manage migrate anchors` with explicit `draft` and `apply` stages, backed by a new offline CLI module:

1. **Draft** reads SQLite primary in read-only mode and paginates the selected namespace. It calls DeepSeek serially, validates candidates with `normalize_anchors`, and writes a review JSONL manifest outside the repository (default under `data/anchor-backfill/`, gitignored). No database writes occur in this stage.
2. Each manifest row contains `memory_id`, namespace, content hash, a bounded text preview for review, proposed anchors, status (`pending`, `approved`, `rejected`, or `skip`), and an optional error. Do not copy complete memory text into the manifest.
3. A reviewer edits the manifest: corrects candidates and marks rows `approved`, `rejected`, or `skip`. An approved empty list is invalid; records without suitable anchors use `skip`.
4. **Apply** accepts only approved rows. Before each write it rereads the memory and checks that it is live, remains in the requested namespace, still has no non-empty anchors, and its content hash matches the reviewed manifest. Conflicts are skipped and reported; they are never force-overwritten.
5. Apply writes only `metadata.anchors` using `update_metadata`, preserving `updated_at`. This follows existing SQLite transaction, metadata-index, Anchor FTS, and dual outbox paths. Each record commits independently; reruns recognize identical existing values as already applied.

Support `--namespace`, `--manifest`, `--limit`, `--model`, `--cli`, and `--timeout`; default namespace is `remember-me`. Draft supports `--dry-run` to count eligible rows without model calls. Apply requires the service to be stopped and relies on the existing primary writer lock. The manifest may contain project memory excerpts and remains local, ignored, and operator-managed.

## Rollout and acceptance

- Stage 1 (pilot): dry-run and draft a small pilot batch in `remember-me`; manually approve/reject/skip every row before apply.
- Run apply against the pilot with the service stopped. Record eligible, approved, rejected, skipped, conflicting, failed, and applied counts; retain the reviewed manifest as the audit artifact.
- Reconcile before/after: eligible count, processed count by outcome, applied rows matched against the manifest, and coverage delta (`anchor_total` / `memories_with_anchors`). Reconciliation is the acceptance evidence; rollback is not required.
- Verify `updated_at` is unchanged, Anchor Search finds approved identifiers, no anchor vectors are created, and SQLite/NDJSON dual health is clean after restart.
- Stage 2: after the pilot quality is acceptable, process the remaining eligible `remember-me` records using the calibrated review intensity.
- Stage 3: expand to all other live namespaces, including `atos-next-master`. Scope is already decided; execute each namespace as its own batch with its own reconciliation.

## Tests

- Candidate parsing/validation: malformed model output, non-string values, duplicates, empty/over-limit values, and anchors absent from source text.
- Review/apply behavior: only approved rows write; rejected/skip/pending do not; manifest tampering or invalid status fails safely.
- Staleness and idempotency: changed text, changed namespace, deleted records, newly populated anchors, identical already-applied rows, and interrupted/repeated apply.
- Storage integration: `updated_at` preservation, transactional Anchor FTS and relation updates, no `index_value_embeddings` rows for anchors, dual outbox synchronization.
- CLI behavior: read-only draft/dry-run, writer lock enforcement, partial failures reported per record, and deterministic JSONL manifest handling.
- Reconciliation: before/after eligible and outcome counts, manifest-to-applied row matching, and coverage-delta reporting.
