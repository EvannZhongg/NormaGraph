from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.config import get_config
from repositories.postgres_graph_store import PostgresGraphStore


def main() -> None:
    parser = argparse.ArgumentParser(description='Ensure the configured PostgreSQL database, schema, and pgvector tables exist.')
    parser.add_argument('--force-enable', action='store_true', help='Run the bootstrap even if postgres.enabled is false in config.yaml.')
    parser.add_argument('--database', help='Optional database name override for this run only.')
    args = parser.parse_args()

    config = get_config()
    if args.force_enable:
        config.postgres.enabled = True
    if args.database:
        config.postgres.database = args.database

    store = PostgresGraphStore(config)
    result = store.ensure_storage_ready()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
