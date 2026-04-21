from __future__ import annotations

import asyncio
from collections import Counter, defaultdict, deque
import json
from datetime import UTC, datetime
from pathlib import Path
import re
import shutil
import uuid
import zipfile
from typing import Any, Sequence

import httpx
from fastapi import BackgroundTasks

from adapters.mineru_client import MinerUApiError, MinerUClient
from core.config import AppConfig
from models.schemas import (
    CreateIngestionJobRequest,
    DocumentSummary,
    GraphEntityEditRequest,
    GraphLabelItem,
    GraphSearchItem,
    GraphRelationEditRequest,
    IngestionJob,
    KgSpaceDetail,
    KgSpaceSummary,
    RequirementDetail,
    StandardDetail,
)
from repositories.job_store import JobStore
from repositories.standard_registry import StandardRegistry
from services.normalization import NormalizationService
from services.report_pipeline import ReportPipelineService
from services.standard_pipeline import StandardPipelineService


SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")
STANDARD_ID_RE = re.compile(r"^(?P<prefix>[a-z]+)(?P<number>\d+):(?P<year>\d{2,4})$", re.IGNORECASE)
KG_SPACE_DIR_RE = re.compile(r"^(?P<prefix>[a-z]+\d+)-(?P<year>\d{2,4})$", re.IGNORECASE)

GRAPH_WORKBENCH_DEFAULT_DEPTH = 2
GRAPH_WORKBENCH_MAX_DEPTH = 4
GRAPH_WORKBENCH_DEFAULT_NODES = 220
GRAPH_WORKBENCH_MAX_NODES = 3000


