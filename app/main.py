from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile

app = FastAPI(
    title="Invoice Processor API",
    description=(
        "Automatic invoice processing: PDF → OCR → AI extraction → "
        "database entry + semantic search."
    ),
    version="0.1.0",
)

ALLOWED_CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png"}
MAX_UPLOAD_SIZE_MB = 10
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024


@app.get("/", tags=["Health"])
def read_root():
    """Basic endpoint checking that the service is working."""
    return {"status": "ok", "service": "invoice-processor"}


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "healthy"}


@app.post("/invoices", status_code=201)
async def upload_invoice(file: Annotated[UploadFile, File()]):
    # Przyjmuje plik faktury (PDF / JPG / PNG).
    # Walidacja — akceptujemy tylko PDF-y i obrazy
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {file.content_type}. "
            f"Accepted: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}",
        )

    # Odczytaj zawartość
    contents = await file.read()
    # Sanity check — za duże pliki odrzucamy
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(contents) / 1024:.1f}MB > {MAX_UPLOAD_SIZE_MB}MB",
        )
    size_kb = round(len(contents) / 1024, 2)
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "size_kb": size_kb,
        "status": "received",
        "message": "File received.",
    }


if __name__ == "__main__":
    # Można uruchomić przez: python -m app.main
    # Ale prościej: uvicorn app.main:app --reload
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
