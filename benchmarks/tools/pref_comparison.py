#!/usr/bin/env python3
"""Compare prefctl vs prefv3 benchmark results.

Usage:
    python -m benchmarks.tools.pref_comparison <prefctl_results.json> <prefv3_results.json>

Outputs:
    1. Per-type accuracy table (side-by-side)
    2. Question-level pass/fail flips
    3. num_memories_found summaries and mean delta (known counts only)
"""

import json
import sys
import statistics
import argparse
from collections import defaultdict

TASK_ORDER = [
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
]


def load_results(path: str) -> dict:
    with open(path) as f:
        data = json.load(f)
    # Support both top-level list and {"results": [...]}
    if isinstance(data, list):
        return {"results": data, "run_id": "", "model": ""}
    return data


def per_type_accuracy(results: list[dict]) -> dict[str, float]:
    by_type = defaultdict(lambda: {"pass": 0, "total": 0})
    for r in results:
        qtype = r["question_type"]
        by_type[qtype]["total"] += 1
        if r.get("label"):
            by_type[qtype]["pass"] += 1
    return {k: v["pass"] / v["total"] if v["total"] > 0 else 0 for k, v in by_type.items()}


def per_type_count(results: list[dict]) -> dict[str, int]:
    by_type = defaultdict(int)
    for r in results:
        by_type[r["question_type"]] += 1
    return dict(by_type)


def question_flips(prefctl_results: list[dict], prefv3_results: list[dict]) -> list[dict]:
    """Find questions that changed pass/fail status."""
    ctl_map = {r["question_id"]: r for r in prefctl_results}
    v3_map = {r["question_id"]: r for r in prefv3_results}
    all_questions = set(ctl_map.keys()) | set(v3_map.keys())
    flips = []
    for q in sorted(all_questions):
        ctl = ctl_map.get(q)
        v3 = v3_map.get(q)
        if ctl and v3:
            ctl_label = bool(ctl.get("label"))
            v3_label = bool(v3.get("label"))
            if ctl_label != v3_label:
                flips.append({
                    "question_id": q,
                    "question": (ctl.get("question") or v3.get("question") or q)[:120],
                    "prefctl": ctl_label,
                    "prefv3": v3_label,
                    "type": qtype_for(q, ctl, v3),
                })
    return flips


def qtype_for(question, *records):
    for r in records:
        if r and r.get("question_type"):
            return r["question_type"]
    return "?"


def memories_delta(prefctl_results: list[dict], prefv3_results: list[dict]) -> dict:
    ctl_mem = [r["num_memories_found"] for r in prefctl_results
               if r.get("num_memories_found") is not None]
    v3_mem = [r["num_memories_found"] for r in prefv3_results
              if r.get("num_memories_found") is not None]
    return {
        "prefctl_missing": len(prefctl_results) - len(ctl_mem),
        "prefv3_missing": len(prefv3_results) - len(v3_mem),
        "prefctl_mean": statistics.mean(ctl_mem) if ctl_mem else float("nan"),
        "prefctl_median": statistics.median(ctl_mem) if ctl_mem else float("nan"),
        "prefctl_stdev": statistics.stdev(ctl_mem) if len(ctl_mem) > 1 else 0,
        "prefv3_mean": statistics.mean(v3_mem) if v3_mem else float("nan"),
        "prefv3_median": statistics.median(v3_mem) if v3_mem else float("nan"),
        "prefv3_stdev": statistics.stdev(v3_mem) if len(v3_mem) > 1 else 0,
        "delta_mean": (statistics.mean(v3_mem) - statistics.mean(ctl_mem)) if ctl_mem and v3_mem else float("nan"),
    }


def overall_accuracy(results: list[dict]) -> float:
    labels = [r.get("label", 0) for r in results]
    return sum(labels) / len(labels) if labels else 0


