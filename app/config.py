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
    DATABASE_URL: str = "postgresql://invoice_user:invoice_pass@localhost:5432/invoices"

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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


# Singleton - importuj to, nie twórz nowej instancji
settings = Settings()
