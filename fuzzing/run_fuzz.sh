#!/usr/bin/env bash
# ==============================================================================
# run_fuzz.sh — continuous libFuzzer campaign for undo264, with crash triage
# Written by Claude Sonnet 5.5
#
# For every (optimisation level x sanitizer) pair it:
#   1. builds fuzz_decoder           (skip with -n)
#   2. fuzzes so that crashes/timeouts/OOMs do NOT stop the run:
#        - fork mode (default): libFuzzer -fork=N supervises N child processes
#        - plain mode (fallback, or -F plain): N independent libFuzzer processes sharing one
#          corpus dir, each restarted after a crash until the time budget is spent
#   3. replays every saved artifact one by one, extracts the sanitizer stack trace
#   4. groups artifacts by identical (project-only) stack trace -> report.md
#
# Layout (all relative to this script's directory, i.e. fuzzing/):
#   work/O{n}/{san}/run_{k}_{SEED_NAME}/      corpus written by libFuzzer
#   crashes/O{n}/{san}/run_{k}_{SEED_NAME}/   crash-* timeout-* oom-* leak-* files,
#                                             fuzz.log, replay/, report.md, report.tsv
#
# Requires: bash >= 4, clang (libFuzzer), cmake, llvm-symbolizer, sha1sum|shasum
# Binaries are built by build_fuzzers.sh (same directory); you can run that on its own.
# ==============================================================================
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"

# ---- Customisable (environment variable or command-line flag) ----------------
SEEDS_DIR="${SEEDS_DIR:-$SCRIPT_DIR/seeds}"        # -S : directory with seed inputs
SEED_NAME="${SEED_NAME:-AVCv1}"                    # -N : label used in run names
WORK_ROOT="${WORK_ROOT:-$SCRIPT_DIR/work}"
CRASH_ROOT="${CRASH_ROOT:-$SCRIPT_DIR/crashes}"
BUILD_ROOT="${BUILD_ROOT:-$ROOT_DIR/build/fuzz}"
PROJECT_ROOT="${PROJECT_ROOT:-$ROOT_DIR}"          # frames under this path are "ours"
CC_BIN="${CC_BIN:-clang}"
OPT_LEVELS="${OPT_LEVELS:-3 0}"                    # -o : e.g. "3 0"
SANITIZERS="${SANITIZERS:-address undefined memory}"  # -s
MAX_TIME="${MAX_TIME:-0}"                          # -t : seconds per configuration
MAX_RUNS="${MAX_RUNS:-0}"                          # -r : executions per configuration
JOBS="${JOBS:-1}"                                  # -j : parallel fork workers
MAX_LEN="${MAX_LEN:-1048576}"                      # bytes
TIMEOUT="${TIMEOUT:-5}"                            # seconds before an input counts as a hang
RSS_MB="${RSS_MB:-2048}"                           # per-process memory limit
SIG_FRAMES="${SIG_FRAMES:-0}"                      # 0 = whole stack, N = top N project frames
# If the TOP frame matches this regex (path is relative to the repo root), the crash happened in a
# low-level helper (bit reader, ...) and only that frame is used as signature: whoever called the
# helper does not make it a different bug. Empty = disabled (whole stack is always used).
SIG_TOP_ONLY="${SIG_TOP_ONLY:-src/util/}"
DO_BUILD="${DO_BUILD:-1}"                          # -n sets this to 0
FUZZ_MODE="${FUZZ_MODE:-fork}"                     # -F : fork | plain
MAX_RESTARTS="${MAX_RESTARTS:-200}"                # safety cap on libFuzzer restarts per configuration
MINIMIZE="${MINIMIZE:-0}"                          # -m : minimise each group's representative
REPORT_ONLY="${REPORT_ONLY:-0}"                    # -R : re-triage the latest run, no fuzzing
SEED_CHECK="${SEED_CHECK:-1}"                      # -P sets this to 0 (skip the seed pre-check)
MINIMIZE_RUNS="${MINIMIZE_RUNS:-5000}"

