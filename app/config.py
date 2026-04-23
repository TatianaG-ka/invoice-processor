"""
Konfiguracja aplikacji - ładowanie ustawień z .env.

UŻYWASZ OD DNIA 2 (kiedy dodajesz OPENAI_API_KEY).

Użycie:
    from app.config import settings
    print(settings.OPENAI_API_KEY)
    print(settings.DATABASE_URL)

Dzięki pydantic-settings otrzymujesz:
- Automatyczne ładowanie z .env
- Walidację typów
- Error messages jeśli brakuje zmiennej
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Ustawienia aplikacji ładowane z .env."""

    # --- AI / LLM ---
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # --- Database ---
    # Default points at an async Postgres (asyncpg driver). Tests swap
    # this for ``sqlite+aiosqlite:///:memory:`` via conftest.
    DATABASE_URL: str = "postgresql+asyncpg://invoice_user:invoice_pass@localhost:5432/invoices"

    # --- Redis (od tygodnia 2) ---
    REDIS_URL: str = "redis://localhost:6379"

    # --- Qdrant (od tygodnia 2) ---
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "invoices"

    # --- Aplikacja ---
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    MAX_FILE_SIZE_MB: int = 10

    # --- Strategia ekstrakcji ---
    EXTRACTOR_STRATEGY: str = "openai"  # 'openai' lub 'local'

    # --- OCR ---
    OCR_LANGUAGES: str = "pol+eng"

    # --- Observability (Langfuse) ---
    # All three blank by default: the Langfuse SDK reads them straight
    # from env on first use and degrades to a no-op when unset (CI,
    # local dev without an account). Production sets all three via
    # Cloud Run secret bindings.
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        # Dev machines often share a single .env across many projects
        # (e.g. HUGGINGFACE_TOKEN, LANGCHAIN_API_KEY). Ignore anything
        # the invoice-processor doesn't declare rather than crash.
        extra="ignore",
    )


# Singleton - importuj to, nie twórz nowej instancji
settings = Settings()
