from __future__ import annotations

import re
import logging
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pymongo.errors import PyMongoError
from pymongo.database import Database

from .config import Settings, get_settings
from .db import PROJECTS_COLLECTION, TOPICS_COLLECTION, USERS_COLLECTION, ensure_database, get_database, utc_now
from .security import read_signed_payload, sign_payload, verify_password

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
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
}


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
    return render(request, "project.html", {"project": project, "topics": topics})


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
