from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "development"
    database_url: str = (
        "postgresql+psycopg://ingestion_user:ingestion_dev_password@127.0.0.1:5432/ingestion_db"
    )
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_xlsx_uncompressed_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    max_rows: int = Field(default=100000, gt=0)
    max_columns: int = Field(default=256, ge=2)
    max_header_rows: int = Field(default=20, ge=1, le=100)
    max_record_bytes: int = Field(default=1024 * 1024, ge=1024)
    max_errors: int = Field(default=50, ge=1, le=1000)
    max_active_uploads: int = Field(default=2, ge=1, le=32)
    import_timeout_seconds: float = Field(default=900, gt=0)
    database_lock_timeout_seconds: float = Field(default=5, gt=0)

    @model_validator(mode="after")
    def check_database(self):
        url = make_url(self.database_url)
        if url.drivername != "postgresql+psycopg":
            raise ValueError("PostgreSQL with psycopg is required.")
        if self.environment == "production" and (
            not url.password or url.password == "ingestion_dev_password"
        ):
            raise ValueError("Production requires an external database password.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
