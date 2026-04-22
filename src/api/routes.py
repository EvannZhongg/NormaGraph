from __future__ import annotations

from pathlib import Path
import shlex
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Response, UploadFile, status

from adapters.llm_client import ResponseAPIError
from models.schemas import (
    ComparisonSummary,
    CreateComparisonRequest,
    CreateIngestionJobRequest,
    DocumentJobsResponse,
    DocumentsResponse,
    GraphEntityEditRequest,
    GraphEntityExistsResponse,
    GraphLabelResponse,
    GraphRelationEditRequest,
    GraphSearchResponse,
    GraphServiceStatus,
    GraphWorkbenchResponse,
    IngestionJob,
    KgSpaceDetail,
    KgSpacesResponse,
    QuestionRequest,
    QuestionResponse,
    ReportComparisonRequest,
    ReportComparisonDetail,
    ReportComparisonResponse,
    ReportSpaceDetail,
    RequirementDetail,
    StandardDetail,
    StandardsResponse,
    SubgraphResponse,
    UpdateGraphEdgeRequest,
    UpdateGraphNodeRequest,
)
from services.ingestion_service import IngestionService


COMMAND_OPTIONS = {
    "standard-id": "standard_id",
    "document-type": "document_type",
    "build-graph": "build_graph",
    "parser-endpoint": "parser_endpoint",
    "source-format": "source_format",
    "normalization-policy": "normalization_policy",
}


