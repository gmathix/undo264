#!/usr/bin/env bash
# ==============================================================================
# build_fuzzers.sh — build the AFL++ binaries of undo264
#
# Builds fuzz_decoder once per (optimisation level x sanitizer variant):
#
#     build/afl/O3-address+undefined/fuzz_decoder   (slow, finds memory bugs / UB)
#     build/afl/O3-none/fuzz_decoder                (fast, finds crashes and hangs)
#
# fuzz_target.c is NOT changed: afl-clang-fast links AFL++'s libAFLDriver when it sees
# -fsanitize=fuzzer, which runs LLVMFuzzerTestOneInput() in persistent mode.
#
# Usage: ./build_fuzzers.sh [options]
#   -o LIST    optimisation levels      (default: "3")
#   -s LIST    sanitizer variants       (default: "address+undefined none")
#              one variant = "none" or any of address / undefined / memory joined with +
#   -D VAR=VAL extra CMake definition, repeatable
#   -q         quiet
#   -h         help
# Env: CC_BIN (default afl-clang-fast)   BUILD_ROOT (default <repo>/build/afl)
#
# Requires: AFL++ built with LLVM mode (afl-clang-fast), cmake >= 3.20
# ==============================================================================
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
CC_BIN="${CC_BIN:-afl-clang-fast}"
BUILD_ROOT="${BUILD_ROOT:-$ROOT_DIR/build/afl}"
OPT_LEVELS="3"
SANITIZERS="address+undefined none"
QUIET=0
EXTRA=""

while getopts "o:s:D:qh" opt; do
  case $opt in
    o) OPT_LEVELS=$OPTARG ;;
    s) SANITIZERS=$OPTARG ;;
    D) EXTRA="$EXTRA -D$OPTARG" ;;
    q) QUIET=1 ;;
    h) sed -n '2,/^set -eu/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) exit 2 ;;
  esac
done

log() { echo "[build] $*" >&2; }
die() { echo "[build] error: $*" >&2; exit 1; }

command -v cmake >/dev/null || die "cmake not found"
command -v "$CC_BIN" >/dev/null || die "$CC_BIN not found (install AFL++ with LLVM mode)"

# afl-clang-fast prints a banner on every call: silence it
export AFL_QUIET=1

# Check that the compiler can link the AFL++ libFuzzer driver
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
echo 'int LLVMFuzzerTestOneInput(const unsigned char *d, unsigned long n) { return 0; }' > "$tmp/t.c"
"$CC_BIN" -fsanitize=fuzzer "$tmp/t.c" -o "$tmp/t" >"$tmp/log" 2>&1 \
  || { cat "$tmp/log" >&2; die "$CC_BIN cannot link with -fsanitize=fuzzer"; }

gen=""
if command -v ninja >/dev/null; then gen="-G Ninja"; fi
jobs=$(nproc 2>/dev/null || echo 4)

build_one() {
  o=$1
  san=$2
  bdir="$BUILD_ROOT/O${o}-${san}"

  # -g + frame pointers: readable stack traces. DFUZZING_BUILD_MODE...: usual fuzzing macro.
  cflags="-g -fno-omit-frame-pointer -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION"
  san_opts=""
  case "$san" in *address*)   san_opts="$san_opts -DENABLE_ASAN=ON" ;; esac
  case "$san" in *memory*)    san_opts="$san_opts -DENABLE_MSAN=ON"; cflags="$cflags -fsanitize-memory-track-origins" ;; esac
  case "$san" in *undefined*) san_opts="$san_opts -DENABLE_UBSAN=ON"; cflags="$cflags -fno-sanitize-recover=undefined" ;; esac
  if [ "$san" != none ] && [ -z "$san_opts" ]; then die "unknown sanitizer variant '$san'"; fi

  # O3 = Release. Other levels = RelWithDebInfo with the optimisation level overridden.
  type=Release
  type_opts=""
  if [ "$o" != 3 ]; then
    type=RelWithDebInfo
    type_opts="-DCMAKE_C_FLAGS_RELWITHDEBINFO=-O$o"
  fi

  mkdir -p "$BUILD_ROOT"
  log "O$o/$san: configuring"
  cmake -S "$ROOT_DIR" -B "$bdir" $gen \
    -DCMAKE_C_COMPILER="$CC_BIN" -DUNDO264_COMPILER=clang \
    -DCMAKE_BUILD_TYPE=$type $type_opts \
    -DBUILD_FUZZERS=ON -DBUILD_TOOLS=OFF \
    $san_opts "-DCMAKE_C_FLAGS=$cflags" $EXTRA \
    >"$bdir.cmake.log" 2>&1 \
    || { tail -20 "$bdir.cmake.log" >&2; die "configure failed (log: $bdir.cmake.log)"; }

  log "O$o/$san: building"
  cmake --build "$bdir" --target fuzz_decoder -j "$jobs" >"$bdir.build.log" 2>&1 \
    || { tail -30 "$bdir.build.log" >&2; die "build failed (log: $bdir.build.log)"; }

  [ -x "$bdir/fuzz_decoder" ] || die "$bdir/fuzz_decoder is missing"
}

for o in $OPT_LEVELS; do
  for san in $SANITIZERS; do
    build_one "$o" "$san"
  done
done

if [ "$QUIET" -eq 0 ]; then
  echo
  echo "Built:"
  for o in $OPT_LEVELS; do
    for san in $SANITIZERS; do
      echo "  $BUILD_ROOT/O${o}-${san}/fuzz_decoder"
    done
  done
  echo
  echo "Replay one input:  <binary> some_input_file"
  echo "Fuzz:              fuzzing/run_fuzz.sh"
fi