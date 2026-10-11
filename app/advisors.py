"""The advisors: one bot, several specialties, each with its own books.

What an advisor is
------------------
A name, an icon for its Telegram button, a description the model reads, and
- in the knowledge base, not here - the books tagged with its id. That is
all. The RAG engine, the agent and its rules are shared; an advisor only
decides *which passages* a search may return, and which conversation the
memory continues (Phase 15, ADR-022).

Why a file, not code
--------------------
Adding a specialty should be an edit to `advisors.json` and an ingest, not a
code change. The file is read once per process; restart the API after
editing it.

Books are tagged, not copied
----------------------------
A chunk carries a list, `advisors`, in its Qdrant payload. The civil code can
belong to the legal and the real-estate advisor at once, stored and embedded
once. `scripts/kb.py` assigns and lists; `scripts/ingest.py --advisor` tags
on the way in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / "advisors.json"


@dataclass(frozen=True)
class Advisor:
    id: str
    name: str
    icon: str
    description: str

    @property
    def button(self) -> str:
        """The text on its keyboard button - and what the button sends back."""
        return f"{self.icon} {self.name}"


@dataclass(frozen=True)
class Registry:
    advisors: tuple[Advisor, ...]
    default_id: str

    def get(self, advisor_id: str | None) -> Advisor:
        """The advisor with this id, or the default for an unknown or empty one.

        Lenient on purpose: a chat whose stored advisor was later removed
        from the file must still get answers, not an error.
        """
        for advisor in self.advisors:
            if advisor.id == advisor_id:
                return advisor
        return self.default

    @property
    def default(self) -> Advisor:
        return next(a for a in self.advisors if a.id == self.default_id)

    @property
    def ids(self) -> list[str]:
        return [a.id for a in self.advisors]

    def by_button(self, text: str) -> Advisor | None:
        """The advisor whose keyboard button sent this text, if any."""
        text = text.strip()
        return next((a for a in self.advisors if a.button == text), None)


def parse(data: dict) -> Registry:
    advisors = tuple(Advisor(**a) for a in data["advisors"])
    ids = [a.id for a in advisors]
    if not advisors:
        raise ValueError("advisors.json lists no advisors")
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate advisor ids in advisors.json: {ids}")
    if data["default"] not in ids:
        raise ValueError(f"default advisor '{data['default']}' is not in {ids}")
    return Registry(advisors=advisors, default_id=data["default"])


@lru_cache
def get_registry() -> Registry:
    return parse(json.loads(REGISTRY.read_text(encoding="utf-8")))
