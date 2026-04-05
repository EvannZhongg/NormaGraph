from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


JobStatus = Literal["queued", "running", "succeeded", "failed"]
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
    derivedDir: str | None = None
    graphStatus: Literal["not_built", "building", "ready", "failed"] = "not_built"
    latestJobId: str | None = None


class StandardsResponse(BaseModel):
    items: list[StandardSummary]


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
