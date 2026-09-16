"""Environment-backed Studio configuration shared by product features."""

from __future__ import annotations

import os
from urllib.parse import urlsplit
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = os.path.abspath(
    os.path.expanduser(os.getenv("WORKSPACE_DIR", "~/fedops-workspace"))
)
WORKSPACE_DISPLAY_DIR = os.path.abspath(
    os.path.expanduser(os.getenv("STUDIO_HOST_WORKSPACE_DIR", WORKSPACE_DIR))
)
BASELINE_CACHE_DIR = os.path.abspath(
    os.path.expanduser(
        os.getenv(
            "STUDIO_BASELINE_CACHE_DIR",
            str(Path(WORKSPACE_DIR) / ".fedops-studio" / "baselines"),
        )
    )
)
APP_SESSION_COOKIE = os.getenv("STUDIO_SESSION_COOKIE", "fedops_studio_session")
FEDOPS_BASE_URL = os.getenv("FEDOPS_BASE_URL", "").strip().rstrip("/")
_base = urlsplit(FEDOPS_BASE_URL)
if _base.scheme not in ("http", "https") or not _base.hostname or _base.username or _base.password or _base.query or _base.fragment:
    raise ValueError("FEDOPS_BASE_URL must be an HTTP(S) URL without credentials, query, or fragment")
FEDOPS_LOGIN_URL = os.getenv("FEDOPS_LOGIN_URL", f"{FEDOPS_BASE_URL}/api/auth/login")
FOLDER_OPENER_URL = os.getenv("STUDIO_FOLDER_OPENER_URL", "").strip() or None
FOLDER_OPENER_TOKEN_FILE = os.getenv("STUDIO_FOLDER_OPENER_TOKEN_FILE", "").strip() or None

LOCAL_BROWSER_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "STUDIO_CORS_ORIGINS",
        "*",
    ).split(",")
    if origin.strip()
]
SOCKET_CORS_ORIGINS: str | list[str] = (
    "*" if "*" in LOCAL_BROWSER_ORIGINS else LOCAL_BROWSER_ORIGINS
)
TRUSTED_HOSTS = [
    host.strip()
    for host in os.getenv("STUDIO_TRUSTED_HOSTS", "*").split(",")
    if host.strip()
]

WEB_DIST_DIR = Path(
    os.getenv(
        "STUDIO_WEB_DIST",
        str(APP_DIR.parents[3] / "apps" / "studio-web" / "dist"),
    )
).resolve()
Path(WORKSPACE_DIR).mkdir(parents=True, exist_ok=True)
