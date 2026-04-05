from __future__ import annotations

import json
from pathlib import Path
import re

from models.schemas import StandardDetail, StandardSummary


STANDARD_RE = re.compile(
    r"(?P<code>(?:GB|SL|DL/T|SDJ|SLJ))\s*(?P<number>\d+)[-—](?P<year>\d{2,4})",
    re.IGNORECASE,
)


class StandardRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def _read(self) -> dict[str, dict]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, dict]) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def list(self) -> list[StandardSummary]:
        data = self._read()
        return [StandardSummary.model_validate(item) for item in data.values()]

    def get(self, standard_id: str) -> StandardDetail | None:
        item = self._read().get(standard_id)
        if not item:
            return None
        return StandardDetail.model_validate(item)

    def upsert(self, detail: StandardDetail) -> None:
        data = self._read()
        data[detail.standardId] = detail.model_dump(mode="json")
        self._write(data)

    def detect_from_filename(self, filename: str) -> tuple[str, str, str] | None:
        match = STANDARD_RE.search(filename)
        if not match:
            return None
        code = f"{match.group('code').lower().replace('/', '').replace(' ', '')}{match.group('number')}"
        year = match.group("year")
        title = re.sub(r"^\d+[_\s-]*", "", Path(filename).stem)
        return f"{code}:{year}", match.group("code").upper() + match.group("number"), title
