from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
import time
from typing import Any
from xml.etree import ElementTree as ET

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from adapters.llm_client import ResponseAPIError, ResponsesAPIClient
from core.config import get_config
from prompts import LLM_REQUIREMENT_EXTRACTION_SYSTEM_PROMPT, build_clause_extraction_prompt


JSON_SCHEMA_NAME = "clause_graph_extraction_batch"


@dataclass
class AttemptResult:
    mode: str
    attempt: int
    http_ok: bool
    response_status: str | None
    has_output_text_field: bool
    extracted_text: bool
    parsed_ok: bool
    error: str | None
    text_preview: str | None
    response_keys: list[str]


def load_clause_batch(kg_space_dir: Path, batch_size: int) -> tuple[str, list[dict[str, Any]]]:
    clauses = json.loads((kg_space_dir / "clauses.json").read_text(encoding="utf-8"))
    manifest = json.loads((kg_space_dir / "space_manifest.json").read_text(encoding="utf-8"))
    standard_uid = manifest.get("standard_id") or "unknown-standard"
    eligible = [item for item in clauses if item.get("body_kind") == "main"]
    batch = eligible[: max(1, batch_size)]
    if not batch:
        raise RuntimeError(f"No eligible clauses found in {kg_space_dir}")
    return standard_uid, batch


def build_xml_system_prompt() -> str:
    return """你是规范条文结构化抽取器。

请把输入条文抽取成 XML，并且只输出 XML，不要输出解释。

必须满足：
1. 根节点是 <batch>。
2. 每个条文输出一个 <item>。
3. 每个 <item> 必须有 <clause_uid>、<clause_ref>、<concepts>、<requirements>。
4. 如果没有规范性要求，仍然要输出该 item，并让 <requirements /> 为空。
5. requirement 节点内允许包含：
   <requirement_text> <modality> <subject> <action_list> <object_list> <applicability_rule>
   <judgement_criteria_list> <evidence_expected_list> <domain_tags_list> <cited_targets> <confidence>
6. modality 只能是 must、should、may、forbidden、conditional 之一。
7. 不要遗漏任何输入 clause_uid。
"""


def build_xml_user_prompt(standard_uid: str, clauses: list[dict[str, Any]]) -> str:
    payload = {
        "standard_uid": standard_uid,
        "task": "请抽取规范要求，并输出为 XML。",
        "clauses": [
            {
                "clause_uid": clause["clause_uid"],
                "clause_ref": clause["clause_ref"],
                "heading_path": clause.get("heading_path", []),
                "chapter_ref": clause.get("chapter_ref"),
                "section_ref": clause.get("section_ref"),
                "source_text_normalized": clause.get("source_text_normalized") or clause.get("source_text"),
                "list_items": [item.get("text_normalized") or item.get("text") for item in clause.get("list_items", [])],
            }
            for clause in clauses
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def extract_text_from_payload(payload: dict[str, Any]) -> tuple[bool, str | None]:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return True, output_text.strip()

    text_parts: list[str] = []
    for output_item in payload.get("output", []):
        for content_item in output_item.get("content", []):
            value = content_item.get("text")
            if isinstance(value, str) and value.strip():
                text_parts.append(value.strip())
            elif isinstance(value, dict):
                nested = value.get("value") or value.get("text")
                if isinstance(nested, str) and nested.strip():
                    text_parts.append(nested.strip())

    if text_parts:
        return True, "\n".join(text_parts)

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return True, content.strip()
        if isinstance(content, list):
            merged: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    merged.append(text.strip())
            if merged:
                return True, "\n".join(merged)

    return False, None


def parse_xml_output(raw_text: str) -> None:
    root = ET.fromstring(raw_text)
    if root.tag != "batch":
        raise ValueError(f"XML root tag must be <batch>, got <{root.tag}>")


def request_json_schema_mode(
    config,
    client: ResponsesAPIClient,
    standard_uid: str,
    clauses: list[dict[str, Any]],
    schema: dict[str, Any],
) -> AttemptResult:
    url = f"{config.llm.base_url.rstrip('/')}/responses"
    headers = {
        "Authorization": f"Bearer {config.llm.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.llm.model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": LLM_REQUIREMENT_EXTRACTION_SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": build_clause_extraction_prompt(standard_uid, clauses)}],
            },
        ],
        "temperature": config.llm.temperature,
        "max_output_tokens": config.llm.max_output_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": JSON_SCHEMA_NAME,
                "schema": schema,
                "strict": True,
            },
        },
    }
    if config.llm.enable_thinking is not None:
        payload["enable_thinking"] = config.llm.enable_thinking

    with httpx.Client(timeout=config.llm.timeout_seconds) as http_client:
        response = http_client.post(url, headers=headers, json=payload)
    data = response.json()
    extracted, raw_text = extract_text_from_payload(data)
    parsed_ok = False
    error = None
    if response.is_success:
        try:
            client._raise_for_response_status(data)
            if not extracted or not raw_text:
                raise ResponseAPIError("Responses API response did not contain output_text.")
            client._parse_json_output(raw_text)
            parsed_ok = True
        except Exception as exc:
            error = str(exc)
    else:
        error = f"HTTP {response.status_code}"
    return AttemptResult(
        mode="json_schema",
        attempt=0,
        http_ok=response.is_success,
        response_status=str(data.get("status")) if isinstance(data, dict) else None,
        has_output_text_field=isinstance(data.get("output_text"), str) and bool(str(data.get("output_text")).strip()),
        extracted_text=extracted,
        parsed_ok=parsed_ok,
        error=error,
        text_preview=(raw_text or "")[:200] if raw_text else None,
        response_keys=sorted(data.keys()) if isinstance(data, dict) else [],
    )


