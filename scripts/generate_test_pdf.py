"""
Skrypt do wygenerowania testowej faktury PDF.

Użycie:
    pip install reportlab
    python scripts/generate_test_pdf.py

Wygeneruje: tests/fixtures/sample_invoice.pdf

Dlaczego to istnieje?
Nie chciałam commitować prawdziwych faktur do publicznego repo (RODO + NDA).
Zamiast tego - generujesz fikcyjną fakturę z realistycznymi danymi,
które wystarczą do testów OCR + ekstrakcji przez LLM.

Możesz też użyć prawdziwej faktury - po prostu zanonimizuj dane i wrzuć
jako tests/fixtures/sample_invoice.pdf.
"""

from pathlib import Path


def generate_sample_invoice():
    """Generuje testową fakturę PDF z fikcyjnymi danymi."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        )
        from reportlab.lib import colors
    except ImportError:
        print("❌ Brak reportlab. Zainstaluj: pip install reportlab")
        return

    # Ścieżka docelowa
    output_path = Path(__file__).parent.parent / "tests" / "fixtures" / "sample_invoice.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    story = []

    # Tytuł
    story.append(Paragraph("<b>FAKTURA VAT</b>", styles["Title"]))
    story.append(Paragraph("Nr: FV/2026/04/0123", styles["Heading2"]))
    story.append(Spacer(1, 0.5 * cm))

    # Daty
    story.append(Paragraph("Data wystawienia: 2026-04-15", styles["Normal"]))
    story.append(Paragraph("Data sprzedaży: 2026-04-15", styles["Normal"]))
    story.append(Paragraph("Termin płatności: 2026-04-29", styles["Normal"]))
    story.append(Spacer(1, 0.5 * cm))

    # Sprzedawca i nabywca
    seller_buyer = [
        ["SPRZEDAWCA", "NABYWCA"],
        [
            "ACME Technologies Sp. z o.o.\nul. Testowa 42/10\n00-001 Warszawa\nNIP: 5252344078",
            "Testowa Firma S.A.\nul. Przykładowa 1\n00-002 Warszawa\nNIP: 1234567890",
        ],
    ]
    table1 = Table(seller_buyer, colWidths=[8 * cm, 8 * cm])
    table1.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(table1)
    story.append(Spacer(1, 1 * cm))

    # Pozycje faktury
    items = [
        ["Lp.", "Nazwa usługi", "Ilość", "Cena netto", "VAT", "Wartość brutto"],
        ["1.", "Usługi konsultingowe IT", "10 godz.", "200,00 zł", "23%", "2 460,00 zł"],
        ["2.", "Wdrożenie systemu CRM", "1 szt.", "5 000,00 zł", "23%", "6 150,00 zł"],
        ["3.", "Szkolenie zespołu", "2 dni", "1 500,00 zł", "23%", "3 690,00 zł"],
    ]
    table2 = Table(items, colWidths=[1.5 * cm, 6 * cm, 2 * cm, 2.5 * cm, 1.5 * cm, 3 * cm])
    table2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(table2)
    story.append(Spacer(1, 0.5 * cm))

    # Podsumowanie
    story.append(Paragraph("<b>Razem netto: 10 000,00 zł</b>", styles["Normal"]))
    story.append(Paragraph("<b>VAT (23%): 2 300,00 zł</b>", styles["Normal"]))
    story.append(Paragraph("<b>Razem brutto: 12 300,00 zł</b>", styles["Heading2"]))
    story.append(Paragraph("Słownie: dwanaście tysięcy trzysta złotych 00/100", styles["Italic"]))
    story.append(Spacer(1, 1 * cm))

    # Sposób płatności
    story.append(Paragraph("<b>Sposób płatności:</b> przelew", styles["Normal"]))
    story.append(Paragraph("<b>Numer konta:</b> 12 3456 7890 1234 5678 9012 3456", styles["Normal"]))

    # Generuj
    doc.build(story)
    print(f"✅ Wygenerowano: {output_path}")
    print(f"   Rozmiar: {output_path.stat().st_size / 1024:.1f} KB")
    print("\nTeraz możesz uruchomić testy: pytest tests/")


if __name__ == "__main__":
    generate_sample_invoice()
