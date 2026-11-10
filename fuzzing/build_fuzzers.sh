#!/usr/bin/env bash
# ==============================================================================
# build_fuzzers.sh — build the libFuzzer binaries of undo264
# Written by Claude Sonnet 5.5
#
# Builds fuzz_decoder once per (optimisation level x sanitizer):
#
#     build/fuzz/O3-address/fuzz_decoder      build/fuzz/O0-address/fuzz_decoder
#     build/fuzz/O3-undefined/fuzz_decoder    build/fuzz/O0-undefined/fuzz_decoder
#     build/fuzz/O3-memory/fuzz_decoder       build/fuzz/O0-memory/fuzz_decoder
#
# Each binary is a normal libFuzzer executable: run it with a corpus directory, e.g.
#     build/fuzz/O3-address/fuzz_decoder corpus/ seeds/ -max_len=65536 -timeout=10
# fuzzing/run_fuzz.sh is one way to drive them (fork mode, crash triage, reports);
# you are free to use them however you like.
#
# Usage: ./build_fuzzers.sh [options]
#   -o LIST    optimisation levels            (default: "3 0")
#   -s LIST    sanitizers                     (default: "address+undefined")
#   -D VAR=VAL extra CMake definition, repeatable, e.g. -D USE_NATIVE_ARCH=OFF
#   -q         quiet: no summary at the end
#   -h         help
# Env: CC_BIN (default clang)   BUILD_ROOT (default <repo>/build/fuzz)
#
# Requires: clang with libFuzzer (GCC has none), cmake >= 3.20.
#   macOS: AppleClang does not ship libFuzzer -> `brew install llvm`, CC_BIN=$(brew --prefix llvm)/bin/clang
# ==============================================================================
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
CC_BIN="${CC_BIN:-clang}"
BUILD_ROOT="${BUILD_ROOT:-$ROOT_DIR/build/fuzz}"
OPT_LEVELS="3 0"
SANITIZERS="address+undefined"
QUIET=0
EXTRA_CMAKE=()

usage() { sed -n '2,/^set -u/p' "${BASH_SOURCE[0]}" | sed '$d' | grep '^#' | sed 's/^# \{0,1\}//'; }

while getopts "o:s:D:qh" opt; do
  case $opt in
    o) OPT_LEVELS=$OPTARG ;;  s) SANITIZERS=$OPTARG ;;  D) EXTRA_CMAKE+=("-D$OPTARG") ;;
    q) QUIET=1 ;;             h) usage; exit 0 ;;       *) usage; exit 2 ;;
  esac
done

log() { printf '\033[1;34m[build]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[build] error:\033[0m %s\n' "$*" >&2; exit 1; }

command -v cmake >/dev/null    || die "cmake not found"
command -v "$CC_BIN" >/dev/null || die "compiler '$CC_BIN' not found (libFuzzer needs clang)"

# ---- Does this compiler actually ship libFuzzer? ------------------------------------------------
# (AppleClang and GCC do not; the error at link time is cryptic, so test it up front.)
_t=$(mktemp -d); trap 'rm -rf "$_t"' EXIT
echo 'int LLVMFuzzerTestOneInput(const unsigned char *d, unsigned long n) { (void)d; return (int)(n & 0); }' > "$_t/t.c"
"$CC_BIN" -fsanitize=fuzzer "$_t/t.c" -o "$_t/t" >"$_t/log" 2>&1 \
  || { cat "$_t/log" >&2; die "'$CC_BIN' cannot link libFuzzer (-fsanitize=fuzzer). Use clang from LLVM (apt install clang libclang-rt-dev / brew install llvm)."; }

gen=(); command -v ninja >/dev/null && gen=(-G Ninja)
jobs=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

