"""Application configuration via environment variables."""

import os
import ssl

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """StatForge API settings loaded from environment."""

    # Application
    app_name: str = "StatForge API"
    app_version: str = "0.1.0"
    debug: bool = True
    environment: str = "development"
    desktop_mode: bool = False
    app_data_path: str | None = None
    log_path: str | None = None

    # Database — set db_backend to "sqlite" for desktop/offline mode (no Docker needed)
    db_backend: str = "postgresql"  # "sqlite" | "postgresql"
    sqlite_path: str = "data/statforge.db"

    # PostgreSQL (used when db_backend = "postgresql")
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "statforge"
    postgres_password: str = "statforge_dev_password"
    postgres_db: str = "statforge"
    # Trusted boot-only inputs, never selected by model/tool arguments or PATH.
    postgres_migration_runtime: str | None = None
    postgres_migration_evidence: str | None = None
    postgres_restore_db: str | None = None
    postgres_sslmode: str | None = None
    postgres_sslrootcert: str | None = None

    def postgres_url(self, driver: str = "postgresql+psycopg", *, restore: bool = False) -> URL:
        if self.postgres_sslmode and (
            self.postgres_sslmode != "verify-full" or not self.postgres_sslrootcert
        ):
            raise ValueError("PostgreSQL TLS requires verify-full and an explicit CA file.")
        database = self.postgres_restore_db if restore else self.postgres_db
        if not database:
            raise ValueError("An explicit independent PostgreSQL restore database is required.")
        query = {}
        if self.postgres_sslmode:
            query["sslmode"] = self.postgres_sslmode
        if self.postgres_sslrootcert:
            query["sslrootcert"] = self.postgres_sslrootcert
        return URL.create(
            driver,
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=database,
            query=query,
        )

    def postgres_async_connect_args(self) -> dict:
        if not self.postgres_sslmode and not self.postgres_sslrootcert:
            return {}
        if self.postgres_sslmode != "verify-full" or not self.postgres_sslrootcert:
            raise ValueError("PostgreSQL TLS requires verify-full and an explicit CA file.")
        context = ssl.create_default_context(cafile=self.postgres_sslrootcert)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return {"ssl": context}

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # Storage — set storage_backend to "local" for desktop/offline mode
    storage_backend: str = "minio"  # "minio" | "local"
    local_storage_path: str = "data/storage"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "statforge_minio"
    minio_secret_key: str = "statforge_minio_secret"
    minio_bucket: str = "statforge"
    minio_secure: bool = False
    # ML uses cancellable HTTP I/O; an explicit region avoids synchronous
    # network discovery while signing the request in the API process.
    minio_region: str = "us-east-1"

    # AI provider
    ai_enabled: bool = False
    ai_provider: str = "openai_compatible"
    ai_model: str | None = None
    ai_base_url: str | None = None
    ai_api_key: SecretStr | None = None

    # PG checkpoints use a separate, stable server secret, never a session JWT.
    h02_checkpoint_integrity_key: SecretStr | None = None

    ai_timeout_seconds: int = Field(default=60, ge=1, le=300)
    ai_max_retries: int = Field(default=2, ge=0, le=10)
    ai_max_output_tokens: int | None = Field(default=None, ge=1)

    # CORS
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ]

    # --- Authentication ---
    jwt_secret_key: str = "statforge-dev-jwt-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # Cookie settings — Secure/HttpOnly required in production
    access_cookie_name: str = "sf_access"
    refresh_cookie_name: str = "sf_refresh"
    cookie_domain: str = ""  # empty = current domain
    cookie_secure: bool = False  # True in production with HTTPS
    cookie_samesite: str = "lax"
    cookie_path: str = "/"

    # --- File Upload ---
    max_upload_size_mb: int = 0  # 0 disables the optional upload byte ceiling.
    archive_max_expanded_size_mb: int = Field(default=4096, gt=0)
    allowed_extensions: list[str] = ["csv", "xlsx", "parquet"]
    preview_max_rows: int = 100
    preview_default_rows: int = 20
    max_columns: int = 500
    max_column_name_length: int = 255
    max_rows: int = 1_000_000
    upload_temp_dir: str = ".tmp_uploads"

    # --- Task execution limits ---
    task_timeout_seconds: int = 600
    task_max_memory_mb: int = 1024
    max_cv_folds: int = 10
    max_stat_variables: int = 50

    @property
    def max_upload_size_bytes(self) -> int | None:
        return self.max_upload_size_mb * 1024 * 1024 if self.max_upload_size_mb > 0 else None

    @property
    def database_url(self) -> str:
        if self.db_backend == "sqlite":
            return f"sqlite+aiosqlite:///{self.sqlite_path}"
        # asyncpg spells the SSL option differently; sync backup keeps libpq's
        # verify-full semantics. SQLAlchemy performs password escaping.
        # SQLAlchemy's asyncpg dialect passes URL query as connection kwargs;
        # libpq TLS names aren't asyncpg kwargs. The engine receives the explicit
        # verified SSLContext above, never ambient/home PG certificate settings.
        url = self.postgres_url("postgresql+asyncpg").difference_update_query(
            ["sslmode", "sslrootcert"]
        )
        return url.render_as_string(hide_password=False)

    @property
    def database_url_sync(self) -> str:
        """Sync URL for Alembic migrations."""
        if self.db_backend == "sqlite":
            return f"sqlite:///{self.sqlite_path}"
        return self.postgres_url("postgresql+psycopg").render_as_string(hide_password=False)

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # The desktop executable can be launched from an arbitrary working
    # directory. Ignore unrelated variables from a nearby legacy .env file
    # instead of preventing the local sidecar from starting.
    model_config = {
        "env_file": None if os.environ.get("STATFORGE_SOURCE_PREVIEW") == "1" else ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
