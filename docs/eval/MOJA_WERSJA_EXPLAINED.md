# Line-by-line: 3 pliki eval — wyjaśnienie

> Polskie tłumaczenie linia po linii dla każdego z 3 plików. Pisane z myślą o czytaniu krok-po-kroku, nie o referencji. Jak natkniesz się na konstrukcję której nie znasz — szukaj jej w "jargon kit" na dole.

---

## Plik 1 — `tests/fixtures/labeled/manifest.json`

JSON-owa lista 11 obiektów. To **jedyne miejsce** w którym definiujesz dane fixtures. Generator i eval script tylko czytają z tego pliku — żadnych literałów w kodzie.

### Anatomia jednego entry

```json
{
  "id": "it_saas_01",
  "expected": "Usługi IT i oprogramowanie",
  "seller": "CloudWorks Sp. z o.o.",
  "seller_nip": "1111111111",
  "lines": [
    {"desc": "Subskrypcja SaaS plan Business - listopad 2026", "qty": 1, "net": "850.00"}
  ],
  "intent": "Clearest IT signal — single SaaS line item, no ambiguity."
}
```

| Klucz | Znaczenie | Czemu tak |
|---|---|---|
| `id` | Identyfikator fixture (snake_case z numerem) | Wykorzystywany jako nazwa pliku XML (`it_saas_01.xml`) — jeden source of truth, zero redundancji |
| `expected` | Oczekiwana kategoria — string z `InvoiceCategory` enum | Generator i eval walidują czy to jest w enumie. Jakikolwiek typo → exit 1 |
| `seller` | Nazwa sprzedawcy widoczna na fakturze | Każdy fixture ma INNĄ nazwę żeby Qdrant nie mógł rozpoznać kategorii po samej similarity sellera |
| `seller_nip` | NIP 10 cyfr (checksum-invalid, synthetic) | Wpisane jako wzór `XXXXXXXXXX` (10 powtórzonych cyfr) — visibly synthetic |
| `lines` | Pozycje faktury, każda z `desc/qty/net` | `net` to **string** (nie float) — generator parsuje przez `Decimal` żeby uniknąć utraty precyzji 0.00000000001 |
| `intent` | 1-zdaniowe wyjaśnienie po co istnieje ten fixture | Diagnostyczne — gdy ktoś otworzy plik za rok, wie czemu nie usunąć |

### 11 entries — kompozycja

| # | id | Kategoria | Co testuje |
|---|---|---|---|
| 1 | `it_saas_01` | IT | Najczystszy IT signal |
| 2 | `it_hosting_02` | IT | Hosting bez słowa "SaaS" |
| 3 | `consulting_business_01` | Konsulting | Klasyczny consulting |
| 4 | `consulting_tax_02` | Konsulting | Tax advisory (inne podpole, ta sama kategoria) |
| 5 | `marketing_ads_01` | Marketing | Platform-branded (Google Ads) |
| 6 | `marketing_agency_02` | Marketing | Service-based (retainer + kreacja) |
| 7 | `office_paper_01` | Materiały biurowe | Klasyk — papier/długopisy/segregatory |
| 8 | `office_toner_02` | Materiały biurowe | Toner — łatwo pomylić z EQUIPMENT |
| 9 | `transport_courier_01` | Transport | Kurier |
| 10 | `equipment_laptop_01` | Sprzęt | Laptop — łatwo pomylić z IT |
| 11 | `adversarial_erp_consulting_software_11` | **IT** (ground truth) | 🔥 ADVERSARIAL: seller "ERP Consulting", 80% wartości = oprogramowanie, 20% = konsulting |

Pokrywane kategorie: 6 z 12 enum (IT, CONSULTING, MARKETING, OFFICE, TRANSPORT, EQUIPMENT). Świadoma decyzja per Twoja deklaracja "tnij 4 fixtures".

---

## Plik 2 — `scripts/generate_eval_fixtures.py`

Konwertuje manifest → 11 plików XML. Walidacja na początku, generacja na końcu. ~190 linii.

### Linie 1-19 — docstring

Wyjaśnia: co robi, co waliduje (enum membership), gdzie zapisuje, jak uruchomić. Pierwsza linia docstringa jest re-używana jako `description=` w argparse na samym dole — `__doc__.splitlines()[0]`.

