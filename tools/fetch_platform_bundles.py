"""Reproducible helper: read the platform's own JS bundles for API evidence.

How the endpoint catalogue in ``adapters/selectors.py`` was derived, made
repeatable. Given a saved page, this:

1. reads the webpack runtime from the saved app entry chunk to recover the
   ``{chunkId: contentHash}`` map and the ``static/js/…`` asset template;
2. downloads the lazy chunks that a route needs (default ``/play-center``);
3. greps them for ``/v1/…`` path literals and prints the calling code.

Everything it fetches is a **public static asset** of the same web app — the same
files any anonymous visitor's browser downloads. It uses no session, no cookies
and no account state, and it never touches an API endpoint.

Usage::

    python tools/fetch_platform_bundles.py --page "path/to/课堂实录.html" \\
        --files "path/to/课堂实录_files" --route /play-center

    python tools/fetch_platform_bundles.py --saved-entry path/to/index.js.下载

This is a development tool. It is not needed to export a course.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://v.sjtu.edu.cn/jy-application-resourcemanage-ui/"

# e.u = i => "static/js/" + (2683===i ? "chunk-svg" : i) + "." + {…}[i] + ".js"
_CHUNK_MAP_RE = re.compile(r"\{((?:\d+:\"[0-9a-f]{8}\",?){3,})\}")
_CHUNK_PAIR_RE = re.compile(r'(\d+):"([0-9a-f]{8})"')
_ROUTE_RE = re.compile(r'\{path:"([^"]+)"[^{}]{0,600}?name:"([^"]+)"')
_LAZY_IDS_RE = re.compile(r"e\.e\((\d+)\)")
_API_PATH_RE = re.compile(r'["`](/v1/[^"`\s]{2,200}?)["`]')


def load_entry_text(entry: Path) -> str:
    return entry.read_text(encoding="utf-8", errors="replace")


def parse_chunk_map(text: str) -> dict[str, str]:
    """Recover the biggest ``{id: hash}`` table, which is the chunk hash map."""
    best: dict[str, str] = {}
    for match in _CHUNK_MAP_RE.finditer(text):
        pairs = dict(_CHUNK_PAIR_RE.findall(match.group(1)))
        if len(pairs) > len(best):
            best = pairs
    return best


def parse_routes(text: str) -> dict[str, str]:
    return {path: name for path, name in _ROUTE_RE.findall(text)}


def route_chunk_ids(text: str, route: str) -> list[str]:
    """Chunk ids a route declares in its dynamic import.

    The window is bounded by the *next* route declaration so that a neighbouring
    route's chunks are not swept in.
    """
    marker = f'path:"{route}"'
    index = text.find(marker)
    if index == -1:
        raise SystemExit(f"route {route!r} not found in the bundle")
    start = index + len(marker)
    next_route = text.find('{path:"', start)
    window = text[start:next_route] if next_route != -1 else text[start : start + 4000]
    seen: list[str] = []
    for cid in _LAZY_IDS_RE.findall(window):
        if cid not in seen:
            seen.append(cid)
    return seen


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size:
        return dest
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
        data = response.read()
    dest.write_bytes(data)
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recover the platform's lazy-chunk map and grep its public JS bundles "
            "for API path literals. Read-only; no authentication is used."
        )
    )
    parser.add_argument(
        "--saved-entry",
        help="saved app entry chunk (the big index.*.js file from *_files/)",
    )
    parser.add_argument("--page", help="saved page HTML (used to locate --files)")
    parser.add_argument("--files", help="the saved *_files directory")
    parser.add_argument("--route", default="/play-center", help="route to enumerate")
    parser.add_argument("--base", default=DEFAULT_BASE, help="asset base URL")
    parser.add_argument("--out", default="local_samples/_analysis/chunks", help="download cache")
    parser.add_argument("--limit", type=int, default=0, help="max chunks to download (0 = all)")
    parser.add_argument("--json", help="also write the discovered paths to this JSON file")
    args = parser.parse_args(argv)

    entry = Path(args.saved_entry) if args.saved_entry else None
    if entry is None and args.files:
        candidates = sorted(Path(args.files).glob("index*.js*"))
        if not candidates:
            raise SystemExit(f"no index*.js* found in {args.files}")
        entry = candidates[0]
    if entry is None or not entry.exists():
        raise SystemExit("provide --saved-entry or --files pointing at the saved assets")

    text = load_entry_text(entry)
    print(f"entry: {entry} ({len(text):,} chars)")

    chunk_map = parse_chunk_map(text)
    print(f"lazy chunks in the hash map: {len(chunk_map)}")

    routes = parse_routes(text)
    print(f"routes discovered: {len(routes)}")

    ids = route_chunk_ids(text, args.route)
    print(f"{args.route} needs {len(ids)} chunks")
    if args.limit:
        ids = ids[: args.limit]

    out = Path(args.out)
    found: dict[str, list[str]] = {}

    for cid in ids:
        digest = chunk_map.get(cid)
        if not digest:
            print(f"  chunk {cid}: no hash in map, skipped")
            continue
        url = f"{args.base.rstrip('/')}/static/js/{cid}.{digest}.js"
        dest = out / f"{cid}.{digest}.js"
        try:
            download(url, dest)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  chunk {cid}: download failed ({exc})")
            continue
        body = dest.read_text(encoding="utf-8", errors="replace")
        paths = sorted(set(_API_PATH_RE.findall(body)))
        if paths:
            found[cid] = paths
            print(f"  chunk {cid}: {len(paths)} API paths")

    all_paths = sorted({p for paths in found.values() for p in paths})
    print(f"\nunique /v1 paths across downloaded chunks: {len(all_paths)}")
    for path in all_paths:
        print("  ", path)

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps({"by_chunk": found, "all": all_paths}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")

    print(
        "\nReminder: a path literal proves the endpoint EXISTS. It does not prove "
        "its query parameters or response shape. Confirm those in DevTools before "
        "relying on them."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
