from __future__ import annotations

import re
import logging
import mimetypes
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from bson import ObjectId
from botocore.exceptions import BotoCoreError, ClientError
from pymongo.errors import PyMongoError
from starlette.concurrency import run_in_threadpool
from pymongo.database import Database

from .config import Settings, get_settings
from . import storage
from .blobs import (
    BLOB_MAX_FILE_BYTES,
    BLOB_MAX_FILES,
    BLOB_MAX_TOTAL_BYTES,
    build_tree,
    decode_text,
    default_file,
    human_size,
    normalize_blob_path,
)
from .db import (
    BLOB_FILES_COLLECTION,
    BLOBS_COLLECTION,
    PROJECTS_COLLECTION,
    TOPICS_COLLECTION,
    USERS_COLLECTION,
    ensure_database,
    get_database,
    utc_now,
)
from .security import read_signed_payload, sign_payload, verify_password

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["human_size"] = human_size
logger = logging.getLogger("copy_paste")

app = FastAPI(
    title="Copy Paste",
    description="Modern web application for projects, topics, and live code notes",
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

RESERVED_SLUGS = {"api", "login", "logout", "static", "favicon.ico"}
INVALID_NAME_MESSAGE = "Please use only letters, numbers, spaces, hyphens, or underscores."
FLASH_COOKIE = "copy_paste_flash"
FLASH_MESSAGES = {
    "project_created": ("success", "Project created"),
    "topic_created": ("success", "Topic created"),
    "topic_deleted": ("success", "Topic deleted"),
    "project_disabled": ("success", "Project disabled"),
    "project_enabled": ("success", "Project enabled"),
    "project_is_disabled": ("error", "This project is disabled, enable it to make changes"),
    "blob_created": ("success", "Blob uploaded"),
    "blob_deleted": ("success", "Blob deleted"),
    "blob_delete_failed": ("error", "Could not delete the blob from object storage"),
}
STORAGE_ERRORS = (storage.StorageNotConfigured, BotoCoreError, ClientError)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value.strip().lower()).strip("-")
    return slug[:80]


def clean_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def has_valid_name_chars(value: str) -> bool:
    return all(char.isalnum() or char in {" ", "-", "_"} for char in value)


def is_valid_name(value: str) -> bool:
    clean_value = clean_name(value)
    return 1 <= len(clean_value) <= 80 and has_valid_name_chars(clean_value)


def next_available_slug(db: Database, collection_name: str, field_name: str, base_slug: str, extra_query: dict | None = None) -> str:
    query_base = extra_query.copy() if extra_query else {}
    candidate = base_slug
    counter = 2
    while True:
        query = {**query_base, field_name: candidate}
        if not db[collection_name].find_one(query, {"_id": 1}):
            return candidate
        candidate = f"{base_slug}-{counter}"
        counter += 1


def render(request: Request, template: str, context: dict, status_code: int = 200) -> HTMLResponse:
    context.setdefault("user", current_user(request))
    flash_key = request.cookies.get(FLASH_COOKIE)
    if flash_key in FLASH_MESSAGES:
        kind, message = FLASH_MESSAGES[flash_key]
        context.setdefault("flash", {"type": kind, "message": message})
    response = templates.TemplateResponse(request, template, context, status_code=status_code)
    if flash_key:
        response.delete_cookie(FLASH_COOKIE)
    return response


def redirect_with_flash(url: str, flash_key: str) -> RedirectResponse:
    """Redirect and show a toast on the next rendered page."""
    response = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(FLASH_COOKIE, flash_key, max_age=60, httponly=True, samesite="lax")
    return response


def error_flash(message: str) -> dict:
    return {"type": "error", "message": message}


def get_project_or_404(db: Database, project_slug: str) -> dict:
    project = db[PROJECTS_COLLECTION].find_one({"slug": project_slug})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


ACTIVE_PROJECTS = {"disabled": {"$ne": True}}


def current_user(request: Request) -> dict | None:
    settings = get_settings()
    return read_signed_payload(request.cookies.get("copy_paste_session"), settings.secret_key)


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def database(settings: Annotated[Settings, Depends(get_settings)]) -> Database:
    return get_database(settings)


@app.on_event("startup")
def startup() -> None:
    try:
        ensure_database(get_database(get_settings()))
    except PyMongoError as exc:
        logger.warning("MongoDB is not available during startup: %s", exc)