usage() {
cat <<EOF
Usage: $0 [options]
  -t SEC    max fuzzing time per configuration (default: 600 if no -t/-r given)
  -r N      max executions per configuration
  -o LIST   optimisation levels, e.g. "3 0"            (default: "$OPT_LEVELS")
  -s LIST   sanitizers, e.g. "address undefined"       (default: "$SANITIZERS")
  -S DIR    seeds directory                            (default: $SEEDS_DIR)
  -N NAME   seed-set label used in run names           (default: $SEED_NAME)
  -j N      parallel workers (fork children / plain processes)  (default: $JOBS)
  -F MODE   fork | plain                               (default: $FUZZ_MODE)
  -n        do not (re)build, use existing binaries
  -R        re-triage only: replay the artifacts of the latest run of each configuration and
            regenerate report.md/report.tsv (no build, no fuzzing; use after changing SIG_*)
  -P        skip the seed pre-check (seeds that crash are normally moved out of the corpus)
  -m        minimise one representative per crash group
  -h        this help
Env overrides: MAX_LEN TIMEOUT RSS_MB SIG_FRAMES SIG_TOP_ONLY CC_BIN PROJECT_ROOT WORK_ROOT CRASH_ROOT
EOF
}

while getopts "t:r:o:s:S:N:j:F:nmPRh" opt; do
  case $opt in
    t) MAX_TIME=$OPTARG ;;  r) MAX_RUNS=$OPTARG ;;  o) OPT_LEVELS=$OPTARG ;;
    s) SANITIZERS=$OPTARG ;; S) SEEDS_DIR=$OPTARG ;; N) SEED_NAME=$OPTARG ;;
    j) JOBS=$OPTARG ;;       n) DO_BUILD=0 ;;        m) MINIMIZE=1 ;;
    F) FUZZ_MODE=$OPTARG ;; P) SEED_CHECK=0 ;; R) REPORT_ONLY=1 ;;
    h) usage; exit 0 ;;      *) usage; exit 2 ;;
  esac
done
if [[ "$MAX_TIME" == 0 && "$MAX_RUNS" == 0 ]]; then MAX_TIME=600; fi
(( REPORT_ONLY )) && DO_BUILD=0
[[ "$FUZZ_MODE" == fork || "$FUZZ_MODE" == plain ]] || { echo "-F must be 'fork' or 'plain'" >&2; exit 2; }

log() { printf '\033[1;34m[fuzz]\033[0m %s\n' "$*" >&2; }   # stderr: build_cfg's stdout is captured
die() { printf '\033[1;31m[fuzz] error:\033[0m %s\n' "$*" >&2; exit 1; }

[[ -d "$SEEDS_DIR" ]] || die "seeds directory not found: $SEEDS_DIR"
command -v "$CC_BIN" >/dev/null || die "compiler '$CC_BIN' not found (libFuzzer needs clang)"
if command -v sha1sum >/dev/null; then SHA=sha1sum; else SHA="shasum -a 1"; fi

# ---- Symbolizer: without it stack traces have no file:line, so nothing can be grouped
SYMBOLIZER=""
for c in "$("$CC_BIN" -print-prog-name=llvm-symbolizer 2>/dev/null)" llvm-symbolizer \
         $(ls /usr/bin/llvm-symbolizer-* 2>/dev/null | sort -V | tail -1); do
  if [[ -n "$c" ]] && command -v "$c" >/dev/null 2>&1; then SYMBOLIZER="$(command -v "$c")"; break; fi
done
[[ -n "$SYMBOLIZER" ]] || die "llvm-symbolizer not found (apt install llvm / brew install llvm)"