### Linie 21-30 — importy + zmiana ścieżki

```python
from __future__ import annotations          # Pozwala używać `list[dict]` bez `from typing import List` — Python 3.9+ feature
import argparse, json, sys                  # Standard library
from decimal import ROUND_HALF_UP, Decimal  # Dokładna arytmetyka pieniężna (NIE float — 0.1+0.2 ≠ 0.3 w float)
from pathlib import Path                    # OS-agnostic ścieżki (Windows / Linux)
from xml.sax.saxutils import escape         # XML escape (& → &amp;, < → &lt;) — chroni przed broken XML jak seller = "Foo & Bar"
```

### Linie 32-34 — sys.path manipulation

```python
ROOT = Path(__file__).resolve().parent.parent   # ROOT = invoice_processor/ (2 poziomy w górę od scripts/generate_eval_fixtures.py)
sys.path.insert(0, str(ROOT))                   # Dodaje invoice_processor/ na początek PYTHONPATH

from app.schemas.category import InvoiceCategory  # noqa: E402  ← komentarz wycisza ostrzeżenie ruff/flake8 że import nie jest na górze
```

**Czemu to:** skrypt jest w `scripts/`, ale chcemy importować z `app/`. Bez tego hack'a musiałabyś uruchamiać `PYTHONPATH=. python scripts/generate_eval_fixtures.py`. Z tym — `python scripts/generate_eval_fixtures.py` działa z każdego cwd.

### Linie 36-46 — stałe

```python
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "labeled" / "manifest.json"
OUT_DIR = ROOT / "tests" / "fixtures" / "labeled"
KSEF_NS = "http://crd.gov.pl/wzor/2025/06/25/13775/"   # FA(3) namespace — copy-paste z app/services/ksef_parser.py:37
ISSUE_DATE = "2026-11-15"                              # Hardcoded data — DETERMINISTIC test (rerun = same XML = same eval)
VAT_RATE = Decimal("0.23")                             # 23% Polish standard VAT
BUYER_NAME = "Test Buyer Sp. z o.o."                   # Jeden buyer dla wszystkich — eval testuje sellera, nie buyera
BUYER_NIP = "0000000000"
VALID_CATEGORIES = {c.value for c in InvoiceCategory}  # Zbiór wszystkich legitymnych kategorii (12 stringów z enum)
REQUIRED_FIELDS = {"id", "expected", "seller", "seller_nip", "lines", "intent"}
REQUIRED_LINE_FIELDS = {"desc", "qty", "net"}
```

### Linia 49-50 — helper do formatowania Decimal

```python
def _q(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
```

