"""diting command line.

PR-1 commands: init, scan, vocab, concept, anchor, lint, status.
Every command accepts ``--json`` for compact, agent-readable output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

from . import __version__
from .bootstrap import mine_candidates
from .lint import lint
from .model import Anchor, Concept, Occurrence, SchemaError, Term
from .store import DITING_DIR, NotInitialized, Repo


class CliError(Exception):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def _emit(args: argparse.Namespace, payload: dict, human: Callable[[dict], str]) -> None:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=None if args.compact else 2))
    else:
        print(human(payload))


def _repo(args: argparse.Namespace) -> Repo:
    try:
        return Repo.find(args.root)
    except NotInitialized as e:
        raise CliError(str(e)) from e


# ---------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.root).resolve())
    created = repo.init_layout()
    symbols = repo.scan()
    repo.write_cache(symbols)
    candidates = mine_candidates(symbols)
    cand_path = repo.dir / "vocab.candidates.yaml"
    cand_path.write_text(yaml.safe_dump(candidates, allow_unicode=True, sort_keys=False), encoding="utf-8")
    payload = {
        "root": str(repo.root),
        "created": created,
        "symbols": len(symbols),
        "candidates": {
            "file": f"{DITING_DIR}/vocab.candidates.yaml",
            "entities": [e["name"] for e in candidates["entities"][:10]],
            "predicates": [p["name"] for p in candidates["predicates"][:10]],
        },
    }

    def human(p: dict) -> str:
        lines = [f"谛听 initialized at {p['root']}"]
        lines += [f"  + {c}" for c in p["created"]] or ["  (layout already present)"]
        lines.append(f"  scanned {p['symbols']} symbols")
        lines.append(f"  candidate entities:   {', '.join(p['candidates']['entities']) or '-'}")
        lines.append(f"  candidate predicates: {', '.join(p['candidates']['predicates']) or '-'}")
        lines.append(f"  review {p['candidates']['file']} and promote terms with `diting vocab add`")
        return "\n".join(lines)

    _emit(args, payload, human)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    repo = _repo(args)
    symbols = repo.symbols(refresh=True)
    items = sorted(symbols.values(), key=lambda s: (s.path, s.start_line))
    if args.grep:
        g = args.grep.lower()
        items = [s for s in items if g in s.ref.lower()]
    payload = {
        "count": len(items),
        "symbols": [
            {"ref": s.ref, "kind": s.kind, "lines": [s.start_line, s.end_line], "fingerprint": s.fingerprint}
            for s in items[: args.limit]
        ],
    }
    _emit(args, payload, lambda p: "\n".join(
        [f"{s['ref']}  ({s['kind']}, L{s['lines'][0]}-{s['lines'][1]})" for s in p["symbols"]]
        + [f"-- {p['count']} symbols"]))
    return 0


def cmd_vocab_add(args: argparse.Namespace) -> int:
    repo = _repo(args)
    vocab = repo.load_vocab()
    if args.kind == "context":
        if args.name not in vocab.contexts:
            vocab.contexts.append(args.name)
    else:
        bucket = vocab.entities if args.kind == "entity" else vocab.predicates
        existing = next((t for t in bucket if t.name == args.name), None)
        term = existing or Term(name=args.name)
        for s in args.synonym or []:
            if s not in term.synonyms:
                term.synonyms.append(s)
        if args.hypernym:
            term.hypernym = args.hypernym
        if args.klass:
            term.klass = args.klass
        if args.context:
            term.context = args.context
        if args.description:
            term.description = args.description
        if existing is None:
            bucket.append(term)
    try:
        repo.save_vocab(vocab)
    except SchemaError as e:
        raise CliError(str(e)) from e
    _emit(args, {"ok": True, "kind": args.kind, "name": args.name}, lambda p: f"vocab: {p['kind']} {p['name']} saved")
    return 0


def cmd_vocab_list(args: argparse.Namespace) -> int:
    repo = _repo(args)
    v = repo.load_vocab().to_dict()

    def human(p: dict) -> str:
        lines = [f"contexts: {', '.join(p['contexts']) or '-'}", "entities:"]
        for e in p["entities"]:
            extra = f" = {', '.join(e['synonyms'])}" if e.get("synonyms") else ""
            hyper = f"  (is-a {e['hypernym']})" if e.get("hypernym") else ""
            lines.append(f"  {e['name']}{extra}{hyper}")
        lines.append("predicates:")
        for t in p["predicates"]:
            extra = f" = {', '.join(t['synonyms'])}" if t.get("synonyms") else ""
            cls = f"  [{t['class']}]" if t.get("class") else ""
            lines.append(f"  {t['name']}{extra}{cls}")
        return "\n".join(lines)

    _emit(args, v, human)
    return 0


def _parse_modifiers(items: list[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise CliError(f"modifier must be key=value, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def cmd_concept_add(args: argparse.Namespace) -> int:
    repo = _repo(args)
    concepts = repo.load_concepts()
    if args.id in concepts and not args.force:
        raise CliError(f"concept {args.id!r} exists (use --force to overwrite, or edit the YAML)")
    try:
        c = Concept(
            id=args.id,
            statement=args.statement,
            context=args.context,
            subject=args.subject,
            predicate=args.predicate,
            object=args.object,
            modifiers=_parse_modifiers(args.modifier),
            lifecycle=args.lifecycle,
            level=args.level,
            verify=args.verify,
        )
        warnings = c.validate(repo.load_vocab())
        path = repo.save_concept(c)
    except SchemaError as e:
        raise CliError(str(e)) from e
    payload = {"ok": True, "id": c.id, "file": str(path.relative_to(repo.root)), "warnings": warnings}
    _emit(args, payload, lambda p: "\n".join([f"concept {p['id']} -> {p['file']}"] + [f"  ! {w}" for w in p["warnings"]]))
    return 0


def cmd_concept_list(args: argparse.Namespace) -> int:
    repo = _repo(args)
    concepts = repo.load_concepts()
    edges = repo.load_edges()
    rows = []
    for c in concepts.values():
        ef = edges.get(c.id)
        rows.append({
            "id": c.id,
            "statement": c.statement,
            "spo": [c.subject, c.predicate, c.object],
            "lifecycle": c.lifecycle,
            "level": c.level,
            "occurrences": len(ef.occurrences) if ef else 0,
        })
    _emit(args, {"count": len(rows), "concepts": rows}, lambda p: "\n".join(
        [f"{r['id']}  [{r['lifecycle']}/{r['level']}]  {r['occurrences']} occ  — {r['statement']}" for r in p["concepts"]]
        or ["(no concepts)"]))
    return 0


def cmd_anchor_add(args: argparse.Namespace) -> int:
    repo = _repo(args)
    concepts = repo.load_concepts()
    concept = concepts.get(args.concept)
    if concept is None:
        raise CliError(f"unknown concept {args.concept!r}")
    if "#" not in args.ref:
        raise CliError("ref must look like path/to/file.py#Qual.name")
    path, symbol = args.ref.split("#", 1)
    symbols = repo.symbols(refresh=True)
    sym = symbols.get(args.ref)
    if sym is None:
        close = [r for r in symbols if r.endswith("#" + symbol) or r.startswith(path + "#")][:5]
        hint = f" (did you mean: {', '.join(close)})" if close else ""
        raise CliError(f"symbol {args.ref} not found{hint}")
    ef = repo.load_edge(concept.id)
    occ_id = args.occurrence or path.replace("/", ".").removesuffix(".py") + ":" + symbol
    occ = ef.occurrence(occ_id)
    if occ is None:
        occ = Occurrence(id=occ_id, variant=args.variant, channel=args.channel)
        ef.occurrences.append(occ)
    else:
        if args.variant:
            occ.variant = args.variant
        if args.channel:
            occ.channel = args.channel
    anchor = Anchor(path=path, symbol=symbol, fingerprint=sym.fingerprint, shape=sym.shape,
                    state="verified", confirmed_version=concept.version)
    occ.anchors = [a for a in occ.anchors if a.ref != anchor.ref] + [anchor]
    repo.save_edges(ef)
    payload = {"ok": True, "concept": concept.id, "occurrence": occ.id, "anchor": anchor.ref,
               "fingerprint": sym.fingerprint}
    _emit(args, payload, lambda p: f"anchored {p['anchor']} -> {p['concept']} / {p['occurrence']}")
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    repo = _repo(args)
    rep = lint(repo)
    payload = rep.to_dict()

    def human(p: dict) -> str:
        lines = [f"{f['level'].upper():7} {f['code']:20} {f['message']}" for f in p["findings"]]
        lines.append(f"-- {p['errors']} error(s), {p['warnings']} warning(s)")
        return "\n".join(lines)

    _emit(args, payload, human)
    return 0 if rep.ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    repo = _repo(args)
    vocab = repo.load_vocab()
    concepts = repo.load_concepts()
    edges = repo.load_edges()
    anchors = [a for ef in edges.values() for o in ef.occurrences for a in o.anchors]
    states: dict[str, int] = {}
    for a in anchors:
        states[a.state] = states.get(a.state, 0) + 1
    payload = {
        "root": str(repo.root),
        "contexts": vocab.contexts,
        "entities": len(vocab.entities),
        "predicates": len(vocab.predicates),
        "concepts": len(concepts),
        "occurrences": sum(len(ef.occurrences) for ef in edges.values()),
        "anchors": len(anchors),
        "anchor_states": states,
    }
    _emit(args, payload, lambda p: "\n".join([
        f"root:        {p['root']}",
        f"contexts:    {', '.join(p['contexts']) or '-'}",
        f"vocabulary:  {p['entities']} entities, {p['predicates']} predicates",
        f"concepts:    {p['concepts']}",
        f"occurrences: {p['occurrences']}  anchors: {p['anchors']}"
        + (f"  ({', '.join(f'{k} {v}' for k, v in sorted(p['anchor_states'].items()))})"
           if p['anchor_states'] else ""),
    ]))
    return 0


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".", help="repository root (default: search upwards from cwd)")
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("--compact", action="store_true", help="single-line JSON")

    p = argparse.ArgumentParser(prog="diting", description="谛听 — a concept index for code.", parents=[common])
    p.add_argument("--version", action="version", version=f"diting {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", parents=[common], help="create .diting/ and mine candidate vocabulary")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("scan", parents=[common], help="rebuild the symbol cache and list symbols")
    s.add_argument("--grep", help="filter refs by substring")
    s.add_argument("--limit", type=int, default=200)
    s.set_defaults(func=cmd_scan)

    v = sub.add_parser("vocab", parents=[common], help="manage the controlled vocabulary")
    vs = v.add_subparsers(dest="vocab_cmd", required=True)
    s = vs.add_parser("add", parents=[common], help="add or extend a term")
    s.add_argument("kind", choices=["entity", "predicate", "context"])
    s.add_argument("name")
    s.add_argument("-s", "--synonym", action="append", help="synonym (repeatable)")
    s.add_argument("--hypernym", help="entities: broader entity (is-a)")
    s.add_argument("--class", dest="klass", choices=["compute", "validate", "display", "persist", "permission", "other"])
    s.add_argument("--context")
    s.add_argument("--description")
    s.set_defaults(func=cmd_vocab_add)
    s = vs.add_parser("list", parents=[common], help="show the vocabulary")
    s.set_defaults(func=cmd_vocab_list)

    c = sub.add_parser("concept", parents=[common], help="manage concepts")
    cs = c.add_subparsers(dest="concept_cmd", required=True)
    s = cs.add_parser("add", parents=[common], help="declare a concept as a controlled S-P-O statement")
    s.add_argument("id", help="dotted id, e.g. trade.member_discount.rounding")
    s.add_argument("statement", help="the human sentence, e.g. 会员折扣金额四舍五入保留两位小数")
    s.add_argument("--context")
    s.add_argument("-S", "--subject")
    s.add_argument("-P", "--predicate")
    s.add_argument("-O", "--object")
    s.add_argument("-m", "--modifier", action="append", help="key=value, e.g. condition='订单金额>=200'")
    s.add_argument("--lifecycle", default="active", choices=["draft", "active", "deprecated", "removed"])
    s.add_argument("--level", default="normal", choices=["critical", "normal", "info"])
    s.add_argument("--verify", default="property", choices=["property", "example", "manual"])
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_concept_add)
    s = cs.add_parser("list", parents=[common], help="list concepts")
    s.set_defaults(func=cmd_concept_list)

    a = sub.add_parser("anchor", parents=[common], help="manage occurrences and anchors")
    as_ = a.add_subparsers(dest="anchor_cmd", required=True)
    s = as_.add_parser("add", parents=[common], help="anchor a concept occurrence to a code symbol")
    s.add_argument("concept")
    s.add_argument("ref", help="path/to/file.py#Qual.name")
    s.add_argument("-o", "--occurrence", help="occurrence id (default derived from ref)")
    s.add_argument("--variant", help="registered deviation at this site, e.g. '展示时取整到元'")
    s.add_argument("--channel", help="e.g. App / Web / backend")
    s.set_defaults(func=cmd_anchor_add)

    s = sub.add_parser("lint", parents=[common], help="validate .diting/ against the current code")
    s.set_defaults(func=cmd_lint)

    s = sub.add_parser("status", parents=[common], help="summary of the index")
    s.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (CliError, SchemaError) as e:
        code = getattr(e, "code", 2)
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        else:
            print(f"diting: error: {e}", file=sys.stderr)
        return code


if __name__ == "__main__":
    sys.exit(main())
