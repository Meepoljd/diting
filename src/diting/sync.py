"""Keeping anchors fresh: ``sync`` (automatic re-anchoring) and ``confirm`` (human/agent sign-off).

Re-anchoring decision tree for every anchor, against the current symbol table:

1. ref exists, fingerprint unchanged         -> ``unchanged`` (a suspect/lost anchor recovers)
2. ref exists, fingerprint changed           -> ``suspect``   (logic changed; needs confirmation)
3. ref gone, same fingerprint elsewhere      -> ``moved``     (move or rename; state ``auto``)
4. ref gone, same shape elsewhere            -> ``reshaped``  (renamed *and* identifiers changed,
                                                re-pointed but ``suspect``)
5. otherwise                                 -> ``lost``

Fingerprints ignore formatting, comments, docstrings and the symbol's own name, so (3) covers
moving a function to another file as well as renaming it. A candidate is only taken when it is
unambiguous (preferring the same file, then the same kind) and not already anchored by another
occurrence of the same concept.

Each anchor also stores a ``basis``: a hash of the fingerprints of *all* sites of its concept at
the moment it was confirmed. When any sibling changes afterwards, the basis no longer matches
the current family hash and ``diting check`` asks for this site to be reviewed too — that is how
a change made in one place but forgotten in another is caught, even across several commits.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .extract import Symbol
from .model import Anchor, Concept, EdgeFile, SchemaError
from .store import Repo


class SyncError(RuntimeError):
    pass


# ------------------------------------------------------------------ family basis


def _current_fp(a: Anchor, symbols: dict[str, Symbol]) -> str:
    sym = symbols.get(a.ref)
    # unresolved anchors keep their recorded fingerprint so that an un-synced move does not
    # look like a logic change of the whole family
    return (sym.fingerprint if sym is not None else a.fingerprint) or "-"


def family_hash(ef: EdgeFile, symbols: dict[str, Symbol]) -> str:
    """Hash of every site's current fingerprint (order independent, path independent)."""
    items = sorted(f"{o.id}|{_current_fp(a, symbols)}" for o in ef.occurrences for a in o.anchors)
    return hashlib.sha1("\n".join(items).encode("utf-8")).hexdigest()[:16]


def rebase_after_add(ef: EdgeFile, symbols: dict[str, Symbol], old_hash: str) -> str:
    """Adding a site must not invalidate siblings that agreed with the family before."""
    new_hash = family_hash(ef, symbols)
    for o in ef.occurrences:
        for a in o.anchors:
            if a.basis in (None, old_hash):
                a.basis = new_hash
    return new_hash


# -------------------------------------------------------------------------- sync


@dataclass
class SyncAction:
    concept: str
    occurrence: str
    ref: str
    action: str  # unchanged | recovered | suspect | moved | reshaped | lost
    new_ref: str | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "")}


@dataclass
class SyncReport:
    actions: list[SyncAction] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict:
        counts: dict[str, int] = {}
        for a in self.actions:
            counts[a.action] = counts.get(a.action, 0) + 1
        return {
            "ok": True,
            "dry_run": self.dry_run,
            "counts": counts,
            "changes": [a.to_dict() for a in self.actions if a.action != "unchanged"],
            "written": self.written,
        }


def _pick(cands: list[Symbol], a: Anchor, kind: str | None) -> tuple[Symbol | None, list[Symbol]]:
    """Unambiguous candidate: unique overall, else unique in the same file, else unique same kind."""
    if len(cands) == 1:
        return cands[0], cands
    for narrowed in (
        [s for s in cands if s.path == a.path],
        [s for s in cands if s.path == a.path and s.kind == kind],
        [s for s in cands if s.kind == kind and s.qualname.split(".")[-1] == a.symbol.split(".")[-1]],
    ):
        if len(narrowed) == 1:
            return narrowed[0], cands
    return None, cands


def _guess_kind(a: Anchor) -> str | None:
    return "method" if "." in a.symbol else None