def build_router(ingestion_service: IngestionService) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health", response_model=GraphServiceStatus)
    async def health(standard_id: str | None = Query(default=None)) -> GraphServiceStatus:
        return GraphServiceStatus(**ingestion_service.get_graph_service_status(standard_id))

    @router.get("/graphs", response_model=GraphWorkbenchResponse)
    @router.get("/v1/graphs", response_model=GraphWorkbenchResponse)
    async def get_graphs(
        standard_id: str = Query(...),
        label: str | None = Query(default=None),
        node_id: str | None = Query(default=None),
        preferred_node_types: str | None = Query(default=None),
        max_depth: int = Query(default=2, ge=1, le=4),
        max_nodes: int = Query(default=220, ge=0, le=3000),
    ) -> GraphWorkbenchResponse:
        try:
            return GraphWorkbenchResponse(
                **ingestion_service.get_graph_workbench(
                    standard_id,
                    label=label,
                    node_id=node_id,
                    preferred_node_types=_parse_csv_values(preferred_node_types),
                    max_depth=max_depth,
                    max_nodes=max_nodes,
                )
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/graph/label/popular", response_model=GraphLabelResponse)
    @router.get("/v1/graph/label/popular", response_model=GraphLabelResponse)
    async def graph_label_popular(
        standard_id: str | None = Query(default=None),
        limit: int = Query(default=120, ge=1, le=500),
    ) -> GraphLabelResponse:
        try:
            return GraphLabelResponse(items=ingestion_service.list_popular_graph_labels(standard_id, limit=limit))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/graph/label/search", response_model=GraphLabelResponse)
    @router.get("/v1/graph/label/search", response_model=GraphLabelResponse)
    async def graph_label_search(
        q: str = Query(...),
        standard_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> GraphLabelResponse:
        try:
            return GraphLabelResponse(items=ingestion_service.search_graph_labels(standard_id, q, limit=limit))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/graph/entity/exists", response_model=GraphEntityExistsResponse)
    @router.get("/v1/graph/entity/exists", response_model=GraphEntityExistsResponse)
    async def graph_entity_exists(
        standard_id: str = Query(...),
        name: str = Query(...),
        exclude_node_id: str | None = Query(default=None),
    ) -> GraphEntityExistsResponse:
        return GraphEntityExistsResponse(**ingestion_service.graph_entity_exists(standard_id, name, exclude_node_id=exclude_node_id))

    @router.post("/graph/entity/edit")
    @router.post("/v1/graph/entity/edit")
    async def graph_entity_edit(request: GraphEntityEditRequest) -> dict[str, Any]:
        try:
            return ingestion_service.edit_graph_entity(request)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @router.post("/graph/relation/edit")
    @router.post("/v1/graph/relation/edit")
    async def graph_relation_edit(request: GraphRelationEditRequest) -> dict[str, Any]:
        try:
            return ingestion_service.edit_graph_relation(request)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    @router.post("/v1/ingestions", response_model=IngestionJob, status_code=status.HTTP_202_ACCEPTED)
    async def create_ingestion(request: CreateIngestionJobRequest, background_tasks: BackgroundTasks) -> IngestionJob:
        try:
            return ingestion_service.create_job(request, background_tasks)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @router.get("/v1/ingestions/{job_id}", response_model=IngestionJob)
    async def get_ingestion(job_id: str) -> IngestionJob:
        job = ingestion_service.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} was not found.")
        return job

    @router.get("/v1/standards", response_model=StandardsResponse)
    async def list_standards() -> StandardsResponse:
        return StandardsResponse(items=ingestion_service.list_standards())

    @router.get("/v1/standards/{standard_id}", response_model=StandardDetail)
    async def get_standard(standard_id: str) -> StandardDetail:
        detail = ingestion_service.get_standard(standard_id)
        if detail is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Standard {standard_id} was not found.")
        return detail

    @router.get("/v1/standards/{standard_id}/subgraph", response_model=SubgraphResponse)
    async def get_standard_subgraph(standard_id: str, node_id: str | None = Query(default=None), depth: int = Query(default=2, ge=1, le=4)) -> SubgraphResponse:
        subgraph = ingestion_service.get_standard_subgraph(standard_id, node_id=node_id, depth=depth)
        if subgraph is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Subgraph for {standard_id} is not available yet.")
        return SubgraphResponse(**subgraph)

    @router.get("/v1/documents", response_model=DocumentsResponse)
    async def list_documents(status_filter: str | None = Query(default=None, alias="status"), q: str | None = Query(default=None)) -> DocumentsResponse:
        items = ingestion_service.list_documents()
        if status_filter:
            items = [item for item in items if item.status == status_filter]
        if q:
            needle = q.strip().lower()
            items = [
                item
                for item in items
                if needle in " ".join(
                    filter(None, [item.displayName, item.title, item.standardId, item.sourceName, item.documentId])
                ).lower()
            ]
        return DocumentsResponse(items=items)

    @router.get("/v1/documents/{document_id}/jobs", response_model=DocumentJobsResponse)
    async def get_document_jobs(document_id: str) -> DocumentJobsResponse:
        return DocumentJobsResponse(items=ingestion_service.list_document_jobs(document_id))

    @router.get("/v1/report-spaces/{document_id}", response_model=ReportSpaceDetail)
    async def get_report_space(document_id: str) -> ReportSpaceDetail:
        try:
            return ReportSpaceDetail(**ingestion_service.get_report_space_detail(document_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.post("/v1/report-spaces/{document_id}/comparisons", response_model=ReportComparisonDetail, status_code=status.HTTP_202_ACCEPTED)
    async def start_report_comparison(
        document_id: str,
        request: ReportComparisonRequest,
        background_tasks: BackgroundTasks,
    ) -> ReportComparisonDetail:
        try:
            return ReportComparisonDetail(**ingestion_service.start_report_comparison(document_id, request.standardId, background_tasks))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ResponseAPIError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @router.get("/v1/report-spaces/{document_id}/comparisons/{standard_id}", response_model=ReportComparisonDetail)
    async def get_report_comparison(document_id: str, standard_id: str) -> ReportComparisonDetail:
        try:
            return ReportComparisonDetail(**ingestion_service.get_report_comparison_detail(document_id, standard_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.post("/v1/report-spaces/{document_id}/units/{unit_uid}/compare", response_model=ReportComparisonResponse)
    async def compare_report_unit(document_id: str, unit_uid: str, request: ReportComparisonRequest) -> ReportComparisonResponse:
        try:
            return ReportComparisonResponse(**ingestion_service.compare_report_unit(document_id, unit_uid, request.standardId))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ResponseAPIError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @router.post("/v1/documents/upload", response_model=IngestionJob, status_code=status.HTTP_202_ACCEPTED)
    async def upload_document(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        command: str | None = Form(default=None),
        document_type: str = Form(default="standard"),
        source_format: str | None = Form(default=None),
        build_graph: bool = Form(default=True),
        normalization_policy: str = Form(default="auto"),
    ) -> IngestionJob:
        options = _parse_command_options(command)
        resolved_document_type = options.get("document_type", document_type)
        resolved_source_format = options.get("source_format") or source_format or _detect_source_format(file.filename or "")
        resolved_build_graph = _parse_bool(options.get("build_graph"), default=build_graph)
        resolved_normalization_policy = options.get("normalization_policy", normalization_policy)
        resolved_parser_endpoint = options.get("parser_endpoint")
        resolved_standard_id = options.get("standard_id")

        upload_path = ingestion_service.config.upload_path_for(file.filename or "upload.bin")
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        payload = await file.read()
        upload_path.write_bytes(payload)

        request = CreateIngestionJobRequest(
            documentType=resolved_document_type,
            sourcePath=str(upload_path),
            sourceFormat=resolved_source_format,
            parserEndpoint=resolved_parser_endpoint,
            normalizationPolicy=resolved_normalization_policy,
            buildGraph=resolved_build_graph,
            standardId=resolved_standard_id,
            metadata={
                "original_filename": file.filename,
                "upload_command": command,
            },
        )
        return ingestion_service.create_job(request, background_tasks)

    @router.post("/v1/documents/{document_id}/retry", response_model=IngestionJob, status_code=status.HTTP_202_ACCEPTED)
    async def retry_document(document_id: str, background_tasks: BackgroundTasks) -> IngestionJob:
        try:
            return ingestion_service.retry_document(document_id, background_tasks)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.delete("/v1/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_document(document_id: str) -> Response:
        deleted = ingestion_service.delete_document(document_id)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document {document_id} was not found.")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/v1/kg-spaces", response_model=KgSpacesResponse)
    async def list_kg_spaces() -> KgSpacesResponse:
        return KgSpacesResponse(items=ingestion_service.list_kg_spaces())

    @router.get("/v1/kg-spaces/{standard_id}", response_model=KgSpaceDetail)
    async def get_kg_space(standard_id: str) -> KgSpaceDetail:
        detail = ingestion_service.get_kg_space_detail(standard_id)
        if detail is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"KG space {standard_id} was not found.")
        return detail

    @router.get("/v1/kg-spaces/{standard_id}/search", response_model=GraphSearchResponse)
    async def search_kg_space(standard_id: str, q: str = Query(...), limit: int = Query(default=20, ge=1, le=100)) -> GraphSearchResponse:
        return GraphSearchResponse(items=ingestion_service.search_kg_nodes(standard_id, q, limit=limit))

    @router.get("/v1/kg-spaces/{standard_id}/subgraph", response_model=SubgraphResponse)
    async def get_kg_space_subgraph(standard_id: str, node_id: str | None = Query(default=None), depth: int = Query(default=2, ge=1, le=4)) -> SubgraphResponse:
        subgraph = ingestion_service.get_standard_subgraph(standard_id, node_id=node_id, depth=depth)
        if subgraph is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Subgraph for {standard_id} is not available.")
        return SubgraphResponse(**subgraph)

    @router.patch("/v1/kg-spaces/{standard_id}/nodes/{node_id}")
    async def patch_node(standard_id: str, node_id: str, payload: UpdateGraphNodeRequest) -> dict[str, Any]:
        try:
            return ingestion_service.update_graph_node(standard_id, node_id, payload.model_dump(exclude_unset=True))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.patch("/v1/kg-spaces/{standard_id}/edges/{edge_id}")
    async def patch_edge(standard_id: str, edge_id: str, payload: UpdateGraphEdgeRequest) -> dict[str, Any]:
        try:
            return ingestion_service.update_graph_edge(standard_id, edge_id, payload.model_dump(exclude_unset=True))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/v1/requirements/{requirement_id}", response_model=RequirementDetail)
    async def get_requirement(requirement_id: str) -> RequirementDetail:
        requirement = ingestion_service.get_requirement_detail(requirement_id)
        if requirement is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Requirement {requirement_id} was not found.")
        return requirement

    @router.post("/v1/qa/ask", response_model=QuestionResponse)
    async def ask_question(request: QuestionRequest) -> QuestionResponse:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=f"QA API is not implemented yet for question: {request.question}")

    @router.post("/v1/comparisons", response_model=ComparisonSummary, status_code=status.HTTP_202_ACCEPTED)
    async def create_comparison(request: CreateComparisonRequest) -> ComparisonSummary:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Comparison API is not implemented yet for report {request.reportDocumentId}.",
        )

    @router.get("/v1/comparisons/{comparison_id}", response_model=ComparisonSummary)
    async def get_comparison(comparison_id: str) -> ComparisonSummary:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=f"Comparison {comparison_id} is not implemented yet.")

    @router.get("/v1/comparisons/{comparison_id}/items")
    async def list_comparison_items(comparison_id: str) -> dict:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=f"Comparison items for {comparison_id} are not implemented yet.")

    return router


def _parse_csv_values(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_command_options(command: str | None) -> dict[str, str]:
    if not command:
        return {}
    tokens = shlex.split(command)
    parsed: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("--"):
            index += 1
            continue
        key = token[2:]
        mapped = COMMAND_OPTIONS.get(key)
        if not mapped:
            index += 1
            continue
        if index + 1 < len(tokens) and not tokens[index + 1].startswith("--"):
            parsed[mapped] = tokens[index + 1]
            index += 2
            continue
        parsed[mapped] = "true"
        index += 1
    return parsed


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _detect_source_format(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        return "docx"
    if suffix == ".doc":
        return "doc"
    return "pdf"
