"""Evaluate LLM categorization accuracy on labeled fixture set.

Loads ``tests/fixtures/labeled/manifest.json`` (validated against the
``InvoiceCategory`` enum at startup), uploads each generated XML fixture
through ``POST /invoices/ksef``, then calls
``POST /invoices/{id}/categorize?force=true`` and compares the LLM's
verdict against the manifest's ``expected`` value.

Reports top-1 accuracy, per-category breakdown, confusion-mismatch
summary, latency min/median/max (percentiles need N>>11), confidence
calibration gap (mean confidence on correct vs wrong predictions) and a
cost estimate reconciled to the $0.000156/call Langfuse observation
captured in the README.

A/B mode (``--baseline``) runs the eval twice — once against the
``--url`` (candidate) and once against ``--baseline``. Reports per-fixture
flips: which fixtures got fixed (wrong → correct) and which regressed
(correct → wrong). This makes prompt-iteration safe: a 2-point average
accuracy bump can hide three regressions.

Usage::

    python scripts/eval_categorization.py
    python scripts/eval_categorization.py --url https://invoice-processor-510066601703.europe-central2.run.app
    python scripts/eval_categorization.py --out docs/eval/run_2026-05-11.json
    python scripts/eval_categorization.py --min-accuracy 0.80
    python scripts/eval_categorization.py --baseline http://localhost:8001
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force UTF-8 stdout/stderr — Windows console defaults to cp1250 which
# chokes on the polish category strings, ✓/✗ ticks and the → arrow.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app.schemas.category import InvoiceCategory  # noqa: E402

MANIFEST_PATH = ROOT / "tests" / "fixtures" / "labeled" / "manifest.json"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "labeled"
DEFAULT_URL = "http://localhost:8000"

# Reconciled to Langfuse traces captured during Phase 8 deploy (see
# docs/langfuse_categorize_trace_detail.png). Updated whenever the
# canonical screenshot is re-taken — keeps eval cost in sync with the
# observability evidence in the README.
COST_PER_CALL_USD = 0.000156

VALID_CATEGORIES = {c.value for c in InvoiceCategory}


@dataclass
class FixtureResult:
    """One categorize call against one fixture."""

    fixture_id: str
    expected: str
    predicted: str | None
    confidence: float | None
    correct: bool
    latency_ms: float
    error: str | None = None


@dataclass
class EvalReport:
    """Aggregate report for one run against one API URL."""

    url: str
    n_total: int
    n_correct: int
    n_errors: int
    accuracy: float
    latency_min_ms: float
    latency_median_ms: float
    latency_max_ms: float
    confidence_mean_correct: float | None
    confidence_mean_wrong: float | None
    confidence_gap: float | None
    estimated_cost_usd: float
    per_category: dict[str, dict[str, int]]
    confusion_mismatches: dict[str, dict[str, int]]
    results: list[dict] = field(default_factory=list)


def load_manifest() -> list[dict]:
    """Parse manifest and reject any entry whose ``expected`` is not in the enum.

    Exits 1 if validation fails — protects the eval from silently
    counting valid LLM predictions as wrong because the manifest
    string drifted away from ``app/schemas/category.py``.
    """
    entries = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    bad = [e["id"] for e in entries if e.get("expected") not in VALID_CATEGORIES]
    if bad:
        print(
            "ERROR: manifest contains 'expected' values not in InvoiceCategory enum.\n"
            f"  Offending fixtures: {bad}\n"
            f"  Valid categories: {sorted(VALID_CATEGORIES)}",
            file=sys.stderr,
        )
        sys.exit(1)
    missing_xml = [e["id"] for e in entries if not (FIXTURE_DIR / f"{e['id']}.xml").exists()]
    if missing_xml:
        print(
            "ERROR: manifest references fixtures whose XML does not exist on disk.\n"
            f"  Missing: {missing_xml}\n"
            "  Run: python scripts/generate_eval_fixtures.py",
            file=sys.stderr,
        )
        sys.exit(1)
    return entries


def upload_ksef(client: httpx.Client, base_url: str, xml_path: Path) -> int:
    """POST one fixture to /invoices/ksef and return the invoice id.

    Accepts both 201 (new row) and 200 (Redis idempotency cache hit) —
    re-running the eval against the same API yields the same invoice
    ids on the second pass, which is fine for our purposes (categorize
    is the actual measurement, ?force=true bypasses its cache).
    """
    with xml_path.open("rb") as fh:
        resp = client.post(
            f"{base_url}/invoices/ksef",
            files={"file": (xml_path.name, fh, "application/xml")},
            timeout=60.0,
        )
    resp.raise_for_status()
    return resp.json()["id"]


def categorize(client: httpx.Client, base_url: str, invoice_id: int) -> tuple[str, float, float]:
    """POST /invoices/{id}/categorize?force=true and return predicted category, confidence, latency.

    ``?force=true`` is non-negotiable: without it the second call onward
    returns the DB-cached category and we'd measure cache hit rate, not
    LLM accuracy. The latency excludes Redis idempotency lookup on the
    KSeF upload — it measures the categorize round trip only.
    """
    t0 = time.perf_counter()
    resp = client.post(
        f"{base_url}/invoices/{invoice_id}/categorize",
        params={"force": "true"},
        # 60s covers cold-start RAG path (embedding singleton load +
        # first-call Qdrant connection + first OpenAI handshake), which
        # observed ~17s max in steady state. 30s default tripped on the
        # first 3-4 fixtures of a fresh container.
        timeout=60.0,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    resp.raise_for_status()
    body = resp.json()
    return body["category"], body["confidence"], elapsed_ms


def run_one(client: httpx.Client, base_url: str, entry: dict) -> FixtureResult:
    fixture_id = entry["id"]
    expected = entry["expected"]
    xml_path = FIXTURE_DIR / f"{fixture_id}.xml"
    try:
        invoice_id = upload_ksef(client, base_url, xml_path)
        predicted, confidence, latency_ms = categorize(client, base_url, invoice_id)
        return FixtureResult(
            fixture_id=fixture_id,
            expected=expected,
            predicted=predicted,
            confidence=confidence,
            correct=(predicted == expected),
            latency_ms=latency_ms,
        )
    except httpx.HTTPError as exc:
        return FixtureResult(
            fixture_id=fixture_id,
            expected=expected,
            predicted=None,
            confidence=None,
            correct=False,
            latency_ms=0.0,
            error=str(exc),
        )


def run_eval(base_url: str, entries: list[dict]) -> list[FixtureResult]:
    """Run categorize against every fixture in the manifest.

    One HTTP error on a fixture does NOT abort the run — a 503
    cold-start on the first fixture would otherwise mask the rest of
    the suite. Errors are recorded and surfaced in the final report.
    """
    results: list[FixtureResult] = []
    with httpx.Client() as client:
        for i, entry in enumerate(entries, start=1):
            print(
                f"  [{i:>2}/{len(entries)}] {entry['id']:<48} expected: {entry['expected']}",
                end=" ",
                flush=True,
            )
            res = run_one(client, base_url, entry)
            if res.error:
                print(f"-> ERROR: {res.error}")
            else:
                tick = "✓" if res.correct else "✗"
                print(f"-> {res.predicted} ({res.confidence:.2f}) {tick}")
            results.append(res)
    return results


def build_report(url: str, results: list[FixtureResult]) -> EvalReport:
    n_total = len(results)
    successful = [r for r in results if r.error is None]
    n_errors = n_total - len(successful)
    n_correct = sum(1 for r in successful if r.correct)
    accuracy = n_correct / n_total if n_total else 0.0

    latencies = sorted(r.latency_ms for r in successful)
    if latencies:
        # N≈11 is too small for percentiles (p95 formula degrades to ~p91 here).
        # min/median/max gives an honest spread without overclaiming precision.
        lat_min = latencies[0]
        lat_median = statistics.median(latencies)
        lat_max = latencies[-1]
    else:
        lat_min = lat_median = lat_max = 0.0

    correct_confs = [r.confidence for r in successful if r.correct and r.confidence is not None]
    wrong_confs = [r.confidence for r in successful if not r.correct and r.confidence is not None]
    mean_c = statistics.mean(correct_confs) if correct_confs else None
    mean_w = statistics.mean(wrong_confs) if wrong_confs else None
    gap = (mean_c - mean_w) if (mean_c is not None and mean_w is not None) else None

    per_cat: dict[str, dict[str, int]] = defaultdict(lambda: {"correct": 0, "total": 0})
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in successful:
        per_cat[r.expected]["total"] += 1
        if r.correct:
            per_cat[r.expected]["correct"] += 1
        else:
            confusion[r.expected][r.predicted or "<error>"] += 1

    return EvalReport(
        url=url,
        n_total=n_total,
        n_correct=n_correct,
        n_errors=n_errors,
        accuracy=accuracy,
        latency_min_ms=lat_min,
        latency_median_ms=lat_median,
        latency_max_ms=lat_max,
        confidence_mean_correct=mean_c,
        confidence_mean_wrong=mean_w,
        confidence_gap=gap,
        estimated_cost_usd=len(successful) * COST_PER_CALL_USD,
        per_category={k: dict(v) for k, v in per_cat.items()},
        confusion_mismatches={k: dict(v) for k, v in confusion.items()},
        results=[asdict(r) for r in results],
    )


def print_report(rep: EvalReport) -> None:
    print()
    print("=== Categorization Eval Results ===")
    print(f"URL:           {rep.url}")
    print(f"Sample:        {rep.n_total} fixtures / {len(rep.per_category)} categories covered")
    print(f"Top-1:         {rep.n_correct}/{rep.n_total} ({rep.accuracy:.1%})")
    print(f"Errors:        {rep.n_errors}")
    print(
        f"Latency:       min={rep.latency_min_ms:.0f}ms  "
        f"median={rep.latency_median_ms:.0f}ms  max={rep.latency_max_ms:.0f}ms"
    )
    if rep.confidence_gap is not None:
        signal = (
            "usable signal"
            if rep.confidence_gap >= 0.099
            else "WEAK — confidence unreliable for routing"
        )
        print(
            f"Confidence:    mean(correct)={rep.confidence_mean_correct:.2f}  "
            f"mean(wrong)={rep.confidence_mean_wrong:.2f}  "
            f"gap={rep.confidence_gap:+.2f}  ← {signal}"
        )
    elif rep.confidence_mean_correct is not None:
        print(
            f"Confidence:    mean(correct)={rep.confidence_mean_correct:.2f}  (no wrong predictions to compare)"
        )
    print(
        f"Cost:          ${rep.estimated_cost_usd:.6f}  ({rep.n_total - rep.n_errors} × ${COST_PER_CALL_USD})"
    )
    print()
    print("Per-category accuracy:")
    for cat, stats in sorted(rep.per_category.items()):
        bar = f"{stats['correct']}/{stats['total']}"
        pct = stats["correct"] / stats["total"] if stats["total"] else 0
        print(f"  {cat:<35} {bar:<8} ({pct:.0%})")
    if rep.confusion_mismatches:
        print()
        print("Confusion (mismatches only):")
        for expected, preds in rep.confusion_mismatches.items():
            for predicted, count in preds.items():
                print(f"  {expected} → {predicted}: {count}")


def diff_runs(baseline: EvalReport, candidate: EvalReport) -> None:
    """Print per-fixture flips between two runs."""
    print()
    print("=== A/B Diff (baseline vs candidate) ===")
    print(
        f"  Baseline:  {baseline.n_correct}/{baseline.n_total} ({baseline.accuracy:.1%})  @ {baseline.url}"
    )
    print(
        f"  Candidate: {candidate.n_correct}/{candidate.n_total} ({candidate.accuracy:.1%})  @ {candidate.url}"
    )

    baseline_by_id = {r["fixture_id"]: r for r in baseline.results}
    candidate_by_id = {r["fixture_id"]: r for r in candidate.results}

    fixed: list[str] = []
    regressed: list[str] = []
    for fixture_id, base_r in baseline_by_id.items():
        cand_r = candidate_by_id.get(fixture_id)
        if cand_r is None:
            continue
        if not base_r["correct"] and cand_r["correct"]:
            fixed.append(
                f"  ✓ FIXED:     {fixture_id}  was: {base_r['predicted']}  now: {cand_r['predicted']}"
            )
        elif base_r["correct"] and not cand_r["correct"]:
            regressed.append(
                f"  ✗ REGRESSED: {fixture_id}  was: {base_r['predicted']}  now: {cand_r['predicted']}"
            )
    print()
    if fixed:
        print("\n".join(fixed))
    if regressed:
        print("\n".join(regressed))
    if not fixed and not regressed:
        print("  (no flips — verdict unchanged on every fixture)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--url", default=DEFAULT_URL, help=f"Candidate API URL (default {DEFAULT_URL})"
    )
    parser.add_argument(
        "--baseline",
        help="Baseline API URL — enables A/B mode (runs eval twice, prints per-fixture flips).",
    )
    parser.add_argument(
        "--out",
        help="Path to JSON output for committable baseline (e.g. docs/eval/run_2026-05-11.json).",
    )
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=0.0,
        help="Exit 1 if candidate accuracy below this floor (default 0.0 = no gate).",
    )
    args = parser.parse_args()

    entries = load_manifest()

    print(f"Running eval against candidate: {args.url}")
    candidate_results = run_eval(args.url, entries)
    candidate_report = build_report(args.url, candidate_results)
    print_report(candidate_report)

    baseline_report: EvalReport | None = None
    if args.baseline:
        print(f"\nRunning eval against baseline: {args.baseline}")
        baseline_results = run_eval(args.baseline, entries)
        baseline_report = build_report(args.baseline, baseline_results)
        print_report(baseline_report)
        diff_runs(baseline_report, candidate_report)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict = {"candidate": asdict(candidate_report)}
        if baseline_report is not None:
            payload["baseline"] = asdict(baseline_report)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nSaved JSON: {out_path}")

    if candidate_report.accuracy < args.min_accuracy:
        print(
            f"\nFAIL: accuracy {candidate_report.accuracy:.1%} below floor {args.min_accuracy:.1%}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
