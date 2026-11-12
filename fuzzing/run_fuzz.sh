#!/usr/bin/env bash
# ==============================================================================
# run_fuzz.sh — continuous AFL++ campaign for undo264, with crash triage
#
#   1. builds the binaries with build_fuzzers.sh                 (skip with -n)
#   2. starts JOBS afl-fuzz processes sharing one output dir (1 main + secondaries,
#      binaries used in turn). AFL++ never stops on a crash: it saves it and goes on.
#      Runs for -t seconds (0 = until Ctrl-C).
#   3. replays every crash/hang with the sanitizer binary and groups them by stack trace
#
# Layout:  findings/<run>/<instance>/{queue,crashes,hangs}
#          findings/<run>/report.md       (+ triage/ with one log per input)
# Re-running with the same -N name resumes the campaign (same queue).
# -R re-triages a run (the latest one by default), even while it is still fuzzing.
# ==============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"

SEEDS_DIR="${SEEDS_DIR:-$SCRIPT_DIR/seeds}"
SEED_NAME="${SEED_NAME:-AVCv1}"
OUT_ROOT="${OUT_ROOT:-$SCRIPT_DIR/findings}"
BUILD_ROOT="${BUILD_ROOT:-$ROOT_DIR/build/afl}"
OPT_LEVELS="${OPT_LEVELS:-3}"
SANITIZERS="${SANITIZERS:-address+undefined none}"   # first one = main instance
JOBS="${JOBS:-4}"
MAX_TIME="${MAX_TIME:-0}"            # seconds, 0 = until Ctrl-C
MAX_LEN="${MAX_LEN:-65536}"          # bytes
TIMEOUT_MS="${TIMEOUT_MS:-2000}"     # an input slower than this is a hang
RUN_NAME="${RUN_NAME:-}"
DO_BUILD=1
REPORT_ONLY=0
TAB=$(printf '\t')

usage() {
  cat <<EOF
Usage: $0 [options]
  -t SEC    fuzzing time, 0 = until Ctrl-C       (default: $MAX_TIME)
  -j N      number of afl-fuzz processes         (default: $JOBS)
  -o LIST   optimisation levels                  (default: "$OPT_LEVELS")
  -s LIST   sanitizer variants                   (default: "$SANITIZERS")
  -S DIR    seeds directory                      (default: $SEEDS_DIR)
  -N NAME   run name (reuse a name to resume)    (default: run_<date>_$SEED_NAME)
  -n        do not build, use existing binaries
  -R        triage only (no build, no fuzzing)
  -h        help
EOF
}

while getopts "t:j:o:s:S:N:nRh" opt; do
  case $opt in
    t) MAX_TIME=$OPTARG ;;
    j) JOBS=$OPTARG ;;
    o) OPT_LEVELS=$OPTARG ;;
    s) SANITIZERS=$OPTARG ;;
    S) SEEDS_DIR=$OPTARG ;;
    N) RUN_NAME=$OPTARG ;;
    n) DO_BUILD=0 ;;
    R) REPORT_ONLY=1 ;;
    h) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

log() { echo "[fuzz] $*" >&2; }
die() { echo "[fuzz] error: $*" >&2; exit 1; }

# ---- Run name ------------------------------------------------------------------
if [ -z "$RUN_NAME" ]; then
  if [ "$REPORT_ONLY" -eq 1 ]; then
    RUN_NAME=$(ls -t "$OUT_ROOT" 2>/dev/null | head -n 1)
  else
    RUN_NAME="run_$(date +%Y%m%d_%H%M)_$SEED_NAME"
  fi
fi
[ -n "$RUN_NAME" ] || die "no run found in $OUT_ROOT"
RUN_DIR="$OUT_ROOT/$RUN_NAME"

# ---- Binaries --------------------------------------------------------------------
BINS=()
for o in $OPT_LEVELS; do
  for s in $SANITIZERS; do
    BINS+=("$BUILD_ROOT/O$o-$s/fuzz_decoder")
  done
done
# crashes are replayed with the first binary that has AddressSanitizer (else the first one)
TRIAGE_BIN="${BINS[0]}"
for b in "${BINS[@]}"; do
  case "$b" in *address*) TRIAGE_BIN="$b"; break ;; esac
