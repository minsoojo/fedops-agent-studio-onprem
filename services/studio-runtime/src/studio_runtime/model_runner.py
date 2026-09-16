"""Host-mounted Hugging Face model download, status, and local inference."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HF_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
HF_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")
HF_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,239}$")
HF_RESULT_MARKER = "__FEDOPS_HF_RESULT__="
MODEL_PREPARATION_LOCK = threading.RLock()
MODEL_PREPARATION_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="model-prepare")
MODEL_PREPARATIONS: dict[str, dict[str, Any]] = {}
GGUF_MODEL_LOCK = threading.RLock()
GGUF_MODELS: dict[tuple[str, int], Any] = {}
PreparationCallback = Callable[[dict[str, Any]], None]
TokenCallback = Callable[[str], None]


class ModelRunnerUnavailable(RuntimeError):
    """A selected local model cannot be downloaded, loaded, or executed."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalized_huggingface_filename(file_name: str | None) -> str | None:
    if file_name is None or not file_name.strip():
        return None
    selected = file_name.strip()
    if (
        not HF_FILENAME.fullmatch(selected)
        or selected.startswith("/")
        or ".." in Path(selected).parts
    ):
        raise ValueError("The Hugging Face model filename is invalid.")
    return selected


def huggingface_model_relative_path(
    repo_id: str,
    revision: str,
    file_name: str | None = None,
) -> Path:
    repo = repo_id.strip()
    selected_revision = revision.strip()
    if not HF_ID.fullmatch(repo):
        raise ValueError("Use a Hugging Face repository ID in owner/model form.")
    if not HF_REVISION.fullmatch(selected_revision) or ".." in selected_revision.split("/"):
        raise ValueError("The Hugging Face revision is invalid.")
    selected_file = _normalized_huggingface_filename(file_name)
    repository = repo.replace("/", "--")
    identity = selected_revision if selected_file is None else f"{selected_revision}\0{selected_file}"
    revision_key = hashlib.sha256(identity.encode()).hexdigest()[:12]
    return Path("huggingface") / repository / revision_key


def _hf_target(
    models_root: Path,
    repo_id: str,
    revision: str,
    file_name: str | None = None,
) -> Path:
    root = models_root.expanduser().resolve()
    target = (root / huggingface_model_relative_path(repo_id, revision, file_name)).resolve()
    target.relative_to(root)
    return target


def _partial_target(target: Path) -> Path:
    return target.with_name(f".{target.name}.partial")


def _directory_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for current, _, names in os.walk(path):
        root = Path(current)
        for name in names:
            try:
                total += (root / name).stat().st_size
            except OSError:
                continue
    return total


def _huggingface_snapshot_size(
    repo_id: str,
    revision: str,
    file_name: str | None = None,
) -> int | None:
    encoded_repo = urllib.parse.quote(repo_id, safe="/")
    encoded_revision = urllib.parse.quote(revision, safe="")
    url = f"https://huggingface.co/api/models/{encoded_repo}/revision/{encoded_revision}?blobs=true"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "FedOps-Agent-Studio/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            document = json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    selected_file = _normalized_huggingface_filename(file_name)
    sizes = []
    for item in document.get("siblings", []):
        if not isinstance(item, dict) or not isinstance(item.get("size"), int):
            continue
        if selected_file is not None and item.get("rfilename") != selected_file:
            continue
        sizes.append(item["size"])
    return sum(sizes) if sizes else None


