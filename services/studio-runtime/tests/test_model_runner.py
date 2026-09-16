import json
import struct
import tempfile
import unittest
from pathlib import Path

from studio_runtime import model_runner


class ModelRunnerTest(unittest.TestCase):
    def test_huggingface_model_path_is_account_local_and_revision_stable(self):
        first = model_runner.huggingface_model_relative_path("Qwen/Qwen3.5-4B", "main")
        repeated = model_runner.huggingface_model_relative_path("Qwen/Qwen3.5-4B", "main")
        changed = model_runner.huggingface_model_relative_path("Qwen/Qwen3.5-4B", "v2")

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, changed)
        self.assertEqual(first.parts[:2], ("huggingface", "Qwen--Qwen3.5-4B"))

    def test_gguf_filename_has_a_distinct_safe_model_path(self):
        repository = "bartowski/Qwen_Qwen3.5-4B-GGUF"
        quantized = model_runner.huggingface_model_relative_path(
            repository, "main", "Qwen_Qwen3.5-4B-Q4_K_M.gguf"
        )
        another_quant = model_runner.huggingface_model_relative_path(
            repository, "main", "Qwen3.5-4B-Q3_K_M.gguf"
        )

        self.assertNotEqual(quantized, another_quant)
        with self.assertRaises(ValueError):
            model_runner.huggingface_model_relative_path(repository, "main", "../model.gguf")

    def test_gguf_status_requires_the_exact_selected_file(self):
        with tempfile.TemporaryDirectory() as directory:
            models = Path(directory) / "models"
            repo = "bartowski/Qwen_Qwen3.5-4B-GGUF"
            filename = "Qwen_Qwen3.5-4B-Q4_K_M.gguf"
            target = models / model_runner.huggingface_model_relative_path(
                repo, "main", filename
            )
            target.mkdir(parents=True)
            (target / filename).write_bytes(b"GGUF-test")
            (target / ".fedops-model.json").write_text(
                json.dumps({
                    "repoId": repo,
                    "revision": "main",
                    "fileName": filename,
                    "format": "gguf",
                    "expectedSize": len(b"GGUF-test"),
                }),
                encoding="utf-8",
            )

            status = model_runner.local_huggingface_status(
                models, repo, "main", filename
            )

        self.assertEqual(status["status"], "installed")
        self.assertTrue(status["modelFile"].endswith(filename))
        self.assertEqual(status["totalBytes"], len(b"GGUF-test"))

    def test_local_huggingface_status_uses_host_mounted_model_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            models = Path(directory) / "account" / "models"
            target = models / model_runner.huggingface_model_relative_path(
                "Qwen/Qwen3.5-4B", "main"
            )
            target.mkdir(parents=True)
            (target / "config.json").write_text("{}", encoding="utf-8")
            (target / ".fedops-model.json").write_text(
                json.dumps({"repoId": "Qwen/Qwen3.5-4B", "revision": "main"}),
                encoding="utf-8",
            )

            status = model_runner.local_huggingface_status(
                models, "Qwen/Qwen3.5-4B", "main"
            )

        self.assertEqual(status["status"], "installed")
        self.assertIn("/models/huggingface/", status["localPath"])

    def test_interrupted_download_is_recovered_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            models = Path(directory) / "account" / "models"
            target = models / model_runner.huggingface_model_relative_path(
                "Qwen/Qwen3.5-4B", "main"
            )
            legacy_staging = target.with_name(f".{target.name}-old-run")
            legacy_staging.mkdir(parents=True)
            (legacy_staging / "model.safetensors.incomplete").write_bytes(b"partial")

            partial = model_runner._recover_interrupted_download(target)
            status = model_runner.local_huggingface_status(
                models, "Qwen/Qwen3.5-4B", "main"
            )

            self.assertEqual(partial.name, f".{target.name}.partial")
            self.assertTrue(partial.is_dir())
            self.assertFalse(legacy_staging.exists())
            self.assertEqual(status["downloadedBytes"], len(b"partial"))
            self.assertIn("Resume", status["detail"])

    def test_safetensors_header_distinguishes_complete_and_partial_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            header = json.dumps({
                "weight": {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]},
            }, separators=(",", ":")).encode()
            complete = root / "model.safetensors"
            complete.write_bytes(struct.pack("<Q", len(header)) + header + b"12345678")

            self.assertEqual(
                model_runner._safetensors_expected_size(complete),
                complete.stat().st_size,
            )
            self.assertTrue(model_runner._snapshot_weights_complete(root))

            complete.write_bytes(complete.read_bytes()[:-1])
            self.assertFalse(model_runner._snapshot_weights_complete(root))

    def test_prepared_model_reports_immediate_completed_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            models = Path(directory) / "models"
            target = models / model_runner.huggingface_model_relative_path(
                "Qwen/Qwen3.5-4B", "main"
            )
            target.mkdir(parents=True)
            (target / "config.json").write_text("{}", encoding="utf-8")
            (target / ".fedops-model.json").write_text(
                json.dumps({"repoId": "Qwen/Qwen3.5-4B", "revision": "main"}),
                encoding="utf-8",
            )

            started = model_runner.start_local_huggingface_preparation(
                models, "Qwen/Qwen3.5-4B", "main"
            )
            current = model_runner.read_local_huggingface_preparation(
                models, "Qwen/Qwen3.5-4B", "main"
            )

            self.assertEqual(started["status"], "succeeded")
            self.assertEqual(current["stage"], "ready")
            self.assertEqual(current["percent"], 100.0)
            self.assertEqual(current["preparationId"], started["preparationId"])


if __name__ == "__main__":
    unittest.main()
