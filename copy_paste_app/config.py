from functools import lru_cache
from os import getenv


class Settings:
    app_name: str = getenv("APP_NAME", "Copy Paste")
    debug: bool = getenv("DEBUG", "false").lower() == "true"
    mongo_uri: str = getenv("MONGO_URI", "mongodb://localhost:27017")
    mongo_database: str = getenv("MONGO_DATABASE", "copy_paste")
    secret_key: str = getenv("SECRET_KEY", "copy-paste-local-secret-change-me")
    # S3-compatible object storage (Cloudflare R2 / AWS S3) for blob files
    object_storage_bucket: str = getenv("OBJECT_STORAGE_BUCKET", "")
    object_storage_endpoint: str = getenv("OBJECT_STORAGE_ENDPOINT", "")
    object_storage_access_key: str = getenv("OBJECT_STORAGE_ACCESS_KEY", "")
    object_storage_secret_key: str = getenv("OBJECT_STORAGE_SECRET_KEY", "")
    object_storage_region: str = getenv("OBJECT_STORAGE_REGION", "auto")


@lru_cache
def get_settings() -> Settings:
    return Settings()