class IngestionService:
    def __init__(
        self,
        config: AppConfig,
        job_store: JobStore,
        registry: StandardRegistry,
        mineru_client: MinerUClient,
        normalization_service: NormalizationService,
        standard_pipeline_service: StandardPipelineService,
        report_pipeline_service: ReportPipelineService,
    ) -> None:
        self.config = config
        self.job_store = job_store
        self.registry = registry
        self.mineru_client = mineru_client
        self.normalization_service = normalization_service
        self.standard_pipeline_service = standard_pipeline_service
        self.report_pipeline_service = report_pipeline_service

    def create_job(self, request: CreateIngestionJobRequest, background_tasks: BackgroundTasks) -> IngestionJob:
        source_path = self._resolve_source_path(request.sourcePath)
        now = datetime.now(UTC)
        document_id = request.documentId or self._document_id(source_path)
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
                "source_format": request.sourceFormat,
                "build_graph_requested": request.buildGraph,
                "normalization_policy": request.normalizationPolicy,
                "standard_id": request.standardId,
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

    def list_document_jobs(self, document_id: str) -> list[IngestionJob]:
        return sorted(self.job_store.list_by_document(document_id), key=lambda item: item.updatedAt, reverse=True)

    def list_documents(self) -> list[DocumentSummary]:
        documents: dict[str, DocumentSummary] = {}

        for detail in self.registry.list_details():
            document_id = detail.documentId or detail.standardId
            source_path = self._guess_source_path(detail)
            updated_at = self._path_updated_at(self._first_existing_path(detail.graphSpaceDir, detail.artifactDir))
            documents[document_id] = DocumentSummary(
                documentId=document_id,
                displayName=detail.aliases[0] if detail.aliases else detail.title,
                standardId=detail.standardId,
                title=detail.title,
                documentType="standard",
                sourcePath=str(source_path) if source_path else None,
                sourceName=source_path.name if source_path else (detail.aliases[0] if detail.aliases else None),
                sourceFormat=self._detect_source_format(source_path) if source_path else None,
                status=self._document_status_from_graph(detail.graphStatus),
                graphStatus=detail.graphStatus,
                latestJobId=detail.latestJobId,
                artifactDir=detail.artifactDir,
                graphSpaceDir=detail.graphSpaceDir,
                createdAt=None,
                updatedAt=updated_at,
            )

        jobs = sorted(self.job_store.list(), key=lambda item: item.updatedAt, reverse=True)
        for job in jobs:
            summary = documents.get(job.documentId)
            detail = self.registry.find_by_document_id(job.documentId)
            if summary is None:
                summary = DocumentSummary(
                    documentId=job.documentId,
                    displayName=job.result.get("source_name") or job.documentId,
                    standardId=(detail.standardId if detail else None) or job.result.get("standard_id"),
                    title=detail.title if detail else None,
                    documentType=job.documentType,
                    sourcePath=job.result.get("source_path"),
                    sourceName=job.result.get("source_name"),
                    sourceFormat=job.result.get("source_format"),
                    status="idle",
                    graphStatus=detail.graphStatus if detail else None,
                    parserProvider=job.parserProvider,
                    artifactDir=(detail.artifactDir if detail else None) or job.result.get("artifact_dir"),
                    graphSpaceDir=(detail.graphSpaceDir if detail else None) or job.result.get("graph_space_dir"),
                    metadata=job.result.get("metadata") or {},
                    createdAt=job.createdAt,
                    updatedAt=job.updatedAt,
                )
                documents[job.documentId] = summary

            summary.documentType = job.documentType
            summary.sourcePath = summary.sourcePath or job.result.get("source_path")
            summary.sourceName = summary.sourceName or job.result.get("source_name")
            summary.sourceFormat = summary.sourceFormat or job.result.get("source_format")
            summary.parserProvider = job.parserProvider
            summary.latestJobId = job.jobId
            summary.latestError = job.error
            summary.progress = job.progress
            summary.metadata = job.result.get("metadata") or summary.metadata
            summary.createdAt = summary.createdAt or job.createdAt
            summary.updatedAt = max(filter(None, [summary.updatedAt, job.updatedAt]), default=job.updatedAt)
            summary.artifactDir = summary.artifactDir or job.result.get("artifact_dir")
            summary.graphSpaceDir = summary.graphSpaceDir or job.result.get("graph_space_dir")
            if detail:
                summary.standardId = summary.standardId or detail.standardId
                summary.title = summary.title or detail.title
                summary.graphStatus = detail.graphStatus
                summary.displayName = summary.displayName or detail.title
            if job.status in {"queued", "running", "failed"}:
                summary.status = job.status
            elif summary.graphStatus == "ready":
                summary.status = "ready"
            else:
                summary.status = "succeeded"

        return sorted(
            documents.values(),
            key=lambda item: item.updatedAt or item.createdAt or datetime.fromtimestamp(0, tz=UTC),
            reverse=True,
        )

    def retry_document(self, document_id: str, background_tasks: BackgroundTasks) -> IngestionJob:
        jobs = self.list_document_jobs(document_id)
        latest_job = jobs[0] if jobs else None
        detail = self.registry.find_by_document_id(document_id)
        source_path = latest_job.result.get("source_path") if latest_job else None
        if not source_path and detail is not None:
            guessed = self._guess_source_path(detail)
            source_path = str(guessed) if guessed else None
        if not source_path:
            raise FileNotFoundError(f"Source file for document {document_id} was not found.")

        request = CreateIngestionJobRequest(
            documentType=latest_job.documentType if latest_job else "standard",
            sourcePath=source_path,
            sourceFormat=(latest_job.result.get("source_format") if latest_job else None) or self._detect_source_format(Path(source_path)),
            parserProvider=latest_job.parserProvider if latest_job else "mineru_api",
            parserEndpoint=latest_job.parserEndpoint if latest_job else None,
            normalizationPolicy=(latest_job.result.get("normalization_policy") if latest_job else None) or "auto",
            buildGraph=bool((latest_job.result.get("build_graph_requested") if latest_job else True)),
            documentId=document_id,
            standardId=(latest_job.result.get("standard_id") if latest_job else None) or (detail.standardId if detail else None),
            metadata={
                **((latest_job.result.get("metadata") if latest_job else None) or {}),
                "retry_of": latest_job.jobId if latest_job else "manual",
            },
        )
        return self.create_job(request, background_tasks)

    def delete_document(self, document_id: str) -> bool:
        detail = self.registry.find_by_document_id(document_id)
        jobs = self.list_document_jobs(document_id)
        deleted = bool(detail or jobs)

        for job in jobs:
            if job.documentType != "report":
                continue
            report_space_dir = (
                job.result.get("report_space_dir")
                or ((job.result.get("report_space") or {}).get("dir") if isinstance(job.result.get("report_space"), dict) else None)
            )
            if isinstance(report_space_dir, str) and report_space_dir:
                self._safe_remove_tree(report_space_dir, self.config.report_spaces_dir)

        for job in jobs:
            self.job_store.delete(job.jobId)

        if detail is not None:
            self.registry.remove(detail.standardId)
            self._safe_remove_tree(detail.artifactDir, self.config.artifacts_dir)
            self._safe_remove_tree(detail.graphSpaceDir, self.config.kg_spaces_dir)

        for job in jobs:
            source_path = job.result.get("source_path")
            if source_path:
                self._safe_remove_file(source_path, self.config.uploads_dir)

        return deleted

    def list_standards(self) -> list:
        return self.registry.list()

    def get_standard(self, standard_id: str) -> StandardDetail | None:
        return self.registry.get(standard_id)

    def list_kg_spaces(self) -> list[KgSpaceSummary]:
        items: dict[str, KgSpaceSummary] = {}
        known_dirs: set[Path] = set()

        for detail in self.registry.list_details():
            summary = self._build_kg_space_summary(detail)
            if summary is not None:
                items[summary.standardId] = summary
                if detail.graphSpaceDir:
                    known_dirs.add(Path(detail.graphSpaceDir).resolve())

        if self.config.kg_spaces_dir.exists():
            for path in self.config.kg_spaces_dir.iterdir():
                if not path.is_dir() or path.resolve() in known_dirs:
                    continue
                if not (path / "graph_nodes.json").exists() or not (path / "graph_edges.json").exists():
                    continue
                standard_id, code = self._standard_id_from_space_dir(path.name)
                node_types, edge_types, requirement_count, updated_at = self._graph_stats(path)
                items[standard_id] = KgSpaceSummary(
                    standardId=standard_id,
                    code=code,
                    title=path.name,
                    graphStatus="ready",
                    graphSpaceDir=str(path),
                    nodeCount=sum(node_types.values()),
                    edgeCount=sum(edge_types.values()),
                    requirementCount=requirement_count,
                    nodeTypes=node_types,
                    edgeTypes=edge_types,
                    updatedAt=updated_at,
                )

        return sorted(items.values(), key=lambda item: item.updatedAt or datetime.fromtimestamp(0, tz=UTC), reverse=True)

    def get_kg_space_detail(self, standard_id: str) -> KgSpaceDetail | None:
        detail = self.registry.get(standard_id)
        if detail is None:
            fallback = self.config.kg_space_dir_for(standard_id)
            if not fallback.exists():
                return None
            code = standard_id.split(":", 1)[0].upper()
            node_types, edge_types, requirement_count, updated_at = self._graph_stats(fallback)
            return KgSpaceDetail(
                standardId=standard_id,
                code=code,
                title=fallback.name,
                graphStatus="ready",
                graphSpaceDir=str(fallback),
                nodeCount=sum(node_types.values()),
                edgeCount=sum(edge_types.values()),
                requirementCount=requirement_count,
                nodeTypes=node_types,
                edgeTypes=edge_types,
                updatedAt=updated_at,
                files=self._list_relative_files(fallback),
            )

        summary = self._build_kg_space_summary(detail)
        if summary is None:
            return None
        return KgSpaceDetail(
            **summary.model_dump(),
            aliases=detail.aliases,
            documentId=detail.documentId,
            files=self._list_relative_files(Path(detail.graphSpaceDir)) if detail.graphSpaceDir else [],
        )

    def search_kg_nodes(self, standard_id: str, query: str, limit: int = 20) -> list[GraphSearchItem]:
        graph_space_dir = self._resolve_graph_space_dir(self.registry.get(standard_id)) or self.config.kg_space_dir_for(standard_id)
        nodes_path = graph_space_dir / "graph_nodes.json"
        if not nodes_path.exists():
            return []

        needle = query.strip().lower()
        if not needle:
            return []

        nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
        matches: list[GraphSearchItem] = []
        for node in nodes:
            haystack = " ".join(
                str(value)
                for value in [node.get("node_uid"), node.get("label"), node.get("text_content")]
                if value
            ).lower()
            if needle not in haystack:
                continue
            excerpt = node.get("text_content") or node.get("label") or node.get("node_uid")
            matches.append(
                GraphSearchItem(
                    nodeId=node.get("node_uid", ""),
                    nodeType=node.get("node_type", "unknown"),
                    label=node.get("label") or node.get("node_uid") or "unnamed",
                    excerpt=(excerpt[:180] + "...") if excerpt and len(excerpt) > 180 else excerpt,
                )
            )
            if len(matches) >= limit:
                break
        return matches

    def update_graph_node(self, standard_id: str, node_id: str, payload: dict) -> dict:
        nodes_path, _ = self._graph_paths_for_standard(standard_id)
        nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
        for node in nodes:
            if node.get("node_uid") != node_id:
                continue
            if "label" in payload:
                node["label"] = payload["label"]
            if "textContent" in payload:
                node["text_content"] = payload["textContent"]
            if "nodeType" in payload:
                node["node_type"] = payload["nodeType"]
            if "properties" in payload:
                node["properties"] = payload["properties"]
            nodes_path.write_text(json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return node
        raise FileNotFoundError(f"Node {node_id} was not found in {standard_id}.")

    def update_graph_edge(self, standard_id: str, edge_id: str, payload: dict) -> dict:
        nodes_path, edges_path = self._graph_paths_for_standard(standard_id)
        nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
        node_ids = {node.get("node_uid") for node in nodes}
        edges = json.loads(edges_path.read_text(encoding="utf-8"))
        for edge in edges:
            if edge.get("edge_uid") != edge_id:
                continue
            if "sourceUid" in payload and payload["sourceUid"] not in node_ids:
                raise FileNotFoundError(f"Source node {payload['sourceUid']} does not exist.")
            if "targetUid" in payload and payload["targetUid"] not in node_ids:
                raise FileNotFoundError(f"Target node {payload['targetUid']} does not exist.")
            if "edgeType" in payload:
                edge["edge_type"] = payload["edgeType"]
            if "sourceUid" in payload:
                edge["source_uid"] = payload["sourceUid"]
            if "targetUid" in payload:
                edge["target_uid"] = payload["targetUid"]
            if "properties" in payload:
                edge["properties"] = payload["properties"]
            edges_path.write_text(json.dumps(edges, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return edge
        raise FileNotFoundError(f"Edge {edge_id} was not found in {standard_id}.")

    def get_graph_service_status(self, standard_id: str | None = None) -> dict[str, Any]:
        selected_space = self.get_kg_space_detail(standard_id) if standard_id else None
        return {
            "status": "healthy",
            "workingDirectory": str(self.config.root_dir),
            "dataDirectory": str(self.config.data_dir),
            "graphSpaceDirectory": str(self.config.kg_spaces_dir),
            "uploadsDirectory": str(self.config.uploads_dir),
            "configuration": {
                "llmProvider": self.config.llm.provider if self.config.llm.enabled else "disabled",
                "llmModel": self.config.llm.model if self.config.llm.enabled else None,
                "llmBaseUrl": self.config.llm.base_url if self.config.llm.enabled else None,
                "embeddingProvider": self.config.embedding.provider if self.config.embedding.enabled else "disabled",
                "embeddingModel": self.config.embedding.model if self.config.embedding.enabled else None,
                "embeddingDimensions": self.config.embedding.dimensions if self.config.embedding.enabled else None,
                "graphExtractionMode": self.config.knowledge_graph.extraction_mode,
                "materializeGraph": self.config.knowledge_graph.materialize_graph,
                "postgresEnabled": self.config.postgres.enabled,
                "graphSpaceCount": len(self.list_kg_spaces()),
            },
            "graphLimits": {
                "defaultDepth": GRAPH_WORKBENCH_DEFAULT_DEPTH,
                "maxDepth": GRAPH_WORKBENCH_MAX_DEPTH,
                "defaultNodes": GRAPH_WORKBENCH_DEFAULT_NODES,
                "maxNodes": GRAPH_WORKBENCH_MAX_NODES,
            },
            "selectedSpace": selected_space.model_dump() if selected_space else None,
        }

    def list_popular_graph_labels(self, standard_id: str | None = None, limit: int = 300) -> list[GraphLabelItem]:
        items: list[GraphLabelItem] = []
        for candidate_standard_id in self._iter_workbench_spaces(standard_id):
            nodes, edges, _ = self._load_graph_records(candidate_standard_id)
            degrees = self._build_degree_map(nodes, edges)
            for node in nodes:
                node_id = str(node.get("node_uid") or "")
                if not node_id:
                    continue
                items.append(
                    GraphLabelItem(
                        standardId=candidate_standard_id,
                        nodeId=node_id,
                        label=self._graph_node_label(node),
                        nodeType=str(node.get("node_type") or "unknown"),
                        degree=degrees.get(node_id, 0),
                        excerpt=self._graph_node_excerpt(node),
                    )
                )
        items.sort(key=lambda item: (-item.degree, item.label.lower(), item.standardId, item.nodeId))
        return items[:limit]

    def search_graph_labels(self, standard_id: str | None, query: str, limit: int = 50) -> list[GraphLabelItem]:
        needle = self._normalize_graph_query(query)
        if not needle:
            return []

        scored_items: list[tuple[int, int, GraphLabelItem]] = []
        for candidate_standard_id in self._iter_workbench_spaces(standard_id):
            nodes, edges, _ = self._load_graph_records(candidate_standard_id)
            degrees = self._build_degree_map(nodes, edges)
            for node in nodes:
                score = self._match_label_search_score(node, needle)
                if score <= 0:
                    continue
                node_id = str(node.get("node_uid") or "")
                if not node_id:
                    continue
                scored_items.append(
                    (
                        score,
                        degrees.get(node_id, 0),
                        GraphLabelItem(
                            standardId=candidate_standard_id,
                            nodeId=node_id,
                            label=self._graph_node_label(node),
                            nodeType=str(node.get("node_type") or "unknown"),
                            degree=degrees.get(node_id, 0),
                            excerpt=self._graph_node_excerpt(node),
                        ),
                    )
                )
        scored_items.sort(key=lambda item: (-item[0], -item[1], item[2].label.lower(), item[2].standardId))
        return [item[2] for item in scored_items[:limit]]

    def graph_entity_exists(self, standard_id: str, name: str, exclude_node_id: str | None = None) -> dict[str, Any]:
        normalized_name = self._normalize_graph_query(name)
        if not normalized_name:
            return {"exists": False, "nodeId": None, "standardId": standard_id}

        nodes, _, _ = self._load_graph_records(standard_id)
        for node in nodes:
            node_id = str(node.get("node_uid") or "")
            if exclude_node_id and node_id == exclude_node_id:
                continue
            if self._normalize_graph_query(self._graph_node_label(node)) == normalized_name:
                return {"exists": True, "nodeId": node_id, "standardId": standard_id}
        return {"exists": False, "nodeId": None, "standardId": standard_id}

    def get_graph_workbench(
        self,
        standard_id: str,
        *,
        label: str | None = None,
        node_id: str | None = None,
        preferred_node_types: Sequence[str] | None = None,
        max_depth: int = GRAPH_WORKBENCH_DEFAULT_DEPTH,
        max_nodes: int = GRAPH_WORKBENCH_DEFAULT_NODES,
    ) -> dict[str, Any]:
        max_depth = max(1, min(max_depth, GRAPH_WORKBENCH_MAX_DEPTH))
        unbounded_nodes = max_nodes <= 0
        max_nodes = 0 if unbounded_nodes else max(1, min(max_nodes, GRAPH_WORKBENCH_MAX_NODES))
        nodes, edges, _ = self._load_graph_records(standard_id)
        if not nodes:
            return {
                "standardId": standard_id,
                "rootNodeId": None,
                "maxDepth": max_depth,
                "maxNodes": max_nodes,
                "isTruncated": False,
                "nodes": [],
                "edges": [],
            }

        node_map = {str(node.get("node_uid")): node for node in nodes if node.get("node_uid")}
        node_type_index = {
            node_key: self._normalize_graph_query(str(node.get("node_type") or ""))
            for node_key, node in node_map.items()
        }
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            source_uid = str(edge.get("source_uid") or "")
            target_uid = str(edge.get("target_uid") or "")
            if source_uid in node_map and target_uid in node_map:
                adjacency[source_uid].append(target_uid)
                adjacency[target_uid].append(source_uid)

        degrees = self._build_degree_map(nodes, edges)
        preferred_type_set: set[str] = set()
        for raw_type in preferred_node_types or []:
            normalized_type = self._normalize_graph_query(raw_type)
            if normalized_type:
                preferred_type_set.add(normalized_type)
        start_node = self._resolve_graph_start_node(nodes, degrees, standard_id, label, node_id)
        if start_node is None:
            raise FileNotFoundError(f"Starting node for {standard_id} was not found.")

        start_node_id = str(start_node.get("node_uid"))
        visited: set[str] = {start_node_id}
        ordered: list[str] = [start_node_id]
        queue: deque[tuple[str, int]] = deque([(start_node_id, 0)])

        while queue:
            current_node_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            neighbors = sorted(
                adjacency.get(current_node_id, []),
                key=lambda candidate_id: (
                    0 if node_type_index.get(candidate_id, "") in preferred_type_set else 1,
                    -degrees.get(candidate_id, 0),
                    self._graph_node_label(node_map[candidate_id]).lower(),
                    candidate_id,
                ),
            )
            for neighbor_id in neighbors:
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                ordered.append(neighbor_id)
                queue.append((neighbor_id, depth + 1))

        is_truncated = False
        if not unbounded_nodes and len(ordered) > max_nodes:
            is_truncated = True
            remaining_slots = max(0, max_nodes - 1)
            prioritized_ids = [
                node_key
                for node_key in ordered[1:]
                if node_type_index.get(node_key, "") in preferred_type_set
            ]
            fallback_ids = [
                node_key
                for node_key in ordered[1:]
                if node_type_index.get(node_key, "") not in preferred_type_set
            ]
            ordered = [start_node_id]
            ordered.extend(prioritized_ids[:remaining_slots])
            if len(ordered) < max_nodes:
                ordered.extend(fallback_ids[: max_nodes - len(ordered)])
            visited = set(ordered)

        workbench_nodes = [self._serialize_workbench_node(node_map[node_key], degrees.get(node_key, 0)) for node_key in ordered if node_key in node_map]
        workbench_edges = [
            self._serialize_workbench_edge(edge)
            for edge in edges
            if str(edge.get("source_uid") or "") in visited and str(edge.get("target_uid") or "") in visited
        ]

        return {
            "standardId": standard_id,
            "rootNodeId": start_node_id,
            "maxDepth": max_depth,
            "maxNodes": max_nodes,
            "isTruncated": is_truncated,
            "nodes": workbench_nodes,
            "edges": workbench_edges,
        }

    def edit_graph_entity(self, request: GraphEntityEditRequest) -> dict[str, Any]:
        nodes_path, edges_path = self._graph_paths_for_standard(request.standardId)
        nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
        edges = json.loads(edges_path.read_text(encoding="utf-8"))

        source_index = self._find_node_by_identity(nodes, request.nodeId, request.entityName)
        if source_index is None:
            raise FileNotFoundError(f"Entity {request.entityName or request.nodeId} was not found in {request.standardId}.")

        source_node = nodes[source_index]
        next_label, next_type, next_text, next_properties = self._normalize_entity_update(source_node, request.updatedData)
        current_label = self._graph_node_label(source_node)
        renamed = self._normalize_graph_query(next_label) != self._normalize_graph_query(current_label)

        if renamed and not request.allowRename:
            raise ValueError("Entity rename requires allowRename=true.")

        duplicate_index = self._find_existing_label_match(nodes, next_label, exclude_node_id=str(source_node.get("node_uid") or ""))
        if duplicate_index is not None:
            if not request.allowMerge:
                raise ValueError(f"Entity '{next_label}' already exists.")
            response, merged_nodes, merged_edges = self._merge_graph_entities(
                request.standardId,
                nodes,
                edges,
                source_index,
                duplicate_index,
                next_label,
                next_type,
                next_text,
                next_properties,
            )
            nodes_path.write_text(json.dumps(merged_nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            edges_path.write_text(json.dumps(merged_edges, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return response

        source_node["label"] = next_label
        source_node["node_type"] = next_type
        source_node["text_content"] = next_text
        source_node["properties"] = next_properties
        nodes_path.write_text(json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        degrees = self._build_degree_map(nodes, edges)
        source_node_id = str(source_node.get("node_uid") or "")
        return {
            "status": "success",
            "message": "Entity updated successfully",
            "data": self._serialize_workbench_node(source_node, degrees.get(source_node_id, 0)),
            "operation_summary": {
                "merged": False,
                "merge_status": "not_attempted",
                "merge_error": None,
                "operation_status": "success",
                "target_entity": None,
                "final_entity": next_label,
                "final_node_id": source_node_id,
                "renamed": renamed,
            },
        }

    def edit_graph_relation(self, request: GraphRelationEditRequest) -> dict[str, Any]:
        _, edges_path = self._graph_paths_for_standard(request.standardId)
        edges = json.loads(edges_path.read_text(encoding="utf-8"))

        edge_index = None
        if request.edgeId:
            for index, edge in enumerate(edges):
                if edge.get("edge_uid") == request.edgeId:
                    edge_index = index
                    break
        if edge_index is None and request.sourceId and request.targetId:
            for index, edge in enumerate(edges):
                if edge.get("source_uid") == request.sourceId and edge.get("target_uid") == request.targetId:
                    edge_index = index
                    break
        if edge_index is None:
            raise FileNotFoundError(f"Relation {request.edgeId or (request.sourceId, request.targetId)} was not found in {request.standardId}.")

        edge = edges[edge_index]
        next_type, next_properties = self._normalize_relation_update(edge, request.updatedData)
        edge["edge_type"] = next_type
        edge["properties"] = next_properties
        edges_path.write_text(json.dumps(edges, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "status": "success",
            "message": "Relation updated successfully",
            "data": self._serialize_workbench_edge(edge),
        }

    def _iter_workbench_spaces(self, standard_id: str | None) -> list[str]:
        if standard_id:
            return [standard_id]
        return [item.standardId for item in self.list_kg_spaces()]

    def _load_graph_records(self, standard_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Path]:
        nodes_path, edges_path = self._graph_paths_for_standard(standard_id)
        return (
            json.loads(nodes_path.read_text(encoding="utf-8")),
            json.loads(edges_path.read_text(encoding="utf-8")),
            nodes_path.parent,
        )

    def _build_degree_map(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, int]:
        degree_map = {str(node.get("node_uid") or ""): 0 for node in nodes if node.get("node_uid")}
        for edge in edges:
            source_uid = str(edge.get("source_uid") or "")
            target_uid = str(edge.get("target_uid") or "")
            if source_uid in degree_map:
                degree_map[source_uid] += 1
            if target_uid in degree_map:
                degree_map[target_uid] += 1
        return degree_map

    def _resolve_graph_start_node(
        self,
        nodes: list[dict[str, Any]],
        degrees: dict[str, int],
        standard_id: str,
        label: str | None,
        node_id: str | None,
    ) -> dict[str, Any] | None:
        node_map = {str(node.get("node_uid") or ""): node for node in nodes if node.get("node_uid")}
        if node_id and node_id in node_map:
            return node_map[node_id]

        normalized_label = self._normalize_graph_query(label)
        if normalized_label and normalized_label != "*":
            exact_matches = [
                node
                for node in nodes
                if self._normalize_graph_query(self._graph_node_label(node)) == normalized_label
                or self._normalize_graph_query(str(node.get("node_uid") or "")) == normalized_label
            ]
            candidate_matches = exact_matches or [
                node
                for node in nodes
                if normalized_label in self._normalize_graph_query(self._graph_node_label(node))
                or normalized_label in self._normalize_graph_query(str(node.get("text_content") or ""))
            ]
            if candidate_matches:
                candidate_matches.sort(
                    key=lambda node: (
                        -degrees.get(str(node.get("node_uid") or ""), 0),
                        len(self._graph_node_label(node)),
                        str(node.get("node_uid") or ""),
                    )
                )
                return candidate_matches[0]

        if standard_id in node_map:
            return node_map[standard_id]
        return nodes[0] if nodes else None

    def _graph_node_label(self, node: dict[str, Any]) -> str:
        label = str(node.get("label") or "").strip()
        if label:
            return label
        text_content = str(node.get("text_content") or "").strip()
        if text_content:
            return text_content
        return str(node.get("node_uid") or "unnamed")

    def _graph_node_excerpt(self, node: dict[str, Any]) -> str | None:
        excerpt = str(node.get("text_content") or node.get("label") or "").strip()
        if not excerpt:
            return None
        return excerpt[:180] + "..." if len(excerpt) > 180 else excerpt

    def _normalize_graph_query(self, value: str | None) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    def _match_label_search_score(self, node: dict[str, Any], needle: str) -> int:
        label = self._normalize_graph_query(self._graph_node_label(node))
        node_uid = self._normalize_graph_query(str(node.get("node_uid") or ""))
        text_content = self._normalize_graph_query(str(node.get("text_content") or ""))
        if label == needle or node_uid == needle:
            return 100
        if label.startswith(needle):
            return 85
        if needle in label:
            return 70
        if needle in node_uid:
            return 60
        if needle in text_content:
            return 40
        return 0

    def _serialize_workbench_node(self, node: dict[str, Any], degree: int) -> dict[str, Any]:
        properties = dict(node.get("properties") or {})
        properties.setdefault("node_uid", node.get("node_uid"))
        properties.setdefault("standard_uid", node.get("standard_uid"))
        if node.get("text_content") is not None:
            properties.setdefault("text_content", node.get("text_content"))
        return {
            "id": str(node.get("node_uid") or ""),
            "label": self._graph_node_label(node),
            "nodeType": str(node.get("node_type") or "unknown"),
            "properties": properties,
            "degree": degree,
        }

    def _serialize_workbench_edge(self, edge: dict[str, Any]) -> dict[str, Any]:
        properties = dict(edge.get("properties") or {})
        properties.setdefault("edge_uid", edge.get("edge_uid"))
        return {
            "id": str(edge.get("edge_uid") or ""),
            "source": str(edge.get("source_uid") or ""),
            "target": str(edge.get("target_uid") or ""),
            "edgeType": str(edge.get("edge_type") or "RELATED_TO"),
            "properties": properties,
        }

    def _find_node_by_identity(self, nodes: list[dict[str, Any]], node_id: str | None, entity_name: str | None) -> int | None:
        if node_id:
            for index, node in enumerate(nodes):
                if node.get("node_uid") == node_id:
                    return index

        normalized_name = self._normalize_graph_query(entity_name)
        if not normalized_name:
            return None

        for index, node in enumerate(nodes):
            if self._normalize_graph_query(self._graph_node_label(node)) == normalized_name:
                return index
            if self._normalize_graph_query(str(node.get("node_uid") or "")) == normalized_name:
                return index
        return None

    def _find_existing_label_match(self, nodes: list[dict[str, Any]], label: str, exclude_node_id: str | None = None) -> int | None:
        normalized_label = self._normalize_graph_query(label)
        if not normalized_label:
            return None
        for index, node in enumerate(nodes):
            if exclude_node_id and node.get("node_uid") == exclude_node_id:
                continue
            if self._normalize_graph_query(self._graph_node_label(node)) == normalized_label:
                return index
        return None

    def _normalize_entity_update(self, node: dict[str, Any], updated_data: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
        next_label = self._graph_node_label(node)
        next_type = str(node.get("node_type") or "unknown")
        next_text = str(node.get("text_content") or "")
        next_properties = dict(node.get("properties") or {})

        explicit_properties = updated_data.get("properties")
        if isinstance(explicit_properties, dict):
            next_properties.update(explicit_properties)

        for key, value in updated_data.items():
            if value is None:
                continue
            if key in {"entity_name", "label"}:
                candidate = str(value).strip()
                if candidate:
                    next_label = candidate
            elif key in {"entity_type", "node_type"}:
                candidate = str(value).strip()
                if candidate:
                    next_type = candidate
            elif key in {"description", "text_content"}:
                next_text = str(value)
            elif key == "properties":
                continue
            else:
                next_properties[key] = value

        return next_label, next_type, next_text, next_properties

    def _normalize_relation_update(self, edge: dict[str, Any], updated_data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        next_type = str(edge.get("edge_type") or "RELATED_TO")
        next_properties = dict(edge.get("properties") or {})

        explicit_properties = updated_data.get("properties")
        if isinstance(explicit_properties, dict):
            next_properties.update(explicit_properties)

        for key, value in updated_data.items():
            if value is None:
                continue
            if key in {"relation_type", "edge_type", "label"}:
                candidate = str(value).strip()
                if candidate:
                    next_type = candidate
            elif key == "properties":
                continue
            else:
                next_properties[key] = value

        return next_type, next_properties

    def _merge_graph_entities(
        self,
        standard_id: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        source_index: int,
        target_index: int,
        next_label: str,
        next_type: str,
        next_text: str,
        next_properties: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        source_node = nodes[source_index]
        target_node = nodes[target_index]
        source_node_id = str(source_node.get("node_uid") or "")
        target_node_id = str(target_node.get("node_uid") or "")
        source_label = self._graph_node_label(source_node)
        target_label = self._graph_node_label(target_node)

        merged_properties = dict(source_node.get("properties") or {})
        merged_properties.update(target_node.get("properties") or {})
        merged_properties.update(next_properties)

        merged_aliases = {source_label, target_label}
        existing_aliases = merged_properties.get("merged_aliases")
        if isinstance(existing_aliases, list):
            merged_aliases.update(str(item) for item in existing_aliases if item)
        merged_properties["merged_aliases"] = sorted(alias for alias in merged_aliases if alias and alias != next_label)
        merged_node_ids = {source_node_id, target_node_id}
        existing_merged_node_ids = merged_properties.get("merged_node_ids")
        if isinstance(existing_merged_node_ids, list):
            merged_node_ids.update(str(item) for item in existing_merged_node_ids if item)
        merged_properties["merged_node_ids"] = sorted(merged_node_ids)

        target_node["label"] = next_label
        target_node["node_type"] = next_type
        target_node["text_content"] = next_text or str(target_node.get("text_content") or source_node.get("text_content") or "")
        target_node["properties"] = merged_properties

        deduped_edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        for edge in edges:
            rewritten_edge = dict(edge)
            if rewritten_edge.get("source_uid") == source_node_id:
                rewritten_edge["source_uid"] = target_node_id
            if rewritten_edge.get("target_uid") == source_node_id:
                rewritten_edge["target_uid"] = target_node_id
            if rewritten_edge.get("source_uid") == rewritten_edge.get("target_uid"):
                continue

            edge_key = (
                str(rewritten_edge.get("source_uid") or ""),
                str(rewritten_edge.get("target_uid") or ""),
                str(rewritten_edge.get("edge_type") or "RELATED_TO"),
            )
            if edge_key in deduped_edges:
                existing_edge = deduped_edges[edge_key]
                existing_properties = dict(existing_edge.get("properties") or {})
                merged_edge_ids = existing_properties.get("merged_edge_ids")
                if not isinstance(merged_edge_ids, list):
                    merged_edge_ids = []
                merged_edge_ids.append(str(rewritten_edge.get("edge_uid") or ""))
                existing_properties["merged_edge_ids"] = sorted({edge_id for edge_id in merged_edge_ids if edge_id})
                for property_key, property_value in (rewritten_edge.get("properties") or {}).items():
                    existing_properties.setdefault(property_key, property_value)
                existing_edge["properties"] = existing_properties
                continue
            deduped_edges[edge_key] = rewritten_edge

        merged_nodes = [node for index, node in enumerate(nodes) if index != source_index]
        merged_edges = list(deduped_edges.values())
        degrees = self._build_degree_map(merged_nodes, merged_edges)

        response = {
            "status": "success",
            "message": f"Entity merged successfully into '{next_label}'",
            "data": self._serialize_workbench_node(target_node, degrees.get(target_node_id, 0)),
            "operation_summary": {
                "merged": True,
                "merge_status": "success",
                "merge_error": None,
                "operation_status": "success",
                "target_entity": target_label,
                "final_entity": next_label,
                "final_node_id": target_node_id,
                "renamed": self._normalize_graph_query(next_label) != self._normalize_graph_query(source_label),
            },
        }
        return response, merged_nodes, merged_edges

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
        detected_standard = self._resolve_standard_descriptor(request, source_path) if request.documentType == "standard" else None
        graph_space_dir = self.config.kg_space_dir_for(detected_standard[0]) if detected_standard else None
        report_space_dir = self.config.report_space_dir_for(job.documentId) if request.documentType == "report" else None
        if graph_space_dir is not None:
            job.result["graph_space_dir"] = str(graph_space_dir)
        if report_space_dir is not None:
            job.result["report_space_dir"] = str(report_space_dir)

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
            elif request.buildGraph and request.documentType == "report":
                job.progress = 0.88
                self._touch(job)
                self._build_report_space(job, source_path, artifact_dir, report_space_dir)

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

    def _build_report_space(
        self,
        job: IngestionJob,
        source_path: Path,
        artifact_dir: Path,
        report_space_dir: Path | None,
    ) -> None:
        if report_space_dir is None:
            raise FileNotFoundError("Report space directory could not be resolved.")

        output = self.report_pipeline_service.run(artifact_dir, job.documentId)
        files = self.report_pipeline_service.write_outputs(
            report_space_dir,
            output,
            artifact_dir=artifact_dir,
            document_id=job.documentId,
            source_path=source_path,
        )
        job.result["report"] = {
            "document_id": job.documentId,
            "report_space_dir": str(report_space_dir),
            "metrics": output.metrics,
            "files": {key: str(path) for key, path in files.items()},
        }
        job.result["report_space"] = {
            "dir": str(report_space_dir),
            "files": self._artifact_index(report_space_dir),
        }
        job.result["artifacts"] = self._artifact_index(artifact_dir)

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

    def _resolve_standard_descriptor(self, request: CreateIngestionJobRequest, source_path: Path) -> tuple[str, str, str] | None:
        if request.standardId:
            standard_id = request.standardId.strip().lower()
            existing = self.registry.get(standard_id)
            if existing is not None:
                return existing.standardId, existing.code, existing.title
            match = STANDARD_ID_RE.match(standard_id)
            if not match:
                return None
            code = f"{match.group('prefix').upper()}{match.group('number')}"
            title = source_path.stem
            return standard_id, code, title
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
            fallback = self.config.kg_space_dir_for(standard_id)
            graph_space_dir = fallback if fallback.exists() else None
        if graph_space_dir is None:
            return None

        nodes_path = graph_space_dir / "graph_nodes.json"
        edges_path = graph_space_dir / "graph_edges.json"
        if not nodes_path.exists() or not edges_path.exists():
            return None

        nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
        edges = json.loads(edges_path.read_text(encoding="utf-8"))
        if not node_id:
            return {"nodes": nodes, "edges": edges}

        node_map = {node["node_uid"]: node for node in nodes}
        if node_id not in node_map:
            return None
        frontier = {node_id}
        visited = {node_id}
        selected_edges: list[dict] = []
        for _ in range(max(1, depth)):
            next_frontier: set[str] = set()
            for edge in edges:
                source_uid = edge.get("source_uid")
                target_uid = edge.get("target_uid")
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
        deduped_edges = {edge["edge_uid"]: edge for edge in selected_edges}
        return {"nodes": selected_nodes, "edges": list(deduped_edges.values())}

    def get_requirement_detail(self, requirement_id: str) -> RequirementDetail | None:
        standard_id = self._standard_id_from_requirement(requirement_id)
        if standard_id is None:
            return None
        detail = self.registry.get(standard_id)
        graph_space_dir = self._resolve_graph_space_dir(detail)
        if graph_space_dir is None:
            fallback = self.config.kg_space_dir_for(standard_id)
            graph_space_dir = fallback if fallback.exists() else None
        if graph_space_dir is None:
            return None

        requirements_path = graph_space_dir / "requirements.json"
        if not requirements_path.exists():
            return None
        requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
        for requirement in requirements:
            if requirement.get("requirement_uid") != requirement_id:
                continue
            return RequirementDetail(
                requirementId=requirement["requirement_uid"],
                standardId=requirement["standard_uid"],
                clauseRef=requirement["clause_ref"],
                requirementText=requirement["requirement_text"],
                modality=requirement["modality"],
                applicabilityRule=requirement.get("applicability_rule"),
                judgementCriteria=requirement.get("judgement_criteria", []),
                evidenceExpected=requirement.get("evidence_expected", []),
                citations=requirement.get("cited_targets", []),
                sourceSpans=[
                    {
                        "pageSpan": requirement.get("source_page_span"),
                        "bbox": requirement.get("source_bbox"),
                    }
                ],
            )
        return None

    def _standard_id_from_requirement(self, requirement_id: str) -> str | None:
        parts = requirement_id.split(":")
        if len(parts) < 2:
            return None
        return ":".join(parts[:2])

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

    def _document_status_from_graph(self, graph_status: str | None) -> str:
        return {
            "ready": "ready",
            "building": "running",
            "failed": "failed",
            "not_built": "idle",
        }.get(graph_status or "", "idle")

    def _guess_source_path(self, detail: StandardDetail) -> Path | None:
        for alias in detail.aliases:
            candidate = self.config.root_dir / "Doc" / alias
            if candidate.exists():
                return candidate.resolve()
        if detail.artifactDir:
            artifact_dir = Path(detail.artifactDir)
            if artifact_dir.exists():
                origin_files = sorted(artifact_dir.glob("*_origin.pdf"))
                if origin_files:
                    return origin_files[0].resolve()
        return None

    def _detect_source_format(self, source_path: Path) -> str:
        suffix = source_path.suffix.lower()
        if suffix == ".docx":
            return "docx"
        if suffix == ".doc":
            return "doc"
        return "pdf"

    def _build_kg_space_summary(self, detail: StandardDetail) -> KgSpaceSummary | None:
        if not detail.graphSpaceDir:
            return None
        graph_space_dir = Path(detail.graphSpaceDir)
        if not graph_space_dir.exists():
            return None
        node_types, edge_types, requirement_count, updated_at = self._graph_stats(graph_space_dir)
        return KgSpaceSummary(
            standardId=detail.standardId,
            code=detail.code,
            title=detail.title,
            graphStatus=detail.graphStatus,
            graphSpaceDir=detail.graphSpaceDir,
            artifactDir=detail.artifactDir,
            nodeCount=sum(node_types.values()),
            edgeCount=sum(edge_types.values()),
            requirementCount=requirement_count,
            nodeTypes=node_types,
            edgeTypes=edge_types,
            updatedAt=updated_at,
        )

    def _graph_stats(self, graph_space_dir: Path) -> tuple[dict[str, int], dict[str, int], int, datetime | None]:
        nodes_path = graph_space_dir / "graph_nodes.json"
        edges_path = graph_space_dir / "graph_edges.json"
        requirements_path = graph_space_dir / "requirements.json"
        node_types: Counter[str] = Counter()
        edge_types: Counter[str] = Counter()
        requirement_count = 0
        if nodes_path.exists():
            for node in json.loads(nodes_path.read_text(encoding="utf-8")):
                node_types[node.get("node_type", "unknown")] += 1
        if edges_path.exists():
            for edge in json.loads(edges_path.read_text(encoding="utf-8")):
                edge_types[edge.get("edge_type", "unknown")] += 1
        if requirements_path.exists():
            requirement_count = len(json.loads(requirements_path.read_text(encoding="utf-8")))
        updated_at = self._path_updated_at(self._first_existing_path(nodes_path, edges_path, requirements_path, graph_space_dir))
        return dict(node_types), dict(edge_types), requirement_count, updated_at

    def _graph_paths_for_standard(self, standard_id: str) -> tuple[Path, Path]:
        graph_space_dir = self._resolve_graph_space_dir(self.registry.get(standard_id)) or self.config.kg_space_dir_for(standard_id)
        nodes_path = graph_space_dir / "graph_nodes.json"
        edges_path = graph_space_dir / "graph_edges.json"
        if not nodes_path.exists() or not edges_path.exists():
            raise FileNotFoundError(f"Graph files for {standard_id} were not found.")
        return nodes_path, edges_path

    def _list_relative_files(self, root: Path) -> list[str]:
        if not root.exists():
            return []
        return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())

    def _standard_id_from_space_dir(self, name: str) -> tuple[str, str]:
        match = KG_SPACE_DIR_RE.match(name)
        if not match:
            return name, name.upper().replace("-", "")
        prefix = match.group("prefix").lower()
        year = match.group("year")
        return f"{prefix}:{year}", prefix.upper()

    def _safe_remove_tree(self, raw_path: str | None, allowed_root: Path) -> None:
        if not raw_path:
            return
        path = Path(raw_path)
        if not path.exists():
            return
        resolved = path.resolve()
        if not resolved.is_relative_to(allowed_root.resolve()):
            return
        shutil.rmtree(resolved, ignore_errors=True)

    def _safe_remove_file(self, raw_path: str, allowed_root: Path) -> None:
        path = Path(raw_path)
        if not path.exists():
            return
        resolved = path.resolve()
        if not resolved.is_relative_to(allowed_root.resolve()):
            return
        resolved.unlink(missing_ok=True)

    def _first_existing_path(self, *raw_paths: str | Path | None) -> Path | None:
        for raw_path in raw_paths:
            if raw_path is None:
                continue
            path = Path(raw_path)
            if path.exists():
                return path
        return None

    def _path_updated_at(self, path: Path | None) -> datetime | None:
        if path is None or not path.exists():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
