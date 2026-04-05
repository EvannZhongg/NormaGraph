from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx

from core.config import AppConfig


class MinerUApiError(RuntimeError):
    pass


class MinerUClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def _base_url(self, endpoint: str | None) -> str:
        candidate = (endpoint or self.config.mineru.default_endpoint).rstrip("/")
        if candidate.endswith(self.config.mineru.api_prefix):
            return candidate[: -len(self.config.mineru.api_prefix)]
        parsed = urlparse(candidate)
        if not parsed.scheme:
            raise MinerUApiError(f"Invalid parser endpoint: {candidate}")
        return candidate

    def _headers(self) -> dict[str, str]:
        if not self.config.mineru_api_key:
            raise MinerUApiError("MINERU_API_KEY is missing. Put it in the project root .env file.")
        return {
            "Authorization": f"Bearer {self.config.mineru_api_key}",
            "Content-Type": "application/json",
        }

    async def request_upload_url(
        self,
        *,
        endpoint: str | None,
        file_name: str,
        data_id: str,
        is_ocr: bool | None = None,
        callback_url: str | None = None,
    ) -> tuple[str, str]:
        base_url = self._base_url(endpoint)
        payload = {
            "language": self.config.mineru.language,
            "enable_formula": self.config.mineru.enable_formula,
            "enable_table": self.config.mineru.enable_table,
            "model_version": self.config.mineru.model_version,
            "files": [
                {
                    "name": file_name,
                    "data_id": data_id,
                    "is_ocr": self.config.mineru.is_ocr if is_ocr is None else is_ocr,
                }
            ],
        }
        if callback_url:
            payload["callback"] = callback_url

        async with httpx.AsyncClient(timeout=self.config.mineru.request_timeout_seconds) as client:
            response = await client.post(
                f"{base_url}{self.config.mineru.api_prefix}/file-urls/batch",
                headers=self._headers(),
                json=payload,
            )
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise MinerUApiError(f"MinerU failed to create upload URL: {body.get('msg')}")
        data = body["data"]
        upload_urls = data.get("file_urls") or []
        if not upload_urls:
            raise MinerUApiError("MinerU returned no upload URLs.")
        return data["batch_id"], upload_urls[0]

    async def upload_file(self, upload_url: str, file_path: Path) -> None:
        async with httpx.AsyncClient(timeout=self.config.mineru.request_timeout_seconds) as client:
            with file_path.open("rb") as handle:
                response = await client.put(upload_url, content=handle.read())
        response.raise_for_status()

    async def get_batch_result(self, endpoint: str | None, batch_id: str) -> dict:
        base_url = self._base_url(endpoint)
        async with httpx.AsyncClient(timeout=self.config.mineru.request_timeout_seconds) as client:
            response = await client.get(
                f"{base_url}{self.config.mineru.api_prefix}/extract-results/batch/{batch_id}",
                headers={"Authorization": self._headers()["Authorization"], "Accept": "*/*"},
            )
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise MinerUApiError(f"MinerU result polling failed: {body.get('msg')}")
        return body["data"]

    async def download_result_zip(self, full_zip_url: str, destination: Path) -> None:
        async with httpx.AsyncClient(timeout=self.config.mineru.result_download_timeout_seconds) as client:
            response = await client.get(full_zip_url)
        response.raise_for_status()
        destination.write_bytes(response.content)