# ------------------------------------------------------------------------------
# What the flags do (see also the CMake SANITIZE option, which turns the list into -fsanitize=...)
#
#  -DSANITIZE=<san>,fuzzer-no-link
#       <san>             address | undefined | memory: the bug detector linked into the binary.
#                         They are separate binaries because ASan and MSan cannot be combined.
#       fuzzer-no-link    SanitizerCoverage: clang inserts a counter increment on every basic-block
#                         edge. libFuzzer reads these "inline 8-bit counters" to learn which inputs
#                         reach new code. "no-link" = instrument only; libFuzzer itself is linked via
#                         FUZZ_ENGINE (-fsanitize=fuzzer) when linking the fuzz_decoder target.
#  -g -fno-omit-frame-pointer
#                         file:line in stack traces, and cheap, reliable stack unwinding.
#  -fno-sanitize-recover=undefined   (undefined only)
#                         By default UBSan prints a warning and carries on, which libFuzzer cannot
#                         see. This makes the first UB abort the process, so it is a "crash".
#  -fsanitize-memory-track-origins   (memory only)
#                         MSan also reports where an uninitialised value was created.
#  -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
#                         Conventional libFuzzer/OSS-Fuzz macro: code may relax checks that block
#                         fuzzing (e.g. checksums). Harmless if unused.
#  BUILD_FUZZERS=ON       adds the fuzz_decoder target and -UNDEBUG: assert() stays active even in
#                         Release builds, so a failed assertion is a finding too.
#  O3  -> CMAKE_BUILD_TYPE=Release (-O3, what you ship)
#  O0  -> RelWithDebInfo with -O0 -g3 (Debug is avoided: it defines SLICES_LOG, which floods output
#         and would slow fuzzing). Same bug, but easier to debug, and no optimiser-dependent UB.
#  -march=native          comes from USE_NATIVE_ARCH (default ON): the binary only runs on a CPU
#                         like the one that built it. For binaries you want to share, pass
#                         -D USE_NATIVE_ARCH=OFF (and a -DCMAKE_C_FLAGS_INIT/-march of your choice).
# ------------------------------------------------------------------------------
build_one() { # opt san
  local o=$1 san=$2 bdir="$BUILD_ROOT/O${o}-${san}"
    local cflags="-g -fno-omit-frame-pointer -fsanitize=fuzzer-no-link -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION"
    local san_opt=() s
    for s in ${san//+/ }; do
      case $s in
        address)   san_opt+=(-DENABLE_ASAN=ON) ;;
        undefined) san_opt+=(-DENABLE_UBSAN=ON); cflags+=" -fno-sanitize-recover=undefined" ;;
        memory)    san_opt+=(-DENABLE_MSAN=ON);  cflags+=" -fsanitize-memory-track-origins" ;;
        *) die "unknown sanitizer '$s' in '$san' (address | undefined | memory, joined with +)" ;;
      esac
    done

  local type=Release extra=()
  if [[ "$o" != 3 ]]; then type=RelWithDebInfo; extra=("-DCMAKE_C_FLAGS_RELWITHDEBINFO=-O${o} -g3"); fi

  mkdir -p "$BUILD_ROOT"
  log "O${o}/${san}: configuring in ${bdir#$ROOT_DIR/}"
  cmake -S "$ROOT_DIR" -B "$bdir" "${gen[@]}" \
    -DCMAKE_C_COMPILER="$CC_BIN" -DUNDO264_COMPILER=clang \
    -DCMAKE_BUILD_TYPE="$type" "${extra[@]}" \
    -DBUILD_FUZZERS=ON -DBUILD_TOOLS=OFF \
    "$san_opt" "-DCMAKE_C_FLAGS=${cflags}" \
    "${EXTRA_CMAKE[@]+"${EXTRA_CMAKE[@]}"}" >"$bdir.cmake.log" 2>&1 \
    || { tail -20 "$bdir.cmake.log" >&2; die "cmake configure failed (log: $bdir.cmake.log)"; }
  log "O${o}/${san}: building"
  cmake --build "$bdir" --target fuzz_decoder -j "$jobs" >"$bdir.build.log" 2>&1 \
    || { tail -30 "$bdir.build.log" >&2; die "build failed (log: $bdir.build.log)"; }
  [[ -x "$bdir/fuzz_decoder" ]] || die "build finished but $bdir/fuzz_decoder is missing"
}

for o in $OPT_LEVELS; do for san in $SANITIZERS; do build_one "$o" "$san"; done; done

if (( ! QUIET )); then
  echo
  echo "Built fuzz binaries:"
  for o in $OPT_LEVELS; do for san in $SANITIZERS; do
    printf '  -O%s  %-10s %s\n' "$o" "$san" "${BUILD_ROOT#$ROOT_DIR/}/O${o}-${san}/fuzz_decoder"
  done; done
  cat <<EOF

Run one (plain libFuzzer; stops at the first crash):
  mkdir -p corpus crashes
  ${BUILD_ROOT#$ROOT_DIR/}/O3-address/fuzz_decoder corpus/ fuzzing/seeds/ \\
      -max_len=65536 -timeout=10 -rss_limit_mb=2048 -artifact_prefix=crashes/
Replay one input:
  ${BUILD_ROOT#$ROOT_DIR/}/O3-address/fuzz_decoder crashes/crash-<hash>

Sanitizer runtime options worth setting:
  export ASAN_OPTIONS=detect_leaks=1:symbolize=1
  export UBSAN_OPTIONS=print_stacktrace=1:halt_on_error=1   # without print_stacktrace UBSan shows no stack
  export MSAN_OPTIONS=halt_on_error=1:symbolize=1
Keep going after crashes with parallel workers: add  -fork=4 -ignore_crashes=1 -ignore_timeouts=1 -ignore_ooms=1
EOF
fi