from __future__ import annotations

import logging
from typing import Any

from core.config import AppConfig


logger = logging.getLogger(__name__)


class PostgresGraphStore:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.postgres.enabled

    def persist_graph(
        self,
        *,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        embedding_map: dict[str, list[float]] | None = None,
    ) -> dict[str, int]:
        if not self.enabled:
            return {"persisted_nodes": 0, "persisted_edges": 0}

        import psycopg
        from pgvector.psycopg import register_vector
        from psycopg import sql
        from psycopg.types.json import Jsonb

        embedding_map = embedding_map or {}
        with psycopg.connect(
            host=self.config.postgres.host,
            port=self.config.postgres.port,
            dbname=self.config.postgres.database,
            user=self.config.postgres.user,
            password=self.config.postgres.password,
            sslmode=self.config.postgres.sslmode,
        ) as conn:
            register_vector(conn)
            self._ensure_schema(conn, sql)
            with conn.cursor() as cur:
                node_table = sql.SQL("{}.kg_nodes").format(sql.Identifier(self.config.postgres.db_schema))
                edge_table = sql.SQL("{}.kg_edges").format(sql.Identifier(self.config.postgres.db_schema))

                for node in nodes:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                node_uid,
                                standard_uid,
                                node_type,
                                label,
                                text_content,
                                properties,
                                embedding
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (node_uid) DO UPDATE SET
                                standard_uid = EXCLUDED.standard_uid,
                                node_type = EXCLUDED.node_type,
                                label = EXCLUDED.label,
                                text_content = EXCLUDED.text_content,
                                properties = EXCLUDED.properties,
                                embedding = COALESCE(EXCLUDED.embedding, {}.embedding),
                                updated_at = NOW()
                            """
                        ).format(node_table, node_table),
                        (
                            node["node_uid"],
                            node.get("standard_uid"),
                            node.get("node_type"),
                            node.get("label"),
                            node.get("text_content"),
                            Jsonb(node.get("properties") or {}),
                            embedding_map.get(node["node_uid"]),
                        ),
                    )

                for edge in edges:
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                edge_uid,
                                standard_uid,
                                edge_type,
                                source_uid,
                                target_uid,
                                properties
                            ) VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (edge_uid) DO UPDATE SET
                                standard_uid = EXCLUDED.standard_uid,
                                edge_type = EXCLUDED.edge_type,
                                source_uid = EXCLUDED.source_uid,
                                target_uid = EXCLUDED.target_uid,
                                properties = EXCLUDED.properties,
                                updated_at = NOW()
                            """
                        ).format(edge_table),
                        (
                            edge["edge_uid"],
                            edge.get("standard_uid"),
                            edge.get("edge_type"),
                            edge.get("source_uid"),
                            edge.get("target_uid"),
                            Jsonb(edge.get("properties") or {}),
                        ),
                    )
            conn.commit()
        logger.info("Persisted graph to PostgreSQL: %s nodes, %s edges", len(nodes), len(edges))
        return {"persisted_nodes": len(nodes), "persisted_edges": len(edges)}

    def _ensure_schema(self, conn: Any, sql_module: Any) -> None:
        schema = sql_module.Identifier(self.config.postgres.db_schema)
        dimensions = self.config.embedding.dimensions
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(sql_module.SQL("CREATE SCHEMA IF NOT EXISTS {}") .format(schema))
            cur.execute(
                sql_module.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.kg_nodes (
                        node_uid TEXT PRIMARY KEY,
                        standard_uid TEXT NOT NULL,
                        node_type TEXT NOT NULL,
                        label TEXT,
                        text_content TEXT,
                        properties JSONB NOT NULL DEFAULT '{}'::jsonb,
                        embedding vector({}),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                ).format(schema, sql_module.SQL(str(dimensions)))
            )
            cur.execute(
                sql_module.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.kg_edges (
                        edge_uid TEXT PRIMARY KEY,
                        standard_uid TEXT NOT NULL,
                        edge_type TEXT NOT NULL,
                        source_uid TEXT NOT NULL,
                        target_uid TEXT NOT NULL,
                        properties JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                ).format(schema)
            )
            cur.execute(
                sql_module.SQL("CREATE INDEX IF NOT EXISTS kg_nodes_standard_uid_idx ON {}.kg_nodes (standard_uid)").format(schema)
            )
            cur.execute(
                sql_module.SQL("CREATE INDEX IF NOT EXISTS kg_edges_standard_uid_idx ON {}.kg_edges (standard_uid)").format(schema)
            )
            cur.execute(
                sql_module.SQL("CREATE INDEX IF NOT EXISTS kg_edges_source_uid_idx ON {}.kg_edges (source_uid)").format(schema)
            )
            cur.execute(
                sql_module.SQL("CREATE INDEX IF NOT EXISTS kg_edges_target_uid_idx ON {}.kg_edges (target_uid)").format(schema)
            )

