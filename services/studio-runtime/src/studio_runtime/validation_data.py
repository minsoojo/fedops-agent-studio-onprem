"""Snapshot explicitly selected, account-local validation files; no networking."""
import hashlib
import os
from pathlib import Path
import stat
import zipfile

MAX_BYTES = 512 * 1024 * 1024
MAX_FILES = 10000


def validation_selection(root: Path, relative: str) -> Path:
    if relative and (relative.startswith('/') or '\\' in relative
                     or any(p in ('', '.', '..') for p in relative.split('/'))):
        raise ValueError('Select a subdirectory inside the validation folder.')
    if any(ord(c) < 32 for c in relative):
        raise ValueError('Invalid validation directory.')
    candidate = root
    for part in relative.split('/') if relative else []:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError('Validation directories must not be symlinks.')
    root = root.resolve(strict=True)
    selected = candidate.resolve(strict=True)
    if not selected.is_dir() or (selected != root and root not in selected.parents):
        raise ValueError('Validation directory is outside the dedicated folder.')
    return selected


def build_validation_archive(root: Path, relative: str, output: Path) -> dict:
    selected = validation_selection(root, relative)
    count = total = 0
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for current, dirs, names in os.walk(selected, followlinks=False):
            dirs.sort()
            for name in dirs:
                if Path(current, name).is_symlink():
                    raise ValueError('Validation data must not contain symlinks.')
            for name in sorted(names):
                if name == '.DS_Store':
                    continue
                path = Path(current, name)
                relative_name = path.relative_to(selected).as_posix()
                if any(p.startswith('.') for p in relative_name.split('/')) or '\\' in relative_name or any(ord(c) < 32 for c in relative_name):
                    raise ValueError('Hidden files and unsafe names are not allowed in validation data.')
                with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
                    before = os.fstat(source.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise ValueError('Only regular validation files can be uploaded.')
                    count += 1
                    total += before.st_size
                    if count > MAX_FILES or total > MAX_BYTES:
                        raise ValueError('Validation data exceeds 10000 files or 512 MiB.')
                    info = zipfile.ZipInfo(relative_name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.external_attr = 0o100600 << 16
                    copied = 0
                    with archive.open(info, 'w') as target:
                        while chunk := source.read(1024 * 1024):
                            copied += len(chunk)
                            if copied > before.st_size:
                                raise ValueError('Validation files changed; refresh and retry.')
                            target.write(chunk)
                    after = os.fstat(source.fileno())
                    if copied != before.st_size or before.st_mtime_ns != after.st_mtime_ns:
                        raise ValueError('Validation files changed; refresh and retry.')
    if not count:
        raise ValueError('The selected validation folder is empty.')
    if output.stat().st_size > MAX_BYTES + 8 * 1024 * 1024:
        raise ValueError('Validation archive exceeds the transport limit.')
    digest = hashlib.sha256()
    with output.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return {'sha256': digest.hexdigest(), 'fileCount': count, 'totalBytes': total}
