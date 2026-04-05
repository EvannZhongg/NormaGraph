from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from models.schemas import (
    ComparisonSummary,
    CreateComparisonRequest,
    CreateIngestionJobRequest,
    IngestionJob,
    QuestionRequest,
    QuestionResponse,
    RequirementDetail,
    StandardDetail,
    StandardsResponse,
    SubgraphResponse,
)
from services.ingestion_service import IngestionService


def build_router(ingestion_service: IngestionService) -> APIRouter:
    router = APIRouter()

    @router.get('/healthz')
    async def healthz() -> dict[str, str]:
        return {'status': 'ok'}

    @router.post('/v1/ingestions', response_model=IngestionJob, status_code=status.HTTP_202_ACCEPTED)
    async def create_ingestion(request: CreateIngestionJobRequest, background_tasks: BackgroundTasks) -> IngestionJob:
        try:
            return ingestion_service.create_job(request, background_tasks)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @router.get('/v1/ingestions/{job_id}', response_model=IngestionJob)
    async def get_ingestion(job_id: str) -> IngestionJob:
        job = ingestion_service.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'Job {job_id} was not found.')
        return job

    @router.get('/v1/standards', response_model=StandardsResponse)
    async def list_standards() -> StandardsResponse:
        return StandardsResponse(items=ingestion_service.list_standards())

    @router.get('/v1/standards/{standard_id}', response_model=StandardDetail)
    async def get_standard(standard_id: str) -> StandardDetail:
        detail = ingestion_service.get_standard(standard_id)
        if detail is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'Standard {standard_id} was not found.')
        return detail

    @router.get('/v1/standards/{standard_id}/subgraph', response_model=SubgraphResponse)
    async def get_standard_subgraph(standard_id: str, node_id: str | None = Query(default=None), depth: int = Query(default=2, ge=1, le=4)) -> SubgraphResponse:
        subgraph = ingestion_service.get_standard_subgraph(standard_id, node_id=node_id, depth=depth)
        if subgraph is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'Subgraph for {standard_id} is not available yet.')
        return SubgraphResponse(**subgraph)

    @router.get('/v1/requirements/{requirement_id}', response_model=RequirementDetail)
    async def get_requirement(requirement_id: str) -> RequirementDetail:
        requirement = ingestion_service.get_requirement_detail(requirement_id)
        if requirement is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'Requirement {requirement_id} was not found.')
        return requirement

    @router.post('/v1/qa/ask', response_model=QuestionResponse)
    async def ask_question(request: QuestionRequest) -> QuestionResponse:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=f'QA API is not implemented yet for question: {request.question}')

    @router.post('/v1/comparisons', response_model=ComparisonSummary, status_code=status.HTTP_202_ACCEPTED)
    async def create_comparison(request: CreateComparisonRequest) -> ComparisonSummary:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f'Comparison API is not implemented yet for report {request.reportDocumentId}.',
        )

    @router.get('/v1/comparisons/{comparison_id}', response_model=ComparisonSummary)
    async def get_comparison(comparison_id: str) -> ComparisonSummary:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=f'Comparison {comparison_id} is not implemented yet.')

    @router.get('/v1/comparisons/{comparison_id}/items')
    async def list_comparison_items(comparison_id: str) -> dict:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=f'Comparison items for {comparison_id} are not implemented yet.')

    return router
