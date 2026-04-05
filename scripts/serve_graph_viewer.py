from __future__ import annotations

import argparse
from functools import partial
import http.server
from pathlib import Path
import sys
from urllib.parse import urlencode
import webbrowser

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.config import get_config
from repositories.standard_registry import StandardRegistry


def _is_graph_dataset_dir(path: Path) -> bool:
    return (path / 'graph_nodes.json').exists() and (path / 'graph_edges.json').exists()


def _resolve_dataset_dir(path: Path) -> Path:
    resolved = path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
    if _is_graph_dataset_dir(resolved):
        return resolved

    if resolved.exists() and resolved.is_dir():
        registry = StandardRegistry(get_config().registry_path)
        detail = registry.find_by_document_id(resolved.name)
        if detail and detail.graphSpaceDir:
            graph_space_dir = Path(detail.graphSpaceDir)
            if _is_graph_dataset_dir(graph_space_dir):
                return graph_space_dir

    return resolved


def _relative_url(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _root_relative_url(path: Path) -> str:
    return '/' + _relative_url(path).lstrip('/')


def build_viewer_url(host: str, port: int, dataset_dir: Path | None, title: str | None) -> str:
    base = f'http://{host}:{port}/viewer/index.html'
    if dataset_dir is None:
        return base
    nodes = dataset_dir / 'graph_nodes.json'
    edges = dataset_dir / 'graph_edges.json'
    requirements = dataset_dir / 'requirements.json'
    params = {
        'nodes': _root_relative_url(nodes),
        'edges': _root_relative_url(edges),
    }
    if requirements.exists():
        params['requirements'] = _root_relative_url(requirements)
    if title:
        params['title'] = title
    return base + '?' + urlencode(params)


def main() -> None:
    parser = argparse.ArgumentParser(description='Serve the standalone graph viewer without using the FastAPI backend.')
    parser.add_argument('--artifact-dir', help='Graph space directory or standard artifact directory to preload in the viewer.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8030)
    parser.add_argument('--title', help='Optional viewer title shown as the source label.')
    parser.add_argument('--no-open', action='store_true', help='Do not automatically open the browser.')
    args = parser.parse_args()

    dataset_dir = _resolve_dataset_dir(Path(args.artifact_dir)) if args.artifact_dir else None
    if dataset_dir and not dataset_dir.exists():
        raise SystemExit(f'dataset directory was not found: {dataset_dir}')
    if dataset_dir and not _is_graph_dataset_dir(dataset_dir):
        raise SystemExit(f'graph dataset files were not found in: {dataset_dir}')

    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(PROJECT_ROOT))
    server = http.server.ThreadingHTTPServer((args.host, args.port), handler)
    url = build_viewer_url(args.host, args.port, dataset_dir, args.title)
    print(f'Graph viewer is available at: {url}')
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down graph viewer server...')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
