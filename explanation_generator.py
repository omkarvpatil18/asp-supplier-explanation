"""
Explanation Generator for ASP-Based Supplier Selection
=======================================================
Parses Clingo stable model output and generates three-level
explanations (summary, rule, trace) for four decision types:
  1. Supplier selected
  2. Disruption-driven rejection
  3. Conditional backup activation
  4. ESG constraint binding

Authors: Omkar Vishwas Patil, Omid Fatahi Valilai
"""

import clingo
import json
import re
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


# ─────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────

@dataclass
class SupplierFacts:
    """Holds all instance facts extracted from the ASP program."""
    primary: dict        = field(default_factory=dict)   # {s: {cap, cost, soc, env}}
    backup: dict         = field(default_factory=dict)   # {b: {cap, cost, soc, env}}
    scenarios: list      = field(default_factory=list)   # [w1, w2, ...]
    disrupted: dict      = field(default_factory=dict)   # {(s,w): True}
    demand: int          = 0
    soc_threshold: int   = 0
    env_threshold: int   = 0


@dataclass
class SolverDecision:
    """Holds parsed decisions from a stable model."""
    selected_primary:  dict = field(default_factory=dict)  # {w: [s, ...]}
    selected_backup:   dict = field(default_factory=dict)  # {w: [b, ...]}
    backup_needed:     dict = field(default_factory=dict)  # {w: bool}
    eff_caps:          dict = field(default_factory=dict)  # {(s,w): cap}
    total_primary_cap: dict = field(default_factory=dict)  # {w: total}


@dataclass
class ExplanationRecord:
    """One complete explanation for one decision in one scenario."""
    decision_type: str
    scenario:      str
    entity:        str          # supplier id or 'backup_pool'
    summary:       str
    rule:          str
    trace:         list         # list of (step_no, predicate, derivation, source)
    metadata:      dict = field(default_factory=dict)


# ─────────────────────────────────────────────
# STEP 1 — PARSE INSTANCE FACTS FROM ASP FILE
# ─────────────────────────────────────────────

def parse_facts(asp_file: str) -> SupplierFacts:
    """
    Extract all ground facts from the ASP instance file
    without solving — just fact extraction.
    """
    facts = SupplierFacts()

    with open(asp_file, "r") as f:
        content = f.read()

    def find_all(pattern):
        return re.findall(pattern, content)

    # Primary suppliers
    for s, c in find_all(r'cap_primary\((\w+),\s*(\d+)\)'):
        if s not in facts.primary:
            facts.primary[s] = {}
        facts.primary[s]['cap'] = int(c)

    for s, c in find_all(r'cost_primary\((\w+),\s*(\d+)\)'):
        if s not in facts.primary:
            facts.primary[s] = {}
        facts.primary[s]['cost'] = int(c)

    for s, v in find_all(r'soc_primary\((\w+),\s*(\d+)\)'):
        if s not in facts.primary:
            facts.primary[s] = {}
        facts.primary[s]['soc'] = int(v)

    for s, v in find_all(r'env_primary\((\w+),\s*(\d+)\)'):
        if s not in facts.primary:
            facts.primary[s] = {}
        facts.primary[s]['env'] = int(v)

    # Backup suppliers
    for b, c in find_all(r'cap_backup\((\w+),\s*(\d+)\)'):
        if b not in facts.backup:
            facts.backup[b] = {}
        facts.backup[b]['cap'] = int(c)

    for b, c in find_all(r'cost_backup\((\w+),\s*(\d+)\)'):
        if b not in facts.backup:
            facts.backup[b] = {}
        facts.backup[b]['cost'] = int(c)

    for b, v in find_all(r'soc_backup\((\w+),\s*(\d+)\)'):
        if b not in facts.backup:
            facts.backup[b] = {}
        facts.backup[b]['soc'] = int(v)

    for b, v in find_all(r'env_backup\((\w+),\s*(\d+)\)'):
        if b not in facts.backup:
            facts.backup[b] = {}
        facts.backup[b]['env'] = int(v)

    # Scenarios — only lowercase identifiers (w1, w2 ...) not rule variables (W)
    facts.scenarios = list(dict.fromkeys(
        s for s in find_all(r'scenario\((\w+)\)')
        if s[0].islower()
    ))

    # Disruptions
    for s, w in find_all(r'disrupted\((\w+),\s*(\w+)\)'):
        facts.disrupted[(s, w)] = True

    # Demand
    d_match = re.search(r'demand\((\d+)\)', content)
    if d_match:
        facts.demand = int(d_match.group(1))

    # Thresholds
    soc_match = re.search(r'soc_threshold\((\d+)\)', content)
    if soc_match:
        facts.soc_threshold = int(soc_match.group(1))

    env_match = re.search(r'env_threshold\((\d+)\)', content)
    if env_match:
        facts.env_threshold = int(env_match.group(1))

    return facts


