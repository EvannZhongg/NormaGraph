from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse

from core.config import AppConfig
from models.schemas import CreateIngestionJobRequest, SourceFormat


@dataclass
class NormalizationResult:
    normalized_path: Path
    normalized_format: SourceFormat
    preprocessing_actions: list[str] = field(default_factory=list)


class NormalizationError(RuntimeError):
    pass


class NormalizationService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def normalize(self, source_path: Path, request: CreateIngestionJobRequest, work_dir: Path) -> NormalizationResult:
        endpoint = request.parserEndpoint or self.config.mineru.default_endpoint
        hostname = (urlparse(endpoint).hostname or "").lower()
        is_local_endpoint = hostname in {host.lower() for host in self.config.normalization.localhost_hosts}

        result = NormalizationResult(
            normalized_path=source_path,
            normalized_format=request.sourceFormat,
            preprocessing_actions=[],
        )

        if request.normalizationPolicy == "none":
            return result

        if is_local_endpoint and request.sourceFormat == "doc":
            result.preprocessing_actions.append("detected_localhost_endpoint")
            result.preprocessing_actions.append("requested_doc_to_pdf")
            return self._convert_doc_to_pdf(source_path, work_dir, result)

        if is_local_endpoint:
            result.preprocessing_actions.append("detected_localhost_endpoint")

        return result

    def _convert_doc_to_pdf(
        self,
        source_path: Path,
        work_dir: Path,
        result: NormalizationResult,
    ) -> NormalizationResult:
        converter = self.config.normalization.local_doc_to_pdf
        if not converter.enabled or not converter.command:
            raise NormalizationError(
                "Local parser endpoint detected for a .doc file, but doc-to-pdf conversion is not configured. "
                "Set normalization.local_doc_to_pdf.enabled=true and provide normalization.local_doc_to_pdf.command in config.yaml."
            )

        input_copy = work_dir / source_path.name
        shutil.copy2(source_path, input_copy)
        command = [part.replace("{input}", str(input_copy)).replace("{output_dir}", str(work_dir)) for part in converter.command]
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            raise NormalizationError(
                "doc-to-pdf conversion failed. "
                f"stdout={completed.stdout.strip()} stderr={completed.stderr.strip()}"
            )

        pdf_path = work_dir / f"{source_path.stem}.pdf"
        if not pdf_path.exists():
            raise NormalizationError(f"doc-to-pdf conversion completed but output was not found at {pdf_path}")

        result.preprocessing_actions.append("converted_doc_to_pdf")
        result.normalized_path = pdf_path
        result.normalized_format = "pdf"
        return result
