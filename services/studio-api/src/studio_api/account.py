"""Account-scoped local paths derived from an authenticated FedOps identity."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import WORKSPACE_DIR, WORKSPACE_DISPLAY_DIR

DEVICE_KEY_PATH = Path(".fedops-studio/device-account-key")


@dataclass(frozen=True)
class AccountContext:
    account_key: str
    user_id: str
    username: str
    handle: str | None
    organization: str | None
    display_name: str
    workspace_root: Path
    display_workspace_root: Path
    relative_workspace: Path


def fedops_profile(raw_profile: Any, login_username: str) -> dict[str, str | None]:
    """Validate the stable fields returned by FedOps Web login/check."""
    profile = raw_profile if isinstance(raw_profile, dict) else {}
    raw_user_id = profile.get("_id") or profile.get("id")
    user_id = str(raw_user_id or "").strip()
    if not user_id:
        raise RuntimeError("FedOps login did not return a stable user identity.")

    username = str(profile.get("username") or login_username).strip()
    if not username:
        raise RuntimeError("FedOps login did not return a username.")
    handle = str(profile.get("handle") or "").strip() or None
    organization = str(profile.get("organization") or "").strip() or None
    display_name = str(profile.get("displayName") or "").strip()
    if not display_name:
        display_name = " ".join(
            str(profile.get(field) or "").strip()
            for field in ("firstName", "lastName")
            if str(profile.get(field) or "").strip()
        )
    return {
        "userId": user_id,
        "username": username,
        "handle": handle,
        "organization": organization,
        "displayName": display_name or handle or username,
    }


def account_key_for_user(user_id: str, workspace_base: Path | None = None) -> str:
    """Return a device-local opaque key without exposing the FedOps Mongo ID."""
    normalized = user_id.strip()
    if not normalized:
        raise ValueError("A stable FedOps user ID is required.")
    secret = _device_key((workspace_base or Path(WORKSPACE_DIR)).expanduser().resolve())
    digest = hmac.new(secret, normalized.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"account-{digest[:16]}"


def create_account_identity(
    raw_profile: Any,
    login_username: str,
) -> dict[str, str | None]:
    profile = fedops_profile(raw_profile, login_username)
    return {
        **profile,
        "accountKey": account_key_for_user(str(profile["userId"])),
    }


def account_context(session: dict[str, Any], *, create: bool = True) -> AccountContext:
    user_id = str(session.get("user_id") or "").strip()
    account_key = str(session.get("account_key") or "").strip()
    if not user_id or not account_key:
        raise PermissionError("The FedOps session does not have a stable account identity.")
    if not re.fullmatch(r"account-[0-9a-f]{16}", account_key):
        raise PermissionError("The local account namespace is invalid.")

    relative_workspace = Path("accounts") / account_key / "projects"
    workspace_base = Path(WORKSPACE_DIR).expanduser().resolve()
    workspace_root = (workspace_base / relative_workspace).resolve()
    try:
        workspace_root.relative_to(workspace_base)
    except ValueError as error:
        raise PermissionError("The account Workspace path is outside the local Workspace.") from error
    display_root = Path(WORKSPACE_DISPLAY_DIR).expanduser() / relative_workspace
    if create:
        workspace_root.mkdir(parents=True, exist_ok=True)
        local_data_root = workspace_root.parent / ".local-data"
        local_data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return AccountContext(
        account_key=account_key,
        user_id=user_id,
        username=str(session.get("username") or ""),
        handle=str(session.get("handle") or "").strip() or None,
        organization=str(session.get("organization") or "").strip() or None,
        display_name=str(session.get("display_name") or session.get("username") or "FedOps Account"),
        workspace_root=workspace_root,
        display_workspace_root=display_root,
        relative_workspace=relative_workspace,
    )


def _device_key(workspace_base: Path) -> bytes:
    path = workspace_base / DEVICE_KEY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    accounts_root = workspace_base / "accounts"
    if not path.exists() and accounts_root.is_dir() and any(accounts_root.iterdir()):
        raise RuntimeError(
            "The Studio device account key is missing. Restore "
            ".fedops-studio/device-account-key from the Workspace backup."
        )
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        try:
            os.write(descriptor, secrets.token_bytes(32))
        finally:
            os.close(descriptor)
    secret = path.read_bytes()
    if len(secret) < 32:
        raise RuntimeError("The local Studio account key is invalid.")
    return secret