def _recover_interrupted_download(target: Path) -> Path:
    """Adopt the previous random staging directory as a resumable download."""
    partial = _partial_target(target)
    if partial.exists():
        return partial
    candidates = sorted(
        (
            path for path in target.parent.glob(f".{target.name}-*")
            if path.is_dir() and not path.is_symlink()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        candidates[0].replace(partial)
    return partial


def _safetensors_expected_size(path: Path) -> int | None:
    """Read the self-describing safetensors header without loading model data."""
    try:
        with path.open("rb") as content:
            encoded_length = content.read(8)
            if len(encoded_length) != 8:
                return None
            header_length = struct.unpack("<Q", encoded_length)[0]
            if header_length <= 0 or header_length > 64 * 1024 * 1024:
                return None
            header = json.loads(content.read(header_length))
    except (OSError, ValueError, json.JSONDecodeError, struct.error):
        return None
    offsets = [
        value["data_offsets"][1]
        for key, value in header.items()
        if key != "__metadata__"
        and isinstance(value, dict)
        and isinstance(value.get("data_offsets"), list)
        and len(value["data_offsets"]) == 2
    ]
    if not offsets or not all(isinstance(offset, int) and offset >= 0 for offset in offsets):
        return None
    return 8 + header_length + max(offsets)


def _snapshot_weights_complete(path: Path) -> bool:
    safetensors = list(path.glob("*.safetensors"))
    index_path = path / "model.safetensors.index.json"
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            expected_names = set(index.get("weight_map", {}).values())
        except (OSError, json.JSONDecodeError, AttributeError):
            return False
        if not expected_names or any(not (path / name).is_file() for name in expected_names):
            return False
    if safetensors:
        return all(
            (expected := _safetensors_expected_size(model)) is not None
            and model.stat().st_size == expected
            for model in safetensors
        )
    return any(file.is_file() for file in path.iterdir() if not file.name.startswith("."))


def _resume_incomplete_safetensors(
    staging: Path,
    repo_id: str,
    revision: str,
    progress: PreparationCallback | None = None,
    total_bytes: int | None = None,
) -> None:
    """Resume model shards already present in the durable staging directory."""
    encoded_repo = urllib.parse.quote(repo_id, safe="/")
    encoded_revision = urllib.parse.quote(revision, safe="")
    for path in sorted(staging.glob("*.safetensors")):
        expected = _safetensors_expected_size(path)
        current = path.stat().st_size
        if expected is None or current >= expected:
            continue
        encoded_name = urllib.parse.quote(path.name, safe="")
        url = f"https://huggingface.co/{encoded_repo}/resolve/{encoded_revision}/{encoded_name}"
        request = urllib.request.Request(
            url,
            headers={
                "Range": f"bytes={current}-",
                "User-Agent": "FedOps-Agent-Studio/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                if response.status != 206:
                    raise ModelRunnerUnavailable(
                        f"The model host did not accept a resume request for {path.name}."
                    )
                with path.open("ab") as target:
                    while chunk := response.read(8 * 1024 * 1024):
                        target.write(chunk)
                        if progress:
                            downloaded = _directory_size(staging)
                            progress({
                                "stage": "downloading",
                                "downloadedBytes": downloaded,
                                "totalBytes": total_bytes,
                                "percent": min(98.0, downloaded * 100 / total_bytes)
                                if total_bytes else 0.0,
                                "detail": f"Resuming {path.name}",
                            })
        except (OSError, urllib.error.URLError) as error:
            raise ModelRunnerUnavailable(
                f"The interrupted download for {path.name} could not be resumed; "
                "the downloaded bytes were preserved."
            ) from error
        if path.stat().st_size != expected:
            raise ModelRunnerUnavailable(
                f"The resumed model shard {path.name} is still incomplete; downloaded bytes were preserved."
            )


def _selected_model_complete(
    path: Path,
    file_name: str | None,
    expected_size: int | None = None,
) -> bool:
    selected_file = _normalized_huggingface_filename(file_name)
    if selected_file is None:
        return _snapshot_weights_complete(path)
    model_file = path / selected_file
    return (
        model_file.is_file()
        and not model_file.is_symlink()
        and model_file.stat().st_size > 0
        and (expected_size is None or model_file.stat().st_size == expected_size)
    )


def local_huggingface_status(
    models_root: Path,
    repo_id: str,
    revision: str,
    file_name: str | None = None,
) -> dict[str, Any]:
    selected_file = _normalized_huggingface_filename(file_name)
    target = _hf_target(models_root, repo_id, revision, selected_file)
    metadata = target / ".fedops-model.json"
    if not metadata.is_file():
        partial = _partial_target(target)
        downloaded = _directory_size(partial)
        return {
            "status": "not-prepared",
            "provider": "local-huggingface",
            "model": repo_id,
            "localPath": str(target),
            "downloadedBytes": downloaded,
            "detail": (
                f"Resume the interrupted local download ({downloaded} bytes preserved)."
                if downloaded else
                "Download this Hugging Face revision to the local account model directory."
            ),
        }
    try:
        value = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    expected_size = value.get("expectedSize") if isinstance(value.get("expectedSize"), int) else None
    valid = (
        value.get("repoId") == repo_id
        and value.get("revision") == revision
        and value.get("fileName") == selected_file
        and _selected_model_complete(target, selected_file, expected_size)
    )
    downloaded = _directory_size(target)
    return {
        "status": "installed" if valid else "error",
        "provider": "local-huggingface",
        "model": repo_id,
        "localPath": str(target),
        "modelFile": str(target / selected_file) if selected_file else None,
        "downloadedBytes": downloaded,
        "totalBytes": expected_size,
        "detail": (
            "The Hugging Face model is stored in the host-mounted account model directory."
            if valid else "The local Hugging Face model directory is incomplete. Prepare it again."
        ),
    }


def prepare_local_huggingface_model(
    models_root: Path,
    repo_id: str,
    revision: str,
    file_name: str | None = None,
    progress: PreparationCallback | None = None,
) -> dict[str, Any]:
    """Download an immutable HF revision into the host-mounted account directory."""
    selected_file = _normalized_huggingface_filename(file_name)
    target = _hf_target(models_root, repo_id, revision, selected_file)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = _recover_interrupted_download(target)
    total_bytes = _huggingface_snapshot_size(repo_id, revision, selected_file)
    if selected_file and total_bytes is None:
        raise ModelRunnerUnavailable(f"{selected_file} was not found in the selected Hugging Face revision.")
    current = local_huggingface_status(models_root, repo_id, revision, selected_file)
    if progress:
        downloaded = int(current.get("downloadedBytes") or 0)
        progress({
            "stage": "checking",
            "downloadedBytes": downloaded,
            "totalBytes": total_bytes,
            "percent": min(98.0, downloaded * 100 / total_bytes)
            if total_bytes else 0.0,
            "detail": "Checking the local model directory and remote snapshot size.",
        })
    if current["status"] == "installed":
        if progress:
            progress({
                "stage": "ready",
                "downloadedBytes": total_bytes or _directory_size(target),
                "totalBytes": total_bytes,
                "percent": 100.0,
                "detail": str(current["detail"]),
            })
        return current
    uvx = shutil.which("uvx")
    if not uvx:
        raise ModelRunnerUnavailable("uvx is required to download a Hugging Face model.")
    staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    if selected_file is None:
        _resume_incomplete_safetensors(staging, repo_id, revision, progress, total_bytes)
    environment = os.environ.copy()
    environment.update({
        "HF_HOME": str(models_root.expanduser().resolve() / ".cache"),
        "HF_HUB_DISABLE_XET": "1",
        "UV_CACHE_DIR": str(models_root.expanduser().resolve() / ".uv-cache"),
        "UV_LINK_MODE": "copy",
    })
    command = [
        uvx,
        "--from", "huggingface-hub",
        "hf", "download", repo_id,
    ]
    if selected_file:
        command.append(selected_file)
    command.extend(["--revision", revision, "--local-dir", str(staging)])
    log_path = staging / ".fedops-download.log"
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            command,
            env=environment,
            text=True,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        while process.poll() is None:
            if time.monotonic() - started > 60 * 60 * 3:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise ModelRunnerUnavailable(
                    "Hugging Face model download exceeded three hours; downloaded files were kept."
                )
            downloaded = _directory_size(staging)
            if progress:
                progress({
                    "stage": "downloading",
                    "downloadedBytes": downloaded,
                    "totalBytes": total_bytes,
                    "percent": min(98.0, downloaded * 100 / total_bytes)
                    if total_bytes else 0.0,
                    "detail": "Downloading model files to the account-local model directory.",
                })
            time.sleep(0.75)
        return_code = process.returncode
    if return_code != 0:
        try:
            output_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:].strip()
        except OSError:
            output_tail = ""
        raise ModelRunnerUnavailable(
            "Hugging Face model download failed; downloaded files were kept for resume.\n"
            + output_tail
        )
    if progress:
        progress({
            "stage": "verifying",
            "downloadedBytes": _directory_size(staging),
            "totalBytes": total_bytes,
            "percent": 99.0,
            "detail": "Verifying all model shards before installation.",
        })
    if not _selected_model_complete(staging, selected_file, total_bytes):
        raise ModelRunnerUnavailable(
            "The Hugging Face download returned without complete model weights; "
            "downloaded files were kept for resume."
        )
    (staging / ".fedops-model.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "source": "huggingface",
            "repoId": repo_id,
            "revision": revision,
            "fileName": selected_file,
            "format": "gguf" if selected_file and selected_file.lower().endswith(".gguf") else "transformers",
            "expectedSize": total_bytes,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if target.exists():
        shutil.rmtree(target)
    staging.replace(target)
    result = local_huggingface_status(models_root, repo_id, revision, selected_file)
    if progress:
        progress({
            "stage": "ready",
            "downloadedBytes": total_bytes or _directory_size(target),
            "totalBytes": total_bytes,
            "percent": 100.0,
            "detail": str(result["detail"]),
        })
    return result


def _preparation_key(
    models_root: Path,
    repo_id: str,
    revision: str,
    file_name: str | None = None,
) -> str:
    identity = (
        f"{models_root.expanduser().resolve()}\0{repo_id}\0{revision}"
        f"\0{_normalized_huggingface_filename(file_name) or ''}"
    )
    return hashlib.sha256(identity.encode()).hexdigest()[:20]


def _read_preparation(key: str) -> dict[str, Any]:
    with MODEL_PREPARATION_LOCK:
        value = MODEL_PREPARATIONS.get(key)
        if not value:
            raise KeyError(key)
        return dict(value)


def start_local_huggingface_preparation(
    models_root: Path,
    repo_id: str,
    revision: str,
    file_name: str | None = None,
) -> dict[str, Any]:
    """Start or reuse one background preparation for an account-local model."""
    selected_file = _normalized_huggingface_filename(file_name)
    target = _hf_target(models_root, repo_id, revision, selected_file)
    key = _preparation_key(models_root, repo_id, revision, selected_file)
    now = _now()
    current = local_huggingface_status(models_root, repo_id, revision, selected_file)
    with MODEL_PREPARATION_LOCK:
        existing = MODEL_PREPARATIONS.get(key)
        if existing and existing.get("status") in {"queued", "running"}:
            return dict(existing)
        state: dict[str, Any] = {
            "preparationId": f"model-{key}",
            "status": "succeeded" if current["status"] == "installed" else "queued",
            "stage": "ready" if current["status"] == "installed" else "queued",
            "percent": 100.0 if current["status"] == "installed" else 0.0,
            "downloadedBytes": int(current.get("downloadedBytes") or 0),
            "totalBytes": current.get("totalBytes") if isinstance(current.get("totalBytes"), int) else None,
            "detail": str(current["detail"]),
            "provider": "local-huggingface",
            "model": repo_id,
            "localPath": str(target),
            "startedAt": now,
            "updatedAt": now,
        }
        MODEL_PREPARATIONS[key] = state
    if state["status"] == "succeeded":
        return dict(state)

    def update(change: dict[str, Any]) -> None:
        with MODEL_PREPARATION_LOCK:
            current_state = MODEL_PREPARATIONS[key]
            current_state.update(change)
            current_state["updatedAt"] = _now()

    def run() -> None:
        update({"status": "running", "stage": "checking"})
        try:
            result = prepare_local_huggingface_model(
                models_root,
                repo_id,
                revision,
                selected_file,
                progress=update,
            )
        except (ModelRunnerUnavailable, OSError, ValueError) as error:
            update({
                "status": "failed",
                "stage": "failed",
                "detail": str(error),
            })
            return
        update({
            "status": "succeeded",
            "stage": "ready",
            "percent": 100.0,
            "detail": str(result["detail"]),
        })

    MODEL_PREPARATION_EXECUTOR.submit(run)
    return _read_preparation(key)


def read_local_huggingface_preparation(
    models_root: Path,
    repo_id: str,
    revision: str,
    file_name: str | None = None,
) -> dict[str, Any]:
    return _read_preparation(_preparation_key(models_root, repo_id, revision, file_name))


def local_huggingface_chat(
    models_root: Path,
    repo_id: str,
    revision: str,
    messages: list[dict[str, str]],
    *,
    file_name: str | None = None,
    context_window: int = 8192,
    max_tokens: int,
    temperature: float,
    on_delta: TokenCallback | None = None,
) -> dict[str, Any]:
    """Run a host-stored Transformers snapshot or one selected GGUF file."""
    selected_file = _normalized_huggingface_filename(file_name)
    status = local_huggingface_status(models_root, repo_id, revision, selected_file)
    if status["status"] != "installed":
        raise ModelRunnerUnavailable(str(status["detail"]))
    if selected_file and selected_file.lower().endswith(".gguf"):
        try:
            from llama_cpp import Llama
            from llama_cpp.llama_chat_format import (
                Jinja2ChatFormatter,
                chat_formatter_to_chat_completion_handler,
            )
        except ImportError as error:
            raise ModelRunnerUnavailable(
                "The GGUF inference runtime is unavailable in this Studio installation."
            ) from error
        model_path = Path(str(status["modelFile"])).resolve()
        selected_context = max(512, min(int(context_window), 32768))
        cache_key = (str(model_path), selected_context)
        with GGUF_MODEL_LOCK:
            model = GGUF_MODELS.get(cache_key)
            if model is None:
                GGUF_MODELS.clear()
                try:
                    model = Llama(
                        model_path=str(model_path),
                        n_ctx=selected_context,
                        n_threads=max(1, min(os.cpu_count() or 4, 8)),
                        n_threads_batch=max(1, min(os.cpu_count() or 4, 8)),
                        n_gpu_layers=0,
                        verbose=False,
                    )
                    chat_template = str(model.metadata.get("tokenizer.chat_template") or "")
                    if "enable_thinking" in chat_template:
                        formatter = Jinja2ChatFormatter(
                            chat_template,
                            eos_token="<|im_end|>",
                            bos_token="",
                        )

                        def direct_formatter(**kwargs: Any) -> Any:
                            return formatter(**kwargs, enable_thinking=False)

                        model.chat_handler = chat_formatter_to_chat_completion_handler(
                            direct_formatter
                        )
                except Exception as error:
                    raise ModelRunnerUnavailable(
                        f"The GGUF model could not be loaded: {error}"
                    ) from error
                GGUF_MODELS[cache_key] = model
        try:
            if on_delta is None:
                with GGUF_MODEL_LOCK:
                    output = model.create_chat_completion(
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                choice = output["choices"][0]
                content = choice.get("message", {}).get("content")
            else:
                pieces: list[str] = []
                usage: dict[str, Any] = {}
                with GGUF_MODEL_LOCK:
                    chunks = model.create_chat_completion(
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        stream=True,
                    )
                    for chunk in chunks:
                        if not isinstance(chunk, dict):
                            continue
                        delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                        if isinstance(delta, str) and delta:
                            pieces.append(delta)
                            on_delta(delta)
                        if isinstance(chunk.get("usage"), dict):
                            usage = chunk["usage"]
                content = "".join(pieces)
                output = {"usage": usage}
        except Exception as error:
            raise ModelRunnerUnavailable(f"Local GGUF inference failed: {error}") from error
        if not isinstance(content, str) or not content.strip():
            raise ModelRunnerUnavailable("The local GGUF runtime returned no text response.")
        return {
            "content": content,
            "usage": output.get("usage") if isinstance(output, dict) else {},
            "model": f"{repo_id}/{selected_file}",
        }
    uv = shutil.which("uv")
    if not uv:
        raise ModelRunnerUnavailable("uv is required to execute a local Hugging Face model.")
    runner = (
        "import json,sys,torch\n"
        "from transformers import AutoConfig,AutoModelForCausalLM,AutoModelForImageTextToText,AutoProcessor,AutoTokenizer\n"
        "path=sys.argv[1]; messages=json.loads(sys.argv[2])\n"
        "config=AutoConfig.from_pretrained(path,local_files_only=True)\n"
        "is_multimodal=getattr(config,'model_type','')=='qwen3_5'\n"
        "loader=AutoModelForImageTextToText if is_multimodal else AutoModelForCausalLM\n"
        "processor=AutoProcessor.from_pretrained(path,local_files_only=True) if is_multimodal else AutoTokenizer.from_pretrained(path,local_files_only=True)\n"
        "model=loader.from_pretrained(path,local_files_only=True,torch_dtype='auto',device_map='auto')\n"
        "inputs=processor.apply_chat_template(messages,add_generation_prompt=True,tokenize=True,return_tensors='pt',return_dict=True).to(model.device)\n"
        "kwargs={'max_new_tokens':int(sys.argv[3]),'do_sample':float(sys.argv[4])>0}\n"
        "if kwargs['do_sample']: kwargs['temperature']=float(sys.argv[4])\n"
        "output=model.generate(**inputs,**kwargs)\n"
        "text=processor.decode(output[0][inputs['input_ids'].shape[-1]:],skip_special_tokens=True)\n"
        "print('" + HF_RESULT_MARKER + "'+json.dumps({'content':text},ensure_ascii=False))\n"
    )
    environment = os.environ.copy()
    environment.update({
        "HF_HOME": str(models_root.expanduser().resolve() / ".cache"),
        "UV_CACHE_DIR": str(models_root.expanduser().resolve() / ".uv-cache"),
        "UV_LINK_MODE": "copy",
    })
    completed = subprocess.run(
        [
            uv, "run", "--no-project",
            "--with", "torch>=2.4",
            "--with", "transformers @ git+https://github.com/huggingface/transformers.git@main",
            "--with", "accelerate>=1.0",
            "--with", "torchvision>=0.19",
            "--with", "pillow>=10",
            "python", "-c", runner,
            str(status["localPath"]), json.dumps(messages, ensure_ascii=False),
            str(max_tokens), str(temperature),
        ],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60 * 60,
        check=False,
    )
    if completed.returncode != 0:
        raise ModelRunnerUnavailable(
            "Local Hugging Face inference failed.\n" + completed.stdout[-4000:].strip()
        )
    encoded = next(
        (line[len(HF_RESULT_MARKER):] for line in completed.stdout.splitlines() if line.startswith(HF_RESULT_MARKER)),
        None,
    )
    if not encoded:
        raise ModelRunnerUnavailable("The local Hugging Face runtime returned no structured response.")
    result = json.loads(encoded)
    content = str(result.get("content") or "")
    if on_delta is not None and content:
        # Non-GGUF runtimes remain compatible and emit their complete answer once.
        on_delta(content)
    return {"content": content, "usage": {}, "model": repo_id}


__all__ = [
    "ModelRunnerUnavailable",
    "huggingface_model_relative_path",
    "local_huggingface_chat",
    "local_huggingface_status",
    "prepare_local_huggingface_model",
    "read_local_huggingface_preparation",
    "start_local_huggingface_preparation",
]