@app.get("/", response_class=HTMLResponse)
def landing(request: Request, db: Annotated[Database, Depends(database)]) -> HTMLResponse:
    try:
        projects = list(db[PROJECTS_COLLECTION].find(ACTIVE_PROJECTS).sort("updated_at", -1).limit(12))
        disabled_projects = list(db[PROJECTS_COLLECTION].find({"disabled": True}).sort("updated_at", -1))
        db_error = None
    except PyMongoError as exc:
        projects = []
        disabled_projects = []
        db_error = f"Could not connect to MongoDB: {exc}"
    return render(
        request,
        "landing.html",
        {"projects": projects, "disabled_projects": disabled_projects, "db_error": db_error},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(PROJECT_DIR / "utilities" / "icon.jpeg", media_type="image/jpeg")


@app.get("/app-icon.jpeg", include_in_schema=False)
def app_icon() -> FileResponse:
    return FileResponse(PROJECT_DIR / "utilities" / "icon.jpeg", media_type="image/jpeg")


@app.get("/login", response_class=HTMLResponse)
def login_view(request: Request) -> HTMLResponse:
    if current_user(request):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "login.html", {"error": None})


@app.post("/login")
def login(
    request: Request,
    response: Response,
    db: Annotated[Database, Depends(database)],
    settings: Annotated[Settings, Depends(get_settings)],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    user = db[USERS_COLLECTION].find_one({"username": username.strip()})
    if not user or not verify_password(password, user.get("password_hash", "")):
        return render(request, "login.html", {"error": "Invalid username or password."}, status_code=401)

    redirect = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    token = sign_payload({"username": user["username"], "role": user.get("role", "admin")}, settings.secret_key)
    redirect.set_cookie("copy_paste_session", token, httponly=True, samesite="lax")
    return redirect


@app.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("copy_paste_session")
    return response


@app.post("/projects")
def create_project(
    request: Request,
    db: Annotated[Database, Depends(database)],
    _: Annotated[dict, Depends(require_user)],
    name: Annotated[str, Form()],
) -> Response:
    clean_project_name = clean_name(name)
    base_slug = slugify(clean_project_name)
    if not is_valid_name(clean_project_name) or not base_slug or base_slug in RESERVED_SLUGS:
        projects = list(db[PROJECTS_COLLECTION].find(ACTIVE_PROJECTS).sort("updated_at", -1).limit(12))
        return render(
            request,
            "landing.html",
            {"projects": projects, "error": INVALID_NAME_MESSAGE, "flash": error_flash("Project not created")},
            400,
        )

    now = utc_now()
    try:
        clean_slug = next_available_slug(db, PROJECTS_COLLECTION, "slug", base_slug)
        db[PROJECTS_COLLECTION].update_one(
        {"slug": clean_slug},
            {
                "$setOnInsert": {
                    "name": clean_project_name,
                    "slug": clean_slug,
                    "topics": [],
                    "created_at": now,
                },
                "$set": {"updated_at": now},
            },
            upsert=True,
        )
    except PyMongoError as exc:
        logger.warning("Could not create project: %s", exc)
        return render(
            request,
            "landing.html",
            {"projects": [], "db_error": f"Could not connect to MongoDB: {exc}", "flash": error_flash("Project not created: database error")},
            503,
        )
    return redirect_with_flash(f"/{clean_slug}", "project_created")


@app.get("/{project_slug}", response_class=HTMLResponse)
def project_view(
    request: Request,
    project_slug: str,
    db: Annotated[Database, Depends(database)],
    _: Annotated[dict, Depends(require_user)],
) -> HTMLResponse:
    project = get_project_or_404(db, project_slug)
    topics = list(db[TOPICS_COLLECTION].find({"project_slug": project_slug}).sort("updated_at", -1))
    blobs = list(db[BLOBS_COLLECTION].find({"project_slug": project_slug}).sort("created_at", -1))
    return render(request, "project.html", {"project": project, "topics": topics, "blobs": blobs})


def set_project_disabled(db: Database, project_slug: str, disabled: bool) -> RedirectResponse:
    get_project_or_404(db, project_slug)
    db[PROJECTS_COLLECTION].update_one(
        {"slug": project_slug},
        {"$set": {"disabled": disabled, "updated_at": utc_now()}},
    )
    if disabled:
        return redirect_with_flash("/", "project_disabled")
    return redirect_with_flash(f"/{project_slug}", "project_enabled")


@app.post("/{project_slug}/disable")
def disable_project(
    project_slug: str,
    db: Annotated[Database, Depends(database)],
    _: Annotated[dict, Depends(require_user)],
) -> RedirectResponse:
    return set_project_disabled(db, project_slug, True)


@app.post("/{project_slug}/enable")
def enable_project(
    project_slug: str,
    db: Annotated[Database, Depends(database)],
    _: Annotated[dict, Depends(require_user)],
) -> RedirectResponse:
    return set_project_disabled(db, project_slug, False)


@app.post("/{project_slug}/topics")
def create_topic(
    request: Request,
    project_slug: str,
    db: Annotated[Database, Depends(database)],
    _: Annotated[dict, Depends(require_user)],
    title: Annotated[str, Form()],
) -> Response:
    project = get_project_or_404(db, project_slug)
    if project.get("disabled"):
        return redirect_with_flash(f"/{project_slug}", "project_is_disabled")

    clean_title = clean_name(title)
    base_slug = slugify(clean_title)
    if not is_valid_name(clean_title) or not base_slug:
        topics = list(db[TOPICS_COLLECTION].find({"project_slug": project_slug}).sort("updated_at", -1))
        return render(
            request,
            "project.html",
            {"project": project, "topics": topics, "error": INVALID_NAME_MESSAGE, "flash": error_flash("Topic not created")},
            400,
        )

    now = utc_now()
    try:
        clean_slug = next_available_slug(
            db,
            TOPICS_COLLECTION,
            "topic_slug",
            base_slug,
            {"project_slug": project_slug},
        )
        db[TOPICS_COLLECTION].update_one(
            {"project_slug": project_slug, "topic_slug": clean_slug},
            {
                "$setOnInsert": {
                    "project_slug": project_slug,
                    "topic_slug": clean_slug,
                    "title": clean_title,
                    "content": "",
                    "created_at": now,
                },
                "$set": {"updated_at": now},
            },
            upsert=True,
        )

        db[PROJECTS_COLLECTION].update_one(
            {"slug": project_slug},
            {
                "$push": {"topics": {"slug": clean_slug, "title": clean_title, "created_at": now, "updated_at": now}},
                "$set": {"updated_at": now},
            },
        )
    except PyMongoError as exc:
        logger.warning("Could not create topic: %s", exc)
        return render(
            request,
            "project.html",
            {"project": project, "topics": [], "error": "Database error, please try again.", "flash": error_flash("Topic not created: database error")},
            503,
        )
    return redirect_with_flash(f"/{project_slug}/{clean_slug}", "topic_created")


@app.get("/{project_slug}/{topic_slug}", response_class=HTMLResponse)
def topic_view(
    request: Request,
    project_slug: str,
    topic_slug: str,
    db: Annotated[Database, Depends(database)],
    _: Annotated[dict, Depends(require_user)],
) -> HTMLResponse:
    project = db[PROJECTS_COLLECTION].find_one({"slug": project_slug})
    topic = db[TOPICS_COLLECTION].find_one({"project_slug": project_slug, "topic_slug": topic_slug})
    if not project or not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    return render(request, "topic.html", {"project": project, "topic": topic})


@app.post("/api/{project_slug}/{topic_slug}")
def save_topic(
    project_slug: str,
    topic_slug: str,
    db: Annotated[Database, Depends(database)],
    _: Annotated[dict, Depends(require_user)],
    content: Annotated[str, Form()],
) -> JSONResponse:
    now = datetime.now(timezone.utc)
    try:
        project = db[PROJECTS_COLLECTION].find_one({"slug": project_slug}, {"disabled": 1})
        if project and project.get("disabled"):
            return JSONResponse({"ok": False, "detail": "Project is disabled"}, status_code=423)
        result = db[TOPICS_COLLECTION].update_one(
            {"project_slug": project_slug, "topic_slug": topic_slug},
            {"$set": {"content": content, "updated_at": now}},
        )
        if not result.matched_count:
            raise HTTPException(status_code=404, detail="Topic not found")
        db[PROJECTS_COLLECTION].update_one(
            {"slug": project_slug, "topics.slug": topic_slug},
            {"$set": {"topics.$.updated_at": now, "updated_at": now}},
        )
    except PyMongoError as exc:
        logger.warning("Could not save topic: %s", exc)
        return JSONResponse({"ok": False, "detail": "Database error"}, status_code=503)
    return JSONResponse({"ok": True, "saved_at": now.isoformat()})


# Registered after /api/... so "/api/<project>/delete" still reaches save_topic
@app.post("/{project_slug}/{topic_slug}/delete")
def delete_topic(
    project_slug: str,
    topic_slug: str,
    db: Annotated[Database, Depends(database)],
    _: Annotated[dict, Depends(require_user)],
) -> RedirectResponse:
    project = get_project_or_404(db, project_slug)
    if project.get("disabled"):
        return redirect_with_flash(f"/{project_slug}", "project_is_disabled")
    result = db[TOPICS_COLLECTION].delete_one({"project_slug": project_slug, "topic_slug": topic_slug})
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="Topic not found")
    db[PROJECTS_COLLECTION].update_one(
        {"slug": project_slug},
        {"$pull": {"topics": {"slug": topic_slug}}, "$set": {"updated_at": utc_now()}},
    )
    return redirect_with_flash(f"/{project_slug}", "topic_deleted")


