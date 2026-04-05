from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
import re
import uuid
import zipfile

import httpx
from fastapi import BackgroundTasks

from adapters.mineru_client import MinerUApiError, MinerUClient
from core.config import AppConfig
from models.schemas import CreateIngestionJobRequest, IngestionJob, RequirementDetail, StandardDetail
from repositories.job_store import JobStore
from repositories.standard_registry import StandardRegistry
from services.normalization import NormalizationService
from services.standard_pipeline import StandardPipelineService


SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")


class IngestionService:
    def __init__(
        self,
        config: AppConfig,
        job_store: JobStore,
        registry: StandardRegistry,
        mineru_client: MinerUClient,
        normalization_service: NormalizationService,
        standard_pipeline_service: StandardPipelineService,
    ) -> None:
        self.config = config
        self.job_store = job_store
        self.registry = registry
        self.mineru_client = mineru_client
        self.normalization_service = normalization_service
        self.standard_pipeline_service = standard_pipeline_service

    def create_job(self, request: CreateIngestionJobRequest, background_tasks: BackgroundTasks) -> IngestionJob:
        source_path = self._resolve_source_path(request.sourcePath)
        now = datetime.now(UTC)
        document_id = self._document_id(source_path)
        job = IngestionJob(
            jobId=str(uuid.uuid4()),
            status="queued",
            documentId=document_id,
            documentType=request.documentType,
            parserProvider=request.parserProvider,
            parserEndpoint=request.parserEndpoint or self.config.mineru.default_endpoint,
            progress=0.0,
            result={
                "source_path": str(source_path),
                "source_name": source_path.name,
                "build_graph_requested": request.buildGraph,
                "metadata": request.metadata,
            },
            createdAt=now,
            updatedAt=now,
        )
        self.job_store.save(job)
        background_tasks.add_task(self._run_job, job.jobId, request)
        return job

    def get_job(self, job_id: str) -> IngestionJob | None:
        return self.job_store.load(job_id)

    def list_standards(self) -> list:
        return self.registry.list()

    def get_standard(self, standard_id: str) -> StandardDetail | None:
        return self.registry.get(standard_id)

    async def _run_job(self, job_id: str, request: CreateIngestionJobRequest) -> None:
        job = self._require_job(job_id)
        source_path = self._resolve_source_path(request.sourcePath)
        job.status = "running"
        job.progress = 0.05
        self._touch(job)

        work_dir = self.config.download_work_dir_for(job.documentId, job.jobId)
        work_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir = self.config.artifact_dir_for(job.documentId)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        detected_standard = self._detect_standard(source_path) if request.documentType == "standard" else None
        graph_space_dir = self.config.kg_space_dir_for(detected_standard[0]) if detected_standard else None
        if graph_space_dir is not None:
            job.result["graph_space_dir"] = str(graph_space_dir)

        try:
            normalization = self.normalization_service.normalize(source_path, request, work_dir)
            job.normalizedFormat = normalization.normalized_format
            job.preprocessingActions = normalization.preprocessing_actions
            job.result["normalized_path"] = str(normalization.normalized_path)
            job.progress = 0.15
            self._touch(job)

            batch_id, upload_url = await self.mineru_client.request_upload_url(
                endpoint=job.parserEndpoint,
                file_name=normalization.normalized_path.name,
                data_id=job.documentId,
            )
            job.result["batch_id"] = batch_id
            job.result["upload_url"] = upload_url.split("?", 1)[0]
            job.progress = 0.3
            self._touch(job)

            await self.mineru_client.upload_file(upload_url, normalization.normalized_path)
            job.progress = 0.45
            self._touch(job)

            result_entry = await self._poll_for_result(job, batch_id, normalization.normalized_path.name)
            full_zip_url = result_entry.get("full_zip_url")
            if not full_zip_url:
                raise MinerUApiError("MinerU finished without returning full_zip_url.")

            zip_path = work_dir / "mineru_result.zip"
            await self.mineru_client.download_result_zip(full_zip_url, zip_path)
            self._extract_zip(zip_path, artifact_dir)

            job.result["artifact_dir"] = str(artifact_dir)
            job.result["result_zip_path"] = str(zip_path)
            job.result["mineru_result"] = result_entry
            job.result["artifacts"] = self._artifact_index(artifact_dir)

            if detected_standard:
                self._upsert_standard_detail(
                    source_path,
                    job,
                    detected_standard,
                    artifact_dir,
                    "building" if request.buildGraph else "not_built",
                    graph_space_dir=graph_space_dir,
                )

            if request.buildGraph and request.documentType == "standard":
                job.progress = 0.88
                self._touch(job)
                self._build_standard_graph(job, source_path, artifact_dir, detected_standard, graph_space_dir)

            if detected_standard and not request.buildGraph:
                self._upsert_standard_detail(
                    source_path,
                    job,
                    detected_standard,
                    artifact_dir,
                    "not_built",
                    graph_space_dir=graph_space_dir,
                )

            job.progress = 1.0
            job.status = "succeeded"
            self._touch(job)
        except Exception as exc:
            if detected_standard:
                self._upsert_standard_detail(
                    source_path,
                    job,
                    detected_standard,
                    artifact_dir,
                    "failed",
                    graph_space_dir=graph_space_dir,
                )
            job.status = "failed"
            job.error = str(exc)
            job.progress = max(job.progress, 0.01)
            self._touch(job)

    def _build_standard_graph(
        self,
        job: IngestionJob,
        source_path: Path,
        artifact_dir: Path,
        detected_standard: tuple[str, str, str] | None,
        graph_space_dir: Path | None,
    ) -> None:
        if not detected_standard:
            job.result["graph_warning"] = "Standard ID could not be detected from filename; graph build skipped."
            return
        if graph_space_dir is None:
            job.result["graph_warning"] = "Graph space directory could not be resolved; graph build skipped."
            return

        standard_id = detected_standard[0]
        output = self.standard_pipeline_service.run(artifact_dir, standard_id)
        files = self.standard_pipeline_service.write_outputs(
            graph_space_dir,
            output,
            artifact_dir=artifact_dir,
            standard_uid=standard_id,
            document_id=job.documentId,
        )
        job.result["graph"] = {
            "standard_id": standard_id,
            "graph_space_dir": str(graph_space_dir),
            "metrics": output.metrics,
            "warnings": output.extraction_warnings,
            "files": {key: str(path) for key, path in files.items()},
        }
        job.result["graph_space"] = {
            "dir": str(graph_space_dir),
            "files": self._artifact_index(graph_space_dir),
        }
        job.result["artifacts"] = self._artifact_index(artifact_dir)
        self._upsert_standard_detail(
            source_path,
            job,
            detected_standard,
            artifact_dir,
            "ready",
            graph_space_dir=graph_space_dir,
        )

    async def _poll_for_result(self, job: IngestionJob, batch_id: str, file_name: str) -> dict:
        deadline = asyncio.get_running_loop().time() + self.config.mineru.poll_timeout_seconds
        last_result: dict | None = None
        consecutive_poll_errors = 0
        while asyncio.get_running_loop().time() < deadline:
            try:
                batch = await self.mineru_client.get_batch_result(job.parserEndpoint, batch_id)
                consecutive_poll_errors = 0
            except httpx.HTTPError as exc:
                consecutive_poll_errors += 1
                job.result["last_poll_error"] = str(exc)
                if consecutive_poll_errors >= self.config.mineru.poll_request_retries:
                    raise
                self._touch(job)
                await asyncio.sleep(self.config.mineru.retry_backoff_seconds * consecutive_poll_errors)
                continue

            extract_result = batch.get("extract_result") or []
            entry = self._select_result(extract_result, job.documentId, file_name)
            if entry is None:
                last_result = {"state": "pending", "err_msg": "Result not found yet"}
            else:
                last_result = entry
                state = entry.get("state")
                job.result["mineru_state"] = state
                job.result["mineru_progress"] = entry.get("extract_progress", {})
                if state == "done":
                    return entry
                if state == "failed":
                    raise MinerUApiError(entry.get("err_msg") or "MinerU reported a failed extraction state.")
            job.progress = min(0.85, max(job.progress, 0.45))
            self._touch(job)
            await asyncio.sleep(self.config.mineru.poll_interval_seconds)
        raise TimeoutError(f"Polling MinerU batch {batch_id} timed out. Last result: {last_result}")

    def _detect_standard(self, source_path: Path) -> tuple[str, str, str] | None:
        return self.registry.detect_from_filename(source_path.name)

    def _upsert_standard_detail(
        self,
        source_path: Path,
        job: IngestionJob,
        detected_standard: tuple[str, str, str],
        artifact_dir: Path,
        graph_status: str,
        *,
        graph_space_dir: Path | None = None,
    ) -> None:
        standard_id, code, title = detected_standard
        existing = self.registry.get(standard_id)
        aliases = set(existing.aliases if existing else [])
        aliases.add(source_path.name)
        resolved_graph_space_dir = str(graph_space_dir) if graph_space_dir else self._graph_space_path(existing)
        detail = StandardDetail(
            standardId=standard_id,
            code=code,
            year=standard_id.split(":")[-1],
            title=(existing.title if existing else title),
            aliases=sorted(aliases),
            effectiveDate=existing.effectiveDate if existing else None,
            documentId=job.documentId,
            artifactDir=str(artifact_dir),
            graphSpaceDir=resolved_graph_space_dir,
            graphStatus=graph_status,
            latestJobId=job.jobId,
        )
        self.registry.upsert(detail)

    def _require_job(self, job_id: str) -> IngestionJob:
        job = self.job_store.load(job_id)
        if job is None:
            raise FileNotFoundError(f"Job {job_id} does not exist.")
        return job

    def _resolve_source_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self.config.root_dir / candidate
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"Source file was not found: {candidate}")
        return candidate.resolve()

    def _document_id(self, source_path: Path) -> str:
        base = SAFE_ID_RE.sub("-", source_path.stem.lower()).strip("-")
        suffix = uuid.uuid4().hex[:8]
        return f"{base[:80]}-{suffix}"

    def _touch(self, job: IngestionJob) -> None:
        job.updatedAt = datetime.now(UTC)
        self.job_store.save(job)

    def _select_result(self, extract_result: list[dict], data_id: str, file_name: str) -> dict | None:
        for entry in extract_result:
            if entry.get("data_id") == data_id:
                return entry
        for entry in extract_result:
            if entry.get("file_name") == file_name:
                return entry
        return extract_result[0] if extract_result else None

    def _extract_zip(self, zip_path: Path, artifact_dir: Path) -> None:
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(artifact_dir)

    def _artifact_index(self, artifact_dir: Path) -> dict[str, str]:
        artifacts: dict[str, str] = {}
        for path in artifact_dir.rglob("*"):
            if path.is_file():
                artifacts[path.relative_to(artifact_dir).as_posix()] = str(path)
        return artifacts

    def get_standard_subgraph(self, standard_id: str, node_id: str | None = None, depth: int = 2) -> dict[str, list[dict]] | None:
        detail = self.registry.get(standard_id)
        graph_space_dir = self._resolve_graph_space_dir(detail)
        if graph_space_dir is None:
            return None

        nodes_path = graph_space_dir / 'graph_nodes.json'
        edges_path = graph_space_dir / 'graph_edges.json'
        if not nodes_path.exists() or not edges_path.exists():
            return None

        nodes = json.loads(nodes_path.read_text(encoding='utf-8'))
        edges = json.loads(edges_path.read_text(encoding='utf-8'))
        if not node_id:
            return {'nodes': nodes, 'edges': edges}

        node_map = {node['node_uid']: node for node in nodes}
        if node_id not in node_map:
            return None
        frontier = {node_id}
        visited = {node_id}
        selected_edges: list[dict] = []
        for _ in range(max(1, depth)):
            next_frontier: set[str] = set()
            for edge in edges:
                source_uid = edge.get('source_uid')
                target_uid = edge.get('target_uid')
                if source_uid in frontier or target_uid in frontier:
                    selected_edges.append(edge)
                    if source_uid and source_uid not in visited:
                        next_frontier.add(source_uid)
                    if target_uid and target_uid not in visited:
                        next_frontier.add(target_uid)
            if not next_frontier:
                break
            visited.update(next_frontier)
            frontier = next_frontier
        selected_nodes = [node_map[uid] for uid in visited if uid in node_map]
        deduped_edges = {edge['edge_uid']: edge for edge in selected_edges}
        return {'nodes': selected_nodes, 'edges': list(deduped_edges.values())}

    def get_requirement_detail(self, requirement_id: str) -> RequirementDetail | None:
        standard_id = self._standard_id_from_requirement(requirement_id)
        if standard_id is None:
            return None
        detail = self.registry.get(standard_id)
        graph_space_dir = self._resolve_graph_space_dir(detail)
        if graph_space_dir is None:
            return None

        requirements_path = graph_space_dir / 'requirements.json'
        if not requirements_path.exists():
            return None
        requirements = json.loads(requirements_path.read_text(encoding='utf-8'))
        for requirement in requirements:
            if requirement.get('requirement_uid') != requirement_id:
                continue
            return RequirementDetail(
                requirementId=requirement['requirement_uid'],
                standardId=requirement['standard_uid'],
                clauseRef=requirement['clause_ref'],
                requirementText=requirement['requirement_text'],
                modality=requirement['modality'],
                applicabilityRule=requirement.get('applicability_rule'),
                judgementCriteria=requirement.get('judgement_criteria', []),
                evidenceExpected=requirement.get('evidence_expected', []),
                citations=requirement.get('cited_targets', []),
                sourceSpans=[
                    {
                        'pageSpan': requirement.get('source_page_span'),
                        'bbox': requirement.get('source_bbox'),
                    }
                ],
            )
        return None

    def _standard_id_from_requirement(self, requirement_id: str) -> str | None:
        parts = requirement_id.split(':')
        if len(parts) < 2:
            return None
        return ':'.join(parts[:2])

    def _resolve_graph_space_dir(self, detail: StandardDetail | None) -> Path | None:
        if detail is None:
            return None
        graph_space = self._graph_space_path(detail)
        if not graph_space:
            return None
        path = Path(graph_space)
        return path if path.exists() else None

    def _graph_space_path(self, detail: StandardDetail | None) -> str | None:
        if detail is None:
            return None
        return detail.graphSpaceDir