def sync(repo: Repo, *, dry_run: bool = False, symbols: dict[str, Symbol] | None = None) -> SyncReport:
    symbols = symbols if symbols is not None else repo.symbols(refresh=True)
    edges = repo.load_edges()
    rep = SyncReport(dry_run=dry_run)
    by_fp: dict[str, list[Symbol]] = {}
    by_shape: dict[str, list[Symbol]] = {}
    for s in symbols.values():
        by_fp.setdefault(s.fingerprint, []).append(s)
        by_shape.setdefault(s.shape, []).append(s)

    for cid, ef in sorted(edges.items()):
        dirty = False
        taken = {a.ref for o in ef.occurrences for a in o.anchors if a.ref in symbols}
        for occ in ef.occurrences:
            for a in occ.anchors:
                act = SyncAction(cid, occ.id, a.ref, "unchanged")
                sym = symbols.get(a.ref)
                if sym is not None:
                    if a.fingerprint is None or sym.fingerprint == a.fingerprint:
                        if a.state in ("suspect", "lost"):
                            act.action = "recovered"
                            act.note = "code is back to the confirmed version"
                            a.state, a.note = "verified", ""
                            dirty = True
                    else:
                        act.action = "suspect"
                        act.note = "logic changed since confirmation; review and run `diting confirm`"
                        if a.state != "suspect" or a.note != act.note:
                            a.state, a.note = "suspect", act.note
                            dirty = True
                    rep.actions.append(act)
                    continue

                kind = _guess_kind(a)
                free = lambda lst: [s for s in lst if s.ref not in taken]  # noqa: E731
                cand, all_c = _pick(free(by_fp.get(a.fingerprint or "", [])), a, kind)
                action = "moved"
                if cand is None and not all_c:
                    cand, all_c = _pick(free(by_shape.get(a.shape or "", [])), a, kind)
                    action = "reshaped"
                if cand is None:
                    act.action = "lost"
                    act.note = ("ambiguous candidates: " + ", ".join(s.ref for s in all_c[:5])) if all_c \
                        else "symbol not found; re-anchor with `diting anchor add` or remove the occurrence"
                    if a.state != "lost" or a.note != act.note:
                        a.state, a.note = "lost", act.note
                        dirty = True
                    rep.actions.append(act)
                    continue

                old_ref = a.ref
                a.path, a.symbol = cand.path, cand.qualname
                taken.add(cand.ref)
                act.action, act.new_ref = action, cand.ref
                if action == "moved":
                    a.state = "auto"
                    a.note = f"moved from {old_ref}"
                    a.shape = cand.shape
                else:
                    # keep the confirmed fingerprint: the body differs and must be confirmed
                    a.state = "suspect"
                    a.note = f"re-pointed from {old_ref} by shape; identifiers changed, review and confirm"
                act.note = a.note
                dirty = True
                rep.actions.append(act)
        if dirty and not dry_run:
            rep.written.append(str(repo.save_edges(ef).relative_to(repo.root)))
    return rep


# ----------------------------------------------------------------------- confirm


def confirm(repo: Repo, concept_id: str, occurrence_ids: list[str] | None = None, *,
            note: str = "", symbols: dict[str, Symbol] | None = None) -> dict:
    """Sign off the selected occurrences (all if none given) against the current code."""
    concepts = repo.load_concepts()
    c: Concept | None = concepts.get(concept_id)
    if c is None:
        raise SyncError(f"unknown concept {concept_id!r}")
    ef = repo.load_edge(concept_id)
    if not ef.occurrences:
        raise SyncError(f"{concept_id}: no occurrences to confirm")
    known = {o.id for o in ef.occurrences}
    wanted = set(occurrence_ids or known)
    unknown = sorted(wanted - known)
    if unknown:
        raise SyncError(f"{concept_id}: unknown occurrence(s) {', '.join(unknown)} (have: {', '.join(sorted(known))})")
    symbols = symbols if symbols is not None else repo.symbols(refresh=True)

    selected = [(o, a) for o in ef.occurrences if o.id in wanted for a in o.anchors]
    missing = [a.ref for _, a in selected if a.ref not in symbols]
    if missing:
        raise SyncError(f"cannot confirm unresolved anchor(s) {', '.join(missing)}; run `diting sync` "
                        f"or re-anchor with `diting anchor add`")
    changed = []
    for o, a in selected:
        sym = symbols[a.ref]
        if a.fingerprint != sym.fingerprint or a.state != "verified" or a.confirmed_version != c.version:
            changed.append({"occurrence": o.id, "ref": a.ref, "was": a.state,
                            "logic_changed": bool(a.fingerprint and a.fingerprint != sym.fingerprint)})
        a.fingerprint, a.shape = sym.fingerprint, sym.shape
        a.state, a.confirmed_version, a.note = "verified", c.version, note
    # only the selected sites are signed off against the current family; siblings that were
    # not reviewed keep their old basis, so `diting check` keeps asking for them
    basis = family_hash(ef, symbols)
    for _, a in selected:
        a.basis = basis
    try:
        path = repo.save_edges(ef)
    except SchemaError as e:  # pragma: no cover - defensive
        raise SyncError(str(e)) from e
    pending = sorted({o.id for o in ef.occurrences for a in o.anchors if a.basis != basis})
    return {
        "ok": True,
        "concept": concept_id,
        "version": c.version,
        "confirmed": sorted(wanted),
        "updated": changed,
        "pending_siblings": pending,
        "file": str(path.relative_to(repo.root)),
    }
