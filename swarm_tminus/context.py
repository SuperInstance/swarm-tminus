"""PLATO tile context formatter + fleet fetcher.

Python port of terax-fleet-modules/src/context-fetcher.ts
(SuperInstance/terax-fleet-modules). stdlib-only — uses `urllib.request`
in place of `fetch()`.

Tile shape mirrors the upstream `PlatoTile`:
    Tile(domain, question, answer, confidence, tags=(), source="")

Two functions:
    format_tiles_as_context(tiles)         - render tiles for an AI prompt
    fetch_fleet_context(endpoint, rooms)   - GET tiles from PLATO server

`fetch_fleet_context` mirrors JavaScript's `Promise.allSettled`:
partial failure (one room's GET throws) does NOT abort other rooms.
On connection error, returns [] (degraded mode). NEVER raises.

Wire format:
    GET {endpoint}/rooms                  -> {"rooms": ["r1","r2",...]}
    GET {endpoint}/room/{room}            -> {"tiles": [...], "tile_count": N}
"""
from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Union
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError


# Default PLATO endpoint port (matches terax-fleet-modules/plato-bridge.ts:1)
PLATO_URL = "http://localhost:8847"


@dataclass(frozen=True)
class Tile:
    """A Q&A memory record from PLATO.

    Mirrors PlatoTile in terax-fleet-modules/plato-bridge.ts:3-10.
    `domain` is the room/topic; `question`/`answer` the canonical pair;
    `confidence` is a [0,1] weight; tags are a list of taxonomic labels.
    """
    domain: str
    question: str
    answer: str
    confidence: float
    tags: tuple[str, ...] = ()
    source: str = ""
    _hash: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Tile":
        # Missing required fields (domain, question, answer) -> raise;
        # caller is responsible for catching and skipping.
        if not isinstance(d, dict):
            raise TypeError(f"Tile requires dict, got {type(d).__name__}")
        if not d.get("domain"):
            raise ValueError("Tile requires 'domain'")
        if not d.get("question"):
            raise ValueError("Tile requires 'question'")
        if not d.get("answer"):
            raise ValueError("Tile requires 'answer'")
        if "confidence" not in d:
            raise ValueError("Tile requires 'confidence'")
        return cls(
            domain=str(d["domain"]),
            question=str(d["question"]),
            answer=str(d["answer"]),
            confidence=float(d["confidence"]),
            tags=tuple(d.get("tags") or ()),
            source=str(d.get("source", "") or ""),
            _hash=str(d.get("_hash", "") or ""),
        )


def format_tiles_as_context(
    tiles: list[Tile],
    max_chars: int = 8000,
    include_metadata: bool = True,
) -> str:
    """Render tiles as a context block suitable for AI prompts.

    Format:
        <fleet-context>
          <room>domain1</room>
          <tile>
            Q: question1
            A: answer1
            confidence: 0.92 | tags: state, anchor, swarm
          </tile>
          <tile>
            Q: question2
            A: answer2
            confidence: 0.85 | tags: ...
          </tile>
          ...
        </fleet-context>

    When `max_chars` is exceeded, tiles are sorted by confidence (highest
    first) and truncated from the bottom — never silently drops the best
    answers. With `include_metadata=False`, the confidence/tags line is
    omitted (useful for smaller/cheaper models).

    Empty input returns "".
    """
    if not tiles:
        return ""

    # Sort by confidence, highest first, so truncation drops the weakest.
    ordered = sorted(tiles, key=lambda t: t.confidence, reverse=True)

    # Build initial text and check length. Truncate from the bottom if too long.
    text = _build_tiles_xml(ordered, include_metadata)
    if len(text) <= max_chars:
        return text

    # Truncate while preserving header and dropping tiles from the bottom
    # (lowest-confidence first, since we sorted highest-first).
    while ordered and len(text) > max_chars:
        ordered.pop()  # remove lowest-confidence
        text = _build_tiles_xml(ordered, include_metadata)

    return text

    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text

    # Truncate while preserving header and dropping tiles from the bottom
    # (lowest-confidence first, since we sorted highest-first).
    while ordered and len(text) > max_chars:
        ordered.pop()  # remove lowest-confidence
        text = "\n".join(lines_rebuild(ordered, include_metadata))

    return text


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


