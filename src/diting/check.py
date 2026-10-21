"""``diting check`` — the CI gate.

Combines, for the current working tree:

* **L0 index consistency** — the incremental symbol index equals a full rebuild.
* **lint** — schema / vocabulary / orphan problems (anchor findings are re-derived below).
* **anchor state** — sites whose code changed since confirmation (``anchor-changed``), sites
  that vanished (``anchor-lost``; ``anchor-moved`` when ``diting sync`` can fix it
  automatically) and sites confirmed against an older concept version (``stale-occurrence``).
* **missed changes** — ``sibling-review``: some site of a concept changed after this site was
  confirmed, so this one must be reviewed (changed too, or confirmed as still correct). State
  based via each anchor's ``basis``; with a diff source (``--base``/``--staged``/``--diff``) the
  impact analysis additionally covers anchors without a basis and scopes the report.

Every concept-bound finding is graded by the concept level through ``config.gate``
(``block`` → exit 1, ``warn`` → reported, ``off`` → ignored).
"""

from __future__ import annotations

from dataclasses import dataclass

from .extract import Symbol
from .impact import DiffSource, impact
from .lint import lint
from .model import SchemaError
from .store import Repo
from .sync import family_hash, sync

GATED_LIFECYCLES = ("active", "deprecated")
_REDERIVED = {"anchor-unresolved", "anchor-drift", "anchor-suspect", "anchor-lost", "stale-occurrence"}


