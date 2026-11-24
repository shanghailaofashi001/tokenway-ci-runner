# TokenWay V11 CI maintenance

This workflow is the durable acceptance path. Repair ordinary defects at the smallest failing layer; reserve a complete acceptance run for the final source state after focused verification.

## Acceptance layers

1. **Fast Go checks:** gofmt, vendored dependency/offline graph checks, go vet and build of Root, Relaykit and Tokenkit.
2. **Module tests:** Root test packages run as separate processes; Relaykit and Tokenkit run in parallel, independent processes.
3. **Integration tests:** wallet ledger, EPay callback, recharge/refund/task settlement regressions and WebSocket coverage remain enabled. The SQLite/MySQL/PostgreSQL wallet and EPay matrix is a separate tagged gate.
4. **Frontend and contract checks:** build, typecheck, unit tests, lint/format/copyright checks and retained TokenWay media contracts.
5. **Same-run release:** only after all acceptance gates succeed, package the exact runner-local source tree, generate RELEASE-MANIFEST.json, FILE-CHECKSUMS.sha256, source-tree SHA-256 and archive SHA-256, then upload the artifact.

## Database fixtures

internal/testsupport/database.go is the single SQL test database provisioner. SQLite uses a t.TempDir() file. MySQL and PostgreSQL create a cryptographically named disposable database on a loopback service, return a DSN for that database, and drop it in t.Cleanup. ClickHouse remains supported for the existing log tests. The helper never drops a caller-supplied database.

Schema-sensitive Model and controller integration tests call the application's model.InitDB() migration path before assertions. This keeps test schemas tied to the same production model definitions and migrations. Local runs without a configured external DB may skip the external-dialect subtest; the CI job always provides MySQL and PostgreSQL services, so the matrix is expected to execute all configured dialects.

## Isolation and parallelism

Every Root test package executes in its own go test process, with a package-specific MySQL and PostgreSQL parent database. Each database fixture can create its own disposable per-test child database. Package-internal test parallelism remains -parallel=1 to protect tests that intentionally mutate package globals. Root package concurrency is bounded (default 2); Relaykit and Tokenkit use their own independent processes. The shared fixtures snapshot and restore DB type, DB handles, Redis/cache, batch and log-consumer globals. Redis-backed caches are disabled for migration fixtures; no package shares an in-memory process global with another package.

## Targeted reruns

For a UI-triggered workflow_dispatch, set go_module_scope to all, root, relaykit or tokenkit; root_test_packages accepts exact comma- or space-separated Go import paths. Set targeted_only=true to skip unrelated integration/frontend stages. Set run_database_matrix=true only when the SQL integration matrix is part of the affected area.

For push-triggered recovery, use commit-message markers:
- [root-packages=github.com/QuantumNous/new-api/controller,github.com/QuantumNous/new-api/model] selects exact Root packages.
- [targeted-only] skips unrelated front-end and integration layers.
- [database-matrix] also runs the tagged three-database wallet/EPay matrix.
- [root-only], [relaykit-only] or [tokenkit-only] limits the module.
A Root package target is validated against go list; unknown or stale package names fail immediately. Failures retain the package/module name and JSON test log as a short-lived diagnostic artifact.

After the focused run passes, run one full acceptance on the final commit. No test, assertion, provider validation, or business guard is removed to make the pipeline green. Previously passed results remain recorded against their original commit; a final release archive is always tied to the exact commit that passed the complete acceptance workflow.
