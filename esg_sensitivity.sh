#!/usr/bin/env bash
# Reproduce Experiment 4 (ESG threshold sensitivity) from the paper.
# Runs pipeline at 6 threshold multipliers on the 100-supplier D=10,000 instance.
# Requires: Python 3.9+, clingo 5.8+ (pip install clingo)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE="$SCRIPT_DIR/explanation_generator.py"
MODEL="$SCRIPT_DIR/model.lp"
BASE="$SCRIPT_DIR/instances/instance_100_d10000.lp"
OUTDIR="$SCRIPT_DIR/results_esg"

BASE_SOC=2400
BASE_ENV=2200

echo "=== ESG Sensitivity Experiment ==="
echo "Base instance: $BASE"
echo "Thresholds: 1.0× 2.0× 3.0× 3.5× 3.7× 3.8×"
echo ""

for MULT_10 in 10 20 30 35 37 38; do
    MULT=$(echo "scale=1; $MULT_10 / 10" | bc)
    SOC_T=$(echo "$BASE_SOC * $MULT_10 / 10" | bc)
    ENV_T=$(echo "$BASE_ENV * $MULT_10 / 10" | bc)
    OUT="$OUTDIR/${MULT_10}x"
    TMPINST="/tmp/instance_esg_${MULT_10}x.lp"

    # Patch thresholds in a temp copy
    sed "s/soc_threshold([0-9]*)/soc_threshold($SOC_T)/; \
         s/env_threshold([0-9]*)/env_threshold($ENV_T)/" \
        "$BASE" > "$TMPINST"

    echo "Running ${MULT}× (soc=${SOC_T}, env=${ENV_T})..."
    python3 "$PIPELINE" "$TMPINST" "$OUT" "$MODEL" 2>&1 | tail -3
    rm -f "$TMPINST"
done

echo ""
echo "Results in: $OUTDIR"