def _build_tiles_xml(tiles: list[Tile], include_metadata: bool) -> str:
    """Build the XML text for a list of tiles.

    Used both by ``format_tiles_as_context`` for the initial render and for
    the truncation path, so the format stays consistent (source field in
    metadata, balanced room tags, uniform indent).

    Rooms are emitted as inline header markers (``<room>DOMAIN</room>``)
    before the first tile of each domain — they act as separators, not as
    content-wrapping tags. This keeps the XML well-formed (balanced tags)
    without the structural complexity of wrapping tiles inside room tags.
    """
    lines = ["<fleet-context>"]
    seen_domains: set[str] = set()
    for tile in tiles:
        if tile.domain and tile.domain not in seen_domains:
            lines.append(f"  <room>{_xml_escape(tile.domain)}</room>")
            seen_domains.add(tile.domain)
        lines.append("  <tile>")
        lines.append(f"    Q: {_xml_escape(tile.question)}")
        lines.append(f"    A: {_xml_escape(tile.answer)}")
        if include_metadata:
            meta_parts = [f"confidence: {tile.confidence:.2f}"]
            if tile.tags:
                meta_parts.append(f"tags: {', '.join(tile.tags)}")
            if tile.source:
                meta_parts.append(f"source: {tile.source}")
            lines.append(f"    {' | '.join(meta_parts)}")
        lines.append("  </tile>")
    lines.append("</fleet-context>")
    return "\n".join(lines)


def fetch_fleet_context(
    endpoint: str = PLATO_URL,
    rooms: Optional[list[str]] = None,
    timeout_sec: float = 5.0,
) -> list[Tile]:
    """Fetch tiles from PLATO server via stdlib `urllib.request`.

    Mirrors JavaScript's `Promise.allSettled`: a failure on one room's
    GET does NOT abort the others. Each room's response is parsed
    independently. Connection errors return [] (degraded mode) — this
    function NEVER raises.

    Wire shape:
        endpoint/rooms                       -> {"rooms": [...]}
        endpoint/room/{room}                 -> {"tiles": [...], "tile_count": N}
    """
    if rooms is None:
        rooms = _fetch_rooms(endpoint, timeout_sec)
        if rooms is None:
            return []

    tiles: list[Tile] = []
    for room in rooms:
        try:
            url = f"{endpoint.rstrip('/')}/room/{room}"
            req = urlrequest.Request(url, headers={"Accept": "application/json"})
            with urlrequest.urlopen(req, timeout=timeout_sec) as resp:
                if resp.status != 200:
                    continue
                body = json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError, socket.timeout, json.JSONDecodeError, OSError):
            # Promise.allSettled-style: this room failed; move on.
            continue

        for raw in body.get("tiles", []) or []:
            try:
                tile = Tile.from_dict(raw)
                if tile.domain and tile.domain != room and not raw.get("domain"):
                    tile = Tile(
                        domain=room,
                        question=tile.question,
                        answer=tile.answer,
                        confidence=tile.confidence,
                        tags=tile.tags,
                        source=tile.source,
                        _hash=tile._hash,
                    )
                tiles.append(tile)
            except (TypeError, ValueError, KeyError):
                continue

    return tiles


def _fetch_rooms(endpoint: str, timeout_sec: float) -> Optional[list[str]]:
    """Fetch the room list. Returns None on failure."""
    try:
        url = f"{endpoint.rstrip('/')}/rooms"
        req = urlrequest.Request(url, headers={"Accept": "application/json"})
        with urlrequest.urlopen(req, timeout=timeout_sec) as resp:
            if resp.status != 200:
                return None
            body = json.loads(resp.read().decode("utf-8"))
        return list(body.get("rooms", []) or [])
    except (URLError, HTTPError, socket.timeout, json.JSONDecodeError, OSError):
        return None


def save_tiles(tiles: list[Tile], path: Union[str, Path]) -> Path:
    """Persist tiles to a JSON file (for offline replay / caching)."""
    p = Path(path)
    payload = {"tile_count": len(tiles), "tiles": [asdict(t) for t in tiles]}
    p.write_text(json.dumps(payload, indent=2))
    return p


def load_tiles(path: Union[str, Path]) -> list[Tile]:
    """Load tiles from a JSON file (mirror of save_tiles)."""
    p = Path(path)
    body = json.loads(p.read_text())
    return [Tile.from_dict(d) for d in body.get("tiles", [])]
