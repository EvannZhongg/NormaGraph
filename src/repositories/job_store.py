from __future__ import annotations

import json
from pathlib import Path

from models.schemas import IngestionJob


class JobStore:
    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def _job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def save(self, job: IngestionJob) -> None:
        path = self._job_path(job.jobId)
        path.write_text(job.model_dump_json(indent=2), encoding="utf-8")

    def load(self, job_id: str) -> IngestionJob | None:
        path = self._job_path(job_id)
        if not path.exists():
            return None
        return IngestionJob.model_validate(json.loads(path.read_text(encoding="utf-8")))
