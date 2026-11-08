#!/bin/bash

# Usage: ./conformance_test.sh [--target <list>] [--catch-san-err] <input_dir>
#   <input_dir>        e.g. AVCv1/ or FRExt/
#   --target <list>    release (default) | asan | ubsan | msan | all, or a comma-separated list (e.g. release,asan)
#   --catch-san-err    sanitizer errors make the script exit with 2 (they are always reported)
#
# The selected targets are built first (build/<target>/undo264). For each subdirectory of <input_dir>,
# each target decodes the bitstream, and the output is compared with the reference .yuv
# (or with ffmpeg's decoding if there is no single reference file).
# Decoder exit codes: 0 = decoded, 3 = out of scope, 1 = sanitizer error, other = decode failure.
#
# Exit code: 0 = all bit-exact (and no sanitizer error if --catch-san-err)
#            1 = a bitstream is not bit-exact or failed to decode
#            2 = all bit-exact, but a sanitizer reported an error (with --catch-san-err)

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGETS=(release)
CATCH_SAN=0
INPUT_DIR=""

usage() {
    echo "Usage: $0 [--target release|asan|ubsan|msan|all[,...]] [--catch-san-err] <input_dir>" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)
            [[ $# -ge 2 ]] || usage
            val="$2"; [[ "$val" == all ]] && val="release,asan,ubsan,msan"
            IFS=, read -ra TARGETS <<< "$val"
            shift 2 ;;
        --catch-san-err) CATCH_SAN=1; shift ;;
        -*) usage ;;
        *) INPUT_DIR="$1"; shift ;;
    esac
done

if [[ ! -d "$INPUT_DIR" ]]; then
    echo "Error: '$INPUT_DIR' is not a directory" >&2
    usage
fi

# ---- Colors (also in GitHub Actions logs; disable with NO_COLOR=1) -----------
if [[ -z "$NO_COLOR" && ( -t 1 || -n "$GITHUB_ACTIONS" ) ]]; then
    RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; BOLD=$'\e[1m'; RST=$'\e[0m'
fi
tag() { printf '  %s[%s]%s ' "$1" "$2" "$RST"; }   # tag <color> <text>

# ---- Sanitizer runtime options: make every error exit with code 1 ------------
export ASAN_OPTIONS=detect_leaks=1:exitcode=1
export LSAN_OPTIONS=exitcode=1
export UBSAN_OPTIONS=halt_on_error=1:exitcode=1:print_stacktrace=1
export MSAN_OPTIONS=halt_on_error=1:exit_code=1

declare -A SAN_NAME=([asan]=ASan [ubsan]=UBSan [msan]=MSan)
declare -A exact mism san fail skip bad_list san_list

# ---- Build -------------------------------------------------------------------
# build_san <target> <ASAN> <UBSAN> <MSAN> [extra cmake args]
build_san() {
    local t="$1"; shift
    cmake -S "$ROOT" -B "$ROOT/build/$t" -DCMAKE_BUILD_TYPE=Debug -DUSE_NATIVE_ARCH=OFF -DBUILD_TOOLS=OFF \
          -DENABLE_ASAN="$1" -DENABLE_UBSAN="$2" -DENABLE_MSAN="$3" "${@:4}" &&
    cmake --build "$ROOT/build/$t" -j "$(nproc)"
}

for t in "${TARGETS[@]}"; do
    echo "${BOLD}Building $t...${RST}"
    case "$t" in
        release) (cd "$ROOT" && cmake --preset release && cmake --build --preset release -j "$(nproc)") ;;
        asan)    build_san asan  ON  OFF OFF ;;
        ubsan)   build_san ubsan OFF ON  OFF ;;
        msan)    build_san msan  OFF OFF ON -DUNDO264_COMPILER=clang ;;
        *) echo "Error: unknown target '$t'" >&2; usage ;;
    esac || { echo "${RED}Build failed for target $t${RST}" >&2; exit 1; }
    if [[ ! -x "$ROOT/build/$t/undo264" ]]; then
        echo "${RED}Error: $ROOT/build/$t/undo264 not found${RST}" >&2
        exit 1
    fi
done

# ---- Reference ---------------------------------------------------------------
# Prepared once per bitstream, after the first successful decode (out-of-scope streams never need it)
prepare_ref() {
    [[ -n "$ref_status" ]] && return "$ref_status"
    ref_status=0
    pix_fmt="$(ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt \
        -of default=noprint_wrappers=1:nokey=1 "$bitstream")"
    if [[ -z "$pix_fmt" ]]; then
        echo "  ffprobe could not determine pix_fmt"
        ref_status=1
    elif [[ $use_ffmpeg -eq 1 ]] && ! ffmpeg -y -v error -i "$bitstream" -f rawvideo -fps_mode passthrough \
            -pix_fmt "$pix_fmt" "$dec_ref" > "${subdir}/${name}_ffmpeg.log" 2>&1; then
        echo "  ffmpeg failed (see ${name}_ffmpeg.log)"
        ref_status=1
    fi
    return "$ref_status"
}

# PSNR report for a bit-inexact output
report_diff() {
    [[ -s "$out" ]] || { echo "      output is empty"; return; }
    local res psnr_log summary
    res="$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "$bitstream")"
    psnr_log="${subdir}/${name}_${t}_psnr.log"
    ffmpeg -y -f rawvideo -pix_fmt "$pix_fmt" -s "$res" -i "$out" \
              -f rawvideo -pix_fmt "$pix_fmt" -s "$res" -i "$dec_ref" \
              -lavfi "psnr=stats_file=${subdir}/${name}_${t}_psnr_perframe.log" -f null - > "$psnr_log" 2>&1
    summary="$(grep -o 'PSNR .*' "$psnr_log" | tail -1)"
    echo "      ${summary:-PSNR filter failed (size/frame-count mismatch?), see $psnr_log}"
}

