#!/usr/bin/env bash
set -Eeuo pipefail

log_dir="${RUNNER_TEMP:-/tmp}/tokenway-go-validation-logs"
mkdir -p "$log_dir"
: > "$log_dir/summary.txt"
: > "$log_dir/failures.txt"

scope="${TOKENWAY_GO_MODULE_SCOPE:-all}"
root_input="${TOKENWAY_ROOT_TEST_PACKAGES:-}"
parallelism="${TOKENWAY_ROOT_TEST_PARALLELISM:-2}"
if ! [[ "$parallelism" =~ ^[1-4]$ ]]; then
  echo "TOKENWAY_ROOT_TEST_PARALLELISM must be between 1 and 4, got: $parallelism" >&2
  exit 2
fi

case "$scope" in
  all|root|relaykit|tokenkit) ;;
  *) echo "Unsupported TOKENWAY_GO_MODULE_SCOPE: $scope" >&2; exit 2 ;;
esac

if [[ -n "$root_input" && "$scope" != "all" && "$scope" != "root" ]]; then
  echo "root_test_packages can only be used with the all or root module scope." >&2
  exit 2
fi
if [[ "${TOKENWAY_TARGETED_ONLY:-false}" == "true" && ( "$scope" == "all" || "$scope" == "root" ) && -z "$root_input" ]]; then
  echo "A targeted Root run must provide one or more exact root_test_packages; use relaykit/tokenkit scope for a targeted module run." >&2
  exit 2
fi

export GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local GOMAXPROCS=1
export GOFLAGS="-mod=vendor -p=1"

