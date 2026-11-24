#!/usr/bin/env bash
set -Eeuo pipefail

log_dir="${RUNNER_TEMP:-/tmp}/tokenway-integration-validation-logs"
mkdir -p "$log_dir" web/dist
if [[ ! -s web/dist/index.html ]]; then
  printf '<!doctype html><html></html>\n' > web/dist/index.html
fi

results="$log_dir/failure-classification.json"
: > "$log_dir/results.ndjson"

run_suite() {
  local package="$1" pattern="$2" dialect="${3:-}"
  local slug="${package//\//-}" discovered logfile status
  if [[ -n "$dialect" ]]; then slug="$slug-$dialect"; fi
  logfile="$log_dir/$slug.log"

  if ! discovered="$(go test -list "$pattern" "./$package" 2>&1)"; then
    printf '%s\n' "$discovered" > "$logfile"
    status=1
  elif ! grep -q '^Test' <<<"$discovered"; then
    printf 'FAIL: no tests discovered for %s / %s\n' "$package" "$pattern" > "$logfile"
    status=2
  elif [[ -n "$dialect" ]]; then
    if TEST_TASK_DB_DIALECT="$dialect" go test -json -v -timeout 12m -parallel=1 -count=1 "./$package" -run "$pattern" > "$logfile" 2>&1; then
      status=0
    else
      status=$?
    fi
  elif go test -json -v -timeout 12m -parallel=1 -count=1 "./$package" -run "$pattern" > "$logfile" 2>&1; then
    status=0
  else
    status=$?
  fi

  python3 - "$results" "$package" "$pattern" "$dialect" "$status" "$logfile" <<'PY'
import json, pathlib, re, sys
out, package, pattern, dialect, raw_status, logfile = sys.argv[1:]
status = int(raw_status)
tests = set()
for line in pathlib.Path(logfile).read_text(encoding="utf-8", errors="replace").splitlines():
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    if event.get("Action") == "fail" and event.get("Test"):
        tests.add(event["Test"])
selector = {
    "go_module_scope": "root",
    "root_test_packages": "github.com/QuantumNous/new-api/" + package,
    "root_test_pattern": pattern,
    "targeted_only": True,
}
if tests:
    selector["root_test_pattern"] = "^(" + "|".join(re.escape(name) for name in sorted(tests)) + ")$"
if dialect:
    selector["task_db_dialect"] = dialect
record = {
    "status": "failed" if status else "passed",
    "suite": "integration",
    "module": "root",
    "package": "github.com/QuantumNous/new-api/" + package,
    "test_pattern": pattern,
    "database_dialect": dialect or None,
    "failed_tests": sorted(tests),
    "rerun_selector": selector,
    "exit_code": status,
}
with open(out, "a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
PY
  cat "$logfile"
  if (( status != 0 )); then
    echo "FAIL: integration suite $package ($dialect) exit $status" >&2
    return "$status"
  fi
  echo "PASS: integration suite $package ($dialect)"
}

failed=0
run_suite service '^(TestProcessRequestBillingRecoverySettlesRefundsAndReviewsUnknownOutcomes|TestSettle.*|TestRefund.*|TestUpdateBatchTasks.*|TestUpdateSunoTasksStalePollsRefundExactlyOnce|TestSweepTimedOutTasks.*)$' || failed=1
run_suite controller '^(TestExecuteTaskSubmission.*|TestResponsesWS.*)$' || failed=1
dialect_selection="${TOKENWAY_TASK_DB_DIALECT:-all}"
case "$dialect_selection" in
  all) dialects=(sqlite mysql postgres) ;;
  sqlite|mysql|postgres) dialects=("$dialect_selection") ;;
  *) echo "Unsupported TOKENWAY_TASK_DB_DIALECT: $dialect_selection" >&2; exit 2 ;;
esac
for dialect in "${dialects[@]}"; do
  run_suite controller '^TestImmediateTaskSettlementDatabase
run_suite relay '^TestResponsesWS.*$' || failed=1
run_suite pkg/wsmanager '^(TestCloseChannel.*|TestUnregisterPreventsClose|TestRegisteredCloseIsIdempotent|TestPublishCloseChannelsNoopsWhenRedisDisabled|TestRedisChannelCloseEventsStayWithinDatabase)$' || failed=1

python3 - "$results" "$log_dir/failure-classification.json" <<'PY'
import json, pathlib, sys
source, destination = map(pathlib.Path, sys.argv[1:])
records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
failures = [item for item in records if item["status"] == "failed"]
report = {
    "schema_version": 1,
    "overall": "failed" if failures else "passed",
    "failed_count": len(failures),
    "results": records,
    "targeted_rerun": [item["rerun_selector"] for item in failures],
}
destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Integration failure classification: {len(failures)} failed suite(s) / {len(records)} total.")
PY

if (( failed != 0 )); then
  echo "One or more integration suites failed; see $results and per-suite logs." >&2
  exit 1
fi
 "$dialect" || failed=1
done
run_suite relay '^TestResponsesWS.*$' || failed=1
run_suite pkg/wsmanager '^(TestCloseChannel.*|TestUnregisterPreventsClose|TestRegisteredCloseIsIdempotent|TestPublishCloseChannelsNoopsWhenRedisDisabled|TestRedisChannelCloseEventsStayWithinDatabase)$' || failed=1

python3 - "$results" "$log_dir/failure-classification.json" <<'PY'
import json, pathlib, sys
source, destination = map(pathlib.Path, sys.argv[1:])
records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
failures = [item for item in records if item["status"] == "failed"]
report = {
    "schema_version": 1,
    "overall": "failed" if failures else "passed",
    "failed_count": len(failures),
    "results": records,
    "targeted_rerun": [item["rerun_selector"] for item in failures],
}
destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Integration failure classification: {len(failures)} failed suite(s) / {len(records)} total.")
PY

if (( failed != 0 )); then
  echo "One or more integration suites failed; see $results and per-suite logs." >&2
  exit 1
fi