# ---- Main loop ---------------------------------------------------------------
declare -A exact mism san fail skip

while IFS= read -r -d '' subdir; do
    name="$(basename "$subdir")"
    echo; echo "${BOLD}== $name ==${RST}"

    # remove leftovers of previous runs
    find "$subdir" -maxdepth 2 \( -name '*_undo264.yuv' -o -name '*_ffmpeg.yuv' \) -delete

    mapfile -d '' bitstreams < <(find "$subdir" -maxdepth 2 \( -iname '*264' -o -iname '*.jsv' -o -iname '*.jvt' -o -iname '*.26l' -o -iname '*.avc' \) -print0)
    mapfile -d '' dec_refs < <(find "$subdir" -maxdepth 2 \( -iname '*.yuv' -o -iname '*.cif' -o -iname '*.qcif' \) -print0)

    if [[ ${#bitstreams[@]} -eq 0 ]]; then
        tag "$YELLOW" SKIP; echo "no .264/.jsv/.jvt/.26l/.avc file found"
        continue
    fi
    [[ ${#bitstreams[@]} -gt 1 ]] && { tag "$YELLOW" WARN; echo "multiple bitstreams found, using ${bitstreams[0]}"; }
    bitstream="${bitstreams[0]}"

    if [[ ${#dec_refs[@]} -eq 1 ]]; then
        use_ffmpeg=0; dec_ref="${dec_refs[0]}"
    else
        use_ffmpeg=1; dec_ref="${subdir}/${name}_ffmpeg.yuv"
        tag "$YELLOW" WARN; echo "no single reference file, using ffmpeg"
    fi
    ref_status=""

    for t in "${TARGETS[@]}"; do
        out="${subdir}/${name}_${t}_undo264.yuv"
        "$ROOT/build/$t/undo264" -i "$bitstream" -o "$out" > "${subdir}/${name}_${t}_undo264.log" 2>&1
        ret=$?

        if [[ $ret -eq 3 ]]; then
            tag "$YELLOW" SKIP; echo "$t: out-of-scope bitstream"
            ((skip[$t]++))
        elif [[ $ret -eq 1 && "$t" != release ]]; then
            tag "$YELLOW" "${t^^}"; echo "${SAN_NAME[$t]} caught an error (see ${name}_${t}_undo264.log)"
            ((san[$t]++)); san_list[$t]+="${san_list[$t]:+, }$name"
        elif [[ $ret -ne 0 ]]; then
            tag "$RED" FAIL; echo "$t: decoder returned $ret (see ${name}_${t}_undo264.log)"
            ((fail[$t]++)); bad_list[$t]+="${bad_list[$t]:+, }$name"
        elif ! prepare_ref; then
            tag "$RED" FAIL; echo "$t: no reference available"
            ((fail[$t]++)); bad_list[$t]+="${bad_list[$t]:+, }$name"
        elif cmp -s "$out" "$dec_ref"; then
            tag "$GREEN" OK; echo "$t: bit-exact"
            ((exact[$t]++))
        else
            tag "$RED" DIFF; echo "$t: not bit-exact"
            report_diff
            ((mism[$t]++)); bad_list[$t]+="${bad_list[$t]:+, }$name"
        fi
        rm -f "$out"
    done

    ((use_ffmpeg)) && rm -f "$dec_ref"
done < <(find "$INPUT_DIR" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)

# ---- Summary -----------------------------------------------------------------
echo; echo "${BOLD}== Summary ==${RST}"
printf '%-9s %7s %7s %7s %7s %7s\n' target exact diff san-err fail skipped
bad=0; san_total=0
for t in "${TARGETS[@]}"; do
    printf '%-9s %7d %7d %7d %7d %7d\n' "$t" "${exact[$t]:-0}" "${mism[$t]:-0}" "${san[$t]:-0}" "${fail[$t]:-0}" "${skip[$t]:-0}"
    bad=$((bad + ${mism[$t]:-0} + ${fail[$t]:-0}))
    san_total=$((san_total + ${san[$t]:-0}))
done

print_list() {   # print_list <title> <color> <array name>
    local -n arr="$3"; local printed=0
    for t in "${TARGETS[@]}"; do
        [[ -n "${arr[$t]}" ]] || continue
        ((printed)) || { echo; echo "$2$1$RST"; printed=1; }
        echo "  $t: ${arr[$t]}" | fold -s -w 100 | sed '2,$s/^/    /'
    done
}
print_list "Not bit-exact or failed to decode:" "$RED" bad_list
print_list "Sanitizer errors:" "$YELLOW" san_list

echo
if [[ $bad -gt 0 ]]; then
    echo "${RED}${BOLD}FAILED${RST}: $bad pass(es) not bit-exact or failed to decode"
    exit 1
elif [[ $san_total -gt 0 && $CATCH_SAN -eq 1 ]]; then
    echo "${YELLOW}${BOLD}SANITIZER ERRORS${RST}: $san_total pass(es), all other output bit-exact"
    exit 2
fi
[[ $san_total -gt 0 ]] && echo "${YELLOW}$san_total sanitizer error(s) ignored (no --catch-san-err)${RST}"
echo "${GREEN}${BOLD}PASSED${RST}"
exit 0