"""Static validation of the .diting/ source of truth against the current code.

This is the PR-1 subset of the gate: schema + vocabulary + anchor resolvability.
P/D/I comparison and staleness arrive with `diting check`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .extract import Symbol
from .model import SchemaError
from .store import Repo


@dataclass
class Finding:
    level: str  # error | warning
    code: str
    message: str
    concept: str | None = None
    ref: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class LintReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": len(self.errors),
            "warnings": len(self.findings) - len(self.errors),
            "findings": [f.to_dict() for f in self.findings],
        }


def lint(repo: Repo, symbols: dict[str, Symbol] | None = None) -> LintReport:
    rep = LintReport()
    add = rep.findings.append
    try:
        vocab = repo.load_vocab()
        concepts = repo.load_concepts()
        edges = repo.load_edges()
    except SchemaError as e:
        add(Finding("error", "schema", str(e)))
        return rep

    symbols = symbols if symbols is not None else repo.symbols(refresh=True)

    for c in concepts.values():
        for w in c.validate(vocab):
            add(Finding("warning", "vocab", w, concept=c.id))
        if c.lifecycle == "active" and c.id not in edges:
            add(Finding("warning", "no-occurrence", f"{c.id}: active concept has no occurrences", concept=c.id))

    for cid, ef in edges.items():
        c = concepts.get(cid)
        if c is None:
            add(Finding("error", "orphan-edges", f"edges for unknown concept {cid!r}", concept=cid))
            continue
        if c.lifecycle == "removed" and any(o.anchors for o in ef.occurrences):
            add(Finding("error", "removed-referenced", f"{cid}: removed concept still has anchors", concept=cid))
        for occ in ef.occurrences:
            if not occ.anchors:
                add(Finding("warning", "empty-occurrence", f"{cid}/{occ.id}: no anchors", concept=cid))
            for a in occ.anchors:
                sym = symbols.get(a.ref)
                if sym is None:
                    add(Finding("error", "anchor-unresolved", f"{cid}/{occ.id}: {a.ref} not found in code",
                                concept=cid, ref=a.ref))
                elif a.fingerprint and sym.fingerprint != a.fingerprint:
                    add(Finding("warning", "anchor-drift",
                                f"{cid}/{occ.id}: {a.ref} changed since it was anchored (run `diting sync`)",
                                concept=cid, ref=a.ref))
                if a.state in ("suspect", "lost"):
                    add(Finding("warning", f"anchor-{a.state}", f"{cid}/{occ.id}: {a.ref} is {a.state}",
                                concept=cid, ref=a.ref))
                if a.confirmed_version is not None and a.confirmed_version < c.version:
                    add(Finding("warning", "stale-occurrence",
                                f"{cid}/{occ.id}: confirmed at v{a.confirmed_version}, concept is v{c.version}",
                                concept=cid, ref=a.ref))
    return rep