# ---- Runtime options of the sanitizers ----------------------------------------
export ASAN_SYMBOLIZER_PATH="$SYMBOLIZER" MSAN_SYMBOLIZER_PATH="$SYMBOLIZER" UBSAN_SYMBOLIZER_PATH="$SYMBOLIZER"
# (overridable from the environment, e.g. ASAN_OPTIONS=symbolize=1 ./run_fuzz.sh ...)
export ASAN_OPTIONS="${ASAN_OPTIONS:-detect_leaks=1:detect_stack_use_after_return=1:strict_string_checks=1:symbolize=1}"
export UBSAN_OPTIONS="${UBSAN_OPTIONS:-print_stacktrace=1:halt_on_error=1:symbolize=1}"
export MSAN_OPTIONS="${MSAN_OPTIONS:-halt_on_error=1:symbolize=1}"

INTERRUPTED=0
trap 'INTERRUPTED=1' INT

# ------------------------------------------------------------------------------
# build O S  -> echoes the path of the binary. The flags live in build_fuzzers.sh (single
# source of truth: the binaries it builds are exactly the ones used here).
# ------------------------------------------------------------------------------
build_cfg() {
  local o=$1 san=$2 bdir="$BUILD_ROOT/O${o}-${san}"
  if (( DO_BUILD )); then
    CC_BIN="$CC_BIN" BUILD_ROOT="$BUILD_ROOT" "$SCRIPT_DIR/build_fuzzers.sh" -q -o "$o" -s "$san" >&2 \
      || die "build of O${o}/${san} failed (see ${bdir#$ROOT_DIR/}.*.log)"
  fi
  [[ -x "$bdir/fuzz_decoder" ]] || die "missing binary $bdir/fuzz_decoder (run build_fuzzers.sh or drop -n)"
  echo "$bdir/fuzz_decoder"
}

# ------------------------------------------------------------------------------
# Triage helpers
# ------------------------------------------------------------------------------
# First contiguous block of "#N 0x... in ..." lines = the stack of the error itself.
first_stack_block() {
  awk '/^[[:space:]]*#[0-9]+ 0x[0-9a-f]+/ { print; inb=1; next } inb { exit }' "$1"
}

# Reduce a raw stack to "function path:line" for frames inside PROJECT_ROOT only
# (drops libFuzzer / sanitizer runtime / libc frames, addresses and columns).
normalize_stack() {
  local raw=$1 out
  out=$(sed -E 's/^[[:space:]]*#[0-9]+ 0x[0-9a-f]+ in //' "$raw" \
        | awk -v root="$PROJECT_ROOT/" 'index($0, " " root) { sub(root, ""); print }' \
        | sed -E 's/:([0-9]+):[0-9]+$/:\1/')
  if [[ -z "$out" ]]; then   # no frame in our code: fall back to module+symbol, addresses stripped
    out=$(sed -E 's/^[[:space:]]*#[0-9]+ 0x[0-9a-f]+ //; s/0x[0-9a-f]+//g' "$raw")
  fi
  # crash inside a low-level helper (e.g. bitreader_refill_half in src/util/bitreader.h):
  # the callers vary with every syntax element, but the bug is the helper's -> crash frame only
  if [[ -n "$SIG_TOP_ONLY" ]] && printf '%s\n' "$out" | head -n 1 | grep -Eq "$SIG_TOP_ONLY"; then
    out=$(printf '%s\n' "$out" | head -n 1)
  fi
  if (( SIG_FRAMES > 0 )); then printf '%s\n' "$out" | head -n "$SIG_FRAMES"; else printf '%s\n' "$out"; fi
}

