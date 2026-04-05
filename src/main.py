from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from adapters.mineru_client import MinerUClient
from api.routes import build_router
from core.config import get_config
from core.logging import configure_logging
from repositories.job_store import JobStore
from repositories.standard_registry import StandardRegistry
from services.ingestion_service import IngestionService
from services.normalization import NormalizationService
from services.standard_pipeline import StandardPipelineService


def create_app() -> FastAPI:
    configure_logging()
    config = get_config()
    job_store = JobStore(config.jobs_dir)
    registry = StandardRegistry(config.registry_path)
    mineru_client = MinerUClient(config)
    normalization_service = NormalizationService(config)
    standard_pipeline_service = StandardPipelineService(config=config)
    ingestion_service = IngestionService(
        config=config,
        job_store=job_store,
        registry=registry,
        mineru_client=mineru_client,
        normalization_service=normalization_service,
        standard_pipeline_service=standard_pipeline_service,
    )

    app = FastAPI(title="Dam Safety KG Agent API", version="0.1.0")
    app.include_router(build_router(ingestion_service))
    return app


app = create_app()


def main() -> None:
    config = get_config()
    uvicorn.run(
        "main:app",
        host=config.server.host,
        port=config.server.port,
        reload=config.server.reload,
    )


if __name__ == "__main__":
    main()
