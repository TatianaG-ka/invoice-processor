"""Render a saved EvalReport JSON in the same format print_report() produces.

Usage: python scripts/print_eval_report.py docs/eval/baseline_2026-05-11.json

Lets you regenerate the terminal output (e.g. for a README screenshot)
without re-running the full eval against a live stack.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COST_PER_CALL_USD = 0.000156


def render(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    d = payload["candidate"] if "candidate" in payload else payload

    print()
    print("=== Categorization Eval Results ===")
    print(f"URL:           {d['url']}")
    print(f"Sample:        {d['n_total']} fixtures / {len(d['per_category'])} categories covered")
    print(f"Top-1:         {d['n_correct']}/{d['n_total']} ({d['accuracy']:.1%})")
    print(f"Errors:        {d['n_errors']}")
    print(
        f"Latency:       min={d['latency_min_ms']:.0f}ms  "
        f"median={d['latency_median_ms']:.0f}ms  max={d['latency_max_ms']:.0f}ms"
    )
    gap = d.get("confidence_gap")
    if gap is not None:
        signal = "usable signal" if gap >= 0.099 else "WEAK — confidence unreliable for routing"
        print(
            f"Confidence:    mean(correct)={d['confidence_mean_correct']:.2f}  "
            f"mean(wrong)={d['confidence_mean_wrong']:.2f}  "
            f"gap={gap:+.2f}  ← {signal}"
        )
    elif d.get("confidence_mean_correct") is not None:
        print(f"Confidence:    mean(correct)={d['confidence_mean_correct']:.2f}  (no wrong predictions to compare)")
    print(
        f"Cost:          ${d['estimated_cost_usd']:.6f}  "
        f"({d['n_total'] - d['n_errors']} × ${COST_PER_CALL_USD})"
    )
    print()
    print("Per-category accuracy:")
    for cat, stats in sorted(d["per_category"].items()):
        bar = f"{stats['correct']}/{stats['total']}"
        pct = stats["correct"] / stats["total"] if stats["total"] else 0
        print(f"  {cat:<35} {bar:<8} ({pct:.0%})")
    if d.get("confusion_mismatches"):
        print()
        print("Confusion (mismatches only):")
        for expected, preds in d["confusion_mismatches"].items():
            for predicted, count in preds.items():
                print(f"  {expected} → {predicted}: {count}")
    print()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/print_eval_report.py <path-to-eval-json>", file=sys.stderr)
        sys.exit(2)
    render(Path(sys.argv[1]))
