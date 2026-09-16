"""Portable hardware information for the Studio host or constrained runtime."""

from __future__ import annotations

import ctypes
import json
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal

HardwareSource = Literal["host", "runtime"]


def collect_hardware_information(
    source: HardwareSource = "runtime",
) -> dict[str, object]:
    """Return one OS-neutral snapshot without third-party Python dependencies."""
    system = platform.system() or "Unknown"
    memory_bytes = _physical_memory_bytes(system)
    if source == "runtime" and system == "Linux":
        memory_bytes = _runtime_memory_bytes(memory_bytes)
    devices = _gpu_devices(system)
    return {
        "source": source,
        "platform": {
            "system": "macOS" if system == "Darwin" else system,
            "release": platform.release() or "Unknown",
            "architecture": platform.machine() or "Unknown",
        },
        "cpu": {
            "model": _cpu_model(system),
            "logicalCores": _logical_cpu_count(source, system),
        },
        "memory": {"totalBytes": max(0, memory_bytes)},
        "gpu": {"detected": bool(devices), "devices": devices},
    }


def read_host_hardware_information(
    bridge_url: str,
    token_file: Path,
) -> dict[str, object]:
    """Read the authenticated host snapshot exposed to a Docker Studio API."""
    token_path = token_file.expanduser()
    if not token_path.is_file():
        raise FileNotFoundError("The host bridge token is unavailable.")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("The host bridge token is empty.")
    request = urllib.request.Request(
        f"{bridge_url.rstrip('/')}/hardware",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=6.0) as response:
            if response.status != 200:
                raise RuntimeError(f"Host bridge returned HTTP {response.status}.")
            payload = json.loads(response.read().decode("utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("The host bridge returned invalid JSON.") from error
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Host bridge rejected hardware access ({error.code}).") from error
    except urllib.error.URLError as error:
        raise RuntimeError("Host bridge is not running.") from error
    except OSError as error:
        raise RuntimeError("Host bridge hardware access failed.") from error
    if not _valid_hardware_payload(payload):
        raise ValueError("The host bridge returned invalid hardware information.")
    return payload


def _cpu_model(system: str) -> str:
    if system == "Darwin":
        value = _run_text(["sysctl", "-n", "machdep.cpu.brand_string"])
        if value:
            return value
    if system == "Windows":
        value = os.getenv("PROCESSOR_IDENTIFIER", "").strip()
        if value:
            return value
    if system == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.casefold().startswith(("model name", "hardware", "processor")) and ":" in line:
                    value = line.split(":", 1)[1].strip()
                    if value and not value.isdigit():
                        return value
        except OSError:
            pass
    return (platform.processor() or platform.machine() or "Unknown CPU").strip()


def _logical_cpu_count(source: HardwareSource, system: str) -> int:
    available = max(1, os.cpu_count() or 1)
    if source != "runtime" or system != "Linux":
        return available
    try:
        affinity = os.sched_getaffinity(0)  # type: ignore[attr-defined]
        available = min(available, max(1, len(affinity)))
    except (AttributeError, OSError):
        pass
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="utf-8").split()[:2]
        if quota != "max":
            available = min(available, max(1, math.ceil(int(quota) / int(period))))
    except (OSError, ValueError, ZeroDivisionError):
        pass
    return available


def _physical_memory_bytes(system: str) -> int:
    if system == "Darwin":
        value = _run_text(["sysctl", "-n", "hw.memsize"])
        if value and value.isdigit():
            return int(value)
    if system == "Windows":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memoryLoad", ctypes.c_ulong),
                ("totalPhysical", ctypes.c_ulonglong),
                ("availablePhysical", ctypes.c_ulonglong),
                ("totalPageFile", ctypes.c_ulonglong),
                ("availablePageFile", ctypes.c_ulonglong),
                ("totalVirtual", ctypes.c_ulonglong),
                ("availableVirtual", ctypes.c_ulonglong),
                ("availableExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return int(status.totalPhysical)
        except (AttributeError, OSError):
            pass
    try:
        return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return 0


def _runtime_memory_bytes(physical_bytes: int) -> int:
    for path in (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value.isdigit():
            limit = int(value)
            if 0 < limit < (1 << 60):
                return min(physical_bytes, limit) if physical_bytes else limit
    return physical_bytes


def _gpu_devices(system: str) -> list[dict[str, object]]:
    devices = _nvidia_devices()
    if devices:
        return devices
    if system == "Darwin":
        return _macos_gpu_devices()
    if system == "Windows":
        return _windows_gpu_devices()
    if system == "Linux":
        return _linux_gpu_devices()
    return []


def _nvidia_devices() -> list[dict[str, object]]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    output = _run_text(
        [
            executable,
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    devices: list[dict[str, object]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        name, _, memory = line.partition(",")
        memory_value = memory.strip()
        devices.append(
            {
                "name": name.strip() or "NVIDIA GPU",
                "memoryBytes": int(float(memory_value) * 1024**2) if _number(memory_value) else None,
                "computeUnits": None,
            }
        )
    return devices


def _macos_gpu_devices() -> list[dict[str, object]]:
    output = _run_text(["system_profiler", "SPDisplaysDataType", "-json"], timeout=12)
    try:
        items = json.loads(output).get("SPDisplaysDataType", [])
    except (AttributeError, json.JSONDecodeError):
        return []
    devices: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("sppci_model") or item.get("_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        compute_units = _optional_integer(item.get("sppci_cores"))
        memory_value = item.get("spdisplays_vram") or item.get("_spdisplays_vram")
        devices.append(
            {
                "name": name,
                "memoryBytes": _memory_string_bytes(memory_value),
                "computeUnits": compute_units,
            }
        )
    return devices


def _windows_gpu_devices() -> list[dict[str, object]]:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if not executable:
        return []
    output = _run_text(
        [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json -Compress",
        ],
        timeout=10,
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return []
    items = payload if isinstance(payload, list) else [payload]
    return [
        {
            "name": str(item.get("Name") or "Windows GPU"),
            "memoryBytes": _optional_integer(item.get("AdapterRAM")),
            "computeUnits": None,
        }
        for item in items
        if isinstance(item, dict) and item.get("Name")
    ]


def _linux_gpu_devices() -> list[dict[str, object]]:
    executable = shutil.which("lspci")
    if not executable:
        return []
    output = _run_text([executable, "-mm"], timeout=6)
    devices: list[dict[str, object]] = []
    for line in output.splitlines():
        if not re.search(r'"(?:VGA compatible|3D|Display) controller"', line, re.IGNORECASE):
            continue
        try:
            fields = shlex.split(line)
        except ValueError:
            continue
        name = " ".join(fields[2:4]).strip() if len(fields) >= 4 else line.strip()
        devices.append({"name": name, "memoryBytes": None, "computeUnits": None})
    return devices


def _run_text(argv: list[str], timeout: int = 5) -> str:
    try:
        completed = subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip()


def _memory_string_bytes(value: object) -> int | None:
    match = re.search(r"([0-9.]+)\s*(TB|GB|MB|KB)", str(value or ""), re.IGNORECASE)
    if not match:
        return None
    units = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    return int(float(match.group(1)) * units[match.group(2).upper()])


def _optional_integer(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _valid_hardware_payload(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("source") == "host"
        and isinstance(value.get("platform"), dict)
        and isinstance(value.get("cpu"), dict)
        and isinstance(value.get("memory"), dict)
        and isinstance(value.get("gpu"), dict)
    )