`quantize` zaokrągla do dwóch miejsc po przecinku. Standard księgowy: `ROUND_HALF_UP` (0.005 → 0.01, nie banker's rounding). Wszystkie kwoty na fakturze idą przez `_q()` żeby format był spójny (`"850.00"`, nigdy `"850.0"` ani `"850"`).

### Linie 53-83 — funkcja `validate(entries)`

Iteruje po wszystkich entry, zbiera errory do listy, zwraca listę. **NIE** podnosi wyjątku w środku — chcemy widzieć WSZYSTKIE problemy naraz, nie naprawiać jeden po drugim.

```python
def validate(entries: list[dict]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()                          # do detekcji duplicate id
    for i, entry in enumerate(entries):
        missing = REQUIRED_FIELDS - entry.keys()        # set difference: wymagane minus dostępne = brakujące
        if missing:
            errors.append(f"[entry #{i}] missing fields: {sorted(missing)}")
            continue                                    # bez wymaganych pól nie ma sensu sprawdzać reszty
        eid = entry["id"]
        if eid in seen_ids:
            errors.append(f"[{eid}] duplicate id")
        seen_ids.add(eid)

        # CRITICAL CHECK — to wymóg #2 z Twoich warunków:
        if entry["expected"] not in VALID_CATEGORIES:
            errors.append(
                f"[{eid}] expected={entry['expected']!r} is NOT in InvoiceCategory enum.\n"
                f"           Valid values: {sorted(VALID_CATEGORIES)}"
            )

        # NIP sanity: 10 cyfr, jako string (żeby leading zero nie zniknęło)
        nip = entry["seller_nip"]
        if not (isinstance(nip, str) and len(nip) == 10 and nip.isdigit()):
            errors.append(f"[{eid}] seller_nip must be 10 digits, got {nip!r}")

        if not entry["lines"]:
            errors.append(f"[{eid}] lines must be non-empty")

        for j, line in enumerate(entry["lines"]):
            line_missing = REQUIRED_LINE_FIELDS - set(line.keys())
            if line_missing:
                errors.append(f"[{eid}] line[{j}] missing fields: {sorted(line_missing)}")
                continue
            try:
                Decimal(str(line["qty"]))               # czy `qty` parsuje się do Decimala?
                Decimal(line["net"])                    # czy `net` parsuje się do Decimala?
            except Exception as exc:
                errors.append(f"[{eid}] line[{j}] invalid Decimal value: {exc}")
    return errors
```

**Dlaczego `set difference` zamiast `if 'id' not in entry`?** Bo daje listę wszystkich brakujących pól na raz, nie jednego.

### Linie 86-152 — funkcja `build_xml(entry)`

Tworzy string XML dla jednego entry. Używa f-string z trzema cudzysłowami (multi-line).

```python
def build_xml(entry: dict) -> str:
    seller_name = escape(entry["seller"])       # & → &amp; — KRYTYCZNE dla nazw typu "Marketing & Co"
    seller_nip = entry["seller_nip"]
    invoice_number = f"FV/EVAL/{entry['id']}/2026"   # Każdy fixture ma unikalny invoice number

    line_blocks: list[str] = []
    net_total = Decimal("0.00")                  # Akumulator dla sumy line totals (NIE float!)
    for idx, line in enumerate(entry["lines"], start=1):    # start=1 bo NrWierszaFa zaczyna od 1, nie 0
        qty = Decimal(str(line["qty"]))                     # str() handluje zarówno int (1) jak i str ("1.5")
        unit_net = Decimal(line["net"])
        line_total = (qty * unit_net).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        net_total += line_total
        line_blocks.append(f"""    <FaWiersz>
      <NrWierszaFa>{idx}</NrWierszaFa>
      <P_7>{escape(line["desc"])}</P_7>
      <P_8A>szt</P_8A>
      <P_8B>{qty}</P_8B>
      <P_9A>{_q(unit_net)}</P_9A>
      <P_11>{_q(line_total)}</P_11>
      <P_12>23</P_12>
    </FaWiersz>""")

    # Totals — net z line_total już zaokrąglony, vat doliczam tu, gross = net + vat
    vat_total = (net_total * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    gross_total = net_total + vat_total

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
  Eval fixture (id={entry["id"]}).
  Auto-generated by scripts/generate_eval_fixtures.py — DO NOT EDIT BY HAND.
  Re-run the generator to regenerate after manifest changes.

  Expected category: {entry["expected"]}
  Intent: {entry["intent"]}
-->
<Faktura xmlns="{KSEF_NS}">
  ...
{chr(10).join(line_blocks)}
  </Fa>
</Faktura>
"""
```

**`chr(10)`** = newline character (kod ASCII 10, czyli `\n`). Używam `chr(10).join(...)` zamiast `"\n".join(...)` bo w pythonie <3.12 wewnątrz f-stringa, w wyrażeniu `{...}`, **nie wolno użyć backslasha `\`**. `"\n"` zawiera backslash → `f"{'\n'.join(line_blocks)}"` to `SyntaxError`. `chr(10)` produkuje ten sam znak bez literalnego backslasha w kodzie, więc parser f-stringa go akceptuje. (Python 3.12+ rozluźnił tę regułę przez PEP 701, ale piszemy kompatybilnie z 3.11.)

**Komentarz w XML "DO NOT EDIT BY HAND"** — chroni przed sytuacją gdy ktoś ręcznie zmieni `it_saas_01.xml`, potem ktoś inny re-uruchamia generator i nadpisze edycje bez ostrzeżenia. Standard senior-engineering hygiene.

### Linie 155-185 — funkcja `main()`

```python
def main() -> int:                              # Zwraca exit code (0 = OK, 1 = error)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Validate manifest only, do not write XML files.")
    args = parser.parse_args()

    if not MANIFEST_PATH.exists():
        print(f"ERROR: manifest not found at {MANIFEST_PATH}", file=sys.stderr)
        return 1                                # FAIL-EARLY: bez manifestu nic nie ma sensu

    entries = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    errors = validate(entries)
    if errors:
        print("Manifest validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1                                # FAIL-EARLY na invalid manifest

    if args.check:                              # Tryb `--check`: tylko walidacja, koniec
        print(f"OK: {len(entries)} entries valid (categories ∈ enum, ids unique, NIPs 10 digits).")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)  # Tworzy folder labeled/ jeśli nie istnieje
    for entry in entries:
        xml = build_xml(entry)
        path = OUT_DIR / f"{entry['id']}.xml"
        path.write_text(xml, encoding="utf-8")  # write_text z UTF-8 — KRYTYCZNE dla polskich znaków
        print(f"  wrote {path.relative_to(ROOT)}")

    print(f"\nGenerated {len(entries)} fixtures in {OUT_DIR.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())                    # `SystemExit(int)` jest cleaner niż `sys.exit(int)` — exit code propaguje
```

---

## Plik 3 — `scripts/eval_categorization.py`

Najdłuższy plik, ~340 linii. Robi 5 rzeczy:
1. Wczytuje manifest, waliduje enum
2. Uruchamia eval (upload XML → categorize → record)
3. Buduje raport (accuracy + per-category + latency + confidence + confusion)
4. Drukuje raport
5. (Opcjonalnie) A/B mode + JSON output + CI gate exit code

### Linie 1-29 — docstring

Wyjaśnia: co robi, co reportuje, jak działa A/B mode, jak uruchomić (5 wariantów).

### Linie 31-49 — importy + sys.path + stałe

```python
from __future__ import annotations

import argparse, json, statistics, sys, time          # Wszystko stdlib
from collections import defaultdict                   # auto-vivifying dict — patrz niżej
from dataclasses import asdict, dataclass, field      # Type-safe immutable records bez __init__/__repr__
from pathlib import Path

import httpx                                          # ONE zewnętrzna zależność. Już jest w repo (FastAPI test client tego używa)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.schemas.category import InvoiceCategory  # noqa: E402

MANIFEST_PATH = ROOT / "tests" / "fixtures" / "labeled" / "manifest.json"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "labeled"
DEFAULT_URL = "http://localhost:8000"
COST_PER_CALL_USD = 0.000156                          # Reconciled do Langfuse trace (Phase 8 screenshot)
VALID_CATEGORIES = {c.value for c in InvoiceCategory}
```

**`defaultdict(lambda: {"correct": 0, "total": 0})`** = dict który gdy spytasz o brakujący klucz, sam tworzy domyślny dict. Bez tego musiałabyś `if key not in d: d[key] = {...}` przed każdym dostępem.

### Linie 52-87 — dataclasses

```python
@dataclass
class FixtureResult:
    """One categorize call against one fixture."""
    fixture_id: str
    expected: str
    predicted: str | None        # None gdy HTTP error
    confidence: float | None     # None gdy HTTP error
    correct: bool
    latency_ms: float
    error: str | None = None     # str gdy był HTTP error, None gdy OK
```

`@dataclass` automatycznie generuje `__init__`, `__repr__`, `__eq__`. Mniej boilerplate niż zwykła klasa, bardziej typed niż dict. `asdict(result)` zamienia w dict do JSON-a.

```python
@dataclass
class EvalReport:
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
    results: list[dict] = field(default_factory=list)  # field() bo mutable default
```

**`field(default_factory=list)`** — w `@dataclass` nie wolno `results: list = []` (mutable shared between instances). Trzeba `field(default_factory=list)` żeby każda instancja dostała własną listę.

### Linie 90-117 — `load_manifest()`

```python
def load_manifest() -> list[dict]:
    entries = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    # CRITICAL CHECK — wymóg #2 z Twoich warunków, dla eval scriptu:
    bad = [e["id"] for e in entries if e.get("expected") not in VALID_CATEGORIES]
    if bad:
        print(
            "ERROR: manifest contains 'expected' values not in InvoiceCategory enum.\n"
            f"  Offending fixtures: {bad}\n"
            f"  Valid categories: {sorted(VALID_CATEGORIES)}",
            file=sys.stderr,
        )
        sys.exit(1)                                # FAIL-EARLY

    # Drugi check — czy XML-e istnieją na dysku?
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
```

Dwa fail-early check'i: enum + plik. Bez nich runtime błąd dopiero przy pierwszym fixture — eval marnowałby 2 godziny żeby umrzeć na 5-tym pliku.

### Linie 120-130 — `upload_ksef()`

```python
def upload_ksef(client: httpx.Client, base_url: str, xml_path: Path) -> int:
    with xml_path.open("rb") as fh:                  # "rb" = read binary — KSeF API wymaga bytes
        resp = client.post(
            f"{base_url}/invoices/ksef",
            files={"file": (xml_path.name, fh, "application/xml")},   # httpx-owy format dla multipart
            timeout=30.0,                            # 30 sek = ochrona przed wisieniem na cold-start
        )
    resp.raise_for_status()                          # 4xx/5xx → wyjątek httpx.HTTPError → catch w run_one
    return resp.json()["id"]                         # Sukces (201 lub 200) zwraca StoredInvoice z polem `id`
```

**Czemu OK na 201 ORAZ 200?** Bo druga próba postu tego samego XML hit ADR-006 Redis idempotency → 200 z tym samym `id`. To DOBRZE — nie chcemy żeby eval przedłużał się o nową fakturę w DB przy każdym uruchomieniu.

### Linie 133-148 — `categorize()` — najważniejsza funkcja

```python
def categorize(client: httpx.Client, base_url: str, invoice_id: int) -> tuple[str, float, float]:
    t0 = time.perf_counter()                        # Wysokorozdzielczy timer (>1ms precision)
    resp = client.post(
        f"{base_url}/invoices/{invoice_id}/categorize",
        params={"force": "true"},                   # 🔥 KRYTYCZNE — bez tego mierzymy DB cache, NIE LLM
        timeout=30.0,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    resp.raise_for_status()
    body = resp.json()
    return body["category"], body["confidence"], elapsed_ms
```

**Bez `?force=true`** — pierwsze wywołanie zrobi LLM, drugie zwróci cached z DB (ADR-007). Eval drugiego uruchomienia mierzyłby cache hit rate, nie LLM. To jest **najczęstsza pułapka w portfolio eval scripts**.

### Linie 151-174 — `run_one()` z try/except

```python
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
            correct=(predicted == expected),         # Top-1 match: exact string compare
            latency_ms=latency_ms,
        )
    except httpx.HTTPError as exc:
        return FixtureResult(
            fixture_id=fixture_id,
            expected=expected,
            predicted=None,
            confidence=None,
            correct=False,                           # Error = wrong (penalty), nie crash całego runa
            latency_ms=0.0,
            error=str(exc),
        )
```

**Try/except wrapping** — jeden 503 cold-start z Cloud Run nie wywala całego eval'u. Error jest zapisany w `FixtureResult.error`, reszta runa kontynuuje.

### Linie 177-200 — `run_eval()` — loop nad wszystkimi fixtures

```python
def run_eval(base_url: str, entries: list[dict]) -> list[FixtureResult]:
    results: list[FixtureResult] = []
    with httpx.Client() as client:                  # Reuse TCP connection — szybciej dla 11 sekwencyjnych callów
        for i, entry in enumerate(entries, start=1):
            print(
                f"  [{i:>2}/{len(entries)}] {entry['id']:<48} expected: {entry['expected']}",
                end=" ",                            # end=" " = NIE drukuj newline, dopisuję wynik na końcu linii
                flush=True,                         # flush=True = zobacz output natychmiast, nie po buforowaniu
            )
            res = run_one(client, base_url, entry)
            if res.error:
                print(f"-> ERROR: {res.error}")
            else:
                tick = "✓" if res.correct else "✗"
                print(f"-> {res.predicted} ({res.confidence:.2f}) {tick}")
            results.append(res)
    return results
```

**`{i:>2}`** = prawe wyrównanie do 2 znaków (1, 2, ..., 11 wyrównane do prawej). **`{entry['id']:<48}`** = lewe wyrównanie do 48 znaków. To kosmetyka — output wygląda jak kolumna, nie jak chaos.

### Linie 203-249 — `build_report()` — aggregacja statystyk

```python
def build_report(url: str, results: list[FixtureResult]) -> EvalReport:
    n_total = len(results)
    successful = [r for r in results if r.error is None]    # Tylko te bez HTTP error
    n_errors = n_total - len(successful)
    n_correct = sum(1 for r in successful if r.correct)
    accuracy = n_correct / n_total if n_total else 0.0      # Guard przeciw division by zero

    # Latency spread — min/median/max (NIE percentyle przy N=11)
    latencies = sorted(r.latency_ms for r in successful)
    if latencies:
        lat_min = latencies[0]
        lat_median = statistics.median(latencies)           # mediana = środkowa wartość
        lat_max = latencies[-1]
    else:
        lat_min = lat_median = lat_max = 0.0
```

**Czemu min/median/max, nie p50/p95?** Bo dla N=11 fixtures `int(0.95 * 11) - 1 = 9`, czyli "p95" wskazuje na 10. wartość z 11 posortowanych — to faktycznie p91, nie p95. Percentyle przy małym N kłamią. `min/median/max` daje uczciwy spread bez przeszacowywania precyzji: median pokazuje typowy run, min/max pokazują rozrzut (np. cold-start outlier). Honest signal > fancy metric.

```python
    # Confidence calibration gap — kluczowa metryka senior-level
    correct_confs = [r.confidence for r in successful if r.correct and r.confidence is not None]
    wrong_confs = [r.confidence for r in successful if not r.correct and r.confidence is not None]
    mean_c = statistics.mean(correct_confs) if correct_confs else None
    mean_w = statistics.mean(wrong_confs) if wrong_confs else None
    gap = (mean_c - mean_w) if (mean_c is not None and mean_w is not None) else None
```

**Calibration gap** = średnia confidence dla poprawnych predykcji MINUS średnia dla błędnych. Jeśli gap > 0.10, możesz używać confidence jako progu "auto-approve vs human-review". Jeśli gap ≈ 0, confidence jest bezużyteczny.

```python
    # Per-category accuracy + confusion matrix (tylko mismatches dla terseness)
    per_cat: dict[str, dict[str, int]] = defaultdict(lambda: {"correct": 0, "total": 0})
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in successful:
        per_cat[r.expected]["total"] += 1
        if r.correct:
            per_cat[r.expected]["correct"] += 1
        else:
            confusion[r.expected][r.predicted or "<error>"] += 1
```

**Mismatch-only confusion** (vs pełna confusion matrix) — dla 6 kategorii pełna matrix to 36 cel, większość zer. Pokazuję tylko cele gdzie był mismatch — łatwiejszy read. Plus `r.predicted or "<error>"` to fallback gdy LLM zwrócił `None`.

```python
    return EvalReport(
        url=url,
        n_total=n_total,
        ... (wszystkie pola dataclass)
        per_category={k: dict(v) for k, v in per_cat.items()},          # defaultdict → zwykły dict (czystszy JSON)
        confusion_mismatches={k: dict(v) for k, v in confusion.items()},
        results=[asdict(r) for r in results],                            # FixtureResult → dict (dla JSON output)
    )
```

### Linie 252-280 — `print_report()` — terminal output

Stylowany formatted output. Najważniejsza linia:

```python
signal = "usable signal" if rep.confidence_gap >= 0.10 else "WEAK — confidence unreliable for routing"
```

To interpretacja w-line — recruiter czytający output rozumie co znaczy gap=0.28 (vs gap=0.02) bez znajomości statystyki.

### Linie 283-310 — `diff_runs()` — A/B mode (30 linii, per Twój wymóg #1)

```python
def diff_runs(baseline: EvalReport, candidate: EvalReport) -> None:
    print("=== A/B Diff (baseline vs candidate) ===")
    print(f"  Baseline:  {baseline.n_correct}/{baseline.n_total} ({baseline.accuracy:.1%})  @ {baseline.url}")
    print(f"  Candidate: {candidate.n_correct}/{candidate.n_total} ({candidate.accuracy:.1%})  @ {candidate.url}")

    # Index po fixture_id dla O(1) lookup
    baseline_by_id = {r["fixture_id"]: r for r in baseline.results}
    candidate_by_id = {r["fixture_id"]: r for r in candidate.results}

    fixed: list[str] = []
    regressed: list[str] = []
    for fixture_id, base_r in baseline_by_id.items():
        cand_r = candidate_by_id.get(fixture_id)
        if cand_r is None:
            continue                                      # Nowy fixture którego nie było w baseline — skip
        if not base_r["correct"] and cand_r["correct"]:
            fixed.append(f"  ✓ FIXED:     {fixture_id}  was: {base_r['predicted']}  now: {cand_r['predicted']}")
        elif base_r["correct"] and not cand_r["correct"]:
            regressed.append(f"  ✗ REGRESSED: {fixture_id}  was: {base_r['predicted']}  now: {cand_r['predicted']}")
```

**Kluczowy senior signal:** pokazuję `fixed` ORAZ `regressed` osobno, nie tylko delta accuracy. Bo "85% → 87%" może oznaczać:
- (a) 2 fixed, 0 regressed — pure win
- (b) 5 fixed, 3 regressed — net +2 ale 3 regresje ukryte

Lista flips zmusza Cię do zobaczenia (b). Senior interview signal.

### Linie 313-355 — `main()` — orchestrator

```python
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL, help=...)
    parser.add_argument("--baseline", help="Baseline API URL — enables A/B mode...")
    parser.add_argument("--out", help="Path to JSON output...")
    parser.add_argument("--min-accuracy", type=float, default=0.0, help="Exit 1 if accuracy below floor...")
    args = parser.parse_args()

    entries = load_manifest()                       # FAIL-EARLY: enum + plik existence

    print(f"Running eval against candidate: {args.url}")
    candidate_results = run_eval(args.url, entries)
    candidate_report = build_report(args.url, candidate_results)
    print_report(candidate_report)

    baseline_report: EvalReport | None = None
    if args.baseline:                               # A/B mode włączony przez --baseline
        print(f"\nRunning eval against baseline: {args.baseline}")
        baseline_results = run_eval(args.baseline, entries)
        baseline_report = build_report(args.baseline, baseline_results)
        print_report(baseline_report)
        diff_runs(baseline_report, candidate_report)

    if args.out:                                    # JSON output dla trendu w czasie
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)   # Tworzy docs/eval/ jeśli nie istnieje
        payload: dict = {"candidate": asdict(candidate_report)}
        if baseline_report is not None:
            payload["baseline"] = asdict(baseline_report)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        # ensure_ascii=False — żeby polskie znaki w category strings (Usługi, Konsulting) zostały jako UTF-8, nie \uXXXX

    if candidate_report.accuracy < args.min_accuracy:
        print(f"\nFAIL: accuracy {candidate_report.accuracy:.1%} below floor {args.min_accuracy:.1%}", file=sys.stderr)
        return 1                                    # CI gate — exit 1 dla GitHub Actions failure
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

