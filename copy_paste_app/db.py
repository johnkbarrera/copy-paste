from __future__ import annotations

from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient
from pymongo.database import Database

from .config import Settings
from .security import hash_password


PROJECTS_COLLECTION = "copy_paste_projects"
TOPICS_COLLECTION = "copy_paste_topics"
USERS_COLLECTION = "copy_paste_users"
BLOBS_COLLECTION = "copy_paste_blobs"
BLOB_FILES_COLLECTION = "copy_paste_blob_files"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_database(settings: Settings) -> Database:
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    return client[settings.mongo_database]


def ensure_database(db: Database) -> None:
    db[USERS_COLLECTION].create_index([("username", ASCENDING)], unique=True)
    db[PROJECTS_COLLECTION].create_index([("slug", ASCENDING)], unique=True)
    db[TOPICS_COLLECTION].create_index([("project_slug", ASCENDING), ("topic_slug", ASCENDING)], unique=True)
    db[BLOBS_COLLECTION].create_index([("project_slug", ASCENDING), ("blob_slug", ASCENDING)], unique=True)
    db[BLOB_FILES_COLLECTION].create_index(
        [("project_slug", ASCENDING), ("blob_slug", ASCENDING), ("path", ASCENDING)], unique=True
    )

    now = utc_now()
    db[USERS_COLLECTION].update_one(
        {"username": "admin"},
        {
            "$setOnInsert": {
                "username": "admin",
                "password_hash": hash_password("admin"),
                "role": "admin",
                "created_at": now,
            },
            "$set": {"updated_at": now},
        },
        upsert=True,
    )
