from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import time
import traceback
import uuid
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from adapters.mineru_client import MinerUApiError, MinerUClient
from core.config import get_config
from core.logging import configure_logging
from models.schemas import CreateIngestionJobRequest, StandardDetail
from repositories.standard_registry import StandardRegistry
from services.normalization import NormalizationService
from services.standard_pipeline import StandardPipelineService


SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log_step(message: str) -> None:
    print(f"[{_timestamp()}] {message}", flush=True)


def infer_source_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".doc":
        return "doc"
    if suffix == ".docx":
        return "docx"
    raise ValueError(f"Unsupported source format for file: {path}")


def make_document_id(source_path: Path) -> str:
    base = SAFE_ID_RE.sub("-", source_path.stem.lower()).strip("-")
    suffix = uuid.uuid4().hex[:8]
    return f"{base[:80]}-{suffix}"


def extract_zip(zip_path: Path, artifact_dir: Path) -> None:
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(artifact_dir)


def artifact_index(artifact_dir: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for path in artifact_dir.rglob("*"):
        if path.is_file():
            artifacts[path.relative_to(artifact_dir).as_posix()] = str(path)
    return artifacts


def sync_registry(config, registry: StandardRegistry, standard_id: str, source_path: Path, document_id: str, artifact_dir: Path, graph_space_dir: Path) -> None:
    existing = registry.get(standard_id)
    detected = registry.detect_from_filename(source_path.name)
    code = existing.code if existing else (detected[1] if detected else standard_id.split(":")[0].upper())
    title = existing.title if existing else (detected[2] if detected else source_path.stem)
    aliases = set(existing.aliases if existing else [])
    aliases.add(source_path.name)
    detail = StandardDetail(
        standardId=standard_id,
        code=code,
        year=existing.year if existing else (standard_id.split(":")[-1] if ":" in standard_id else None),
        title=title,
        aliases=sorted(aliases),
        effectiveDate=existing.effectiveDate if existing else None,
        documentId=document_id,
        artifactDir=str(artifact_dir),
        graphSpaceDir=str(graph_space_dir),
        graphStatus="ready",
        latestJobId=existing.latestJobId if existing else None,
    )
    registry.upsert(detail)


async def run() -> int:
    parser = argparse.ArgumentParser(description="Foreground ingestion test runner with terminal progress and logs.")
    parser.add_argument("--source-path", required=True, help="Path to the source PDF/DOC/DOCX file.")
    parser.add_argument("--document-type", choices=["standard", "report"], default="standard")
    parser.add_argument("--source-format", choices=["pdf", "doc", "docx"], help="Override source format. Defaults to file suffix.")
    parser.add_argument("--parser-endpoint", help="Override MinerU parser endpoint. Defaults to config.yaml.")
    parser.add_argument("--normalization-policy", choices=["auto", "none", "force_pdf_for_localhost"], default="auto")
    parser.add_argument("--no-build-graph", action="store_true", help="Skip KG build even for standards.")
    parser.add_argument("--standard-id", help="Explicit standard UID, for example sl258:2017.")
    parser.add_argument("--disable-llm", action="store_true", help="Force heuristic extraction for this run only.")
    parser.add_argument("--llm-timeout-seconds", type=int, help="Override LLM timeout for this run only.")
    args = parser.parse_args()

    configure_logging()
    config = get_config()
    if args.disable_llm:
        config.llm.enabled = False
        config.knowledge_graph.extraction_mode = "heuristic"
    if args.llm_timeout_seconds is not None:
        config.llm.timeout_seconds = args.llm_timeout_seconds

    source_path = Path(args.source_path)
    if not source_path.is_absolute():
        source_path = (PROJECT_ROOT / source_path).resolve()
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"Source file was not found: {source_path}")

    source_format = args.source_format or infer_source_format(source_path)
    document_id = make_document_id(source_path)
    run_id = str(uuid.uuid4())
    work_dir = config.download_work_dir_for(document_id, run_id)
    artifact_dir = config.artifact_dir_for(document_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    build_graph = not args.no_build_graph
    request = CreateIngestionJobRequest(
        documentType=args.document_type,
        sourcePath=str(source_path),
        sourceFormat=source_format,
        parserProvider="mineru_api",
        parserEndpoint=args.parser_endpoint or config.mineru.default_endpoint,
        normalizationPolicy=args.normalization_policy,
        buildGraph=build_graph,
        metadata={"title": source_path.stem},
    )

    mineru_client = MinerUClient(config)
    normalization_service = NormalizationService(config)
    pipeline_service = StandardPipelineService(config=config)
    registry = StandardRegistry(config.registry_path)

    log_step("Start foreground ingestion test")
    log_step(f"source={source_path}")
    log_step(f"document_id={document_id}")
    log_step(f"parser_endpoint={request.parserEndpoint}")
    log_step(
        f"kg_mode={config.knowledge_graph.extraction_mode} | llm_enabled={config.llm.enabled} | "
        f"llm_model={config.llm.model} | build_graph={build_graph}"
    )

    try:
        step_started = time.perf_counter()
        log_step("Step 1/6 Normalize source")
        normalization = normalization_service.normalize(source_path, request, work_dir)
        log_step(
            f"normalized_path={normalization.normalized_path} | format={normalization.normalized_format} | "
            f"actions={normalization.preprocessing_actions}"
        )
        log_step(f"Step 1 done in {time.perf_counter() - step_started:.2f}s")

        step_started = time.perf_counter()
        log_step("Step 2/6 Request MinerU upload URL")
        batch_id, upload_url = await mineru_client.request_upload_url(
            endpoint=request.parserEndpoint,
            file_name=normalization.normalized_path.name,
            data_id=document_id,
        )
        log_step(f"batch_id={batch_id}")
        log_step(f"upload_url={upload_url.split('?', 1)[0]}")
        log_step(f"Step 2 done in {time.perf_counter() - step_started:.2f}s")

        step_started = time.perf_counter()
        file_size_mb = normalization.normalized_path.stat().st_size / (1024 * 1024)
        log_step(f"Step 3/6 Upload file to MinerU OSS | size={file_size_mb:.2f} MB")
        await mineru_client.upload_file(upload_url, normalization.normalized_path)
        log_step(f"Step 3 done in {time.perf_counter() - step_started:.2f}s")

        step_started = time.perf_counter()
        log_step("Step 4/6 Poll MinerU result")
        deadline = asyncio.get_running_loop().time() + config.mineru.poll_timeout_seconds
        result_entry: dict | None = None
        poll_count = 0
        consecutive_errors = 0
        while asyncio.get_running_loop().time() < deadline:
            poll_count += 1
            try:
                batch = await mineru_client.get_batch_result(request.parserEndpoint, batch_id)
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                log_step(f"poll#{poll_count} error={exc}")
                if consecutive_errors >= config.mineru.poll_request_retries:
                    raise
                await asyncio.sleep(config.mineru.retry_backoff_seconds * consecutive_errors)
                continue

            extract_result = batch.get("extract_result") or []
            result_entry = None
            for entry in extract_result:
                if entry.get("data_id") == document_id:
                    result_entry = entry
                    break
            if result_entry is None and extract_result:
                result_entry = extract_result[0]

            if result_entry is None:
                log_step(f"poll#{poll_count} state=pending")
                await asyncio.sleep(config.mineru.poll_interval_seconds)
                continue

            state = result_entry.get("state")
            progress = result_entry.get("extract_progress") or {}
            log_step(f"poll#{poll_count} state={state} progress={json.dumps(progress, ensure_ascii=False)}")
            if state == "done":
                break
            if state == "failed":
                raise MinerUApiError(result_entry.get("err_msg") or "MinerU reported failed state.")
            await asyncio.sleep(config.mineru.poll_interval_seconds)

        if result_entry is None or result_entry.get("state") != "done":
            raise TimeoutError(f"Polling MinerU batch {batch_id} timed out.")
        log_step(f"Step 4 done in {time.perf_counter() - step_started:.2f}s")

        step_started = time.perf_counter()
        log_step("Step 5/6 Download and extract MinerU result zip")
        full_zip_url = result_entry.get("full_zip_url")
        if not full_zip_url:
            raise MinerUApiError("MinerU finished without returning full_zip_url.")
        zip_path = work_dir / "mineru_result.zip"
        await mineru_client.download_result_zip(full_zip_url, zip_path)
        extract_zip(zip_path, artifact_dir)
        log_step(f"result_zip={zip_path}")
        log_step(f"artifact_dir={artifact_dir}")
        log_step(f"Step 5 done in {time.perf_counter() - step_started:.2f}s")

        if build_graph and args.document_type == "standard":
            step_started = time.perf_counter()
            detected = registry.detect_from_filename(source_path.name)
            standard_id = args.standard_id or (detected[0] if detected else None)
            if not standard_id:
                raise ValueError("Failed to infer standard_id from filename. Please pass --standard-id explicitly.")
            graph_space_dir = config.kg_space_dir_for(standard_id)
            log_step(f"Step 6/6 Build KG | standard_id={standard_id}")
            log_step(f"graph_space_dir={graph_space_dir}")
            output = pipeline_service.run(artifact_dir, standard_id)
            files = pipeline_service.write_outputs(
                graph_space_dir,
                output,
                artifact_dir=artifact_dir,
                standard_uid=standard_id,
                document_id=document_id,
            )
            sync_registry(config, registry, standard_id, source_path, document_id, artifact_dir, graph_space_dir)
            log_step(
                "graph_metrics="
                + json.dumps(
                    {
                        "requirements": output.metrics.get("requirement_count"),
                        "graph_nodes": output.metrics.get("graph_node_count"),
                        "graph_edges": output.metrics.get("graph_edge_count"),
                        "extraction_mode_effective": output.metrics.get("extraction_mode_effective"),
                        "embedding_generation_status": output.metrics.get("embedding_generation_status"),
                        "postgres_persist_status": output.metrics.get("postgres_persist_status"),
                    },
                    ensure_ascii=False,
                )
            )
            if output.extraction_warnings:
                log_step("extraction_warnings=" + json.dumps(output.extraction_warnings, ensure_ascii=False))
            log_step("graph_space_files=" + json.dumps({key: str(path) for key, path in files.items()}, ensure_ascii=False))
            log_step(f"Step 6 done in {time.perf_counter() - step_started:.2f}s")
        else:
            log_step("Step 6/6 skipped (build_graph disabled or document_type is report)")

        log_step("Finished successfully")
        log_step("artifacts=" + json.dumps(artifact_index(artifact_dir), ensure_ascii=False))
        return 0
    except Exception as exc:
        log_step(f"FAILED: {exc}")
        traceback.print_exc()
        return 1


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
