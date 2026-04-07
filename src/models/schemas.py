from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


JobStatus = Literal["queued", "running", "succeeded", "failed"]
DocumentStatus = Literal["idle", "queued", "running", "succeeded", "failed", "ready"]
DocumentType = Literal["standard", "report"]
SourceFormat = Literal["pdf", "doc", "docx"]
ParserProvider = Literal["mineru_api"]
NormalizationPolicy = Literal["auto", "none", "force_pdf_for_localhost"]


class CreateIngestionJobRequest(BaseModel):
    documentType: DocumentType
    sourcePath: str
    sourceFormat: SourceFormat
    parserProvider: ParserProvider = "mineru_api"
    parserEndpoint: str | None = None
    normalizationPolicy: NormalizationPolicy = "auto"
    buildGraph: bool = True
    documentId: str | None = None
    standardId: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestionJob(BaseModel):
    jobId: str
    status: JobStatus
    documentId: str
    documentType: DocumentType
    parserProvider: ParserProvider
    parserEndpoint: str | None = None
    normalizedFormat: SourceFormat | None = None
    preprocessingActions: list[str] = Field(default_factory=list)
    progress: float = 0.0
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    createdAt: datetime
    updatedAt: datetime


class StandardSummary(BaseModel):
    standardId: str
    code: str
    year: str | None = None
    title: str


class StandardDetail(StandardSummary):
    aliases: list[str] = Field(default_factory=list)
    effectiveDate: str | None = None
    documentId: str | None = None
    artifactDir: str | None = None
    graphSpaceDir: str | None = None
    graphStatus: Literal["not_built", "building", "ready", "failed"] = "not_built"
    latestJobId: str | None = None


class StandardsResponse(BaseModel):
    items: list[StandardSummary]


class DocumentSummary(BaseModel):
    documentId: str
    displayName: str
    standardId: str | None = None
    title: str | None = None
    documentType: DocumentType = "standard"
    sourcePath: str | None = None
    sourceName: str | None = None
    sourceFormat: SourceFormat | None = None
    status: DocumentStatus = "idle"
    graphStatus: Literal["not_built", "building", "ready", "failed"] | None = None
    progress: float = 0.0
    latestJobId: str | None = None
    latestError: str | None = None
    parserProvider: ParserProvider | None = None
    artifactDir: str | None = None
    graphSpaceDir: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    createdAt: datetime | None = None
    updatedAt: datetime | None = None


class DocumentsResponse(BaseModel):
    items: list[DocumentSummary]


class DocumentJobsResponse(BaseModel):
    items: list[IngestionJob]


class KgSpaceSummary(BaseModel):
    standardId: str
    code: str
    title: str
    graphStatus: Literal["not_built", "building", "ready", "failed"] = "not_built"
    graphSpaceDir: str | None = None
    artifactDir: str | None = None
    nodeCount: int = 0
    edgeCount: int = 0
    requirementCount: int = 0
    nodeTypes: dict[str, int] = Field(default_factory=dict)
    edgeTypes: dict[str, int] = Field(default_factory=dict)
    updatedAt: datetime | None = None


class KgSpaceDetail(KgSpaceSummary):
    aliases: list[str] = Field(default_factory=list)
    documentId: str | None = None
    files: list[str] = Field(default_factory=list)


class KgSpacesResponse(BaseModel):
    items: list[KgSpaceSummary]


class GraphSearchItem(BaseModel):
    nodeId: str
    nodeType: str
    label: str
    excerpt: str | None = None


class GraphSearchResponse(BaseModel):
    items: list[GraphSearchItem]


class GraphLabelItem(BaseModel):
    standardId: str
    nodeId: str
    label: str
    nodeType: str
    degree: int = 0
    excerpt: str | None = None


class GraphLabelResponse(BaseModel):
    items: list[GraphLabelItem]


class GraphWorkbenchNode(BaseModel):
    id: str
    label: str
    nodeType: str
    properties: dict[str, Any] = Field(default_factory=dict)
    degree: int = 0


class GraphWorkbenchEdge(BaseModel):
    id: str
    source: str
    target: str
    edgeType: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphWorkbenchResponse(BaseModel):
    standardId: str
    rootNodeId: str | None = None
    maxDepth: int
    maxNodes: int
    isTruncated: bool = False
    nodes: list[GraphWorkbenchNode] = Field(default_factory=list)
    edges: list[GraphWorkbenchEdge] = Field(default_factory=list)


class GraphEntityExistsResponse(BaseModel):
    exists: bool
    nodeId: str | None = None
    standardId: str | None = None


class GraphEntityEditRequest(BaseModel):
    standardId: str
    nodeId: str | None = None
    entityName: str | None = None
    updatedData: dict[str, Any] = Field(default_factory=dict)
    allowRename: bool = False
    allowMerge: bool = False


class GraphRelationEditRequest(BaseModel):
    standardId: str
    edgeId: str | None = None
    sourceId: str | None = None
    targetId: str | None = None
    updatedData: dict[str, Any] = Field(default_factory=dict)


class GraphServiceStatus(BaseModel):
    status: str
    workingDirectory: str
    dataDirectory: str
    graphSpaceDirectory: str
    uploadsDirectory: str
    configuration: dict[str, Any] = Field(default_factory=dict)
    graphLimits: dict[str, int] = Field(default_factory=dict)
    selectedSpace: dict[str, Any] | None = None


class UpdateGraphNodeRequest(BaseModel):
    label: str | None = None
    textContent: str | None = None
    nodeType: str | None = None
    properties: dict[str, Any] | None = None


class UpdateGraphEdgeRequest(BaseModel):
    edgeType: str | None = None
    sourceUid: str | None = None
    targetUid: str | None = None
    properties: dict[str, Any] | None = None


class SubgraphResponse(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class RequirementDetail(BaseModel):
    requirementId: str
    standardId: str
    clauseRef: str
    requirementText: str
    modality: Literal["must", "should", "may", "forbidden", "conditional"]
    applicabilityRule: str | None = None
    judgementCriteria: list[str] = Field(default_factory=list)
    evidenceExpected: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    sourceSpans: list[dict[str, Any]] = Field(default_factory=list)


class QuestionRequest(BaseModel):
    question: str
    standardIds: list[str] = Field(default_factory=list)
    expandCitations: bool = True


class QuestionResponse(BaseModel):
    answer: str
    standardIds: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    graphHops: list[dict[str, Any]] = Field(default_factory=list)


class CreateComparisonRequest(BaseModel):
    reportDocumentId: str
    targetStandardIds: list[str] = Field(default_factory=list)
    projectContext: dict[str, Any] = Field(default_factory=dict)
    autoRouteStandards: bool = True


class ComparisonSummary(BaseModel):
    comparisonId: str
    status: JobStatus
    reportDocumentId: str
    targetStandardIds: list[str] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


class ComparisonItem(BaseModel):
    requirementId: str
    status: Literal["covered", "partial", "missing", "unknown"]
    reason: str | None = None
    suggestion: str | None = None
    evidenceNodeIds: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