---

## Jak to uruchomić — workflow krok-po-kroku

```bash
# 0. Najpierw zwaliduj manifest BEZ generowania XML (sanity check):
python scripts/generate_eval_fixtures.py --check
# Oczekiwany output: "OK: 11 entries valid (categories ∈ enum, ids unique, NIPs 10 digits)."
# Jeśli błąd → fix manifest, repeat.

# 1. Wygeneruj XMLe:
python scripts/generate_eval_fixtures.py
# Tworzy 11 plików w tests/fixtures/labeled/*.xml

# 2. Odpal lokalne API (potrzebne: Postgres, Redis, Qdrant + OPENAI_API_KEY w .env):
docker-compose up --build
# Czekaj aż FastAPI w logach pokaże "Application startup complete."

# 3. Pierwsze uruchomienie eval:
python scripts/eval_categorization.py
# Output do terminala: per-fixture progress + final report

# 4. Zapis baseline (commitable JSON):
python scripts/eval_categorization.py --out docs/eval/baseline_2026-05-11.json

# 5. Po zmianie prompta — porównanie A/B:
#    (zakładając że baseline jest na innym porcie/URLu — np. innego brancha deployed na osobnym Cloud Run rev)
python scripts/eval_categorization.py \
  --url http://localhost:8000 \
  --baseline https://invoice-processor-510066601703.europe-central2.run.app

# 6. CI gate (zwraca exit 1 jeśli accuracy spadnie poniżej 80%):
python scripts/eval_categorization.py --min-accuracy 0.80
```

