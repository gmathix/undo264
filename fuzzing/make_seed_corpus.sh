#!/bin/bash
# Usage: ./make_seed_corpus.sh <output_dir> <input_dir> [<input_dir> ...]
#   <input_dir> is e.g. AVCv1/ or FRExt/ (same layout as conformance_test.sh)
# Written by Claude Sonnet 5.5, inspired by test/conformance/conformance_test.sh
#
# Copies every bitstream that undo264 accepts into <output_dir>, named by their original name
# Does not check for duplicates !
#
# Env overrides:
#   DECODER   path to undo264   (default: same as conformance_test.sh)
#   MAX_SIZE  max file size for `find -size`, e.g. 256k (default: no limit)

DECODER="${DECODER:-../build/release/undo264}"

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <output_dir> <input_dir> [<input_dir> ...]" >&2
    exit 1
fi
if [[ ! -x "$DECODER" ]]; then
    echo "Error: decoder not found or not executable at $DECODER" >&2
    exit 1
fi

out_dir="$1"; shift
mkdir -p "$out_dir" || exit 1

size_args=()
[[ -n "$MAX_SIZE" ]] && size_args=(-size "-$MAX_SIZE")

tmp_yuv="$(mktemp)"
trap 'rm -f "$tmp_yuv"' EXIT

kept=0; dup=0; outscope=0; failed=0

for input_dir in "$@"; do
    if [[ ! -d "$input_dir" ]]; then
        echo "Error: '$input_dir' is not a directory" >&2
        exit 1
    fi

    while IFS= read -r -d '' subdir; do
        mapfile -d '' bitstreams < <(find "$subdir" -maxdepth 2 -type f "${size_args[@]}" \
            \( -iname '*264' -o -iname '*.jsv' -o -iname '*.jvt' -o -iname '*.26l' -o -iname '*.avc' \) -print0)

        for bs in "${bitstreams[@]}"; do
            "$DECODER" -i "$bs" -o "$tmp_yuv" > /dev/null 2>&1
            ret=$?
            if [[ $ret -eq 3 ]]; then
                echo "[SKIP] out of scope: $bs"; ((outscope++)); continue
            elif [[ $ret -ne 0 ]]; then
                echo "[FAIL] decoder exit $ret: $bs"; ((failed++)); continue
            fi

            bs_name=$(echo $bs | rev | cut -d"/" -f 1 | rev)
            cp -- "$bs" "$out_dir/$bs_name" && ((kept++))
        done
    done < <(find "$input_dir" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)
done

echo
echo "Corpus: $out_dir"
echo "kept=$kept duplicates=$dup out_of_scope=$outscope failed=$failed"