@dataclass
class CheckFinding:
    severity: str  # block | warn
    code: str
    message: str
    concept: str | None = None
    level: str | None = None
    occurrence: str | None = None
    ref: str | None = None
    fix: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def check(repo: Repo, src: DiffSource | None = None, *, verify_index: bool = True) -> dict:
    gate = repo.config["gate"]
    findings: list[CheckFinding] = []
    suppressed = 0

    def add(code: str, message: str, *, level: str | None = None, fixed_severity: str | None = None,
            **kw) -> None:
        nonlocal suppressed
        sev = fixed_severity or gate.get(level or "normal", "block")
        if sev == "off":
            suppressed += 1
            return
        findings.append(CheckFinding(sev, code, message, level=level, **kw))

    # ---- L0: incremental index == full rebuild
    index_diffs: list[dict] = []
    if verify_index:
        index_diffs = repo.verify_index()
        for d in index_diffs:
            add("index-inconsistent", f"{d['ref']}: {d['problem']}", fixed_severity="block",
                ref=d["ref"], fix="delete .diting/.cache/ and report a bug")
    symbols: dict[str, Symbol] = repo.symbols(refresh=True)

    # ---- lint (non-anchor findings)
    rep = lint(repo, symbols)
    for f in rep.findings:
        if f.code in _REDERIVED:
            continue
        add(f.code, f.message, fixed_severity="block" if f.level == "error" else "warn", concept=f.concept, ref=f.ref)
    if any(f.code == "schema" for f in rep.findings):
        return _result(findings, suppressed, index_diffs, None)

    try:
        concepts = repo.load_concepts()
        edges = repo.load_edges()
    except SchemaError as e:  # pragma: no cover - lint reports it already
        add("schema", str(e), fixed_severity="block")
        return _result(findings, suppressed, index_diffs, None)

    # what `diting sync` would do for unresolved anchors
    planned = {(a.concept, a.occurrence, a.ref): a for a in sync(repo, dry_run=True, symbols=symbols).actions}

    # ---- optional diff: impact analysis
    imp = impact(repo, src) if src is not None else None
    diff_review: dict[str, set[str]] = {}
    if imp is not None:
        for c in imp["concepts"]:
            if c["review"]:
                diff_review[c["id"]] = set(c["review"])

    for cid, ef in sorted(edges.items()):
        c = concepts.get(cid)
        if c is None or c.lifecycle not in GATED_LIFECYCLES:
            continue
        lvl = c.level
        fam = family_hash(ef, symbols)
        changed_sites = sorted({o.id for o in ef.occurrences for a in o.anchors
                                if a.ref in symbols and a.fingerprint and symbols[a.ref].fingerprint != a.fingerprint})
        for occ in ef.occurrences:
            occ_flagged = False
            for a in occ.anchors:
                where = f"{cid}/{occ.id}"
                sym = symbols.get(a.ref)
                if sym is None:
                    plan = planned.get((cid, occ.id, a.ref))
                    if plan is not None and plan.action == "moved":
                        add("anchor-moved", f"{where}: {a.ref} moved to {plan.new_ref}", level=lvl, concept=cid,
                            occurrence=occ.id, ref=a.ref, fix="run `diting sync` and commit .diting/")
                    elif plan is not None and plan.action == "reshaped":
                        add("anchor-lost", f"{where}: {a.ref} gone; likely renamed with changes to {plan.new_ref}",
                            level=lvl, concept=cid, occurrence=occ.id, ref=a.ref,
                            fix="run `diting sync`, review the site, then `diting confirm`")
                    else:
                        add("anchor-lost", f"{where}: {a.ref} not found in code", level=lvl, concept=cid,
                            occurrence=occ.id, ref=a.ref,
                            fix="re-anchor with `diting anchor add` or remove the occurrence from .diting/edges/")
                    occ_flagged = True
                    continue
                if a.fingerprint and sym.fingerprint != a.fingerprint:
                    add("anchor-changed", f"{where}: logic of {a.ref} changed since it was confirmed",
                        level=lvl, concept=cid, occurrence=occ.id, ref=a.ref,
                        fix=f"make sure every site of {cid} is consistent, then `diting confirm {cid}`")
                    occ_flagged = True
                elif a.state in ("suspect", "lost"):
                    add(f"anchor-{a.state}", f"{where}: {a.ref} is marked {a.state}", level=lvl, concept=cid,
                        occurrence=occ.id, ref=a.ref, fix=f"review, then `diting confirm {cid} {occ.id}`")
                    occ_flagged = True
                if a.confirmed_version is not None and a.confirmed_version < c.version:
                    add("stale-occurrence", f"{where}: confirmed at v{a.confirmed_version}, concept is now v{c.version}",
                        level=lvl, concept=cid, occurrence=occ.id, ref=a.ref,
                        fix=f"re-check the site against the new statement, then `diting confirm {cid} {occ.id}`")
                    occ_flagged = True
            if occ_flagged:
                continue
            # missed change: this site is unchanged, but the family moved on without it
            behind = any(a.basis is not None and a.basis != fam for a in occ.anchors)
            in_diff = occ.id in diff_review.get(cid, set()) and any(a.basis != fam for a in occ.anchors)
            if behind or in_diff:
                others = [s for s in changed_sites if s != occ.id]
                why = f"sibling(s) {', '.join(others)} changed" if others else "other sites changed since this one was confirmed"
                var = f" (registered variant: {occ.variant})" if occ.variant else ""
                add("sibling-review", f"{cid}/{occ.id}: {why} but this site was not updated or re-confirmed{var}",
                    level=lvl, concept=cid, occurrence=occ.id, ref=", ".join(a.ref for a in occ.anchors),
                    fix=f"apply the same change here, or `diting confirm {cid} {occ.id}` if it is still correct")

    return _result(findings, suppressed, index_diffs, imp)


def _result(findings: list[CheckFinding], suppressed: int, index_diffs: list[dict], imp: dict | None) -> dict:
    blocking = [f for f in findings if f.severity == "block"]
    out = {
        "ok": not blocking,
        "blocking": len(blocking),
        "warnings": len(findings) - len(blocking),
        "suppressed": suppressed,
        "index_consistent": not index_diffs,
        "findings": [f.to_dict() for f in sorted(findings, key=lambda f: (f.severity != "block", f.concept or "",
                                                                             f.occurrence or "", f.code))],
    }
    if imp is not None:
        out["impact"] = {
            "source": imp["source"],
            "concepts": [{"id": c["id"], "changed": c["changed"], "review": c["review"]} for c in imp["concepts"]],
            "unindexed": imp["unindexed"],
        }
    return out
