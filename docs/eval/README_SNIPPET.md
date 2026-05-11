# README snippet — paste-ready Evaluation section

Replace the placeholder "Evaluation" section in your main README with the
block below **after** running the eval and filling in the four
`<populate>` markers from the script's stdout.

---

## Evaluation

Beyond unit tests, the LLM categorization path is evaluated against a
hand-labeled fixture set so accuracy lives next to cost and latency in
the README, not "it returns JSON."

The eval set is **11 synthetic FA(3) invoices across 6 highest-volume
expense categories + 1 adversarial test case** (ERP licence + consulting
hours, 80 / 20 value split — ground-truth `Usługi IT i oprogramowanie`,
testing whether the LLM respects line-item proportions or shortcuts to
seller-name keywords). Fixtures are generated from a single manifest at
[`tests/fixtures/labeled/manifest.json`](tests/fixtures/labeled/manifest.json)
so adding a category is a JSON edit, not an XML hand-write.

### Latest run ([`docs/eval/baseline_2026-05-11.json`](docs/eval/baseline_2026-05-11.json))

| Metric | Value | Notes |
|---|---|---|
| Top-1 accuracy | 10 / 11 (90.9 %) | Fresh LLM call per fixture (`?force=true` bypasses the ADR-007 DB cache) |
| Per-category accuracy | see below | 6 categories — IT / CONSULTING / MARKETING / OFFICE / TRANSPORT / EQUIPMENT. Only IT scores below 100 % (2 / 3) — the one miss is the adversarial fixture, by design |
| Latency | min = 1633 ms, median = 2497 ms, max = 7023 ms | End-to-end including KSeF parse + Qdrant top-3 lookup + LLM round trip. min/median/max instead of p50/p95 because percentiles need N>>11. Max is the first-fixture spike (Qdrant collection first-touch + first OpenAI handshake on the warm container); subsequent fixtures stay near the median |
| Confidence calibration gap | +0.10 (mean correct 0.90 − mean wrong 0.80) | Sits exactly on the `> 0.10` "usable signal" threshold → at this calibration confidence alone isn't enough to gate `auto-approve vs human-review` routing; needs prompt iteration or N>11 to firm up |
| Cost per run | $0.001716 (11 × $0.000156) | Reconciled to per-call cost observed in Langfuse — see [`docs/langfuse_categorize_trace_detail.png`](docs/langfuse_categorize_trace_detail.png) (model `gpt-4o-mini`, 821 prompt → 55 completion tokens) |

The one miss is the adversarial fixture `adversarial_erp_consulting_software_11`: an ERP licence
(12 000 PLN) + 30 hours of consulting (3 000 PLN) — 80 / 20 net split toward IT. The LLM
predicted `Konsulting i doradztwo` (0.80 confidence), shortcutting to the seller name
("Konsulting") and the human-readable "consulting" line item instead of weighing line totals.
This is the exact failure mode the fixture was designed to surface, and at 0.80 it lands below
the 0.90 confidence the model assigns to the other 10 — usable as a routing signal at the
fixture level even when the aggregate gap is borderline.

### How to reproduce

```bash
# 1. Generate the 11 XML fixtures from the manifest
python scripts/generate_eval_fixtures.py

# 2. Start the stack locally (or skip if you target the live URL)
docker-compose up --build

# 3. Run the eval and save the committable baseline
python scripts/eval_categorization.py \
  --out docs/eval/run_$(date +%Y-%m-%d).json \
  --min-accuracy 0.80
```

The manifest's `expected` literals are validated against `app.schemas.category.InvoiceCategory`
at startup — a typo in the manifest fails fast with exit 1, so a correct
LLM prediction can never be silently counted as wrong.

### A/B mode for prompt iteration

Comparing prompt versions (e.g. before / after adding a few-shot example)
without hiding regressions behind an average accuracy bump:

```bash
python scripts/eval_categorization.py \
  --baseline https://invoice-processor-510066601703.europe-central2.run.app \
  --url http://localhost:8000
```

Output lists per-fixture flips: `FIXED` (`baseline wrong → candidate
correct`) and `REGRESSED` (`baseline correct → candidate wrong`). A "85% →
87%" average can hide three regressions traded for five fixes — flips
make that visible.