done

# ---- Triage ------------------------------------------------------------------------
triage() {
  local tri="$RUN_DIR/triage" total=0 nonrepro=0 f log rc stack top kind sig size groups
  command -v llvm-symbolizer >/dev/null || log "warning: llvm-symbolizer not found, stack traces will have no file:line"
  [ -x "$TRIAGE_BIN" ] || die "missing binary $TRIAGE_BIN"
  rm -rf "$tri"; mkdir -p "$tri"
  : > "$tri/index.tsv"

  # replay settings: readable stack traces (handle_abort: also get a stack from the SIGABRT sent on hangs)
  export ASAN_OPTIONS="detect_leaks=1:symbolize=1:handle_abort=1"
  export UBSAN_OPTIONS="print_stacktrace=1:symbolize=1"
  export MSAN_OPTIONS="symbolize=1"

  while IFS= read -r f; do
    total=$((total + 1))
    log="$tri/$total.log"
    timeout -s ABRT $((TIMEOUT_MS / 1000 + 3)) "$TRIAGE_BIN" "$f" >"$log" 2>&1
    rc=$?
    if [ "$rc" -eq 0 ]; then nonrepro=$((nonrepro + 1)); continue; fi

    # stack of the error = first block of "#N 0x... in func file:line"; keep our own frames only
    stack=$(awk '/^ *#[0-9]+ 0x/ { print; b = 1; next } b { exit }' "$log" \
      | grep -F "$ROOT_DIR/src/" \
      | sed -E 's|^ *#[0-9]+ 0x[0-9a-f]+ in ([^ ]+) .*/(src/[^ ]+):([0-9]+)(:[0-9]+)?$|\1 \2:\3|')
    # crash inside a low-level helper (src/util/): the callers do not make it a different bug
    top=$(printf '%s\n' "$stack" | head -n 1)
    case "$top" in *" src/util/"*) stack="$top" ;; esac

    kind=$(grep -m1 -E 'ERROR: |runtime error|Assertion' "$log" | cut -c1-100)
    if [ "$rc" -eq 124 ]; then kind="hang (timeout)"; fi
    if [ -z "$stack" ]; then stack=$(printf '%s' "$kind" | sed -E 's/0x[0-9a-f]+//g; s/[0-9]+/N/g'); fi
    if [ -z "$stack" ]; then stack="unknown (exit code $rc)"; fi

    sig=$(printf '%s' "$stack" | cksum | cut -d' ' -f1)
    size=$(wc -c < "$f" | tr -d ' ')
    top=$(printf '%s\n' "$stack" | head -n 1)
    printf '%s\t%s\t%s\t%s\t%s\n' "$sig" "$size" "$f" "$kind" "$top" >> "$tri/index.tsv"
    printf '%s\n' "$stack" > "$tri/$sig.stack"
  done < <(find "$RUN_DIR" -type f \( -path '*/crashes/id:*' -o -path '*/hangs/id:*' \) | sort)

  groups=$(cut -f1 "$tri/index.tsv" | sort -u | wc -l | tr -d ' ')
  {
    echo "# Fuzzing report — $RUN_NAME"
    echo
    echo "- Replayed with: \`$TRIAGE_BIN\`"
    echo "- Crashes + hangs saved by AFL++: **$total** — reproduced: **$((total - nonrepro))** — not reproducible: **$nonrepro**"
    echo "- Unique stack traces: **$groups** (stacks in \`triage/<signature>.stack\`)"
    echo
    echo "| Inputs | Signature | Kind | Top frame | Smallest input |"
    echo "|-------:|-----------|------|-----------|----------------|"
    # smallest input first within each signature, then biggest groups first
    sort -t "$TAB" -k1,1 -k2,2n "$tri/index.tsv" | awk -F'\t' '
      { n[$1]++ }
      !($1 in rep) { rep[$1] = $3; size[$1] = $2; kind[$1] = $4; top[$1] = $5; order[++g] = $1 }
      END { for (i = 1; i <= g; i++) { s = order[i]
              printf "| %d | `%s` | %s | `%s` | `%s` (%s B) |\n", n[s], s, kind[s], top[s], rep[s], size[s] } }' \
      | sort -t '|' -k2,2nr
  } > "$RUN_DIR/report.md"

  log "$total artifact(s), $((total - nonrepro)) reproduced, $groups unique stack trace(s)"
  log "report: $RUN_DIR/report.md"
}