# --- Blobs: folder uploads; contents in object storage, metadata in MongoDB ---


def blob_error(detail: str, status_code: int) -> JSONResponse:
    return JSONResponse({"ok": False, "detail": detail}, status_code=status_code)


def get_blob_or_404(db: Database, project_slug: str, blob_slug: str) -> dict:
    blob = db[BLOBS_COLLECTION].find_one({"project_slug": project_slug, "blob_slug": blob_slug})
    if not blob:
        raise HTTPException(status_code=404, detail="Blob not found")
    return blob


@app.post("/{project_slug}/blobs")
async def create_blob(
    request: Request,
    project_slug: str,
    db: Annotated[Database, Depends(database)],
    _: Annotated[dict, Depends(require_user)],
) -> JSONResponse:
    project = await run_in_threadpool(get_project_or_404, db, project_slug)
    if project.get("disabled"):
        return blob_error("This project is disabled, enable it to make changes", 423)

    form = await request.form(max_files=BLOB_MAX_FILES, max_fields=BLOB_MAX_FILES + 10)
    name = clean_name(str(form.get("name", "")))
    uploads = form.getlist("files")
    paths = [str(path) for path in form.getlist("paths")]
    if not is_valid_name(name) or not slugify(name):
        return blob_error(INVALID_NAME_MESSAGE, 400)
    if len(uploads) != len(paths):
        return blob_error("Malformed upload", 400)

    files: list[tuple[str, bytes, str]] = []
    metadata: list[dict] = []
    seen: set[str] = set()
    total = 0
    skipped = 0
    for upload, raw_path in zip(uploads, paths):
        path = normalize_blob_path(raw_path)
        if not path or path in seen or isinstance(upload, str):
            skipped += 1
            continue
        data = await upload.read(BLOB_MAX_FILE_BYTES + 1)
        if len(data) > BLOB_MAX_FILE_BYTES:
            skipped += 1
            continue
        total += len(data)
        if total > BLOB_MAX_TOTAL_BYTES:
            return blob_error(f"Folder is larger than {human_size(BLOB_MAX_TOTAL_BYTES)}", 413)
        is_text = decode_text(data) is not None
        content_type = "text/plain; charset=utf-8" if is_text else (mimetypes.guess_type(path)[0] or "application/octet-stream")
        seen.add(path)
        files.append((path, data, content_type))
        metadata.append({"path": path, "size": len(data), "is_text": is_text})
    if not files:
        return blob_error("No files to upload (empty folder, or everything was ignored or too large)", 400)

    def save() -> str:
        now = utc_now()
        blob_id = ObjectId()
        blob_slug = next_available_slug(db, BLOBS_COLLECTION, "blob_slug", slugify(name), {"project_slug": project_slug})
        # Upload contents first so metadata never points at missing objects
        storage.put_files(str(blob_id), files)
        try:
            db[BLOBS_COLLECTION].insert_one(
                {
                    "_id": blob_id,
                    "project_slug": project_slug,
                    "blob_slug": blob_slug,
                    "name": name,
                    "file_count": len(metadata),
                    "total_size": total,
                    "skipped_count": skipped,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            db[BLOB_FILES_COLLECTION].insert_many(
                [{**item, "blob_id": blob_id, "project_slug": project_slug, "blob_slug": blob_slug} for item in metadata]
            )
        except PyMongoError:
            db[BLOBS_COLLECTION].delete_one({"_id": blob_id})
            db[BLOB_FILES_COLLECTION].delete_many({"blob_id": blob_id})
            storage.delete_blob(str(blob_id))
            raise
        db[PROJECTS_COLLECTION].update_one({"slug": project_slug}, {"$set": {"updated_at": now}})
        return blob_slug

    try:
        blob_slug = await run_in_threadpool(save)
    except storage.StorageNotConfigured as exc:
        logger.warning("Blob upload without storage: %s", exc)
        return blob_error("Object storage is not configured on the server", 503)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Could not upload blob to object storage: %s", exc)
        return blob_error("Could not upload files to object storage", 502)
    except PyMongoError as exc:
        logger.warning("Could not save blob metadata: %s", exc)
        return blob_error("Database error, please try again", 503)

    url = f"/{project_slug}/blob/{blob_slug}"
    response = JSONResponse({"ok": True, "url": url, "files": len(metadata), "skipped": skipped})
    response.set_cookie(FLASH_COOKIE, "blob_created", max_age=60, httponly=True, samesite="lax")
    return response


@app.get("/{project_slug}/blob/{blob_slug}", response_class=HTMLResponse)
def blob_view(
    request: Request,
    project_slug: str,
    blob_slug: str,
    db: Annotated[Database, Depends(database)],
    _: Annotated[dict, Depends(require_user)],
    file: str | None = None,
) -> HTMLResponse:
    project = get_project_or_404(db, project_slug)
    blob = get_blob_or_404(db, project_slug, blob_slug)
    files = list(
        db[BLOB_FILES_COLLECTION]
        .find({"blob_id": blob["_id"]}, {"_id": 0, "path": 1, "size": 1, "is_text": 1})
        .sort("path", 1)
    )
    selected_path = file if any(item["path"] == file for item in files) else default_file(files)
    selected = next((item for item in files if item["path"] == selected_path), None)
    content = None
    storage_error = None
    if selected and selected["is_text"]:
        try:
            content = decode_text(storage.read_file(str(blob["_id"]), selected["path"]))
        except STORAGE_ERRORS as exc:
            logger.warning("Could not read blob file: %s", exc)
            storage_error = "Could not load this file from object storage."
    return render(
        request,
        "blob.html",
        {
            "project": project,
            "blob": blob,
            "tree": build_tree(files),
            "selected": selected,
            "content": content,
            "storage_error": storage_error,
            "expand_all": len(files) <= 80,
        },
    )


@app.get("/{project_slug}/blob/{blob_slug}/raw")
def blob_download(
    project_slug: str,
    blob_slug: str,
    file: str,
    db: Annotated[Database, Depends(database)],
    _: Annotated[dict, Depends(require_user)],
) -> RedirectResponse:
    blob = get_blob_or_404(db, project_slug, blob_slug)
    if not db[BLOB_FILES_COLLECTION].find_one({"blob_id": blob["_id"], "path": file}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="File not found")
    filename = re.sub(r'["\\\r\n]', "_", file.rsplit("/", 1)[-1])
    try:
        url = storage.download_url(str(blob["_id"]), file, filename)
    except STORAGE_ERRORS as exc:
        logger.warning("Could not sign blob download: %s", exc)
        raise HTTPException(status_code=502, detail="Object storage unavailable") from exc
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@app.post("/{project_slug}/blob/{blob_slug}/delete")
def delete_blob(
    project_slug: str,
    blob_slug: str,
    db: Annotated[Database, Depends(database)],
    _: Annotated[dict, Depends(require_user)],
) -> RedirectResponse:
    project = get_project_or_404(db, project_slug)
    if project.get("disabled"):
        return redirect_with_flash(f"/{project_slug}", "project_is_disabled")
    blob = get_blob_or_404(db, project_slug, blob_slug)
    try:
        storage.delete_blob(str(blob["_id"]))
    except STORAGE_ERRORS as exc:
        logger.warning("Could not delete blob objects: %s", exc)
        return redirect_with_flash(f"/{project_slug}/blob/{blob_slug}", "blob_delete_failed")
    db[BLOB_FILES_COLLECTION].delete_many({"blob_id": blob["_id"]})
    db[BLOBS_COLLECTION].delete_one({"_id": blob["_id"]})
    return redirect_with_flash(f"/{project_slug}", "blob_deleted")
