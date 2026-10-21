"""Core data model: vocabulary, concepts, occurrences and anchors.

The source of truth is human-confirmed YAML under ``.diting/``. Everything derived
(symbol tables, inverted index, closures) lives in ``.diting/.cache/`` and can be rebuilt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

LIFECYCLES = ("draft", "active", "deprecated", "removed")
LEVELS = ("critical", "normal", "info")
ANCHOR_STATES = ("verified", "auto", "suspect", "lost")
PREDICATE_CLASSES = ("compute", "validate", "display", "persist", "permission", "other")
_CONCEPT_ID = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")


class SchemaError(ValueError):
    pass


# ---------------------------------------------------------------- vocabulary


@dataclass
class Term:
    name: str
    synonyms: list[str] = field(default_factory=list)
    hypernym: str | None = None
    klass: str | None = None  # predicates only: compute/validate/...
    context: str | None = None
    description: str = ""

    def surface_forms(self) -> list[str]:
        return [self.name, *self.synonyms]

    @classmethod
    def from_dict(cls, d: dict | str, *, predicate: bool = False) -> "Term":
        if isinstance(d, str):
            return cls(name=d)
        if "name" not in d:
            raise SchemaError(f"term without name: {d!r}")
        klass = d.get("class")
        if predicate and klass is not None and klass not in PREDICATE_CLASSES:
            raise SchemaError(f"predicate {d['name']!r}: class must be one of {PREDICATE_CLASSES}")
        return cls(
            name=str(d["name"]),
            synonyms=[str(s) for s in d.get("synonyms", []) or []],
            hypernym=d.get("hypernym"),
            klass=klass,
            context=d.get("context"),
            description=d.get("description", "") or "",
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"name": self.name}
        if self.synonyms:
            d["synonyms"] = list(self.synonyms)
        if self.hypernym:
            d["hypernym"] = self.hypernym
        if self.klass:
            d["class"] = self.klass
        if self.context:
            d["context"] = self.context
        if self.description:
            d["description"] = self.description
        return d


@dataclass
class Vocabulary:
    contexts: list[str] = field(default_factory=list)
    entities: list[Term] = field(default_factory=list)
    predicates: list[Term] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Vocabulary":
        d = d or {}
        v = cls(
            contexts=[str(c) for c in d.get("contexts", []) or []],
            entities=[Term.from_dict(e) for e in d.get("entities", []) or []],
            predicates=[Term.from_dict(p, predicate=True) for p in d.get("predicates", []) or []],
        )
        v.validate()
        return v

    def to_dict(self) -> dict:
        return {
            "contexts": list(self.contexts),
            "entities": [e.to_dict() for e in self.entities],
            "predicates": [p.to_dict() for p in self.predicates],
        }

    def validate(self) -> None:
        seen: dict[str, str] = {}
        for kind, terms in (("entity", self.entities), ("predicate", self.predicates)):
            for t in terms:
                for form in t.surface_forms():
                    key = form.lower()
                    owner = f"{kind}:{t.name}"
                    if key in seen and seen[key] != owner:
                        raise SchemaError(f"surface form {form!r} is claimed by both {seen[key]} and {owner}")
                    seen[key] = owner
        names = {e.name for e in self.entities}
        for e in self.entities:
            if e.hypernym and e.hypernym not in names:
                raise SchemaError(f"entity {e.name!r}: unknown hypernym {e.hypernym!r}")

    def entity(self, name: str) -> Term | None:
        return next((e for e in self.entities if e.name == name), None)

    def predicate(self, name: str) -> Term | None:
        return next((p for p in self.predicates if p.name == name), None)

    def descendants(self, entity: str) -> set[str]:
        """The entity itself plus every entity whose hypernym chain reaches it."""
        out = {entity}
        changed = True
        while changed:
            changed = False
            for e in self.entities:
                if e.hypernym in out and e.name not in out:
                    out.add(e.name)
                    changed = True
        return out


# ------------------------------------------------------------------ concepts


@dataclass
class Concept:
    id: str
    statement: str
    context: str | None = None
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    modifiers: dict[str, Any] = field(default_factory=dict)
    lifecycle: str = "active"
    level: str = "normal"
    verify: str = "property"  # property | example | manual
    tests: list[str] = field(default_factory=list)
    version: int = 1

    @classmethod
    def from_dict(cls, d: dict) -> "Concept":
        for key in ("id", "statement"):
            if not d.get(key):
                raise SchemaError(f"concept missing required field {key!r}: {d!r}")
        c = cls(
            id=str(d["id"]),
            statement=str(d["statement"]),
            context=d.get("context"),
            subject=d.get("subject"),
            predicate=d.get("predicate"),
            object=d.get("object"),
            modifiers=dict(d.get("modifiers") or {}),
            lifecycle=d.get("lifecycle", "active"),
            level=d.get("level", "normal"),
            verify=d.get("verify", "property"),
            tests=[str(t) for t in d.get("tests", []) or []],
            version=int(d.get("version", 1)),
        )
        c.validate()
        return c

    def validate(self, vocab: Vocabulary | None = None) -> list[str]:
        """Raise on structural errors; return soft warnings (vocabulary mismatches)."""
        if not _CONCEPT_ID.match(self.id):
            raise SchemaError(f"concept id {self.id!r} must be dotted lower_snake, e.g. trade.discount.rounding")
        if self.lifecycle not in LIFECYCLES:
            raise SchemaError(f"{self.id}: lifecycle must be one of {LIFECYCLES}")
        if self.level not in LEVELS:
            raise SchemaError(f"{self.id}: level must be one of {LEVELS}")
        warnings: list[str] = []
        if vocab is not None:
            if self.context and vocab.contexts and self.context not in vocab.contexts:
                warnings.append(f"{self.id}: context {self.context!r} not in vocabulary")
            for role, name, lookup in (
                ("subject", self.subject, vocab.entity),
                ("object", self.object, vocab.entity),
                ("predicate", self.predicate, vocab.predicate),
            ):
                if name and lookup(name) is None:
                    warnings.append(f"{self.id}: {role} {name!r} not in vocabulary")
        return warnings

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"id": self.id, "statement": self.statement}
        for key in ("context", "subject", "predicate", "object"):
            if getattr(self, key):
                d[key] = getattr(self, key)
        if self.modifiers:
            d["modifiers"] = dict(self.modifiers)
        d["lifecycle"] = self.lifecycle
        d["level"] = self.level
        d["verify"] = self.verify
        if self.tests:
            d["tests"] = list(self.tests)
        d["version"] = self.version
        return d


# ------------------------------------------------------- occurrences/anchors


@dataclass
class Anchor:
    path: str
    symbol: str
    fingerprint: str | None = None
    shape: str | None = None
    state: str = "verified"
    confirmed_version: int | None = None  # concept version at last human confirmation
    note: str = ""

    @property
    def ref(self) -> str:
        return f"{self.path}#{self.symbol}"

    @classmethod
    def from_dict(cls, d: dict) -> "Anchor":
        for key in ("path", "symbol"):
            if not d.get(key):
                raise SchemaError(f"anchor missing {key!r}: {d!r}")
        state = d.get("state", "verified")
        if state not in ANCHOR_STATES:
            raise SchemaError(f"anchor {d['path']}#{d['symbol']}: state must be one of {ANCHOR_STATES}")
        return cls(
            path=str(d["path"]),
            symbol=str(d["symbol"]),
            fingerprint=d.get("fingerprint"),
            shape=d.get("shape"),
            state=state,
            confirmed_version=d.get("confirmed_version"),
            note=d.get("note", "") or "",
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"path": self.path, "symbol": self.symbol}
        if self.fingerprint:
            d["fingerprint"] = self.fingerprint
        if self.shape:
            d["shape"] = self.shape
        d["state"] = self.state
        if self.confirmed_version is not None:
            d["confirmed_version"] = self.confirmed_version
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class Occurrence:
    id: str
    anchors: list[Anchor] = field(default_factory=list)
    variant: str | None = None
    channel: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Occurrence":
        if not d.get("id"):
            raise SchemaError(f"occurrence without id: {d!r}")
        return cls(
            id=str(d["id"]),
            anchors=[Anchor.from_dict(a) for a in d.get("anchors", []) or []],
            variant=d.get("variant"),
            channel=d.get("channel"),
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"id": self.id}
        if self.variant:
            d["variant"] = self.variant
        if self.channel:
            d["channel"] = self.channel
        d["anchors"] = [a.to_dict() for a in self.anchors]
        return d


@dataclass
class EdgeFile:
    """All occurrences of one concept (stored as edges/<concept_id>.yaml)."""

    concept: str
    occurrences: list[Occurrence] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "EdgeFile":
        if not d.get("concept"):
            raise SchemaError(f"edge file without concept: {d!r}")
        ef = cls(
            concept=str(d["concept"]),
            occurrences=[Occurrence.from_dict(o) for o in d.get("occurrences", []) or []],
        )
        ids = [o.id for o in ef.occurrences]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise SchemaError(f"{ef.concept}: duplicate occurrence ids {sorted(dup)}")
        return ef

    def to_dict(self) -> dict:
        return {"concept": self.concept, "occurrences": [o.to_dict() for o in self.occurrences]}

    def occurrence(self, occ_id: str) -> Occurrence | None:
        return next((o for o in self.occurrences if o.id == occ_id), None)