def request_xml_mode(config, standard_uid: str, clauses: list[dict[str, Any]]) -> AttemptResult:
    url = f"{config.llm.base_url.rstrip('/')}/responses"
    headers = {
        "Authorization": f"Bearer {config.llm.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.llm.model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": build_xml_system_prompt()}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": build_xml_user_prompt(standard_uid, clauses)}],
            },
        ],
        "temperature": config.llm.temperature,
        "max_output_tokens": config.llm.max_output_tokens,
    }
    if config.llm.enable_thinking is not None:
        payload["enable_thinking"] = config.llm.enable_thinking

    with httpx.Client(timeout=config.llm.timeout_seconds) as http_client:
        response = http_client.post(url, headers=headers, json=payload)
    data = response.json()
    extracted, raw_text = extract_text_from_payload(data)
    parsed_ok = False
    error = None
    if response.is_success:
        try:
            if isinstance(data, dict):
                response_status = data.get("status")
                if response_status in {"failed", "cancelled", "incomplete"}:
                    raise ResponseAPIError(f"Responses API returned non-success status: {response_status}")
            if not extracted or not raw_text:
                raise ResponseAPIError("Responses API response did not contain output_text.")
            parse_xml_output(raw_text)
            parsed_ok = True
        except Exception as exc:
            error = str(exc)
    else:
        error = f"HTTP {response.status_code}"
    return AttemptResult(
        mode="xml_text",
        attempt=0,
        http_ok=response.is_success,
        response_status=str(data.get("status")) if isinstance(data, dict) else None,
        has_output_text_field=isinstance(data.get("output_text"), str) and bool(str(data.get("output_text")).strip()),
        extracted_text=extracted,
        parsed_ok=parsed_ok,
        error=error,
        text_preview=(raw_text or "")[:200] if raw_text else None,
        response_keys=sorted(data.keys()) if isinstance(data, dict) else [],
    )


def summarize(results: list[AttemptResult]) -> dict[str, Any]:
    by_mode: dict[str, list[AttemptResult]] = {}
    for item in results:
        by_mode.setdefault(item.mode, []).append(item)

    summary: dict[str, Any] = {}
    for mode, mode_results in sorted(by_mode.items()):
        error_counts = Counter(item.error or "ok" for item in mode_results)
        summary[mode] = {
            "attempt_count": len(mode_results),
            "http_ok_count": sum(1 for item in mode_results if item.http_ok),
            "has_output_text_field_count": sum(1 for item in mode_results if item.has_output_text_field),
            "extracted_text_count": sum(1 for item in mode_results if item.extracted_text),
            "parsed_ok_count": sum(1 for item in mode_results if item.parsed_ok),
            "error_counts": dict(sorted(error_counts.items())),
        }
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare JSON Schema output with XML text output on the current Responses endpoint.")
    parser.add_argument(
        "--kg-space",
        default="data/kg_spaces/sl258-2017",
        help="KG space used to pick a representative clause batch.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=6,
        help="How many clauses to include in the test prompt.",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=10,
        help="How many times to run each mode.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="Optional delay between attempts.",
    )
    parser.add_argument(
        "--output",
        default="data/test-temp/llm-output-mode-compare.json",
        help="Where to write the comparison report JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = get_config().model_copy(deep=True)
    client = ResponsesAPIClient(config)
    if not client.enabled:
        print(f"LLM client is not configured. Set {config.llm.api_key_env} in .env first.", file=sys.stderr)
        return 2

    kg_space_dir = (PROJECT_ROOT / args.kg_space).resolve()
    output_path = (PROJECT_ROOT / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    standard_uid, clauses = load_clause_batch(kg_space_dir, args.batch_size)
    schema = json.loads((config.schema_dir / "clause_graph_extraction.schema.json").read_text(encoding="utf-8"))

    results: list[AttemptResult] = []
    for attempt in range(1, max(1, args.attempts) + 1):
        print(f"Attempt {attempt}/{args.attempts} - json_schema", flush=True)
        result = request_json_schema_mode(config, client, standard_uid, clauses, schema)
        result.attempt = attempt
        results.append(result)
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

        print(f"Attempt {attempt}/{args.attempts} - xml_text", flush=True)
        result = request_xml_mode(config, standard_uid, clauses)
        result.attempt = attempt
        results.append(result)
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    report = {
        "metadata": {
            "kg_space_dir": str(kg_space_dir),
            "model": config.llm.model,
            "base_url": config.llm.base_url,
            "batch_size": len(clauses),
            "attempts_per_mode": max(1, args.attempts),
        },
        "summary": summarize(results),
        "results": [asdict(item) for item in results],
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\nSummary")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"\nDetailed report written to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
