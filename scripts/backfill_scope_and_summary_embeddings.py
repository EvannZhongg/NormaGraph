from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from adapters.llm_client import EmbeddingsAPIClient, ResponseAPIError, ResponsesAPIClient
from core.config import get_config
from prompts import (
    LLM_KG_SPACE_PROFILE_SYSTEM_PROMPT,
    LLM_REPORT_SCOPE_SUMMARY_SYSTEM_PROMPT,
    build_kg_space_profile_prompt,
    build_report_scope_summary_prompt,
)


KG_SPACE_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scope_summary": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["scope_summary", "keywords"],
}

REPORT_SCOPE_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scope_summary": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["scope_summary", "keywords"],
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill KG/report scope summaries and summary embeddings for existing local data."
    )
    parser.add_argument("--kg-spaces-dir", default="data/kg_spaces", help="Directory containing standard KG spaces.")
    parser.add_argument("--report-spaces-dir", default="data/report_spaces", help="Directory containing report spaces.")
    parser.add_argument("--skip-kg-spaces", action="store_true", help="Do not process standard KG spaces.")
    parser.add_argument("--skip-report-spaces", action="store_true", help="Do not process report spaces.")
    parser.add_argument("--dry-run", action="store_true", help="Build summaries and print counts without writing files or calling embeddings.")
    args = parser.parse_args()

    config = get_config()
    llm_client = ResponsesAPIClient(config)
    embedding_client = EmbeddingsAPIClient(config)

    kg_spaces_dir = _resolve_path(args.kg_spaces_dir)
    report_spaces_dir = _resolve_path(args.report_spaces_dir)

    if not args.skip_kg_spaces:
        for space_dir in sorted(path for path in kg_spaces_dir.iterdir() if path.is_dir()) if kg_spaces_dir.exists() else []:
            result = backfill_kg_space(space_dir, config=config, llm_client=llm_client, embedding_client=embedding_client, dry_run=args.dry_run)
            print(_format_result("kg_space", space_dir, result))

    if not args.skip_report_spaces:
        for space_dir in sorted(path for path in report_spaces_dir.iterdir() if path.is_dir()) if report_spaces_dir.exists() else []:
            result = backfill_report_space(space_dir, config=config, llm_client=llm_client, embedding_client=embedding_client, dry_run=args.dry_run)
            print(_format_result("report_space", space_dir, result))


