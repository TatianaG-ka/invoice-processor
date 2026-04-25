"""Application configuration loaded from .env via pydantic-settings.

Usage:
    from app.config import settings
    print(settings.OPENAI_API_KEY)
    print(settings.DATABASE_URL)
"""

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Propagate ``.env`` into ``os.environ`` so third-party SDKs that read
# directly from the process environment (Langfuse, OpenAI official
# client) pick up the same values that pydantic-settings loads into
# ``settings`` below. pydantic-settings reads the file itself, but
# into its own model — it does not export into ``os.environ``.
load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from .env."""

    # --- AI / LLM ---
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # --- Database ---
    # Default points at an async Postgres (asyncpg driver). Tests swap
    # this for ``sqlite+aiosqlite:///:memory:`` via conftest.
    DATABASE_URL: str = "postgresql+asyncpg://invoice_user:invoice_pass@localhost:5432/invoices"

    # --- Redis (queue backend) ---
    REDIS_URL: str = "redis://localhost:6379"

    # --- Qdrant (vector store) ---
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "invoices"

    # --- Application ---
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    MAX_FILE_SIZE_MB: int = 10

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


# Singleton — import this, do not instantiate Settings yourself.
settings = Settings()