# ─────────────────────────────────────────────
# STEP 2 — SOLVE WITH CLINGO AND PARSE OUTPUT
# ─────────────────────────────────────────────

def solve_and_parse(asp_file: str, facts: SupplierFacts,
                    model_file: str = None) -> Optional[SolverDecision]:
    """
    Run Clingo on the ASP instance file plus an optional separate model file,
    capture the optimal stable model, and parse all relevant predicates.
    """
    ctl = clingo.Control(["--opt-mode=opt"])  # no -n 1: enumerate to find true optimum
    ctl.load(asp_file)
    if model_file:
        ctl.load(model_file)
    ctl.ground([("base", [])])

    decision = SolverDecision()
    best_cost   = None
    model_atoms = []

    with ctl.solve(yield_=True) as handle:
        for model in handle:
            cost  = model.cost
            atoms = [str(a) for a in model.symbols(shown=True)]
            if best_cost is None or cost < best_cost:
                best_cost   = cost
                model_atoms = atoms

    if not model_atoms:
        print("  [WARNING] No stable model found.")
        return None

    print(f"    Optimal cost     : {best_cost}")

    # Parse selected primary
    for w in facts.scenarios:
        decision.selected_primary[w] = []
        decision.selected_backup[w]  = []
        decision.backup_needed[w]    = False

    for atom in model_atoms:
        # select(S, W)
        m = re.match(r'select\((\w+),(\w+)\)', atom)
        if m:
            s, w = m.group(1), m.group(2)
            if w in decision.selected_primary:
                decision.selected_primary[w].append(s)

        # select_backup(B, W)
        m = re.match(r'select_backup\((\w+),(\w+)\)', atom)
        if m:
            b, w = m.group(1), m.group(2)
            if w in decision.selected_backup:
                decision.selected_backup[w].append(b)

    # Derive backup_needed and eff_caps from facts
    for w in facts.scenarios:
        total = 0
        for s, sdata in facts.primary.items():
            cap = sdata.get('cap', 0)
            eff = 0 if (s, w) in facts.disrupted else cap
            decision.eff_caps[(s, w)] = eff
            total += eff
        decision.total_primary_cap[w] = total
        decision.backup_needed[w] = (total < facts.demand)

    return decision


# ─────────────────────────────────────────────
# STEP 3 — GENERATE EXPLANATIONS
# ─────────────────────────────────────────────

