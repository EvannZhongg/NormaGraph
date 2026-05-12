from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.config import get_config
from services.ingestion_service import IngestionService


class ReportComparisonConcurrencyTest(unittest.TestCase):
    def test_iter_concurrent_results_uses_llm_batch_max_concurrency(self) -> None:
        service = IngestionService.__new__(IngestionService)
        service.config = get_config().model_copy(deep=True)
        service.config.llm.batch_max_concurrency = 3

        active_count = 0
        max_active_count = 0
        lock = threading.Lock()

        def worker(value: int) -> int:
            nonlocal active_count, max_active_count
            with lock:
                active_count += 1
                max_active_count = max(max_active_count, active_count)
            time.sleep(0.1)
            with lock:
                active_count -= 1
            return value * 2

        results = list(service._iter_concurrent_results([1, 2, 3, 4], worker, task_name="test-concurrency"))

        self.assertEqual(len(results), 4)
        self.assertGreaterEqual(max_active_count, 2)

    def test_iter_concurrent_results_runs_serially_when_concurrency_is_one(self) -> None:
        service = IngestionService.__new__(IngestionService)
        service.config = get_config().model_copy(deep=True)
        service.config.llm.batch_max_concurrency = 1

        active_count = 0
        max_active_count = 0
        lock = threading.Lock()

        def worker(value: int) -> int:
            nonlocal active_count, max_active_count
            with lock:
                active_count += 1
                max_active_count = max(max_active_count, active_count)
            time.sleep(0.02)
            with lock:
                active_count -= 1
            return value

        results = list(service._iter_concurrent_results([1, 2, 3], worker, task_name="test-serial"))

        self.assertEqual(len(results), 3)
        self.assertEqual(max_active_count, 1)


if __name__ == "__main__":
    unittest.main()
