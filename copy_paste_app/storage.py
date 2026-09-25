"""S3-compatible object storage (Cloudflare R2 / AWS S3) holding blob file contents."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import boto3
from botocore.config import Config

from .config import get_settings

UPLOAD_WORKERS = 8


class StorageNotConfigured(RuntimeError):
    pass


@lru_cache
def get_client():
    settings = get_settings()
    if not (settings.object_storage_bucket and settings.object_storage_access_key and settings.object_storage_secret_key):
        raise StorageNotConfigured("Object storage is not configured (OBJECT_STORAGE_* variables)")
    return boto3.client(
        "s3",
        endpoint_url=settings.object_storage_endpoint or None,
        aws_access_key_id=settings.object_storage_access_key,
        aws_secret_access_key=settings.object_storage_secret_key,
        region_name=settings.object_storage_region or "auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def bucket() -> str:
    return get_settings().object_storage_bucket


def blob_prefix(blob_id: str) -> str:
    return f"blobs/{blob_id}/"


def put_files(blob_id: str, files: list[tuple[str, bytes, str]]) -> None:
    """Upload (path, data, content_type) tuples under the blob's prefix, in parallel."""
    client = get_client()
    prefix = blob_prefix(blob_id)

    def put(item: tuple[str, bytes, str]) -> None:
        path, data, content_type = item
        client.put_object(Bucket=bucket(), Key=prefix + path, Body=data, ContentType=content_type)

    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        # list() re-raises the first upload error
        list(pool.map(put, files))


def read_file(blob_id: str, path: str) -> bytes:
    response = get_client().get_object(Bucket=bucket(), Key=blob_prefix(blob_id) + path)
    return response["Body"].read()


def read_files(blob_id: str, paths: list[str]):
    """Yield (path, data) in the given order, fetching several objects in parallel."""
    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        yield from zip(paths, pool.map(lambda path: read_file(blob_id, path), paths))


def download_url(blob_id: str, path: str, filename: str, expires: int = 300) -> str:
    """Short-lived signed URL that downloads the file as an attachment."""
    return get_client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket(),
            "Key": blob_prefix(blob_id) + path,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=expires,
    )


def delete_blob(blob_id: str) -> None:
    client = get_client()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket(), Prefix=blob_prefix(blob_id)):
        keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if keys:
            client.delete_objects(Bucket=bucket(), Delete={"Objects": keys, "Quiet": True})
