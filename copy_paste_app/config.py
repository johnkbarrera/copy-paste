from functools import lru_cache
from os import getenv


class Settings:
    app_name: str = getenv("APP_NAME", "Copy Paste")
    debug: bool = getenv("DEBUG", "false").lower() == "true"
    mongo_uri: str = getenv("MONGO_URI", "mongodb://localhost:27017")
    mongo_database: str = getenv("MONGO_DATABASE", "copy_paste")
    secret_key: str = getenv("SECRET_KEY", "copy-paste-local-secret-change-me")


@lru_cache
def get_settings() -> Settings:
    return Settings()
