"""Aggregate and print benchmark results."""

import json
import os
import sys
from collections import defaultdict

from benchmarks.config import RESULTS_DIR


def print_report(results: list[dict], model: str = ""):
    """Print per-task accuracy, overall accuracy, abstention accuracy."""
    if not results:
        print("No results to report.")
        return

    by_task = defaultdict(list)
    abstention = []

    for r in results:
        qtype = r["question_type"]
        by_task[qtype].append(r["label"])
        if r.get("is_abstention"):
            abstention.append(r["label"])

    header = "LongMemEval Results"
    if model:
        header += f" — {model}"
    print(f"\n{'=' * 60}")
    print(header)
    print(f"{'=' * 60}")

    print("\nResults by task:")
    task_accs = []
    for task_type in [
        "single-session-user",
        "single-session-assistant",
        "single-session-preference",
        "multi-session",
        "temporal-reasoning",
        "knowledge-update",
    ]:
        labels = by_task.get(task_type, [])
        if labels:
            acc = sum(labels) / len(labels)
            task_accs.append(acc)
            print(f"  {task_type:30s}: {acc:.1%} ({len(labels)})")

    print(f"\n{'─' * 40}")
    if task_accs:
        print(f"  {'Task-averaged accuracy':30s}: {sum(task_accs)/len(task_accs):.1%}")

    all_labels = [r["label"] for r in results]
    if all_labels:
        print(f"  {'Overall accuracy':30s}: {sum(all_labels)/len(all_labels):.1%}")

    if abstention:
        print(f"  {'Abstention accuracy':30s}: {sum(abstention)/len(abstention):.1%} ({len(abstention)})")

    print(f"  {'Total questions':30s}: {len(results)}")
    print(f"{'=' * 60}\n")


def save_results(results: list[dict], run_id: str, model: str):
    """Save results JSON to disk."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    model_slug = model.replace("/", "_")
    path = os.path.join(RESULTS_DIR, f"{run_id}_{model_slug}.json")
    with open(path, "w") as f:
        json.dump({"run_id": run_id, "model": model, "results": results}, f, indent=2)
    print(f"Results saved to {path}")
    return path


def load_and_print(path: str):
    """Load results from file and print report."""
    with open(path) as f:
        data = json.load(f)
    print_report(data["results"], model=data.get("model", ""))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m benchmarks.longmemeval.report <results_file.json>")
        sys.exit(1)
    load_and_print(sys.argv[1])