mapfile -t discovered_packages < <(
  go list -f '{{if or .TestGoFiles .XTestGoFiles}}{{.ImportPath}}{{end}}' ./... |
    sed '/^$/d' |
    sort -u
)
if (( ${#discovered_packages[@]} == 0 )); then
  echo "FAIL: no Root Go test packages were discovered." >&2
  exit 1
fi

packages=()
if [[ "$scope" == "all" || "$scope" == "root" ]]; then
  if [[ -n "$root_input" ]]; then
    mapfile -t packages < <(printf '%s' "$root_input" | tr ', ' '\n' | sed '/^[[:space:]]*$/d' | sort -u)
    if (( ${#packages[@]} == 0 )); then
      echo "FAIL: root_test_packages was supplied but contained no import paths." >&2
      exit 2
    fi
    for requested in "${packages[@]}"; do
      if ! printf '%s\n' "${discovered_packages[@]}" | grep -Fxq "$requested"; then
        echo "FAIL: requested Root test package is not present in this pinned source: $requested" >&2
        exit 2
      fi
    done
    echo "Targeted Root packages selected (${#packages[@]}):"
    printf '  %s\n' "${packages[@]}"
  elif [[ "${TOKENWAY_RECOVERY_ONLY:-false}" == "true" ]]; then
    packages=(github.com/QuantumNous/new-api/service)
  elif [[ "${TOKENWAY_FINANCE_TARGETED_ONLY:-false}" == "true" ]]; then
    packages=(
      github.com/QuantumNous/new-api/common
      github.com/QuantumNous/new-api/service
    )
  elif [[ "${TOKENWAY_ROUTER_ONLY:-false}" == "true" ]]; then
    packages=(github.com/QuantumNous/new-api/router)
  else
    packages=("${discovered_packages[@]}")
    if (( ${#packages[@]} < 40 )); then
      echo "FAIL: discovered only ${#packages[@]} Root test packages; expected at least 40." >&2
      exit 1
    fi
    for required in \
      github.com/QuantumNous/new-api/e2e \
      github.com/QuantumNous/new-api/middleware \
      github.com/QuantumNous/new-api/oauth \
      github.com/QuantumNous/new-api/pkg/wsmanager \
      github.com/QuantumNous/new-api/relay/channel/vertex \
      github.com/QuantumNous/new-api/setting/system_setting; do
      if ! printf '%s\n' "${packages[@]}" | grep -Fxq "$required"; then
        echo "FAIL: required Root test package was not discovered: $required" >&2
        exit 1
      fi
    done
    echo "Discovered ${#packages[@]} Root test packages, including required e2e and channel adapter coverage."
  fi
else
  echo "Root module tests skipped by requested scope: $scope"
fi

run_root_package() (
  set -Eeuo pipefail
  local pkg="$1"
  local slug="${pkg//\//-}"
  local logfile="$log_dir/${slug}.jsonl"
  local failure_log="$log_dir/${slug}.failure.txt"
  local key
  key="$(printf '%s' "$pkg" | sha256sum | cut -c1-12)"
  local mysql_db="twroot_${key}"
  local pg_db="twroot_${key}"

  cleanup_package_databases() {
    mysql --protocol=TCP -h127.0.0.1 -uroot -ptokenway-test \
      -e "DROP DATABASE IF EXISTS $mysql_db;" >/dev/null 2>&1 || true
    PGPASSWORD=tokenway-test psql -h127.0.0.1 -Utokenway -d postgres \
      -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $pg_db WITH (FORCE)" \
      >/dev/null 2>&1 || true
  }
  trap cleanup_package_databases EXIT

  mysql --protocol=TCP -h127.0.0.1 -uroot -ptokenway-test \
    -e "CREATE DATABASE $mysql_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
  PGPASSWORD=tokenway-test psql -h127.0.0.1 -Utokenway -d postgres \
    -v ON_ERROR_STOP=1 -c "CREATE DATABASE $pg_db" >/dev/null

  export TEST_MYSQL_DSN="root:tokenway-test@tcp(127.0.0.1:3306)/$mysql_db?charset=utf8mb4&parseTime=True&loc=Local"
  export TEST_MYSQL_LOG_DSN="$TEST_MYSQL_DSN"
  export TEST_FIXED_MYSQL_DSN="$TEST_MYSQL_DSN"
  export TEST_FIXED_MYSQL_LOG_DSN="$TEST_MYSQL_DSN"
  export TEST_POSTGRES_DSN="postgres://tokenway:tokenway-test@127.0.0.1:5432/$pg_db?sslmode=disable"
  export TEST_POSTGRES_LOG_DSN="$TEST_POSTGRES_DSN"
  export TEST_FIXED_POSTGRES_DSN="$TEST_POSTGRES_DSN"
  export TEST_FIXED_POSTGRES_LOG_DSN="$TEST_POSTGRES_DSN"

  echo "ROOT PACKAGE TESTS: $pkg"
  local test_args=(-json -timeout 15m -parallel=1 -count=1)
  if [[ "${TOKENWAY_RECOVERY_ONLY:-false}" == "true" && -z "$root_input" ]]; then
    test_args+=(-run '^TestProcessRequestBillingRecoverySettlesRefundsAndReviewsUnknownOutcomes$')
  fi

  if timeout --signal=TERM --kill-after=30s 17m go test "${test_args[@]}" "$pkg" > "$logfile" 2>&1; then
    printf 'PASS: %s\n' "$pkg" > "$log_dir/${slug}.status"
    echo "PASS: $pkg"
    return 0
  else
    local status=$?
    {
      echo "FAIL: $pkg (exit $status)"
      python3 - "$logfile" <<'PY'
import json
import sys

events = []
with open(sys.argv[1], encoding="utf-8") as stream:
    for line in stream:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

failed_tests = {
    event.get("Test")
    for event in events
    if event.get("Action") == "fail" and event.get("Test")
}
package_failed = any(
    event.get("Action") == "fail" and not event.get("Test")
    for event in events
)
for event in events:
    if event.get("Action") != "output":
        continue
    test_name = event.get("Test")
    if package_failed or not failed_tests or test_name in failed_tests:
        output = event.get("Output", "").rstrip()
        if output:
            print(f"[{test_name or 'package'}] {output}")
PY
    } > "$failure_log"
    printf 'FAIL: %s (exit %s)\n' "$pkg" "$status" > "$log_dir/${slug}.status"
    cat "$failure_log" >&2
    return "$status"
  fi
)

run_module() (
  set -Eeuo pipefail
  local module="$1"
  local logfile="$log_dir/${module}.log"
  echo "Running isolated $module Go test process."
  if (cd "$module" && timeout --signal=TERM --kill-after=30s 22m go test -mod=vendor -p=1 -count=1 ./...) > "$logfile" 2>&1; then
    printf 'PASS: %s full test suite\n' "$module" > "$log_dir/${module}.status"
    echo "PASS: $module full test suite"
    return 0
  else
    local status=$?
    {
      echo "FAIL: $module full test suite (exit $status)"
      tail -n 160 "$logfile" || true
    } > "$log_dir/${module}.failure.txt"
    printf 'FAIL: %s full test suite (exit %s)\n' "$module" "$status" > "$log_dir/${module}.status"
    cat "$log_dir/${module}.failure.txt" >&2
    return "$status"
  fi
)

module_pids=()
if [[ "$scope" == "relaykit" || ( "$scope" == "all" && -z "$root_input" && "${TOKENWAY_FINANCE_TARGETED_ONLY:-false}" != "true" && "${TOKENWAY_ROUTER_ONLY:-false}" != "true" && "${TOKENWAY_RECOVERY_ONLY:-false}" != "true" ) ]]; then
  run_module relaykit &
  module_pids+=("$!")
fi
if [[ "$scope" == "tokenkit" || ( "$scope" == "all" && -z "$root_input" && "${TOKENWAY_FINANCE_TARGETED_ONLY:-false}" != "true" && "${TOKENWAY_ROUTER_ONLY:-false}" != "true" && "${TOKENWAY_RECOVERY_ONLY:-false}" != "true" ) ]]; then
  run_module tokenkit &
  module_pids+=("$!")
fi
if [[ -n "$root_input" ]]; then
  echo "Skipping nested modules for this targeted Root-package run."
fi

failed=0
root_pids=()
if (( ${#packages[@]} > 0 )); then
  echo "Root package worker limit: $parallelism (each package is a separate process with its own MySQL/PostgreSQL databases)"
  for pkg in "${packages[@]}"; do
    run_root_package "$pkg" &
    root_pids+=("$!")
    if (( ${#root_pids[@]} >= parallelism )); then
      if wait "${root_pids[0]}"; then :; else failed=1; fi
      root_pids=("${root_pids[@]:1}")
    fi
  done
  for pid in "${root_pids[@]}"; do
    if wait "$pid"; then :; else failed=1; fi
  done
fi

for pid in "${module_pids[@]}"; do
  if wait "$pid"; then :; else failed=1; fi
done

{
  echo "Go validation summary:"
  for status_file in "$log_dir"/*.status; do
    [[ -f "$status_file" ]] || continue
    cat "$status_file"
  done
} | tee "$log_dir/summary.txt"
grep '^FAIL:' "$log_dir/summary.txt" > "$log_dir/failures.txt" || true

if (( failed != 0 )); then
  echo "Go validation still has failures. Exact affected package/module names and logs are in $log_dir."
  exit 1
fi

echo "All selected Root, Relaykit, and Tokenkit test suites passed."
