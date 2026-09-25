"""Folder "blobs": uploaded directory snapshots shown as a read-only file tree."""

from __future__ import annotations

import posixpath

BLOB_MAX_TOTAL_BYTES = 50 * 1024 * 1024
BLOB_MAX_FILE_BYTES = 5 * 1024 * 1024
BLOB_MAX_FILES = 3000
# Skipped anywhere in the path, both in the browser and here
BLOB_IGNORED_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".uv-cache",
    ".mypy_cache",
    ".pytest_cache",
    ".idea",
    ".DS_Store",
    "Thumbs.db",
}
README_NAMES = ("readme.md", "readme.txt", "readme.rst", "readme")


def normalize_blob_path(raw_path: str) -> str | None:
    """Return a safe relative POSIX path, or None if it is invalid or ignored."""
    path = raw_path.replace("\\", "/").strip().lstrip("/")
    if not path:
        return None
    path = posixpath.normpath(path)
    parts = path.split("/")
    if path.startswith("..") or any(part in {"", ".", ".."} for part in parts):
        return None
    if any(part in BLOB_IGNORED_NAMES for part in parts):
        return None
    return path


def decode_text(data: bytes) -> str | None:
    """Decode UTF-8 text; binary files (NUL bytes or invalid UTF-8) return None."""
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def build_tree(files: list[dict]) -> list[dict]:
    """Nest flat file paths into directories (folders first, then files, by name)."""
    root: dict = {"children": {}}
    for file in files:
        node = root
        parts = file["path"].split("/")
        for depth, part in enumerate(parts[:-1]):
            node = node["children"].setdefault(
                part, {"name": part, "path": "/".join(parts[: depth + 1]), "dir": True, "children": {}}
            )
        node["children"][parts[-1]] = {**file, "name": parts[-1], "dir": False}

    def to_list(node: dict) -> list[dict]:
        items = sorted(node["children"].values(), key=lambda item: (not item["dir"], item["name"].lower()))
        for item in items:
            if item["dir"]:
                item["children"] = to_list(item)
        return items

    return to_list(root)


def default_file(files: list[dict]) -> str | None:
    """README at the root if present, else the first text file, else the first file."""
    for file in files:
        if file["path"].lower() in README_NAMES:
            return file["path"]
    for file in files:
        if file.get("is_text"):
            return file["path"]
    return files[0]["path"] if files else None


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024 or unit == "MB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
