"""Authenticated FedOps Web adapter used by Agent Studio features."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

from ..config import FEDOPS_BASE_URL, FEDOPS_LOGIN_URL


def _parse_set_cookie_headers(headers: list[str]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for header in headers:
        simple_cookie = SimpleCookie()
        simple_cookie.load(header)
        for key, morsel in simple_cookie.items():
            cookies[key] = morsel.value
    return cookies


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{key}={value}" for key, value in cookies.items())


def login_to_fedops(username: str, password: str) -> dict[str, Any]:
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        FEDOPS_LOGIN_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            cookies = _parse_set_cookie_headers(
                response.headers.get_all("Set-Cookie") or []
            )
            raw_response = response.read()
    except urllib.error.HTTPError as error:
        error.read()
        if error.code in {401, 403}:
            raise PermissionError("The username or password is incorrect.") from error
        raise RuntimeError(
            f"FedOps login request failed. (HTTP {error.code})"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError("Could not connect to the FedOps server.") from error

    access_token = cookies.get("access_token")
    if not access_token:
        raise RuntimeError(
            "The FedOps login response did not include an access_token cookie."
        )
    authentication = {
        "cookies": cookies,
        "cookieHeader": _cookie_header(cookies),
        "accessToken": access_token,
    }
    try:
        profile = json.loads(raw_response.decode("utf-8")) if raw_response else {}
    except json.JSONDecodeError as error:
        raise RuntimeError("The FedOps login response is not valid JSON.") from error
    if not isinstance(profile, dict) or not (profile.get("_id") or profile.get("id")):
        profile = request_fedops_json(
            {"fedops_auth": authentication},
            f"{FEDOPS_BASE_URL}/api/auth/check",
        )
    authentication["profile"] = profile
    return authentication


def request_fedops_json(
    session: dict[str, Any],
    url: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> Any:
    if not url:
        raise RuntimeError("FedOps API URL is not configured.")

    fedops_auth = session.get("fedops_auth") or {}
    headers = {"Accept": "application/json"}
    if fedops_auth.get("cookieHeader"):
        headers["Cookie"] = fedops_auth["cookieHeader"]
    if fedops_auth.get("accessToken"):
        headers["Authorization"] = f"Bearer {fedops_auth['accessToken']}"

    request_body = None
    if payload is not None:
        request_body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=request_body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw_response = response.read()
    except urllib.error.HTTPError as error:
        try:
            error_body = error.read().decode("utf-8", errors="ignore")
        except OSError:
            error_body = ""
        if error.code in {401, 403}:
            raise PermissionError(
                "FedOps authentication has expired or you do not have permission."
            ) from error
        message = f"FedOps API request failed. (HTTP {error.code})"
        if error_body:
            message = f"{message} {error_body[:200]}"
        raise RuntimeError(message) from error
    except urllib.error.URLError as error:
        raise RuntimeError("Could not connect to the FedOps API server.") from error

    if not raw_response:
        return {}
    try:
        return json.loads(raw_response.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError("FedOps API response is not valid JSON.") from error


def _fedops_headers(
    session: dict[str, Any],
    *,
    accept: str = "application/json",
) -> dict[str, str]:
    fedops_auth = session.get("fedops_auth") or {}
    headers = {"Accept": accept}
    if fedops_auth.get("cookieHeader"):
        headers["Cookie"] = str(fedops_auth["cookieHeader"])
    if fedops_auth.get("accessToken"):
        headers["Authorization"] = f"Bearer {fedops_auth['accessToken']}"
    return headers


def _connection(url: str) -> tuple[http.client.HTTPConnection, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            parsed.hostname, parsed.port or 443, timeout=60
        )
    elif parsed.scheme == "http":
        connection = http.client.HTTPConnection(
            parsed.hostname, parsed.port or 80, timeout=60
        )
    else:
        raise RuntimeError("FedOps returned an unsupported API URL.")
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    return connection, target


def upload_fedops_binary(
    session: dict[str, Any],
    path: str,
    source: Path,
    *,
    content_type: str,
    headers: dict[str, str] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    timeout: int = 60,
) -> Any:
    """Stream an artifact to authenticated FedOps Web without loading it in memory."""
    url = fedops_api_url(path)
    if not source.is_file():
        raise RuntimeError(f"The upload artifact no longer exists: {source.name}")
    request_headers = _fedops_headers(session)
    request_headers.update(headers or {})
    total_bytes = source.stat().st_size
    request_headers.update(
        {
            "Content-Type": content_type,
            "Content-Length": str(total_bytes),
        }
    )
    response = None
    body = b""
    last_error: OSError | None = None
    # Model and Release endpoints are checksum-idempotent. One fresh-connection
    # retry safely covers a proxy/TLS reset between the two consecutive uploads.
    for attempt in range(2):
        connection, target = _connection(url)
        connection.timeout = timeout
        try:
            connection.putrequest("POST", target)
            for name, value in request_headers.items():
                connection.putheader(name, value)
            connection.endheaders()
            uploaded_bytes = 0
            with source.open("rb") as content:
                while chunk := content.read(1024 * 1024):
                    connection.send(chunk)
                    uploaded_bytes += len(chunk)
                    if progress_callback:
                        progress_callback(uploaded_bytes, total_bytes)
            response = connection.getresponse()
            body = response.read()
            break
        except OSError as error:
            last_error = error
            if attempt == 0:
                time.sleep(0.1)
        finally:
            connection.close()
    if response is None:
        detail = (
            f"{type(last_error).__name__}: {last_error}"
            if last_error
            else "unknown transport error"
        )
        raise RuntimeError(
            f"Could not upload the artifact to FedOps Web. ({detail})"
        ) from last_error
    if response.status in {401, 403}:
        raise PermissionError(
            "FedOps authentication expired or the Task is not owned by this account."
        )
    if response.status < 200 or response.status >= 300:
        detail = body.decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(
            f"FedOps artifact upload failed. (HTTP {response.status}) {detail}"
        )
    try:
        return json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "FedOps artifact upload response is not valid JSON."
        ) from error


def download_authenticated_fedops_artifact(
    session: dict[str, Any],
    path: str,
    destination: Path,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> Path:
    url = fedops_api_url(path)
    connection, target = _connection(url)
    headers = _fedops_headers(session, accept="application/octet-stream")
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    digest = hashlib.sha256()
    try:
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        if response.status in {401, 403}:
            response.read()
            raise PermissionError(
                "FedOps authentication expired or artifact access was denied."
            )
        if response.status < 200 or response.status >= 300:
            detail = response.read().decode("utf-8", errors="ignore")[:300]
            raise RuntimeError(
                f"FedOps artifact download failed. (HTTP {response.status}) {detail}"
            )
        with destination.open("xb") as output:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if expected_size is not None and size > expected_size:
                    raise RuntimeError("FedOps artifact exceeds its declared size.")
                digest.update(chunk)
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    if expected_size is not None and size != expected_size:
        destination.unlink(missing_ok=True)
        raise RuntimeError("FedOps artifact size does not match its descriptor.")
    if expected_sha256 and digest.hexdigest() != expected_sha256:
        destination.unlink(missing_ok=True)
        raise RuntimeError("FedOps artifact checksum does not match its descriptor.")
    return destination


def download_fedops_artifact(
    url: str,
    destination: Path,
    expected_size: int,
) -> None:
    """Stream one Web-issued S3 artifact without exposing its signed URL."""
    parsed = urllib.parse.urlparse(url)
    allow_insecure = os.getenv("STUDIO_ALLOW_INSECURE_ARTIFACT_URLS") == "1"
    if parsed.scheme != "https" and not (allow_insecure and parsed.scheme == "http"):
        raise RuntimeError("FedOps returned an unsupported artifact URL.")
    if not parsed.netloc:
        raise RuntimeError("FedOps returned an invalid artifact URL.")
    if expected_size < 0:
        raise RuntimeError("FedOps returned an invalid artifact size.")

    request = urllib.request.Request(
        url, headers={"Accept": "application/octet-stream"}
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with (
            urllib.request.urlopen(request, timeout=30) as response,
            destination.open("wb") as output,
        ):
            final_scheme = urllib.parse.urlparse(response.geturl()).scheme
            if final_scheme != "https" and not (
                allow_insecure and final_scheme == "http"
            ):
                raise RuntimeError(
                    "The artifact download redirected to an unsupported URL."
                )
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > expected_size:
                    raise RuntimeError(
                        "A Baseline artifact is larger than its release manifest."
                    )
                output.write(chunk)
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        OSError,
    ) as error:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Could not download a FedOps Baseline artifact.") from error
    except RuntimeError:
        destination.unlink(missing_ok=True)
        raise
    if written != expected_size:
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            "A Baseline artifact size does not match its release manifest."
        )


def fedops_api_url(path: str, query: dict[str, Any] | None = None) -> str:
    url = f"{FEDOPS_BASE_URL}/api/{path.lstrip('/')}"
    if query:
        values = {
            key: value
            for key, value in query.items()
            if value is not None and value != ""
        }
        if values:
            url = f"{url}?{urllib.parse.urlencode(values)}"
    return url


def fedops_request(
    session: dict[str, Any],
    path: str,
    *,
    method: str = "GET",
    query: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    return request_fedops_json(
        session,
        fedops_api_url(path, query),
        method=method,
        payload=payload,
    )


def quote_segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")