def backfill_kg_space(
    space_dir: Path,
    *,
    config: Any,
    llm_client: ResponsesAPIClient,
    embedding_client: EmbeddingsAPIClient,
    dry_run: bool,
) -> dict[str, Any]:
    nodes_path = space_dir / "graph_nodes.json"
    if not nodes_path.exists():
        return {"status": "skipped_missing_graph_nodes"}

    manifest = _read_json_object(space_dir / "space_manifest.json")
    standard_id = str(manifest.get("standard_id") or space_dir.name.replace("-", ":"))
    nodes = _read_json_array(nodes_path)
    chapters = [
        node
        for node in nodes
        if node.get("node_type") == "chapter"
        and str((node.get("properties") or {}).get("summary") or "").strip()
    ]
    profile = _build_kg_space_profile(standard_id=standard_id, space_dir=space_dir, chapters=chapters)
    if not dry_run:
        profile = _maybe_generate_kg_profile_with_llm(profile, chapters=chapters, llm_client=llm_client)
    documents = _build_kg_summary_embedding_documents(profile)

    if dry_run:
        return {
            "status": "dry_run",
            "chapter_summary_count": len(chapters),
            "embedding_document_count": len(documents),
        }

    (space_dir / "kg_space_profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _merge_embedding_inputs(space_dir / "embedding_inputs.jsonl", documents)
    vector_count = _merge_embedding_store(
        space_dir / "embedding_store.jsonl",
        documents,
        config=config,
        embedding_client=embedding_client,
    )
    _update_metrics(
        space_dir / "segmentation_metrics.json",
        {
            "kg_space_profile_status": profile.get("status"),
            "kg_space_profile_chapter_count": len(profile.get("chapters") or []),
            "summary_embedding_backfill_status": "completed" if vector_count else "skipped_no_vectors",
            "summary_embedding_backfill_vector_count": vector_count,
        },
    )
    return {
        "status": "completed",
        "chapter_summary_count": len(chapters),
        "embedding_document_count": len(documents),
        "vector_count": vector_count,
    }


def backfill_report_space(
    space_dir: Path,
    *,
    config: Any,
    llm_client: ResponsesAPIClient,
    embedding_client: EmbeddingsAPIClient,
    dry_run: bool,
) -> dict[str, Any]:
    sections_path = space_dir / "sections.json"
    units_path = space_dir / "report_units.json"
    if not sections_path.exists() or not units_path.exists():
        return {"status": "skipped_missing_report_files"}

    manifest = _read_json_object(space_dir / "space_manifest.json")
    document_id = str(manifest.get("document_id") or space_dir.name)
    sections = _read_json_array(sections_path)
    report_units = _read_json_array(units_path)
    scope_summary = _build_report_scope_summary(document_id=document_id, sections=sections, report_units=report_units)
    if not dry_run:
        scope_summary = _maybe_generate_report_scope_with_llm(
            scope_summary,
            sections=sections,
            report_units=report_units,
            llm_client=llm_client,
        )
    documents = [_scope_summary_document(scope_summary)] if scope_summary.get("summary") else []

    if dry_run:
        return {
            "status": "dry_run",
            "scope_summary_status": scope_summary.get("status"),
            "embedding_document_count": len(documents),
        }

    (space_dir / "scope_summary.json").write_text(
        json.dumps(scope_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _merge_report_scope_node(space_dir, scope_summary)
    _merge_embedding_inputs(space_dir / "embedding_inputs.jsonl", documents)
    vector_count = _merge_embedding_store(
        space_dir / "embedding_store.jsonl",
        documents,
        config=config,
        embedding_client=embedding_client,
    )
    _update_metrics(
        space_dir / "segmentation_metrics.json",
        {
            "scope_summary_status": scope_summary.get("status"),
            "scope_summary_section_count": len(scope_summary.get("sections") or []),
            "summary_embedding_backfill_status": "completed" if vector_count else "skipped_no_vectors",
            "summary_embedding_backfill_vector_count": vector_count,
        },
    )
    return {
        "status": "completed",
        "scope_summary_status": scope_summary.get("status"),
        "embedding_document_count": len(documents),
        "vector_count": vector_count,
    }


def _build_kg_space_profile(*, standard_id: str, space_dir: Path, chapters: list[dict[str, Any]]) -> dict[str, Any]:
    chapter_profiles = []
    summary_parts = []
    for node in sorted(chapters, key=lambda item: str(((item.get("properties") or {}).get("ref") or item.get("node_uid") or ""))):
        props = node.get("properties") or {}
        title = str(props.get("title") or node.get("label") or "").strip()
        ref = props.get("ref")
        summary = str(props.get("summary") or "").strip()
        if not summary:
            continue
        chapter_profiles.append(
            {
                "node_uid": node.get("node_uid"),
                "ref": ref,
                "title": title,
                "summary": summary,
                "summary_source_clause_count": props.get("summary_source_clause_count", 0),
                "summary_source_truncated": bool(props.get("summary_source_truncated")),
            }
        )
        heading = " ".join(str(part).strip() for part in [ref, title] if str(part or "").strip())
        summary_parts.append(f"{heading}：{summary}" if heading else summary)

    scope_summary = "；".join(summary_parts)
    if len(scope_summary) > 1400:
        scope_summary = scope_summary[:1400].rstrip() + "..."
    return {
        "profile_uid": f"{standard_id}:scope_summary",
        "standard_id": standard_id,
        "space_dir": str(space_dir),
        "status": "completed" if scope_summary else "empty",
        "summary_source": "local",
        "scope_summary": scope_summary,
        "keywords": _extract_kg_keywords(chapter_profiles, scope_summary),
        "chapters": chapter_profiles,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _maybe_generate_kg_profile_with_llm(
    profile: dict[str, Any],
    *,
    chapters: list[dict[str, Any]],
    llm_client: ResponsesAPIClient,
) -> dict[str, Any]:
    if not llm_client.enabled or not chapters:
        return profile
    try:
        payload = llm_client.create_structured_output(
            system_prompt=LLM_KG_SPACE_PROFILE_SYSTEM_PROMPT,
            user_prompt=build_kg_space_profile_prompt(str(profile.get("standard_id") or ""), _kg_prompt_chapters(chapters)),
            schema_name="kg_space_profile",
            schema=KG_SPACE_PROFILE_SCHEMA,
        )
    except ResponseAPIError as exc:
        profile["summary_source_error"] = str(exc)
        return profile
    scope_summary = _normalize_text(str(payload.get("scope_summary") or ""))
    if scope_summary:
        profile["scope_summary"] = scope_summary
        profile["status"] = "completed"
        profile["summary_source"] = "llm"
    profile["keywords"] = _dedupe_strings(payload.get("keywords") or profile.get("keywords") or [])[:24]
    return profile


def _kg_prompt_chapters(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for node in chapters:
        props = node.get("properties") or {}
        rows.append(
            {
                "node_uid": node.get("node_uid"),
                "ref": props.get("ref"),
                "title": props.get("title") or node.get("label"),
                "summary": props.get("summary") or "",
            }
        )
    return rows


def _extract_kg_keywords(chapters: list[dict[str, Any]], scope_summary: str) -> list[str]:
    values = []
    for chapter in chapters:
        values.append(chapter.get("title"))
    for keyword in [
        "大坝",
        "水库",
        "安全鉴定",
        "基础资料",
        "现场检查",
        "安全检测",
        "监测",
        "防洪",
        "渗流",
        "结构安全",
        "抗震",
        "金属结构",
        "运行管理",
        "评价报告",
    ]:
        if keyword in scope_summary:
            values.append(keyword)
    return _dedupe_strings(values)[:24]


def _build_kg_summary_embedding_documents(profile: dict[str, Any]) -> list[dict[str, Any]]:
    standard_id = str(profile.get("standard_id") or "")
    documents = []
    scope_summary = str(profile.get("scope_summary") or "").strip()
    if scope_summary:
        documents.append(
            {
                "node_uid": str(profile.get("profile_uid") or f"{standard_id}:scope_summary"),
                "standard_uid": standard_id,
                "node_type": "kg_scope_summary",
                "text": scope_summary,
            }
        )
    for chapter in profile.get("chapters") or []:
        node_uid = str(chapter.get("node_uid") or "").strip()
        summary = str(chapter.get("summary") or "").strip()
        if not node_uid or not summary:
            continue
        heading = " ".join(
            str(part).strip()
            for part in [chapter.get("ref"), chapter.get("title")]
            if str(part or "").strip()
        )
        documents.append(
            {
                "node_uid": f"{node_uid}#summary",
                "source_node_uid": node_uid,
                "standard_uid": standard_id,
                "node_type": "chapter_summary",
                "text": "\n".join(part for part in [heading, summary] if part).strip(),
            }
        )
    return documents


def _build_report_scope_summary(*, document_id: str, sections: list[dict[str, Any]], report_units: list[dict[str, Any]]) -> dict[str, Any]:
    section_profiles = []
    for section in sorted(sections, key=lambda item: (int(item.get("order_index") or 0), str(item.get("section_uid") or ""))):
        if section.get("section_kind") == "toc":
            continue
        section_uid = str(section.get("section_uid") or "").strip()
        title = str(section.get("title") or "").strip()
        if not section_uid or not title:
            continue
        summary = str(section.get("summary_overall") or section.get("summary") or section.get("content_preview") or "").strip()
        section_profiles.append(
            {
                "section_uid": section_uid,
                "title": title,
                "section_kind": section.get("section_kind"),
                "path": section.get("path") or [],
                "structural_path": section.get("structural_path") or [],
                "summary": summary,
                "member_count": int(section.get("member_count") or 0),
                "page_span": section.get("page_span") or [],
            }
        )

    summary_parts = []
    for section in section_profiles[:12]:
        if section.get("summary"):
            summary_parts.append(f"{section['title']}：{section['summary']}")
        else:
            summary_parts.append(section["title"])
    summary = "；".join(summary_parts)
    if not summary:
        unit_previews = [
            str(unit.get("text_normalized") or unit.get("text") or "").strip()
            for unit in sorted(report_units, key=lambda item: (int(item.get("order_index") or 0), str(item.get("unit_uid") or "")))[:12]
        ]
        summary = "；".join(value[:160] for value in unit_previews if value)
    if len(summary) > 1200:
        summary = summary[:1200].rstrip() + "..."
    return {
        "scope_summary_uid": f"report:{document_id}:scope_summary",
        "document_id": document_id,
        "status": "completed" if summary else "empty",
        "summary_source": "local",
        "summary": summary,
        "keywords": _extract_report_keywords(section_profiles, summary),
        "section_titles": [item["title"] for item in section_profiles[:12]],
        "sections": section_profiles,
        "source_section_count": len(section_profiles),
        "source_unit_count": len(report_units),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _maybe_generate_report_scope_with_llm(
    scope_summary: dict[str, Any],
    *,
    sections: list[dict[str, Any]],
    report_units: list[dict[str, Any]],
    llm_client: ResponsesAPIClient,
) -> dict[str, Any]:
    if not llm_client.enabled or not sections:
        return scope_summary
    try:
        payload = llm_client.create_structured_output(
            system_prompt=LLM_REPORT_SCOPE_SUMMARY_SYSTEM_PROMPT,
            user_prompt=build_report_scope_summary_prompt(str(scope_summary.get("document_id") or ""), sections, report_units),
            schema_name="report_scope_summary",
            schema=REPORT_SCOPE_SUMMARY_SCHEMA,
        )
    except ResponseAPIError as exc:
        scope_summary["summary_source_error"] = str(exc)
        return scope_summary
    summary = _normalize_text(str(payload.get("scope_summary") or ""))
    if summary:
        scope_summary["summary"] = summary
        scope_summary["status"] = "completed"
        scope_summary["summary_source"] = "llm"
    scope_summary["keywords"] = _dedupe_strings(payload.get("keywords") or scope_summary.get("keywords") or [])[:24]
    return scope_summary


def _extract_report_keywords(sections: list[dict[str, Any]], summary: str) -> list[str]:
    values = [section.get("title") for section in sections]
    for keyword in [
        "大坝",
        "水库",
        "安全鉴定",
        "现场检查",
        "安全检测",
        "监测",
        "防洪",
        "渗流",
        "结构安全",
        "抗震",
        "金属结构",
        "运行管理",
        "基础资料",
        "评价报告",
    ]:
        if keyword in summary:
            values.append(keyword)
    return _dedupe_strings(values)[:24]


def _scope_summary_document(scope_summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_uid": scope_summary["scope_summary_uid"],
        "document_id": scope_summary.get("document_id"),
        "node_type": "report_scope_summary",
        "text": scope_summary.get("summary") or "",
    }


def _merge_report_scope_node(space_dir: Path, scope_summary: dict[str, Any]) -> None:
    nodes_path = space_dir / "report_nodes.json"
    edges_path = space_dir / "report_edges.json"
    if not nodes_path.exists() or not edges_path.exists() or not scope_summary.get("summary"):
        return
    nodes = _read_json_array(nodes_path)
    edges = _read_json_array(edges_path)
    document_id = str(scope_summary.get("document_id") or space_dir.name)
    report_uid = f"report:{document_id}"
    scope_uid = str(scope_summary.get("scope_summary_uid") or "")
    nodes = [node for node in nodes if node.get("node_uid") != scope_uid]
    nodes.append(
        {
            "node_uid": scope_uid,
            "document_id": document_id,
            "node_type": "report_scope_summary",
            "label": "scope_summary",
            "text_content": scope_summary.get("summary") or "",
            "properties": scope_summary,
        }
    )
    edge_uid = _edge_uid("SUMMARIZES", scope_uid, report_uid)
    edges = [edge for edge in edges if edge.get("edge_uid") != edge_uid]
    edges.append(
        {
            "edge_uid": edge_uid,
            "document_id": document_id,
            "edge_type": "SUMMARIZES",
            "source_uid": scope_uid,
            "target_uid": report_uid,
            "properties": {},
        }
    )
    nodes_path.write_text(json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    edges_path.write_text(json.dumps(edges, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _merge_embedding_inputs(path: Path, documents: list[dict[str, Any]]) -> None:
    if not documents:
        return
    existing = _read_jsonl_by_node_uid(path)
    for document in documents:
        existing[str(document["node_uid"])] = document
    _write_jsonl(path, list(existing.values()))


def _merge_embedding_store(
    path: Path,
    documents: list[dict[str, Any]],
    *,
    config: Any,
    embedding_client: EmbeddingsAPIClient,
) -> int:
    if not documents or not embedding_client.enabled:
        return 0
    existing = _read_jsonl_by_node_uid(path)
    batch_size = max(1, config.embedding.batch_size)
    vector_count = 0
    for index in range(0, len(documents), batch_size):
        batch = documents[index : index + batch_size]
        vectors = embedding_client.embed_texts([item["text"] for item in batch])
        for item, vector in zip(batch, vectors):
            record = {
                **item,
                "embedding_model": config.embedding.model,
                "embedding_dimensions": len(vector),
                "embedding": vector,
            }
            existing[str(item["node_uid"])] = record
            vector_count += 1
    _write_jsonl(path, list(existing.values()))
    return vector_count


def _update_metrics(path: Path, updates: dict[str, Any]) -> None:
    metrics = _read_json_object(path)
    metrics.update(updates)
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _read_json_array(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _read_jsonl_by_node_uid(path: Path) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return items
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        node_uid = str(item.get("node_uid") or "")
        if node_uid:
            items[node_uid] = item
    return items


def _write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + ("\n" if items else ""),
        encoding="utf-8",
    )


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _dedupe_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _normalize_text(str(value or ""))
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _edge_uid(edge_type: str, source_uid: str, target_uid: str) -> str:
    import hashlib

    digest = hashlib.sha1(f"{edge_type}|{source_uid}|{target_uid}".encode("utf-8")).hexdigest()[:16]
    return f"edge:{digest}"


def _resolve_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def _format_result(kind: str, path: Path, result: dict[str, Any]) -> str:
    details = " ".join(f"{key}={value}" for key, value in result.items())
    return f"{kind} {path.name}: {details}"


if __name__ == "__main__":
    main()
