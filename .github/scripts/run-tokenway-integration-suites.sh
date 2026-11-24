#!/usr/bin/env bash
set -Eeuo pipefail
export GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local GOMAXPROCS=1
export GOFLAGS="-mod=vendor -p=1"
: "${RUNNER_TEMP:=/tmp}"
log_dir="$RUNNER_TEMP/tokenway-integration-logs"
mkdir -p "$log_dir"
report="$log_dir/integration-failure-classification.json"
failed=0

record_result() {
  python3 - "$report" "$1" "$2" "$3" "$4" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
package, pattern, status, variant = sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
    "schema_version": 1, "overall": "passed", "failed_count": 0,
    "results": [], "targeted_rerun": []
}
selector = {
    "go_module_scope": "root",
    "root_test_packages": "github.com/QuantumNous/new-api/" + package,
    "root_test_pattern": pattern,
    "targeted_only": True,
}
if variant.startswith("task-db-"):
    selector["task_db_dialect"] = variant.removeprefix("task-db-")
report["results"].append({
    "status": "failed" if status else "passed",
    "suite": "integration",
    "module": "root",
    "package": "github.com/QuantumNous/new-api/" + package,
    "test_pattern": pattern,
    "variant": variant or None,
    "rerun_selector": selector,
})
failures = [item for item in report["results"] if item["status"] == "failed"]
report["failed_count"] = len(failures)
report["overall"] = "failed" if failures else "passed"
report["targeted_rerun"] = [item["rerun_selector"] for item in failures]
path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

run_suite() {
  local package="$1" pattern="$2" variant=""
  if (( $# >= 3 )); then variant="$3"; fi
  local slug logfile status discovered
  slug="$(printf '%s' "$package" | tr '/' '-')"
  if [[ -n "$variant" ]]; then slug="$slug-$variant"; fi
  logfile="$log_dir/$slug.log"
  echo "===== Discover integration tests: $package / $pattern ====="
  if discovered="$(go test -list "$pattern" "./$package" 2>&1)" && grep -q '^Test' <<<"$discovered"; then
    echo "$discovered" | grep '^Test' | sed 's/^/  /'
    echo "===== Run integration tests: $package / $pattern ====="
    if go test -json -v -timeout 15m -parallel=1 -count=1 "./$package" -run "$pattern" >"$logfile" 2>&1; then
      status=0
    else
      status=$?
    fi
  else
    status=$?
    if (( status == 0 )); then status=2; fi
    printf '%s\n' "$discovered" >"$logfile"
    echo "FAIL: required integration tests were not discovered in $package: $pattern" >&2
  fi
  if (( status == 0 )); then
    printf 'PASS: %s %s %s\n' "$package" "$variant" "$pattern" >"$log_dir/$slug.status"
  else
    printf 'FAIL: %s %s %s (exit %s)\n' "$package" "$variant" "$pattern" "$status" >"$log_dir/$slug.status"
    failed=1
  fi
  record_result "$package" "$pattern" "$status" "$variant"
  cat "$logfile"
  return 0
}

run_suite service '^(TestProcessRequestBillingRecoverySettlesRefundsAndReviewsUnknownOutcomes|TestSettle.*|TestRefund.*|TestUpdateBatchTasks.*|TestUpdateSunoTasksStalePollsRefundExactlyOnce|TestSweepTimedOutTasks.*|TestTaskReportsOnceAtTheFinalState)'
run_suite controller '^(TestExecuteTaskSubmission.*|TestResponsesWS.*|TestResponsesWebSocket.*|TestResponsesInterruptedStreamHealth|TestResponsesStreamOutcomesPreserveAccounting)'
for dialect in sqlite mysql postgres; do
  echo "===== Task settlement database matrix: $dialect ====="
  TEST_TASK_DB_DIALECT="$dialect" run_suite controller '^TestImmediateTaskSettlementDatabase' "task-db-$dialect"
done
run_suite relay '^TestResponsesWS.*'
run_suite pkg/wsmanager '^(TestCloseChannel.*|TestUnregisterPreventsClose|TestRegisteredCloseIsIdempotent|TestPublishCloseChannelsNoopsWhenRedisDisabled|TestRedisChannelCloseEventsStayWithinDatabase)'

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  python3 - "$report" "$GITHUB_STEP_SUMMARY" <<'PY'
import json, pathlib, sys
report = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
with pathlib.Path(sys.argv[2]).open("a", encoding="utf-8") as summary:
    summary.write("## TokenWay integration test classification\n\n")
    summary.write(f"- Result: **{report['overall']}**\n")
    summary.write(f"- Failed suites: {report['failed_count']}\n")
    for item in report["results"]:
        summary.write(f"- {item['status'].upper()}: {item['package']} — {item['test_pattern']}")
        if item.get("variant"):
            summary.write(f" ({item['variant']})")
        summary.write("\n")
    if report["targeted_rerun"]:
        summary.write("\nTargeted rerun selectors are in integration-failure-classification.json.\n")
PY
fi
if (( failed != 0 )); then
  echo "FAIL: one or more TokenWay business integration suites failed. Logs and integration-failure-classification.json are in $log_dir." >&2
  exit 1
fi
echo "PASS: all TokenWay billing, refund, task settlement, WebSocket and Redis isolation integration suites."
