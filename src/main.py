from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from adapters.mineru_client import MinerUClient
from api.routes import build_router
from core.config import get_config
from core.logging import configure_logging
from repositories.job_store import JobStore
from repositories.standard_registry import StandardRegistry
from services.ingestion_service import IngestionService
from services.normalization import NormalizationService
from services.standard_pipeline import StandardPipelineService


def _configure_webui_routes(app: FastAPI, webui_dir: Path) -> None:
    index_path = webui_dir / "index.html"

    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/webui/", status_code=307)

    @app.get("/webui", include_in_schema=False)
    @app.get("/webui/", include_in_schema=False)
    @app.get("/webui/{full_path:path}", include_in_schema=False)
    async def serve_webui(full_path: str = "") -> FileResponse:
        if full_path:
            requested = (webui_dir / full_path).resolve()
            if requested.exists() and requested.is_file() and requested.is_relative_to(webui_dir.resolve()):
                return FileResponse(requested)
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="Web UI build output was not found. Build the frontend into /webui first.")
        return FileResponse(index_path)


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

    app = FastAPI(title="Dam Safety KG Agent API", version="0.2.0")
    app.include_router(build_router(ingestion_service))
    _configure_webui_routes(app, config.webui_dir)
    return app


def main() -> None:
    config = get_config()
    uvicorn.run(
        "main:app",
        host=config.server.host,
        port=config.server.port,
        reload=config.server.reload,
    )


app = create_app()


if __name__ == "__main__":
    main()