---

## Jargon kit — pojęcia użyte powyżej

| Pojęcie | Co to | Gdzie w kodzie |
|---|---|---|
| **`Decimal`** | Dokładna arytmetyka pieniężna. Float ma błędy zaokrąglenia (0.1+0.2 ≠ 0.3) → księgowość wymaga Decimal | Generator linia 28, 92 |
| **`@dataclass`** | Auto-generowany `__init__`/`__repr__`/`__eq__` dla klasy bez boilerplate | Eval linie 52, 67 |
| **`defaultdict`** | Dict który tworzy domyślną wartość gdy klucz nieobecny — zero `if key not in d` boilerplate | Eval linie 228, 229 |
| **`statistics.median`** | Mediana — środkowa wartość po sortowaniu | Eval linia 216 |
| **`httpx.Client`** | Synchroniczny HTTP client (sibling do `requests`, async-ready) | Eval linia 181 |
| **`time.perf_counter()`** | Wysokorozdzielczy timer (>1ms precision) — lepszy do mierzenia latencji niż `time.time()` | Eval linia 137 |
| **`raise_for_status()`** | Podnosi wyjątek dla 4xx/5xx, NIC dla 2xx — wbudowana łapaczka błędów httpx | Eval linia 127, 143 |
| **`f-string z `{:.1%}`** | Format float jako procent (`0.85` → `85.0%`) | Eval linie 257, 348 |
| **`field(default_factory=list)`** | W `@dataclass` mutable defaults wymagają factory function (immutable shared state bug) | Eval linia 85 |
| **`asdict(dataclass_instance)`** | Konwertuje dataclass na dict (do JSON serializacji) | Eval linia 246, 343 |
| **`json.dumps(..., ensure_ascii=False)`** | Polskie znaki jako UTF-8 (`Usługi`), nie escape sequence (`łusługi`) | Eval linia 346 |
| **`chr(10)`** | Newline character (ASCII 10) — workaround bo w f-stringach pre-3.12 wyrażenie `{...}` nie może zawierać backslasha `\`, więc `"\n".join(...)` rzuca SyntaxError | Generator linia 121 |
| **`sys.path.insert(0, str(ROOT))`** | Hack żeby skrypt w `scripts/` mógł importować z `app/` bez `PYTHONPATH=.` | Generator linia 33, Eval linia 44 |
| **`# noqa: E402`** | Wycisza ruff/flake8 że import nie jest na górze pliku — uzasadnione bo sys.path manipulation musi być przed importem | Generator linia 34, Eval linia 45 |