# ---- Report only -----------------------------------------------------------------
if [ "$REPORT_ONLY" -eq 1 ]; then
  [ -d "$RUN_DIR" ] || die "run not found: $RUN_DIR"
  triage
  exit 0
fi

# ---- Checks and build ------------------------------------------------------------
command -v afl-fuzz >/dev/null || die "afl-fuzz not found"
command -v timeout >/dev/null || die "GNU timeout not found (coreutils)"
[ -d "$SEEDS_DIR" ] && [ -n "$(ls -A "$SEEDS_DIR")" ] || die "seeds directory missing or empty: $SEEDS_DIR"

if [ "$DO_BUILD" -eq 1 ]; then
  "$SCRIPT_DIR/build_fuzzers.sh" -q -o "$OPT_LEVELS" -s "$SANITIZERS" || die "build failed"
fi
for b in "${BINS[@]}"; do
  [ -x "$b" ] || die "missing binary $b (drop -n or run build_fuzzers.sh)"
done

# ---- AFL++ environment ---------------------------------------------------------------
export AFL_NO_UI=1                              # no TUI, we run in the background
export AFL_AUTORESUME=1                         # same -N name = resume the existing queue
export AFL_SKIP_CRASHES=1                       # a seed that crashes does not abort the run
export AFL_SKIP_CPUFREQ=1                       # do not refuse to start because of the CPU governor
export AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1  # do not refuse to start because of core_pattern
# AFL++ needs sanitizers that abort (not just print) and do not symbolize while fuzzing
export ASAN_OPTIONS="abort_on_error=1:detect_leaks=0:symbolize=0:allocator_may_return_null=1"
export UBSAN_OPTIONS="halt_on_error=1:abort_on_error=1:symbolize=0"
export MSAN_OPTIONS="exit_code=86:abort_on_error=1:symbolize=0:allocator_may_return_null=1"

# ---- Start the instances ----------------------------------------------------------------
mkdir -p "$RUN_DIR"
time_opt=""
if [ "$MAX_TIME" -gt 0 ]; then time_opt="-V $MAX_TIME"; fi

PIDS=""
k=1
while [ "$k" -le "$JOBS" ]; do
  bin="${BINS[$(( (k - 1) % ${#BINS[@]} ))]}"
  if [ "$k" -eq 1 ]; then name=main; role="-M main"; else name="s$k"; role="-S s$k"; fi
  log "starting $name with ${bin#$ROOT_DIR/}"
  afl-fuzz -i "$SEEDS_DIR" -o "$RUN_DIR" $role -m none -t "${TIMEOUT_MS}+" -G "$MAX_LEN" $time_opt \
    -- "$bin" >"$RUN_DIR/$name.log" 2>&1 &
  PIDS="$PIDS $!"
  k=$((k + 1))
done

any_alive() {
  local p
  for p in $PIDS; do
    if kill -0 "$p" 2>/dev/null; then return 0; fi
  done
  return 1
}

STOP=0
trap 'STOP=1' INT TERM
start=$SECONDS
last_status=$SECONDS
log "fuzzing run $RUN_NAME (Ctrl-C to stop, then triage starts)"
while [ "$STOP" -eq 0 ] && any_alive; do
  sleep 5
  if [ $((SECONDS - last_status)) -ge 60 ]; then
    last_status=$SECONDS
    if command -v afl-whatsup >/dev/null; then afl-whatsup -s "$RUN_DIR" 2>/dev/null; fi
  fi
done
if [ $((SECONDS - start)) -lt 15 ]; then
  log "afl-fuzz stopped right after the start, last lines of main.log:"
  tail -n 15 "$RUN_DIR/main.log" >&2
fi

log "stopping afl-fuzz"
kill $PIDS 2>/dev/null
wait 2>/dev/null

triage