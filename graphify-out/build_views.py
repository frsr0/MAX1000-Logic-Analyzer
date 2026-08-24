"""Build navigable sub-graphs from the canonical Graphify graph.

This is intentionally a filtering step: it does not re-extract source files or
call Gemini. The canonical graph remains graphify-out/graph.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from graphify.build import build_from_json
from graphify.cluster import cluster
from graphify.export import to_html, to_json


ROOT = Path("graphify-out")
MASTER = ROOT / "graph.json"
VIEWS = ROOT / "views"


def source_path(node: dict) -> str:
    return str(node.get("source_file") or "").replace("\\", "/").lower()


def make_view(
    slug: str,
    title: str,
    predicate: Callable[[dict], bool],
    nodes_by_id: dict[str, dict],
    links: list[dict],
    hyperedges: list[dict],
) -> dict:
    selected = {node_id for node_id, node in nodes_by_id.items() if predicate(node)}
    view_nodes = [nodes_by_id[node_id] for node_id in selected]
    view_links = [
        link
        for link in links
        if link.get("source") in selected and link.get("target") in selected
    ]
    view_hyperedges = []
    for hyperedge in hyperedges:
        members = [node_id for node_id in hyperedge.get("nodes", []) if node_id in selected]
        if len(members) >= 2:
            item = dict(hyperedge)
            item["nodes"] = members
            view_hyperedges.append(item)

    extraction = {
        "nodes": view_nodes,
        "edges": view_links,
        "hyperedges": view_hyperedges,
    }
    graph = build_from_json(extraction, directed=False, root=Path("."))
    communities = cluster(graph) if graph.number_of_nodes() else {}
    labels = {cid: f"{title} / Community {cid}" for cid in communities}

    out_dir = VIEWS / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    graph_path = out_dir / "graph.json"
    html_path = out_dir / "graph.html"
    to_json(graph, communities, str(graph_path), force=True, community_labels=labels)
    to_html(graph, communities, str(html_path), community_labels=labels)

    return {
        "slug": slug,
        "title": title,
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "communities": len(communities),
        "graph": str(graph_path).replace("\\", "/"),
        "html": str(html_path).replace("\\", "/"),
    }


def main() -> None:
    data = json.loads(MASTER.read_text(encoding="utf-8"))
    nodes_by_id = {node["id"]: node for node in data.get("nodes", []) if node.get("id")}
    links = data.get("links", [])
    hyperedges = data.get("hyperedges", [])

    def is_frontend(node: dict) -> bool:
        path = source_path(node)
        return (
            path.startswith("frontend/")
            or path.startswith("docs/wiki/frontend/")
            or path.startswith("backend/app/api/")
            or path.startswith("backend/app/websocket/")
            or path in {"backend/app/config.py", "backend/app/capture/session.py"}
        )

    def is_backend(node: dict) -> bool:
        path = source_path(node)
        return (
            path.startswith("backend/")
            or path.startswith("docs/wiki/backend/")
            or path.startswith("frontend/src/api/")
            or path.startswith("frontend/src/state/")
        )

    def is_hardware(node: dict) -> bool:
        path = source_path(node)
        return (
            path.startswith("hdl/")
            or path.startswith("host/")
            or path.startswith("docs/wiki/hdl/")
            or path.startswith("backend/app/hardware/")
            or path.startswith("backend/app/capture/")
            or path.startswith("backend/app/generator/")
            or path.startswith("backend/app/serial/")
        )

    def is_legacy(node: dict) -> bool:
        path = source_path(node)
        label = str(node.get("label") or "").lower()
        return (
            path.startswith("host/driver/")
            or "legacy" in path
            or "legacy" in label
            or "existing_host_adapter" in path
            or path == "backend/app/hardware/base.py"
            or path == "docs/wiki/backend/existing-host-adapter.md"
        )

    specs = [
        ("frontend", "Frontend", is_frontend),
        ("backend", "Backend", is_backend),
        ("hardware-boundary", "Hardware Boundary", is_hardware),
        ("legacy", "Legacy / Compatibility", is_legacy),
    ]
    summaries = [make_view(slug, title, predicate, nodes_by_id, links, hyperedges) for slug, title, predicate in specs]

    VIEWS.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Graphify derived views",
        "",
        "These are filtered views of the canonical graph at `../graph.json`; they do not re-extract source files.",
        "",
    ]
    for summary in summaries:
        rel_dir = summary["slug"]
        lines.extend(
            [
                f"## {summary['title']}",
                "",
                f"{summary['nodes']:,} nodes · {summary['edges']:,} edges · {summary['communities']:,} communities",
                f"- [Open HTML graph](./{rel_dir}/graph.html)",
                f"- [Raw JSON](./{rel_dir}/graph.json)",
                "",
            ]
        )
    (VIEWS / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
