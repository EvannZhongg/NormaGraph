from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
from typing import Any

from core.config import AppConfig


@dataclass
class GraphBuildResult:
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    embedding_documents: list[dict[str, Any]]


class GraphMaterializationService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def build(
        self,
        *,
        standard_uid: str,
        structure_nodes: list[dict[str, Any]],
        clauses: list[dict[str, Any]],
        requirements: list[dict[str, Any]],
    ) -> GraphBuildResult:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        embedding_documents: list[dict[str, Any]] = []
        embedding_target_types = set(self.config.embedding.target_node_types)
        seen_nodes: set[str] = set()
        seen_edges: set[str] = set()
        clause_map = {clause["clause_uid"]: clause for clause in clauses}
        requirement_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for requirement in requirements:
            requirement_groups[requirement["parent_clause_uid"]].append(requirement)

        def add_node(node: dict[str, Any]) -> None:
            node_uid = node["node_uid"]
            if node_uid in seen_nodes:
                return
            seen_nodes.add(node_uid)
            nodes.append(node)
            if node.get("node_type") in embedding_target_types:
                embedding_text = node.get("embedding_text", node.get("text_content"))
                if isinstance(embedding_text, str) and embedding_text.strip():
                    embedding_documents.append(
                        {
                            "node_uid": node_uid,
                            "standard_uid": standard_uid,
                            "node_type": node["node_type"],
                            "text": embedding_text.strip(),
                        }
                    )

        def add_edge(edge_type: str, source_uid: str, target_uid: str, properties: dict[str, Any] | None = None) -> None:
            edge_uid = self._edge_uid(edge_type, source_uid, target_uid)
            if edge_uid in seen_edges:
                return
            seen_edges.add(edge_uid)
            edges.append(
                {
                    "edge_uid": edge_uid,
                    "standard_uid": standard_uid,
                    "edge_type": edge_type,
                    "source_uid": source_uid,
                    "target_uid": target_uid,
                    "properties": properties or {},
                }
            )

        add_node(
            {
                "node_uid": standard_uid,
                "standard_uid": standard_uid,
                "node_type": "standard",
                "label": standard_uid,
                "text_content": standard_uid,
                "properties": {"standard_uid": standard_uid},
            }
        )

        children_by_parent: dict[str, list[str]] = defaultdict(list)
        for structure_node in structure_nodes:
            add_node(
                {
                    "node_uid": structure_node["node_uid"],
                    "standard_uid": standard_uid,
                    "node_type": structure_node["node_type"],
                    "label": structure_node.get("title") or structure_node.get("ref"),
                    "text_content": structure_node.get("raw_text") or structure_node.get("title") or structure_node.get("ref"),
                    "properties": structure_node,
                }
            )
            parent_uid = structure_node.get("parent_uid") or standard_uid
            add_edge("CONTAINS", parent_uid, structure_node["node_uid"])
            children_by_parent[parent_uid].append(structure_node["node_uid"])

        for clause in clauses:
            text_content = clause.get("source_text_normalized") or clause.get("source_text") or clause["clause_ref"]
            add_node(
                {
                    "node_uid": clause["clause_uid"],
                    "standard_uid": standard_uid,
                    "node_type": "clause",
                    "label": clause["clause_ref"],
                    "text_content": f"{' > '.join(clause.get('heading_path', []))}\n{clause['clause_ref']} {text_content}".strip(),
                    "properties": clause,
                }
            )
            parent_uid = clause.get("parent_uid") or standard_uid
            add_edge("CONTAINS", parent_uid, clause["clause_uid"])
            children_by_parent[parent_uid].append(clause["clause_uid"])
            for table in clause.get("tables", []):
                table_label = table.get("table_caption") or table.get("table_title") or table.get("table_ref") or table["table_uid"]
                table_text_content = '\n'.join(
                    part for part in [' > '.join(clause.get('heading_path', [])), clause['clause_ref'], table_label] if part
                ).strip()
                table_embedding_text = '\n'.join(
                    part
                    for part in [
                        ' > '.join(clause.get('heading_path', [])),
                        clause['clause_ref'],
                        table.get("table_title") or table_label,
                        table.get("table_html"),
                    ]
                    if part
                ).strip()
                add_node(
                    {
                        "node_uid": table["table_uid"],
                        "standard_uid": standard_uid,
                        "node_type": "table",
                        "label": table_label,
                        "text_content": table_text_content,
                        "embedding_text": table_embedding_text,
                        "properties": table,
                    }
                )
                add_edge("CONTAINS", clause["clause_uid"], table["table_uid"])
                children_by_parent[clause["clause_uid"]].append(table["table_uid"])

        for siblings in children_by_parent.values():
            for left, right in zip(siblings, siblings[1:]):
                add_edge("NEXT", left, right)

        for clause_uid, items in requirement_groups.items():
            clause = clause_map.get(clause_uid)
            clause_concepts = clause.get("concepts", []) if clause else []
            for requirement in items:
                add_node(
                    {
                        "node_uid": requirement["requirement_uid"],
                        "standard_uid": standard_uid,
                        "node_type": "requirement",
                        "label": requirement["requirement_text"],
                        "text_content": requirement["requirement_text"],
                        "properties": requirement,
                    }
                )
                add_edge("DERIVES_REQUIREMENT", requirement["parent_clause_uid"], requirement["requirement_uid"])

                for concept in self._dedupe_strings([*clause_concepts, *requirement.get("domain_tags", [])]):
                    concept_uid = self._concept_uid(concept)
                    add_node(
                        {
                            "node_uid": concept_uid,
                            "standard_uid": standard_uid,
                            "node_type": "concept",
                            "label": concept,
                            "text_content": concept,
                            "properties": {"name": concept},
                        }
                    )
                    add_edge("ABOUT", requirement["requirement_uid"], concept_uid)

                for citation in requirement.get("cited_targets", []):
                    standard_code = citation.get("standard_code")
                    if not standard_code:
                        continue
                    ref_uid = self._reference_standard_uid(standard_code)
                    add_node(
                        {
                            "node_uid": ref_uid,
                            "standard_uid": standard_uid,
                            "node_type": "reference_standard",
                            "label": standard_code,
                            "text_content": standard_code,
                            "properties": {"standard_code": standard_code},
                        }
                    )
                    add_edge(
                        "CITES_STANDARD",
                        requirement["requirement_uid"],
                        ref_uid,
                        {"citation_type": citation.get("citation_type", "unknown"), "clause_ref": citation.get("clause_ref")},
                    )

        return GraphBuildResult(nodes=nodes, edges=edges, embedding_documents=embedding_documents)

    def _concept_uid(self, concept: str) -> str:
        digest = hashlib.sha1(concept.encode("utf-8")).hexdigest()[:12]
        return f"concept:{digest}"

    def _reference_standard_uid(self, standard_code: str) -> str:
        digest = hashlib.sha1(standard_code.lower().encode("utf-8")).hexdigest()[:12]
        return f"reference-standard:{digest}"

    def _edge_uid(self, edge_type: str, source_uid: str, target_uid: str) -> str:
        digest = hashlib.sha1(f"{edge_type}|{source_uid}|{target_uid}".encode("utf-8")).hexdigest()[:16]
        return f"edge:{digest}"

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if not value:
                continue
            item = value.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result