class ExplanationGenerator:
    """
    Generates summary, rule, and trace explanations for all
    four decision types from solver decisions and instance facts.
    """

    def __init__(self, facts: SupplierFacts, decision: SolverDecision):
        self.facts    = facts
        self.decision = decision

    def generate_all(self) -> list:
        records = []
        for w in self.facts.scenarios:
            records += self._explain_backup_activation(w)
            records += self._explain_selected_suppliers(w)
            records += self._explain_disruption_rejections(w)
            records += self._explain_esg_binding(w)
        return records

    # ── Decision Type 1: Backup Activation ──────────────────────────

    def _explain_backup_activation(self, w: str) -> list:
        if not self.decision.backup_needed[w]:
            return []

        total  = self.decision.total_primary_cap[w]
        demand = self.facts.demand
        backups = sorted(self.decision.selected_backup[w])
        gap    = demand - total

        # SUMMARY
        summary = (
            f"Backup suppliers were activated in scenario {w} because total available "
            f"primary capacity of {total:,} units fell below required demand of "
            f"{demand:,} units, triggering the conditional backup selection rule. "
            f"{len(backups)} backup supplier(s) ({', '.join(backups)}) were selected "
            f"to cover the residual demand gap of {gap:,} units."
        )

        # RULE
        rule = (
            f"Governing construct: backup_needed({w}) predicate.\n"
            f"Derivation rule: backup_needed(W) :- total_avail_primary_cap(W, Total), "
            f"demand(D), Total < D.\n"
            f"Integrity constraint: :- backup_needed(W), not 1 {{ select_backup(B,W) : backup(B) }}.\n"
            f"Trigger values: total_avail_primary_cap({w}, {total}), demand({demand}). "
            f"Condition {total} < {demand} evaluates TRUE."
        )

        # TRACE
        trace = []
        step = 1

        # Show a representative disrupted supplier contributing to capacity reduction
        disrupted_in_w = [s for s in self.facts.primary
                          if (s, w) in self.facts.disrupted]
        if disrupted_in_w:
            rep = sorted(disrupted_in_w)[0]
            nom_cap = self.facts.primary[rep].get('cap', 0)
            trace.append((step,
                f"cap_primary({rep}, {nom_cap})",
                f"Nominal capacity of supplier {rep} is {nom_cap} units.",
                "Input fact"))
            step += 1
            trace.append((step,
                f"disrupted({rep}, {w})",
                f"Supplier {rep} is disrupted in scenario {w}.",
                "Input fact"))
            step += 1
            trace.append((step,
                f"eff_cap({rep}, {w}, 0)",
                f"Effective capacity of {rep} set to zero by disruption rule.",
                "Derived: disruption capacity rule"))
            step += 1

        trace.append((step,
            f"total_avail_primary_cap({w}, {total})",
            f"Aggregate effective primary capacity in scenario {w} is {total:,} units "
            f"(sum over all non-disrupted primary suppliers).",
            "Derived: aggregation rule"))
        step += 1

        trace.append((step,
            f"demand({demand})",
            f"Total demand requirement is {demand:,} units.",
            "Input fact"))
        step += 1

        trace.append((step,
            f"backup_needed({w})",
            f"{total:,} < {demand:,} — backup activation predicate fires.",
            "Derived: backup trigger rule"))
        step += 1

        for b in backups:
            bcap = self.facts.backup[b].get('cap', 0)
            trace.append((step,
                f"select_backup({b}, {w})",
                f"Backup supplier {b} (capacity {bcap}) selected to contribute "
                f"to covering residual gap of {gap:,} units.",
                "Decision: choice rule + backup integrity constraint"))
            step += 1

        return [ExplanationRecord(
            decision_type = "backup_activation",
            scenario      = w,
            entity        = "backup_pool",
            summary       = summary,
            rule          = rule,
            trace         = trace,
            metadata      = {
                "total_primary_cap": total,
                "demand": demand,
                "gap": gap,
                "backups_selected": backups,
                "n_backups": len(backups)
            }
        )]

    # ── Decision Type 2: Supplier Selected ──────────────────────────

    def _explain_selected_suppliers(self, w: str) -> list:
        records = []
        for s in sorted(self.decision.selected_primary[w]):
            sdata  = self.facts.primary.get(s, {})
            cap    = sdata.get('cap', 0)
            cost   = sdata.get('cost', 0)
            soc    = sdata.get('soc', 0)
            env    = sdata.get('env', 0)
            eff    = self.decision.eff_caps.get((s, w), 0)

            summary = (
                f"Supplier {s} was selected in scenario {w} with an effective capacity "
                f"of {eff:,} units (nominal: {cap:,}), unit cost {cost}, "
                f"social score {soc}, and environmental score {env}. "
                f"Selection satisfies demand contribution within cost and ESG criteria."
            )

            rule = (
                f"Governing construct: select({s},{w}) — choice rule {{ select(S,W) }} "
                f":- primary(S), scenario(W).\n"
                f"Integrity constraint: demand satisfaction — total capacity of selected "
                f"suppliers must meet or exceed demand({self.facts.demand:,}).\n"
                f"Supplier {s} is non-disrupted in scenario {w}: "
                f"eff_cap({s},{w},{eff}) = nominal capacity {cap}."
            )

            trace = [
                (1, f"primary({s})",
                 f"Supplier {s} is declared as a primary supplier.",
                 "Input fact"),
                (2, f"scenario({w})",
                 f"Scenario {w} is active.",
                 "Input fact"),
                (3, f"cap_primary({s}, {cap})",
                 f"Nominal capacity of {s} is {cap:,} units.",
                 "Input fact"),
                (4, f"eff_cap({s}, {w}, {eff})",
                 f"Supplier {s} is not disrupted in scenario {w}; "
                 f"effective capacity equals nominal capacity {eff:,}.",
                 "Derived: disruption capacity rule (not disrupted branch)"),
                (5, f"select({s}, {w})",
                 f"Supplier {s} is selected in scenario {w} by choice rule; "
                 f"contributes {eff:,} units toward demand {self.facts.demand:,}.",
                 "Decision: choice rule + demand satisfaction constraint"),
            ]

            records.append(ExplanationRecord(
                decision_type = "supplier_selected",
                scenario      = w,
                entity        = s,
                summary       = summary,
                rule          = rule,
                trace         = trace,
                metadata      = {
                    "cap": cap, "eff_cap": eff,
                    "cost": cost, "soc": soc, "env": env
                }
            ))
        return records

    # ── Decision Type 3: Disruption-Driven Rejection ─────────────────

    def _explain_disruption_rejections(self, w: str) -> list:
        records = []
        disrupted_in_w = [s for s in self.facts.primary
                          if (s, w) in self.facts.disrupted]
        for s in sorted(disrupted_in_w):
            sdata  = self.facts.primary.get(s, {})
            cap    = sdata.get('cap', 0)
            cost   = sdata.get('cost', 0)

            summary = (
                f"Supplier {s} was excluded from scenario {w} because a disruption "
                f"event reduced its effective capacity to zero (nominal capacity: "
                f"{cap:,} units). A supplier with zero effective capacity cannot "
                f"contribute to demand satisfaction and is therefore not selected."
            )

            rule = (
                f"Governing construct: eff_cap({s},{w},0) — derived by rule: "
                f"eff_cap(S,W,0) :- disrupted(S,W), primary(S).\n"
                f"Consequence: select({s},{w}) cannot hold because any allocation "
                f"from {s} in scenario {w} would be bounded by eff_cap = 0, "
                f"contributing nothing to demand satisfaction.\n"
                f"Input facts: disrupted({s},{w}) = TRUE, cap_primary({s},{cap})."
            )

            trace = [
                (1, f"cap_primary({s}, {cap})",
                 f"Nominal capacity of supplier {s} is {cap:,} units.",
                 "Input fact"),
                (2, f"disrupted({s}, {w})",
                 f"Supplier {s} is marked as disrupted in scenario {w}.",
                 "Input fact"),
                (3, f"eff_cap({s}, {w}, 0)",
                 f"Disruption rule sets effective capacity of {s} to zero in scenario {w}.",
                 "Derived: eff_cap(S,W,0) :- disrupted(S,W), primary(S)"),
                (4, f"not select({s}, {w})",
                 f"Supplier {s} is not selected in scenario {w}: "
                 f"zero effective capacity means selection cannot contribute to "
                 f"demand {self.facts.demand:,}. Integrity constraint prevents "
                 f"selection of zero-capacity suppliers.",
                 "Consequence: demand satisfaction integrity constraint"),
            ]

            records.append(ExplanationRecord(
                decision_type = "disruption_rejection",
                scenario      = w,
                entity        = s,
                summary       = summary,
                rule          = rule,
                trace         = trace,
                metadata      = {"nominal_cap": cap, "eff_cap": 0, "cost": cost}
            ))
        return records

    # ── Decision Type 4: ESG Constraint Binding ──────────────────────

    def _explain_esg_binding(self, w: str) -> list:
        records = []
        selected = self.decision.selected_primary[w]
        selected_backup = self.decision.selected_backup[w]

        # Compute aggregate scores
        agg_soc = sum(self.facts.primary[s].get('soc', 0)
                      for s in selected if s in self.facts.primary)
        agg_soc += sum(self.facts.backup[b].get('soc', 0)
                       for b in selected_backup if b in self.facts.backup)

        agg_env = sum(self.facts.primary[s].get('env', 0)
                      for s in selected if s in self.facts.primary)
        agg_env += sum(self.facts.backup[b].get('env', 0)
                       for b in selected_backup if b in self.facts.backup)

        soc_t = self.facts.soc_threshold
        env_t = self.facts.env_threshold

        soc_binding = (agg_soc < soc_t * 1.05)  # within 5% of threshold = binding
        env_binding = (agg_env < env_t * 1.05)

        if not soc_binding and not env_binding:
            # Non-binding: generate informational explanation
            soc_headroom = agg_soc - soc_t
            env_headroom = agg_env - env_t
            summary = (
                f"ESG constraints are non-binding in scenario {w}. "
                f"The selected portfolio achieves aggregate social score {agg_soc:,} "
                f"(threshold: {soc_t:,}, headroom: +{soc_headroom:,}) and "
                f"environmental score {agg_env:,} (threshold: {env_t:,}, "
                f"headroom: +{env_headroom:,}). "
                f"Selection is driven by cost minimisation and demand satisfaction."
            )
            rule = (
                f"Integrity constraints soc_threshold and env_threshold are inactive "
                f"in scenario {w}.\n"
                f"Aggregate social score {agg_soc:,} >= threshold {soc_t:,}: "
                f"social constraint not binding.\n"
                f"Aggregate environmental score {agg_env:,} >= threshold {env_t:,}: "
                f"environmental constraint not binding.\n"
                f"Primary driver of selection: cost minimisation objective "
                f"(#minimise directive, priority @2)."
            )
            trace = [
                (1, f"soc_threshold({soc_t})",
                 f"Minimum social score threshold is {soc_t:,}.",
                 "Input fact"),
                (2, f"env_threshold({env_t})",
                 f"Minimum environmental score threshold is {env_t:,}.",
                 "Input fact"),
                (3, f"agg_soc({w}, {agg_soc})",
                 f"Aggregate social score of selected portfolio in scenario {w} "
                 f"is {agg_soc:,}.",
                 "Derived: sum over select(S,W), soc_primary(S,SS)"),
                (4, f"agg_env({w}, {agg_env})",
                 f"Aggregate environmental score of selected portfolio in scenario {w} "
                 f"is {agg_env:,}.",
                 "Derived: sum over select(S,W), env_primary(S,ES)"),
                (5, "esg_non_binding",
                 f"Both thresholds satisfied with headroom "
                 f"(soc: +{agg_soc - soc_t:,}, env: +{agg_env - env_t:,}). "
                 f"ESG integrity constraints do not restrict the solution space.",
                 "Evaluation: non-binding state confirmed"),
            ]
            binding_state = "non_binding"

        else:
            # Binding: ESG is actively constraining selection
            which = []
            if soc_binding:
                which.append(f"social (threshold {soc_t:,}, achieved {agg_soc:,})")
            if env_binding:
                which.append(f"environmental (threshold {env_t:,}, achieved {agg_env:,})")

            summary = (
                f"ESG constraints are binding in scenario {w}. "
                f"The {' and '.join(which)} threshold(s) are active, requiring "
                f"inclusion of higher-scoring suppliers to satisfy minimum policy "
                f"requirements. Procurement cost is elevated above the cost-optimal "
                f"baseline to satisfy sustainability policy."
            )
            rule = (
                f"Active integrity constraint(s) in scenario {w}:\n"
                + (f"  :- scenario({w}), soc_threshold({soc_t}), "
                   f"#sum{{SS,S: select(S,{w}), soc_primary(S,SS); ...}} < {soc_t}.\n"
                   if soc_binding else "")
                + (f"  :- scenario({w}), env_threshold({env_t}), "
                   f"#sum{{ES,S: select(S,{w}), env_primary(S,ES); ...}} < {env_t}.\n"
                   if env_binding else "")
                + f"These constraints restrict the feasible solution space, "
                f"forcing selection of suppliers with higher ESG scores "
                f"at increased procurement cost."
            )
            trace = [
                (1, f"soc_threshold({soc_t})",
                 f"Minimum social score threshold is {soc_t:,}.",
                 "Input fact"),
                (2, f"env_threshold({env_t})",
                 f"Minimum environmental threshold is {env_t:,}.",
                 "Input fact"),
                (3, f"agg_soc({w}, {agg_soc})",
                 f"Aggregate social score of portfolio is {agg_soc:,}.",
                 "Derived: aggregation over selected suppliers"),
                (4, f"agg_env({w}, {agg_env})",
                 f"Aggregate environmental score of portfolio is {agg_env:,}.",
                 "Derived: aggregation over selected suppliers"),
                (5, "esg_binding",
                 f"One or more ESG thresholds are active. "
                 f"The solver is forced to include higher-scoring suppliers, "
                 f"elevating procurement cost above the unconstrained optimum.",
                 "Consequence: ESG integrity constraints restrict solution space"),
            ]
            binding_state = "binding"

        records.append(ExplanationRecord(
            decision_type = "esg_constraint",
            scenario      = w,
            entity        = "portfolio",
            summary       = summary,
            rule          = rule,
            trace         = trace,
            metadata      = {
                "agg_soc": agg_soc, "agg_env": agg_env,
                "soc_threshold": soc_t, "env_threshold": env_t,
                "binding_state": binding_state
            }
        ))
        return records


