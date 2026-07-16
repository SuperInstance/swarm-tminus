"""Tests for swarm_tminus.context — PLATO tile formatter + fleet fetcher.

Stdlib `unittest` only. All network calls are mocked.
"""
import json
import socket
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import URLError

from swarm_tminus.context import (
    Tile,
    format_tiles_as_context,
    fetch_fleet_context,
    save_tiles,
    load_tiles,
    PLATO_URL,
)


def _mock_response(body_dict, status: int = 200) -> MagicMock:
    """Build a context-manager mock of urllib.request.urlopen."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = json.dumps(body_dict).encode("utf-8")
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestTileDataclass(unittest.TestCase):
    def test_01_minimal_fields(self):
        t = Tile(domain="d", question="Q?", answer="A!", confidence=0.9)
        self.assertEqual(t.domain, "d")
        self.assertEqual(t.question, "Q?")
        self.assertEqual(t.answer, "A!")
        self.assertAlmostEqual(t.confidence, 0.9)
        self.assertEqual(t.tags, ())
        self.assertEqual(t.source, "")

    def test_02_all_fields(self):
        t = Tile(
            domain="swarm-anchor", question="what is it?",
            answer="file-based shared state", confidence=0.92,
            tags=("state", "anchor"), source="docs", _hash="abc123",
        )
        self.assertEqual(t.tags, ("state", "anchor"))
        self.assertEqual(t._hash, "abc123")

    def test_03_frozen_hashable(self):
        t = Tile(domain="d", question="Q", answer="A", confidence=0.5)
        self.assertEqual(hash(t), hash(t))
        with self.assertRaises(Exception):
            t.domain = "modified"  # frozen dataclass

    def test_04_from_dict(self):
        d = {
            "domain": "d", "question": "Q", "answer": "A",
            "confidence": 0.7, "tags": ["x", "y"], "source": "s", "_hash": "h",
        }
        t = Tile.from_dict(d)
        self.assertEqual(t.tags, ("x", "y"))
        self.assertEqual(t._hash, "h")

    def test_05_from_dict_missing_optional(self):
        t = Tile.from_dict({"domain": "d", "question": "Q",
                             "answer": "A", "confidence": 0.5})
        self.assertEqual(t.tags, ())
        self.assertEqual(t.source, "")


class TestFormatTiles(unittest.TestCase):
    def test_01_empty_returns_empty_string(self):
        self.assertEqual(format_tiles_as_context([]), "")

    def test_02_single_tile_renders(self):
        t = Tile(domain="d", question="What?", answer="Answer.",
                 confidence=0.9, tags=("x",))
        out = format_tiles_as_context([t])
        self.assertIn("<fleet-context>", out)
        self.assertIn("Q: What?", out)
        self.assertIn("A: Answer.", out)
        self.assertIn("confidence: 0.90", out)
        self.assertIn("tags: x", out)

    def test_03_no_metadata_omits_confidence_line(self):
        t = Tile(domain="d", question="Q", answer="A", confidence=0.9)
        out = format_tiles_as_context([t], include_metadata=False)
        self.assertIn("Q: Q", out)
        self.assertIn("A: A", out)
        self.assertNotIn("confidence:", out)

    def test_04_truncates_by_lowest_confidence(self):
        tiles = [
            Tile(domain="d", question=f"Q{i}", answer=f"A{i}",
                 confidence=0.5 + i * 0.1, tags=(f"t{i}",))
            for i in range(10)
        ]
        # Force tiny max_chars
        out = format_tiles_as_context(tiles, max_chars=600)
        self.assertLess(len(out), 1200)  # well under truncated list
        # Highest-confidence tile (Q9, conf=1.4) must still be present
        self.assertIn("Q9", out)
        # Lowest-confidence tile (Q0) should have been dropped
        self.assertNotIn("Q0", out)

    def test_05_preserves_highest_confidence_when_truncated(self):
        # Order shuffled — must still keep highest.
        tiles = [
            Tile(domain="d", question="low", answer="a", confidence=0.1),
            Tile(domain="d", question="high", answer="b", confidence=0.99),
            Tile(domain="d", question="mid", answer="c", confidence=0.5),
        ]
        out = format_tiles_as_context(tiles, max_chars=200)
        self.assertIn("high", out)
        self.assertNotIn("low", out)

    def test_06_xml_escape_angle_brackets(self):
        t = Tile(domain="d", question="<script>?", answer="a & b",
                 confidence=0.5)
        out = format_tiles_as_context([t])
        self.assertIn("&lt;script&gt;?", out)
        self.assertIn("a &amp; b", out)

    def test_07_groups_by_domain(self):
        tiles = [
            Tile(domain="A", question="Q1", answer="A1", confidence=0.9),
            Tile(domain="B", question="Q2", answer="A2", confidence=0.8),
        ]
        out = format_tiles_as_context(tiles)
        self.assertIn("<room>A</room>", out)
        self.assertIn("<room>B</room>", out)

    def test_08_room_tags_are_balanced(self):
        """Regression: room open/close tags must be balanced for any number of domains."""
        import re
        tiles = [
            Tile(domain=f"d{i}", question=f"Q{i}", answer=f"A{i}",
                 confidence=0.5 + i * 0.1)
            for i in range(5)
        ]
        out = format_tiles_as_context(tiles)
        opens = len(re.findall(r"<room>", out))
        closes = len(re.findall(r"</room>", out))
        self.assertEqual(opens, closes,
                         f"unbalanced room tags: {opens} opens, {closes} closes")

    def test_09_source_preserved_through_truncation(self):
        """Regression: source field must survive truncation."""
        tiles = [
            Tile(domain="d", question=f"Q{i}", answer=f"A{i}",
                 confidence=0.5 + i * 0.05, source=f"src{i}")
            for i in range(20)
        ]
        # Force truncation.
        out = format_tiles_as_context(tiles, max_chars=500)
        # At least one source line should still be present.
        self.assertIn("source:", out,
                      "source field lost during truncation")

    def test_10_indentation_uniform(self):
        """Regression: all tile content lines should have the same indent."""
        tiles = [Tile(domain="d", question="Q", answer="A", confidence=0.9,
                      tags=("t",), source="s")]
        out = format_tiles_as_context(tiles)
        # Every tile content line should start with exactly 4 spaces.
        for line in out.splitlines():
            if "Q:" in line or "A:" in line or "confidence:" in line:
                stripped_indent = len(line) - len(line.lstrip(" "))
                self.assertEqual(stripped_indent, 4,
                                 f"non-uniform indent in: {line!r}")


class TestFetchFleetContext(unittest.TestCase):
    def test_01_returns_empty_on_connection_refused(self):
        with patch("swarm_tminus.context.urlrequest.urlopen") as mock:
            mock.side_effect = URLError("Connection refused")
            result = fetch_fleet_context(
                "http://localhost:8847", rooms=["r1"]
            )
            self.assertEqual(result, [])

    def test_02_returns_empty_on_timeout(self):
        with patch("swarm_tminus.context.urlrequest.urlopen") as mock:
            mock.side_effect = socket.timeout("timed out")
            result = fetch_fleet_context(
                "http://localhost:8847", rooms=["r1"]
            )
            self.assertEqual(result, [])

    def test_03_parses_mock_response(self):
        with patch("swarm_tminus.context.urlrequest.urlopen") as mock:
            mock.side_effect = [
                _mock_response({
                    "tiles": [
                        {"domain": "r1", "question": "Q", "answer": "A",
                         "confidence": 0.9, "tags": ["t"], "source": "s"},
                    ],
                    "tile_count": 1,
                }),
            ]
            result = fetch_fleet_context(
                "http://localhost:8847", rooms=["r1"]
            )
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].domain, "r1")
            self.assertEqual(result[0].question, "Q")
            self.assertAlmostEqual(result[0].confidence, 0.9)

    def test_04_handles_malformed_json(self):
        with patch("swarm_tminus.context.urlrequest.urlopen") as mock:
            resp = MagicMock()
            resp.status = 200
            resp.read.return_value = b"{not json"
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            mock.return_value = resp
            result = fetch_fleet_context(
                "http://localhost:8847", rooms=["r1"]
            )
            self.assertEqual(result, [])

    def test_05_partial_failure_all_settled_style(self):
        """One room's GET fails; other rooms still succeed."""
        from urllib.error import URLError as _URLError

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            req_obj = args[0] if args else kwargs.get("url", "")
            url = req_obj.full_url if hasattr(req_obj, "full_url") else str(req_obj)
            if "room/good" in url:
                return _mock_response({
                    "tiles": [{"domain": "good", "question": "Q",
                               "answer": "A", "confidence": 0.8}],
                    "tile_count": 1,
                })
            raise _URLError("connection refused for bad room")

        with patch("swarm_tminus.context.urlrequest.urlopen",
                   side_effect=side_effect):
            result = fetch_fleet_context(
                "http://localhost:8847", rooms=["good", "bad"]
            )
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].domain, "good")

    def test_06_rooms_none_fetches_all(self):
        with patch("swarm_tminus.context.urlrequest.urlopen") as mock:
            mock.side_effect = [
                _mock_response({"rooms": ["r1", "r2"]}),
                _mock_response({"tiles": [
                    {"domain": "r1", "question": "Q1", "answer": "A1",
                     "confidence": 0.7}], "tile_count": 1}),
                _mock_response({"tiles": [
                    {"domain": "r2", "question": "Q2", "answer": "A2",
                     "confidence": 0.5}], "tile_count": 1}),
            ]
            result = fetch_fleet_context("http://localhost:8847")
            self.assertEqual(len(result), 2)

    def test_07_invalid_tile_records_are_skipped(self):
        """A tile missing required fields is silently skipped."""
        with patch("swarm_tminus.context.urlrequest.urlopen") as mock:
            mock.side_effect = [
                _mock_response({
                    "tiles": [
                        {"domain": "r1", "question": "Q", "answer": "A",
                         "confidence": 0.9},  # valid
                        {"missing": "fields"},  # invalid
                    ],
                    "tile_count": 2,
                }),
            ]
            result = fetch_fleet_context(
                "http://localhost:8847", rooms=["r1"]
            )
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].question, "Q")


class TestSaveLoad(unittest.TestCase):
    def test_01_save_and_load_roundtrip(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "tiles.json")
            tiles = [
                Tile(domain="d1", question="Q1", answer="A1", confidence=0.8,
                     tags=("t",), source="s"),
                Tile(domain="d2", question="Q2", answer="A2", confidence=0.7),
            ]
            save_tiles(tiles, p)
            loaded = load_tiles(p)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0].domain, "d1")
            self.assertEqual(loaded[1].tags, ())

    def test_02_default_endpoint_constant(self):
        self.assertEqual(PLATO_URL, "http://localhost:8847")


if __name__ == "__main__":
    unittest.main()