kind_of() {
  local l=$1 k
  if k=$(grep -m1 -oE 'ERROR: AddressSanitizer: [A-Za-z_-]+' "$l"); then echo "ASan: ${k##* }"; return; fi
  if k=$(grep -m1 -oE 'WARNING: MemorySanitizer: [A-Za-z_-]+' "$l"); then echo "MSan: ${k##* }"; return; fi
  if k=$(grep -m1 -oE 'runtime error: .*' "$l"); then
    echo "UBSan: ${k#runtime error: }" | sed -E 's/[0-9]+/N/g' | cut -c1-80; return; fi
  if grep -q 'ERROR: LeakSanitizer' "$l"; then echo "LSan: memory-leak"; return; fi
  if grep -q -E "Assertion .* failed" "$l"; then echo "assertion-failure"; return; fi
  if k=$(grep -m1 -oE 'ERROR: libFuzzer: [a-z -]+' "$l"); then k=${k#ERROR: libFuzzer: }; echo "libFuzzer: ${k%% after*}"; return; fi
  echo "unknown"
}

# analyze BIN CRASHDIR OPT SAN RUNNAME   -> writes replay/, report.md, report.tsv
analyze_crashes() {
  local bin=$1 cdir=$2 o=$3 san=$4 run=$5
  local rdir="$cdir/replay"; mkdir -p "$rdir"
  : > "$rdir/index.tsv"
  local -a files=()
  while IFS= read -r f; do files+=("$f"); done < <(
    find "$cdir" -maxdepth 1 -type f \( -name 'crash-*' -o -name 'timeout-*' -o -name 'oom-*' -o -name 'leak-*' \) | sort)
  local total=${#files[@]} nonrepro=0 f
  log "replaying $total artifact(s) from ${cdir#$SCRIPT_DIR/}"

  : > "$rdir/nonreproducible.txt"
  for f in "${files[@]+"${files[@]}"}"; do
    local name; name=$(basename "$f")
    timeout $((TIMEOUT + 30)) "$bin" -timeout="$TIMEOUT" -rss_limit_mb="$RSS_MB" "$f" >"$rdir/$name.log" 2>&1
    local rc=$?
    if (( rc == 0 )); then echo "$name" >> "$rdir/nonreproducible.txt"; nonrepro=$((nonrepro+1)); continue; fi
    first_stack_block "$rdir/$name.log" > "$rdir/$name.rawstack"
    if [[ ! -s "$rdir/$name.rawstack" ]]; then echo "(no stack trace; exit code $rc)" > "$rdir/$name.rawstack"; fi
    normalize_stack "$rdir/$name.rawstack" > "$rdir/$name.stack"
    local sig; sig=$($SHA < "$rdir/$name.stack" | cut -c1-10)
    local kind; kind=$(kind_of "$rdir/$name.log")
    local size; size=$(wc -c < "$f" | tr -d ' ')
    printf '%s\t%s\t%s\t%s\t%s\n' "$sig" "$size" "$name" "$kind" "$(head -1 "$rdir/$name.stack")" >> "$rdir/index.tsv"
  done

  local nrepro=$((total - nonrepro))
  local groups; groups=$(cut -f1 "$rdir/index.tsv" | sort | uniq -c | sort -rn)
  local ngroups; ngroups=$(printf '%s' "$groups" | grep -c . || true)

  # ---- report.tsv (one line per group) and report.md ----------------------------
  : > "$cdir/report.tsv"
  {
    echo "# Fuzzing report — O${o} / ${san} / ${run}"
    echo
    echo "- Binary: \`${bin#$ROOT_DIR/}\`"
    echo "- Seeds: \`$SEED_NAME\`   Date: $(date -u +%F\ %T) UTC"
    echo "- Artifacts saved: **$total** — reproduced: **$nrepro** — not reproducible: **$nonrepro**"
    echo "- Unique stack traces: **$ngroups**"
    echo
    if (( ngroups > 0 )); then
      echo "| # | Signature | Inputs | Kind(s) | Top frame | Smallest input |"
      echo "|---|-----------|-------:|---------|-----------|----------------|"
    fi
  } > "$cdir/report.md"

  local i=0 count sig
  local details="$rdir/details.md"; : > "$details"
  while read -r count sig; do
    [[ -z "${sig:-}" ]] && continue
    i=$((i+1))
    local rep kinds top members
    rep=$(awk -F'\t' -v s="$sig" '$1==s {print $2"\t"$3}' "$rdir/index.tsv" | sort -n | head -1 | cut -f2)
    kinds=$(awk -F'\t' -v s="$sig" '$1==s {print $4}' "$rdir/index.tsv" | sort -u | paste -sd';' -)
    top=$(awk -F'\t' -v s="$sig" '$1==s {print $5; exit}' "$rdir/index.tsv")
    members=$(awk -F'\t' -v s="$sig" '$1==s {print $3}' "$rdir/index.tsv")
    local repsize; repsize=$(awk -F'\t' -v s="$sig" -v n="$rep" '$1==s && $3==n {print $2}' "$rdir/index.tsv")
    local minfile=""
    if (( MINIMIZE )); then
      minfile="$rdir/$sig.min"
      log "minimising $rep (group $sig)"
      "$bin" -minimize_crash=1 -runs="$MINIMIZE_RUNS" -timeout="$TIMEOUT" -rss_limit_mb="$RSS_MB" \
             -exact_artifact_path="$minfile" "$cdir/$rep" >"$rdir/$sig.minimize.log" 2>&1 || true
      [[ -s "$minfile" ]] || minfile=""
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$sig" "$count" "$kinds" "$top" "$rep" "$repsize" >> "$cdir/report.tsv"
    echo "| $i | \`$sig\` | $count | $kinds | \`$top\` | \`$rep\` ($repsize B) |" >> "$cdir/report.md"
    {
      echo "### Crash $i — \`$sig\`"
      echo
      echo "- Kind(s): $kinds"
      echo "- Inputs with this stack ($count): $(echo "$members" | sed 's/.*/`&`/' | paste -sd' ' -)"
      echo "- Reproduce: \`${bin#$ROOT_DIR/} crashes/O${o}/${san}/${run}/${rep}\`"
      [[ -n "$minfile" ]] && echo "- Minimised input: \`${minfile#$SCRIPT_DIR/}\`"
      echo
      echo "Stack (project frames only):"
      echo '```'
      cat "$rdir/$rep.stack"
      echo '```'
      echo
      echo "<details><summary>Sanitizer output (first 60 lines)</summary>"
      echo
      echo '```'
      head -60 "$rdir/$rep.log"
      echo '```'
      echo "</details>"
      echo
    } >> "$details"
  done <<< "$groups"

  if (( ngroups > 0 )); then { echo; echo "## Details"; echo; cat "$details"; } >> "$cdir/report.md"; fi
  if (( nonrepro > 0 )); then
    { echo "## Not reproducible when replayed alone"; echo
      sed 's/.*/- `&`/' "$rdir/nonreproducible.txt"; } >> "$cdir/report.md"
  fi
  rm -f "$details"
  SUMMARY+=("O${o}/${san}/${run}|$total|$nrepro|$ngroups")
  log "O${o}/${san}: $total artifact(s), $nrepro reproduced, $ngroups unique stack trace(s) -> ${cdir#$SCRIPT_DIR/}/report.md"
}

# ------------------------------------------------------------------------------
# Seed pre-check
# ------------------------------------------------------------------------------
# libFuzzer executes every seed before it starts mutating. A seed that crashes the decoder
# stops a plain-mode run right there (and every restart), so run each seed once first.
# Seeds are cut to MAX_LEN exactly as libFuzzer would (head -c). Crashing seeds are saved as
# crashes/.../crash-seed-<name> (they are findings, and get triaged like any other artifact);
# the others are copied to crashes/.../seeds_ok/, which is what the fuzzer is given.
# Echoes the directory to use as seed corpus.
preflight_seeds() { # bin cdir
  local bin=$1 cdir=$2 good="$2/seeds_ok" f n=0 bad=0 rel flat
  mkdir -p "$good"
  while IFS= read -r f; do
    rel=${f#"$SEEDS_DIR"/}; flat=${rel//\//__}
    head -c "$MAX_LEN" "$f" > "$good/$flat"
    n=$((n + 1))
    if ! timeout $((TIMEOUT + 30)) "$bin" -timeout="$TIMEOUT" -rss_limit_mb="$RSS_MB" "$good/$flat" >/dev/null 2>&1; then
      mv "$good/$flat" "$cdir/crash-seed-$flat"; bad=$((bad + 1))
    fi
  done < <(find "$SEEDS_DIR" -type f | sort)
  log "seed check: $n seed(s), $bad crash(es)/hang(s) -> saved as crash-seed-* ; $((n - bad)) usable"
  echo "$good"
}

# ------------------------------------------------------------------------------
# Fuzzing sessions
# ------------------------------------------------------------------------------
# libFuzzer flags shared by both modes
base_args() { # corpus cdir
  printf '%s\n' "$1" "$ACTIVE_SEEDS" "-max_len=$MAX_LEN" "-timeout=$TIMEOUT" "-rss_limit_mb=$RSS_MB" \
                "-artifact_prefix=$2/" "-print_final_stats=1"
}

# One fork-mode session of at most $4 seconds (0 = no time limit, then MAX_RUNS applies).
session_fork() { # bin corpus cdir left
  local -a args=(); while IFS= read -r a; do args+=("$a"); done < <(base_args "$2" "$3")
  args+=(-fork="$JOBS" -ignore_crashes=1 -ignore_timeouts=1 -ignore_ooms=1)
  (( $4 > 0 ))       && args+=(-max_total_time="$4")
  (( MAX_RUNS > 0 )) && args+=(-runs="$MAX_RUNS")
  "$1" "${args[@]}" 2>&1 | tee -a "$3/fuzz.log" | grep -E '^(#[0-9]+|INFO: (fuzzed|exiting))' | tail -n 3
}

# Plain mode: $JOBS independent libFuzzer processes on ONE corpus dir (libFuzzer reloads the
# corpus periodically, so they see each other's finds). A process ends at its first crash;
# its loop restarts it until the deadline ($4 = value of $SECONDS at which to stop).
session_plain() { # bin corpus cdir deadline
  local bin=$1 corpus=$2 cdir=$3 end=$4 k
  local -a pids=() base=(); while IFS= read -r a; do base+=("$a"); done < <(base_args "$corpus" "$cdir")
  for (( k=1; k<=JOBS; k++ )); do
    (
      restarts=0
      while :; do
        args=("${base[@]}")
        if (( MAX_TIME > 0 )); then
          left=$(( end - SECONDS )); (( left < 3 )) && break
          args+=(-max_total_time="$left")
        elif (( restarts > 0 )); then break; fi
        (( MAX_RUNS > 0 )) && args+=(-runs=$(( (MAX_RUNS + JOBS - 1) / JOBS )))
        "$bin" "${args[@]}" >>"$cdir/fuzz.worker$k.log" 2>&1
        restarts=$((restarts + 1)); (( restarts >= MAX_RESTARTS )) && break
      done
    ) &
    pids+=($!)
  done
  wait "${pids[@]}"
}

# Fuzz one configuration. Fork mode is used first; if the fork *parent* keeps dying (see
# comments below) the configuration falls back to plain mode for the remaining time.
fuzz_config() { # bin corpus cdir
  local bin=$1 corpus=$2 cdir=$3 mode=$FUZZ_MODE
  local end=$(( SECONDS + MAX_TIME )) restarts=0 quick_fail=0
  : > "$cdir/fuzz.log"
  if [[ $mode == fork ]]; then
    while :; do
      local left=0
      if (( MAX_TIME > 0 )); then left=$(( end - SECONDS )); (( left < 3 )) && break
      elif (( restarts > 0 )); then break; fi
      local t0=$SECONDS
      log "fork-mode session $((restarts + 1)) (budget ${left}s, ${JOBS} child process(es))"
      session_fork "$bin" "$corpus" "$cdir" "$left"
      restarts=$((restarts + 1))
      # A zero-byte artifact is written by libFuzzer's own crash handler when the fork PARENT
      # dies (SHA-1 da39a3ee... = empty input). It is not a decoder bug: discard it.
      local bad; bad=$(find "$cdir" -maxdepth 1 -type f -size 0 \( -name 'crash-*' -o -name 'timeout-*' -o -name 'oom-*' -o -name 'leak-*' \) | wc -l | tr -d ' ')
      find "$cdir" -maxdepth 1 -type f -size 0 \( -name 'crash-*' -o -name 'timeout-*' -o -name 'oom-*' -o -name 'leak-*' \) -delete
      local ran=$(( SECONDS - t0 ))
      if (( bad > 0 )) || (( MAX_TIME > 0 && ran < left - 5 )); then
        quick_fail=$((quick_fail + 1))
        log "WARNING: libFuzzer's fork parent died after ${ran}s (internal libFuzzer fault, not the decoder); see $cdir/fuzz.log"
        if (( quick_fail >= 2 || MAX_TIME == 0 )); then
          log "falling back to plain mode for the rest of this configuration"; mode=plain; break
        fi
      else
        quick_fail=0
      fi
      (( restarts >= MAX_RESTARTS )) && break
    done
  fi
  if [[ $mode == plain ]]; then
    log "plain-mode fuzzing: $JOBS process(es), restarted after every crash"
    session_plain "$bin" "$corpus" "$cdir" "$end"
    if (( MAX_TIME > 0 && end - SECONDS > 5 )); then
      log "WARNING: plain-mode workers stopped $(( end - SECONDS ))s early (MAX_RESTARTS=$MAX_RESTARTS reached): a shallow bug"
      log "         (or a seed input) crashes the decoder right after every restart. Fix it, remove that seed, or raise MAX_RESTARTS."
    fi
  fi
  local nc; nc=$(find "$corpus" -type f | wc -l | tr -d ' ')
  log "corpus $(basename "$corpus"): $nc new input(s) saved in ${corpus#$SCRIPT_DIR/}"
}

# ------------------------------------------------------------------------------
# Main loop
# ------------------------------------------------------------------------------
declare -a SUMMARY=()
for o in $OPT_LEVELS; do
  for san in $SANITIZERS; do
    (( INTERRUPTED )) && { log "interrupted, skipping remaining configurations"; break 2; }
    bin=$(build_cfg "$o" "$san") || exit 1

    if (( REPORT_ONLY )); then
      last=$(find "$CRASH_ROOT/O${o}/${san}" -mindepth 1 -maxdepth 1 -type d -name 'run_*' 2>/dev/null | sort -V | tail -1)
      [[ -n "$last" ]] || { log "no run found for O${o}/${san}, skipping"; continue; }
      analyze_crashes "$bin" "$last" "$o" "$san" "$(basename "$last")"
      continue
    fi

    parent="$WORK_ROOT/O${o}/${san}"; mkdir -p "$parent"
    n=$(find "$parent" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
    while [[ -e "$parent/run_${n}_${SEED_NAME}" || -e "$CRASH_ROOT/O${o}/${san}/run_${n}_${SEED_NAME}" ]]; do n=$((n+1)); done
    run="run_${n}_${SEED_NAME}"
    corpus="$parent/$run"; cdir="$CRASH_ROOT/O${o}/${san}/$run"
    mkdir -p "$corpus" "$cdir"

    ACTIVE_SEEDS="$SEEDS_DIR"
    (( SEED_CHECK )) && ACTIVE_SEEDS=$(preflight_seeds "$bin" "$cdir")
    log "fuzzing O${o}/${san} as $run  (mode=$FUZZ_MODE time=${MAX_TIME}s runs=${MAX_RUNS} jobs=$JOBS)"
    fuzz_config "$bin" "$corpus" "$cdir"
    analyze_crashes "$bin" "$cdir" "$o" "$san" "$run"
  done
done

echo
printf '%-45s %10s %11s %8s\n' "configuration/run" "artifacts" "reproduced" "unique"
for s in "${SUMMARY[@]+"${SUMMARY[@]}"}"; do IFS='|' read -r a b c d <<< "$s"; printf '%-45s %10s %11s %8s\n' "$a" "$b" "$c" "$d"; done