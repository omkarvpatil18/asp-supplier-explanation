# Explainable Decision Support for Resilient and Sustainable Supplier Selection

This repository contains the explanation generator pipeline and instance files for the paper:

> Patil, O. & Valilai, O.F. (2026). *Explainable Decision Support for Resilient and Sustainable
> Supplier Selection: A Logic-Driven Approach.* Proceedings of ICORES 2026.

The underlying ASP supplier selection model, the full instance files, and the RPA–ERP integration
architecture are from the companion journal paper:

> Patil, O. & Valilai, O.F. (2026). *[Title of journal paper].* Engineering Applications of
> Artificial Intelligence. doi:10.1016/j.engappai.2026.115955

---

## Repository contents

```
README.md                      This file
explanation_generator.py       Explanation pipeline (Python 3, requires Clingo 5.8+)
model.lp                       ASP supplier selection model (from journal paper)
instances/
  instance_100_d8000.lp        100-supplier instance, demand = 8,000
  instance_100_d10000.lp       100-supplier instance, demand = 10,000
  instance_100_d12000.lp       100-supplier instance, demand = 12,000
  instance_500_d40000.lp       500-supplier instance, demand = 40,000
  instance_500_d45000.lp       500-supplier instance, demand = 45,000
  instance_500_d50000.lp       500-supplier instance, demand = 50,000
  instance_1000_d90000.lp      1000-supplier instance, demand = 90,000
  instance_1000_d95000.lp      1000-supplier instance, demand = 95,000
  instance_1000_d100000.lp     1000-supplier instance, demand = 100,000
results/
  100_d8000/                   Pipeline outputs for each instance
  100_d10000/                  (explanations.json, explanations.txt)
  ...
```

---

## Requirements

- Python 3.9 or later
- [Clingo](https://potassco.org/clingo/) 5.8 or later

Install Clingo via pip:

```bash
pip install clingo
```

---

## Running the pipeline

```bash
python explanation_generator.py <instance_file> [output_dir] [model_file]
```

**Example — reproduce the 100-supplier D=10,000 result from the paper:**

```bash
python explanation_generator.py instances/instance_100_d10000.lp output/ model.lp
```

This produces two files in `output/`:

- `explanations.json` — structured audit log (one record per decision, all three levels)
- `explanations.txt` — human-readable text (summary level per decision)

**Reproduce all nine instances:**

```bash
for scale in 100 500 1000; do
  case $scale in
    100)  demands="8000 10000 12000" ;;
    500)  demands="40000 45000 50000" ;;
    1000) demands="90000 95000 100000" ;;
  esac
  for d in $demands; do
    python explanation_generator.py \
      instances/instance_${scale}_d${d}.lp \
      results/${scale}_d${d}/ \
      model.lp
  done
done
```

Expected total output: **18,764 explanation records** across all nine instances.

---

## Explanation record structure

Each JSON record has the following fields:

| Field | Description |
|---|---|
| `decision_type` | `supplier_selected`, `disruption_zero_capacity`, `backup_activation`, `esg_constraint_state`, or `infeasibility` |
| `entity` | Supplier or backup ID |
| `scenario` | Disruption scenario (`w1`, `w2`, `w3`) |
| `summary` | One-sentence rationale (manager level) |
| `rule_level` | Governing ASP construct with parameter values (auditor level) |
| `trace` | Ordered predicate derivation chain (developer level) |
| `metadata` | Solver output values used to populate the templates |

---

## ESG sensitivity experiment

To reproduce Experiment 4 (Table 7 in the paper), run the pipeline on
`instances/instance_100_d10000.lp` with modified threshold values. The
base thresholds are `soc_threshold(2400)` and `env_threshold(2200)`. Edit
these in the instance file or pass overrides:

```bash
# Example: 3.5× threshold (soc=8400, env=7700)
sed 's/soc_threshold(2400)/soc_threshold(8400)/; s/env_threshold(2200)/env_threshold(7700)/' \
  instances/instance_100_d10000.lp > /tmp/instance_3_5x.lp
python explanation_generator.py /tmp/instance_3_5x.lp output_3_5x/ model.lp
```

The infeasibility boundary is at 3.8× (soc_threshold=9120), where the solver returns UNSAT
because the threshold exceeds the maximum achievable social score of 9,109.

---

## Notes on optimisation

The pipeline uses `--opt-mode=opt` **without** the `-n 1` flag. This enumerates improving
models until the true optimum is proven. Using `-n 1` returns the first stable model found
during search and is not guaranteed to be optimal; in practice it yields a suboptimal social
score. The true optimal selects all primary suppliers in every instance.

---

## Relationship to journal paper

The supplier selection model (`model.lp`), the base instance files, and the RPA–ERP
architecture are from the journal paper. The novel contribution of this repository is
the explanation generator: the three-level template definitions, the four-type decision
taxonomy, and the pipeline that reads ASP stable model output and populates explanation
templates without constructing a separate approximation model.

The instance files in `instances/` are the demand-variant versions used in this paper's
evaluation. The base instances (without demand suffix) are from the journal paper's archive.

---

## Citation

If you use this code or the instance files, please cite both papers:

```bibtex
@inproceedings{patil_explainable_2026,
  title     = {Explainable Decision Support for Resilient and Sustainable
               Supplier Selection: A Logic-Driven Approach},
  author    = {Patil, Omkar and Valilai, Omid Fatahi},
  booktitle = {Proceedings of the International Conference on Operations
               Research and Enterprise Systems (ICORES)},
  year      = {2026}
}

@article{patil_logic_2026,
  title   = {[Title of journal paper]},
  author  = {Patil, Omkar and Valilai, Omid Fatahi},
  journal = {Engineering Applications of Artificial Intelligence},
  doi     = {10.1016/j.engappai.2026.115955},
  year    = {2026}
}
```
