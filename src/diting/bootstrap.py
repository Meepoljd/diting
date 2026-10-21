"""Cold start: mine candidate vocabulary from code for a human to confirm.

Heuristics only. Nothing produced here enters ``vocab.yaml`` automatically; candidates are
written to ``.diting/vocab.candidates.yaml`` and promoted by a human (or an agent with a
human in the loop).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from .extract import Symbol

_SPLIT = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

# verb prefix -> predicate class
VERB_CLASSES: dict[str, str] = {
    **dict.fromkeys(["calc", "calculate", "compute", "apply", "sum", "total", "round", "convert", "estimate"], "compute"),
    **dict.fromkeys(["validate", "check", "verify", "ensure", "assert", "is", "has", "can", "should"], "validate"),
    **dict.fromkeys(["format", "render", "display", "show", "print", "to", "draw", "view"], "display"),
    **dict.fromkeys(["save", "store", "persist", "insert", "update", "delete", "write", "load", "get", "fetch", "find", "read", "query"], "persist"),
    **dict.fromkeys(["allow", "deny", "authorize", "permit", "grant", "forbid"], "permission"),
}

_STOP = {
    "self", "cls", "init", "main", "test", "tests", "the", "a", "an", "of", "for", "and", "or", "in",
    "on", "by", "with", "to", "from", "new", "old", "tmp", "util", "utils", "helper", "helpers",
    "base", "impl", "manager", "service", "handler", "data", "info", "value", "item", "obj", "args",
    "kwargs", "str", "int", "float", "list", "dict", "set", "none", "true", "false", "result",
}


def split_identifier(name: str) -> list[str]:
    return [t.lower() for part in name.split("_") for t in _SPLIT.findall(part)]


def mine_candidates(symbols: dict[str, Symbol], *, top: int = 40) -> dict:
    entity_counts: Counter[str] = Counter()
    entity_where: dict[str, set[str]] = defaultdict(set)
    verb_counts: Counter[str] = Counter()
    verb_where: dict[str, set[str]] = defaultdict(set)

    for s in symbols.values():
        leaf = s.qualname.split(".")[-1]
        if leaf.startswith("test_") or s.path.startswith("tests/") or "/tests/" in s.path:
            continue
        tokens = split_identifier(leaf)
        if not tokens:
            continue
        if s.kind == "class":
            for t in tokens:
                if t not in _STOP and len(t) > 2:
                    entity_counts[t] += 3
                    entity_where[t].add(s.ref)
        else:
            verb, nouns = tokens[0], tokens[1:]
            if verb in VERB_CLASSES:
                verb_counts[verb] += 1
                verb_where[verb].add(s.ref)
            for t in nouns:
                if t not in _STOP and t not in VERB_CLASSES and len(t) > 2:
                    entity_counts[t] += 1
                    entity_where[t].add(s.ref)

    def pack(counts: Counter[str], where: dict[str, set[str]], verbs: bool) -> list[dict]:
        out = []
        for term, score in counts.most_common(top):
            item: dict = {"name": term, "score": score, "seen_in": sorted(where[term])[:5]}
            if verbs:
                item["class"] = VERB_CLASSES[term]
            out.append(item)
        return out

    return {
        "note": "Candidates mined from identifiers. Review, rename to domain language, add synonyms, "
        "then promote into vocab.yaml (e.g. `diting vocab add`). This file is safe to delete.",
        "entities": pack(entity_counts, entity_where, verbs=False),
        "predicates": pack(verb_counts, verb_where, verbs=True),
    }
