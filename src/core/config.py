from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import os
import re
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parents[2]


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8010
    reload: bool = False


class StorageConfig(BaseModel):
    data_dir: str = "data"
    jobs_dir: str = "data/jobs"
    artifacts_dir: str = "data/artifacts"
    downloads_dir: str = "data/downloads"
    kg_spaces_dir: str = "data/kg_spaces"
    registry_path: str = "data/registry/standards.json"


class MinerUConfig(BaseModel):
    default_endpoint: str = "https://mineru.net"
    api_prefix: str = "/api/v4"
    model_version: str = "vlm"
    language: str = "ch"
    enable_formula: bool = True
    enable_table: bool = True
    is_ocr: bool = True
    request_timeout_seconds: int = 120
    poll_interval_seconds: int = 8
    poll_timeout_seconds: int = 900
    result_download_timeout_seconds: int = 300
    poll_request_retries: int = 5
    retry_backoff_seconds: int = 3


class LocalDocToPdfConfig(BaseModel):
    enabled: bool = False
    command: list[str] = Field(default_factory=list)


class NormalizationConfig(BaseModel):
    localhost_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    local_doc_to_pdf: LocalDocToPdfConfig = Field(default_factory=LocalDocToPdfConfig)


class LLMConfig(BaseModel):
    enabled: bool = True
    provider: Literal["openai_responses"] = "openai_responses"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    structured_output_mode: Literal["auto", "text_format", "response_format"] = "auto"
    enable_thinking: bool | None = None
    temperature: float = 0.0
    max_output_tokens: int = 6000
    timeout_seconds: int = 180
    clause_batch_size: int = 6
    batch_max_retries: int = 1
    batch_retry_backoff_seconds: float = 2.0
    batch_max_concurrency: int = 1
    api_key_env: str = "OPENAI_API_KEY"
    organization_env: str | None = None
    project_env: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    organization: str | None = Field(default=None, repr=False)
    project: str | None = Field(default=None, repr=False)


class EmbeddingConfig(BaseModel):
    enabled: bool = False
    provider: Literal["openai_embeddings"] = "openai_embeddings"
    base_url: str = "https://api.openai.com/v1"
    model: str = "text-embedding-3-small"
    dimensions: int = 1536
    timeout_seconds: int = 120
    batch_size: int = 24
    api_key_env: str = "OPENAI_API_KEY"
    api_key: str | None = Field(default=None, repr=False)
    target_node_types: list[str] = Field(default_factory=lambda: ["clause", "requirement"])


class PostgresConfig(BaseModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 5433
    database: str = "normagraph"
    user: str = "postgres"
    password_env: str = "POSTGRES_PASSWORD"
    password: str | None = Field(default=None, repr=False)
    db_schema: str = Field(default="kg", alias="schema")
    sslmode: str = "prefer"


class KnowledgeGraphConfig(BaseModel):
    extraction_mode: Literal["heuristic", "llm", "hybrid"] = "llm"
    fallback_to_heuristic_on_llm_error: bool = True
    include_appendix_requirements: bool = False
    materialize_graph: bool = True


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    mineru: MinerUConfig = Field(default_factory=MinerUConfig)
    normalization: NormalizationConfig = Field(default_factory=NormalizationConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    knowledge_graph: KnowledgeGraphConfig = Field(default_factory=KnowledgeGraphConfig)
    mineru_api_key: str | None = None
    root_dir: Path = ROOT_DIR

    @property
    def data_dir(self) -> Path:
        return self.root_dir / self.storage.data_dir

    @property
    def jobs_dir(self) -> Path:
        return self.root_dir / self.storage.jobs_dir

    @property
    def artifacts_dir(self) -> Path:
        return self.root_dir / self.storage.artifacts_dir

    @property
    def downloads_dir(self) -> Path:
        return self.root_dir / self.storage.downloads_dir

    @property
    def kg_spaces_dir(self) -> Path:
        return self.root_dir / self.storage.kg_spaces_dir

    @property
    def registry_path(self) -> Path:
        return self.root_dir / self.storage.registry_path

    @property
    def resource_dir(self) -> Path:
        return self.root_dir / "src" / "resources"

    @property
    def schema_dir(self) -> Path:
        return self.resource_dir / "schemas"

    def artifact_dir_for(self, document_id: str) -> Path:
        return self.artifacts_dir / self._safe_storage_segment(document_id)

    def download_work_dir_for(self, document_id: str, job_id: str) -> Path:
        return self.downloads_dir / self._safe_storage_segment(document_id) / self._safe_storage_segment(job_id)

    def kg_space_dir_for(self, standard_id: str) -> Path:
        return self.kg_spaces_dir / self._safe_storage_segment(standard_id)

    @staticmethod
    def _safe_storage_segment(value: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
        return sanitized or "unknown"


def _load_yaml_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a YAML object.")
    return data


def _load_secret(*names: str | None) -> str | None:
    for name in names:
        if not name:
            continue
        value = os.getenv(name)
        if value:
            return value
    return None


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    load_dotenv(ROOT_DIR / ".env")
    config_data = _load_yaml_config(ROOT_DIR / "config.yaml")
    llm_data = config_data.setdefault("llm", {})
    embedding_data = config_data.setdefault("embedding", {})
    postgres_data = config_data.setdefault("postgres", {})
    config_data["mineru_api_key"] = os.getenv("MINERU_API_KEY")
    llm_data["api_key"] = _load_secret(llm_data.get("api_key_env", "OPENAI_API_KEY"), "LLM_API_KEY")
    llm_data["organization"] = _load_secret(llm_data.get("organization_env"))
    llm_data["project"] = _load_secret(llm_data.get("project_env"))
    embedding_data["api_key"] = _load_secret(
        embedding_data.get("api_key_env", "OPENAI_API_KEY"),
        "EMBEDDING_API_KEY",
        llm_data.get("api_key_env", "OPENAI_API_KEY"),
        "OPENAI_API_KEY",
    )
    postgres_data["password"] = _load_secret(postgres_data.get("password_env", "POSTGRES_PASSWORD"))
    config = AppConfig.model_validate(config_data)
    for directory in [
        config.data_dir,
        config.jobs_dir,
        config.artifacts_dir,
        config.downloads_dir,
        config.kg_spaces_dir,
        config.registry_path.parent,
        config.schema_dir,
    ]:
        directory.mkdir(parents=True, exist_ok=True)
    return config