def abstention_accuracy(results: list[dict]) -> tuple[float, int]:
    abst = [r.get("label", 0) for r in results if r.get("is_abstention")]
    if not abst:
        return (0, 0)
    return (sum(abst) / len(abst), len(abst))


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m benchmarks.tools.pref_comparison <prefctl_results.json> <prefv3_results.json>")
        sys.exit(1)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("control", help="Control results JSON")
    parser.add_argument("variant", help="Variant results JSON")
    parser.add_argument("--gate", type=float, default=0.85,
                        help="Preference accuracy threshold, from 0 to 1 (default: 0.85)")
    args = parser.parse_args()
    if not 0 <= args.gate <= 1:
        parser.error("--gate must be between 0 and 1")
    ctl_data = load_results(args.control)
    v3_data = load_results(args.variant)

    ctl_results = ctl_data.get("results", ctl_data) if isinstance(ctl_data, dict) else ctl_data
    v3_results = v3_data.get("results", v3_data) if isinstance(v3_data, dict) else v3_data

    ctl_run = ctl_data.get("run_id", "")
    v3_run = v3_data.get("run_id", "")
    ctl_model = ctl_data.get("model", "")
    v3_model = v3_data.get("model", "")

    # ---- Section 1: Per-type accuracy table ----
    print("=" * 72)
    print("PREFCTL vs PREFV3 — PER-TYPE ACCURACY")
    print("=" * 72)
    print(f"  Run IDs: prefctl={ctl_run or 'N/A'}  prefv3={v3_run or 'N/A'}")
    print(f"  Models:  prefctl={ctl_model or 'N/A'}  prefv3={v3_model or 'N/A'}")
    print(f"{'=' * 72}")

    ctl_by_type = per_type_accuracy(ctl_results)
    v3_by_type = per_type_accuracy(v3_results)
    ctl_counts = per_type_count(ctl_results)
    v3_counts = per_type_count(v3_results)

    header = f"  {'Task Type':<30s}  {'PREFCTL':>8s}  {'PREFV3':>8s}  {'Delta':>8s}  {'N':>6s}"
    print(header)
    print("  " + "-" * 64)

    for task_type in TASK_ORDER:
        ctl_acc = ctl_by_type.get(task_type, 0)
        v3_acc = v3_by_type.get(task_type, 0)
        delta = (v3_acc - ctl_acc) * 100
        n = ctl_counts.get(task_type, 0)
        v3_n = v3_counts.get(task_type, 0)
        n_str = f"{n}/{v3_n}"
        print(f"  {task_type:<30s}  {ctl_acc:>6.1%}   {v3_acc:>6.1%}   {delta:+7.1f}%   {n_str:>6s}")

    ctl_overall = overall_accuracy(ctl_results)
    v3_overall = overall_accuracy(v3_results)
    delta_overall = (v3_overall - ctl_overall) * 100
    print("  " + "-" * 64)
    print(f"  {'Overall':<30s}  {ctl_overall:>6.1%}   {v3_overall:>6.1%}   {delta_overall:+7.1f}%   {len(ctl_results):>6d}")

    # Abstention
    ctl_abs_acc, ctl_abs_n = abstention_accuracy(ctl_results)
    v3_abs_acc, v3_abs_n = abstention_accuracy(v3_results)
    if ctl_abs_n > 0 or v3_abs_n > 0:
        print(f"\n  Abstention:  prefctl={ctl_abs_acc:.1%} ({ctl_abs_n})  prefv3={v3_abs_acc:.1%} ({v3_abs_n})")

    # ---- Section 2: Question-level flips ----
    print(f"\n{'=' * 72}")
    print("QUESTION-LEVEL FLIPS (prefctl -> prefv3)")
    print("=" * 72)

    ctl_ids = {r["question_id"] for r in ctl_results}
    v3_ids = {r["question_id"] for r in v3_results}
    print(f"  Unmatched question_ids: prefctl={len(ctl_ids - v3_ids)}  prefv3={len(v3_ids - ctl_ids)}")

    flips = question_flips(ctl_results, v3_results)
    if flips:
        print(f"  {'Type':<28s}  {'Question':<40s}  {'PREFCTL':>8s}  {'PREFV3':>8s}")
        print("  " + "-" * 60)
        for f in flips:
            arrow = "FAIL -> PASS" if f["prefv3"] and not f["prefctl"] else "PASS -> FAIL"
            print(f"  {f['type']:<28s}  {f['question']:<40s}  {arrow:>12s}")
    else:
        print("  No flips detected.")

    # ---- Section 3: num_memories_found deltas ----
    print(f"\n{'=' * 72}")
    print("num_memories_found DELTAS")
    print("=" * 72)
    delta = memories_delta(ctl_results, v3_results)
    print(f"  PREFCTL  mean={delta['prefctl_mean']:.1f}  median={delta['prefctl_median']:.1f}  stdev={delta['prefctl_stdev']:.2f}")
    print(f"  PREFV3   mean={delta['prefv3_mean']:.1f}  median={delta['prefv3_median']:.1f}  stdev={delta['prefv3_stdev']:.2f}")
    print(f"  DELTA    mean={delta['delta_mean']:+.1f}")
    print(f"  Missing counts: prefctl={delta['prefctl_missing']}  prefv3={delta['prefv3_missing']}")

    # ---- Gate check ----
    pref_acc_ctl = ctl_by_type.get("single-session-preference", 0)
    pref_acc_v3 = v3_by_type.get("single-session-preference", 0)
    gate_pref = pref_acc_v3 >= args.gate
    print(f"\n{'=' * 72}")
    print("GATE CHECK")
    print("=" * 72)
    print(f"  Preference accuracy:  PREFCTL={pref_acc_ctl:.1%}  PREFV3={pref_acc_v3:.1%}  target>={args.gate:.0%}  {'PASS' if gate_pref else 'FAIL'}")

    # Check regression on other types
    regressions = []
    for task_type in TASK_ORDER:
        if task_type == "single-session-preference":
            continue
        ctl_acc = ctl_by_type.get(task_type, 0)
        v3_acc = v3_by_type.get(task_type, 0)
        if v3_acc < ctl_acc:
            regressions.append((task_type, ctl_acc, v3_acc))
    if regressions:
        print(f"  Regressions detected:")
        for t, c, v in regressions:
            print(f"    {t}: {c:.1%} -> {v:.1%} ({(v - c):+.1%})")
    else:
        print("  No regressions on non-preference types.")

    print()


if __name__ == "__main__":
    main()
