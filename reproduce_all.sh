#!/usr/bin/env bash
# Reproduce all 18,764 explanation records from the paper.
# Usage: bash reproduce_all.sh
# Requires: Python 3.9+, clingo 5.8+ (pip install clingo)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE="$SCRIPT_DIR/explanation_generator.py"
MODEL="$SCRIPT_DIR/model.lp"
INSTANCES="$SCRIPT_DIR/instances"
OUTDIR="$SCRIPT_DIR/results_reproduced"

echo "=== ASP Supplier Explanation Pipeline ==="
echo "Output directory: $OUTDIR"
echo ""

TOTAL=0

for SCALE in 100 500 1000; do
    case $SCALE in
        100)  DEMANDS="8000 10000 12000" ;;
        500)  DEMANDS="40000 45000 50000" ;;
        1000) DEMANDS="90000 95000 100000" ;;
    esac
    for D in $DEMANDS; do
        INSTANCE="$INSTANCES/instance_${SCALE}_d${D}.lp"
        OUT="$OUTDIR/${SCALE}_d${D}"
        echo "Running: ${SCALE}-supplier instance, D=${D}"
        python3 "$PIPELINE" "$INSTANCE" "$OUT" "$MODEL"
        COUNT=$(python3 -c "import json; print(len(json.load(open('$OUT/explanations.json'))))")
        echo "  → ${COUNT} records"
        TOTAL=$((TOTAL + COUNT))
    done
done

echo ""
echo "=== Complete ==="
echo "Total records generated: ${TOTAL}"
echo "Expected:                18764"
if [ "$TOTAL" -eq 18764 ]; then
    echo "MATCH ✓"
else
    echo "MISMATCH — check instance files and model"
fi