# ─────────────────────────────────────────────
# STEP 4 — FORMAT AND EXPORT
# ─────────────────────────────────────────────

def format_record(rec: ExplanationRecord) -> str:
    """Pretty-print one explanation record in human-readable form."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"DECISION TYPE : {rec.decision_type.upper().replace('_',' ')}")
    lines.append(f"SCENARIO      : {rec.scenario}")
    lines.append(f"ENTITY        : {rec.entity}")
    lines.append("-" * 70)
    lines.append("[SUMMARY LEVEL]")
    lines.append(rec.summary)
    lines.append("")
    lines.append("[RULE LEVEL]")
    lines.append(rec.rule)
    lines.append("")
    lines.append("[TRACE LEVEL]")
    for step_no, predicate, derivation, source in rec.trace:
        lines.append(f"  Step {step_no}. {predicate}")
        lines.append(f"          {derivation}")
        lines.append(f"          Source: {source}")
    lines.append("=" * 70)
    return "\n".join(lines)


def export_json(records: list, out_path: str):
    """Export all explanation records to a structured JSON file."""
    data = []
    for rec in records:
        data.append({
            "decision_type": rec.decision_type,
            "scenario":      rec.scenario,
            "entity":        rec.entity,
            "summary":       rec.summary,
            "rule":          rec.rule,
            "trace": [
                {
                    "step":       s,
                    "predicate":  p,
                    "derivation": d,
                    "source":     src
                }
                for s, p, d, src in rec.trace
            ],
            "metadata": rec.metadata
        })
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  [JSON] Exported {len(data)} explanation records to {out_path}")


def export_coverage_matrix(
        all_records: dict, scenarios: list, out_path: str):
    """
    Write a CSV coverage matrix:
    rows = instances, columns = decision_type x level combinations.
    """
    decision_types = [
        "supplier_selected",
        "disruption_rejection",
        "backup_activation",
        "esg_constraint"
    ]
    levels = ["summary", "rule", "trace"]

    header = ["instance"] + [
        f"{dt}_{lv}" for dt in decision_types for lv in levels
    ]

    rows = []
    for instance_key, records in all_records.items():
        row = {"instance": instance_key}
        for dt in decision_types:
            present = any(r.decision_type == dt for r in records)
            for lv in levels:
                col = f"{dt}_{lv}"
                row[col] = "1" if present else "0"
        rows.append(row)

    with open(out_path, "w") as f:
        f.write(",".join(header) + "\n")
        for row in rows:
            f.write(",".join(row[h] for h in header) + "\n")

    print(f"  [CSV] Coverage matrix written to {out_path}")


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────

def run_pipeline(asp_file: str, output_dir: str = ".",
                 model_file: str = None):
    """
    Full pipeline: parse facts -> solve -> generate explanations
    -> export results.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"ASP EXPLANATION GENERATOR")
    print(f"Instance : {asp_file}")
    if model_file:
        print(f"Model    : {model_file}")
    print(f"{'='*60}\n")

    # Step 1: Parse facts
    print("[1] Parsing instance facts...")
    facts = parse_facts(asp_file)
    print(f"    Primary suppliers : {len(facts.primary)}")
    print(f"    Backup suppliers  : {len(facts.backup)}")
    print(f"    Scenarios         : {facts.scenarios}")
    print(f"    Demand            : {facts.demand:,}")
    print(f"    Disrupted pairs   : {len(facts.disrupted)}")
    print(f"    Soc threshold     : {facts.soc_threshold}")
    print(f"    Env threshold     : {facts.env_threshold}")

    # Step 2: Solve
    print("\n[2] Running Clingo solver...")
    decision = solve_and_parse(asp_file, facts, model_file)
    if decision is None:
        print("    ABORT: No stable model found.")
        return

    for w in facts.scenarios:
        n_sel  = len(decision.selected_primary[w])
        n_back = len(decision.selected_backup[w])
        n_dis  = len([s for s in facts.primary if (s,w) in facts.disrupted])
        print(f"    Scenario {w}: {n_sel} primary selected, "
              f"{n_back} backup selected, {n_dis} disrupted")

    # Step 3: Generate explanations
    print("\n[3] Generating explanations...")
    gen     = ExplanationGenerator(facts, decision)
    records = gen.generate_all()
    print(f"    Total explanation records generated: {len(records)}")

    by_type = {}
    for r in records:
        by_type.setdefault(r.decision_type, []).append(r)
    for dt, recs in by_type.items():
        print(f"    {dt:<30}: {len(recs)} records")

    # Step 4: Export
    print("\n[4] Exporting results...")

    # Human-readable text
    txt_path = output_dir / "explanations.txt"
    with open(txt_path, "w") as f:
        for rec in records:
            f.write(format_record(rec))
            f.write("\n\n")
    print(f"  [TXT] Human-readable explanations written to {txt_path}")

    # JSON
    json_path = output_dir / "explanations.json"
    export_json(records, str(json_path))

    # Coverage matrix (single instance)
    instance_key = Path(asp_file).stem
    csv_path = output_dir / "coverage_matrix.csv"
    export_coverage_matrix({instance_key: records}, facts.scenarios, str(csv_path))

    print(f"\n{'='*60}")
    print("PIPELINE COMPLETE")
    print(f"{'='*60}\n")

    return records, facts, decision


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python explanation_generator.py <instance_file> "
              "[output_dir] [model_file]")
        print("Example: python explanation_generator.py instance_100.lp "
              "./output_100 model.lp")
        sys.exit(1)

    asp_file   = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "./explanation_output"
    model_file = sys.argv[3] if len(sys.argv) > 3 else None
    run_pipeline(asp_file, output_dir, model_file)