---

## Co byś zrobiła inaczej / co rozszerzyć

Świadomie pominięte (Phase 2 jeśli interview tego potrzebuje):

1. **Per-fixture confidence threshold sweep** — pokazałoby "co by się stało gdybyś auto-approve tylko gdy confidence > 0.75?". Useful dla zdefiniowania human-review threshold.
2. **Bootstrap confidence interval na accuracy** — dla N=11 wystarczy "85%, ±15% przy 95% CI". Pokazuje że rozumiesz że 11 fixtures to mały sample.
3. **Cost-per-correct-prediction** — `cost / correct_predictions` jako business metric ("ile płacisz za jedną poprawną kategoryzację").
4. **Per-line-item ablation** — przy adversarial fixture (ERP 80/20), spróbuj eval z TYLKO software line item, potem z TYLKO consulting line item — pokaże w której konfiguracji LLM zmienia verdict.

Każde to ~15-30 min dodatkowo. Pierwsze 3 są easy wins jeśli się okaże że interview wymaga "more eval rigor".

---

## Najczęstsze pułapki przy pierwszym uruchomieniu

1. **`OPENAI_API_KEY` nie ustawiony** — eval umiera na pierwszym categorize z 503/500. Sprawdź `.env`.
2. **Qdrant pusty** — bez pre-indexed invoices RAG few-shot będzie zero-shot. Eval nadal działa, ale accuracy niska. Workaround: uruchom `docker-compose up`, dodaj parę KSeF invoices przez `/invoices/ksef` jako "training set" przed eval.
3. **Cold-start 503 z Cloud Run** — pierwszy fixture umiera, reszta OK. Worth: rerun lub `--baseline` only po warm-up ping.
4. **Manifest z literałem `"Konsulting"` zamiast `"Konsulting i doradztwo"`** — exit 1 na `load_manifest()`. Brak silent fail = funkcja działa zgodnie z założeniem.
5. **`tests/fixtures/labeled/{id}.xml` nie istnieje** — `load_manifest()` to wykrywa i mówi "Run: python scripts/generate_eval_fixtures.py". Zero domysłów.
