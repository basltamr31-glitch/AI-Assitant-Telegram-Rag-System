"""Tests for the advisor registry and how books are tied to advisors (Phase 15)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.advisors import get_registry, parse
from app.rag.store import VectorStore

ROOT = Path(__file__).resolve().parents[1]


def registry_data(**overrides) -> dict:
    data = {
        "default": "legal",
        "advisors": [
            {"id": "legal", "name": "المستشار القانوني", "icon": "⚖️", "description": "قوانين"},
            {"id": "real_estate", "name": "المستشار العقاري", "icon": "🏠", "description": "عقارات"},
        ],
    }
    data.update(overrides)
    return data


# --- the registry ---------------------------------------------------------------


def test_the_shipped_registry_loads() -> None:
    registry = get_registry()
    assert registry.ids == ["legal", "real_estate", "economic", "companies"]
    assert registry.default.id == "legal"


def test_an_unknown_advisor_falls_back_to_the_default() -> None:
    """A chat whose advisor was removed from the file still gets answers."""
    registry = parse(registry_data())
    assert registry.get("removed_long_ago").id == "legal"
    assert registry.get(None).id == "legal"


def test_a_keyboard_button_maps_back_to_its_advisor() -> None:
    registry = parse(registry_data())
    assert registry.by_button("🏠 المستشار العقاري").id == "real_estate"
    assert registry.by_button("  🏠 المستشار العقاري ").id == "real_estate"
    assert registry.by_button("ما عقوبة السرقة؟") is None


@pytest.mark.parametrize(
    "bad",
    [
        {"default": "nobody"},
        {"advisors": []},
        {"advisors": [
            {"id": "x", "name": "a", "icon": "1", "description": "d"},
            {"id": "x", "name": "b", "icon": "2", "description": "d"},
        ], "default": "x"},
    ],
)
def test_a_broken_registry_is_refused_at_load(bad: dict) -> None:
    with pytest.raises(ValueError):
        parse(registry_data(**bad))


# --- ingest decides the advisor -------------------------------------------------


def load_ingest():
    spec = importlib.util.spec_from_file_location("ingest", ROOT / "scripts" / "ingest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_folder_decides_the_advisor(tmp_path: Path) -> None:
    ingest = load_ingest()
    (tmp_path / "real_estate").mkdir()
    (tmp_path / "real_estate" / "rent-law.pdf").write_bytes(b"%PDF")
    ingest.MATERIAL = tmp_path
    assert ingest.resolve_advisors("rent-law", None) == ["real_estate"]


def test_one_book_can_serve_two_advisors(tmp_path: Path) -> None:
    ingest = load_ingest()
    for advisor in ("legal", "real_estate"):
        (tmp_path / advisor).mkdir()
        (tmp_path / advisor / "civil-law.pdf").write_bytes(b"%PDF")
    ingest.MATERIAL = tmp_path
    assert ingest.resolve_advisors("civil-law", None) == ["legal", "real_estate"]


def test_an_explicit_advisor_wins_and_an_unknown_one_is_refused(tmp_path: Path) -> None:
    ingest = load_ingest()
    ingest.MATERIAL = tmp_path
    assert ingest.resolve_advisors("anything", ["economic"]) == ["economic"]
    with pytest.raises(SystemExit):
        ingest.resolve_advisors("anything", ["astrology"])


def test_a_book_with_no_advisor_is_refused_not_guessed(tmp_path: Path) -> None:
    ingest = load_ingest()
    ingest.MATERIAL = tmp_path
    with pytest.raises(SystemExit, match="which advisor"):
        ingest.resolve_advisors("loose-book", None)


# --- the store filters by advisor ----------------------------------------------


class RecordingClient:
    def __init__(self) -> None:
        self.filters = []

    def query_points(self, *, query_filter=None, **kwargs):
        self.filters.append(query_filter)
        return SimpleNamespace(points=[])


def test_a_search_for_one_advisor_filters_on_its_books() -> None:
    client = RecordingClient()
    store = VectorStore(client=client)  # type: ignore[arg-type]

    store.search([0.1], advisor="real_estate")
    store.search([0.1])

    condition = client.filters[0].must[0]
    assert condition.key == "advisors" and condition.match.value == "real_estate"
    # No advisor: no filter at all - the MCP server and the evals see everything.
    assert client.filters[1] is None
