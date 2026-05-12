from __future__ import annotations

from pathlib import Path
import json
import shutil
import sys
import threading
import time
import unittest
import uuid
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from services.ingestion_service import IngestionService


class ReportComparisonIoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = PROJECT_ROOT / "data" / "test-temp" / f"report-comparison-io-{uuid.uuid4().hex[:8]}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.service = IngestionService.__new__(IngestionService)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_write_text_atomic_replaces_target_without_leaving_temp_files(self) -> None:
        target_path = self.temp_dir / "comparison.json"

        self.service._write_text_atomic(target_path, '{"status":"running"}')

        self.assertEqual(target_path.read_text(encoding="utf-8"), '{"status":"running"}')
        self.assertEqual(list(self.temp_dir.glob("*.tmp")), [])

    def test_read_json_file_with_retries_recovers_from_transient_empty_file(self) -> None:
        target_path = self.temp_dir / "comparison.json"
        target_path.write_text("", encoding="utf-8")

        def delayed_write() -> None:
            time.sleep(0.03)
            target_path.write_text(json.dumps({"status": "running"}, ensure_ascii=False), encoding="utf-8")

        worker = threading.Thread(target=delayed_write, daemon=True)
        worker.start()
        payload = self.service._read_json_file_with_retries(target_path, retries=4, delay_seconds=0.02)
        worker.join(timeout=0.2)

        self.assertEqual(payload["status"], "running")

    def test_write_text_atomic_retries_transient_permission_error_on_replace(self) -> None:
        target_path = self.temp_dir / "comparison.json"
        original_replace = Path.replace
        attempts = {"count": 0}

        def flaky_replace(path_obj: Path, target: Path) -> Path:
            if path_obj.name.endswith(".tmp") and Path(target) == target_path and attempts["count"] < 2:
                attempts["count"] += 1
                raise PermissionError("file is temporarily locked")
            return original_replace(path_obj, target)

        with mock.patch.object(Path, "replace", autospec=True, side_effect=flaky_replace):
            self.service._write_text_atomic(target_path, '{"status":"running"}', replace_retries=3, replace_delay_seconds=0.001)

        self.assertEqual(target_path.read_text(encoding="utf-8"), '{"status":"running"}')
        self.assertEqual(attempts["count"], 2)

    def test_read_json_file_with_retries_recovers_from_transient_permission_error(self) -> None:
        target_path = self.temp_dir / "comparison.json"
        target_path.write_text(json.dumps({"status": "running"}, ensure_ascii=False), encoding="utf-8")
        original_read_text = Path.read_text
        attempts = {"count": 0}

        def flaky_read_text(path_obj: Path, *args: object, **kwargs: object) -> str:
            if path_obj == target_path and attempts["count"] < 2:
                attempts["count"] += 1
                raise PermissionError("file is temporarily locked")
            return original_read_text(path_obj, *args, **kwargs)

        with mock.patch.object(Path, "read_text", autospec=True, side_effect=flaky_read_text):
            payload = self.service._read_json_file_with_retries(target_path, retries=3, delay_seconds=0.001)

        self.assertEqual(payload["status"], "running")
        self.assertEqual(attempts["count"], 2)


if __name__ == "__main__":
    unittest.main()
