"""Forward index: a natural-language sentence -> concepts -> occurrences -> code sites.

Matching is deliberately simple and explainable (no embeddings):

1. the sentence is matched against every *surface form* of the controlled vocabulary
   (term names + synonyms, case-insensitive, longest match wins);
2. matched entities are expanded along the is-a hierarchy: asking about a broader entity
   finds concepts about its descendants, asking about a narrower one also finds (with a lower
   score) concepts stated for its ancestors;
3. concepts are scored by which S-P-O slots matched, plus a small text-overlap bonus over the
   statement / modifiers / id so that terms not yet in the vocabulary still help.

Every hit carries the reasons it matched so humans and agents can judge it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .bootstrap import split_identifier
from .extract import Symbol
from .model import Concept, EdgeFile, Vocabulary

W_SLOT_EXACT = 3.0
W_SLOT_NARROWER = 2.0  # query names a broader entity, concept is about a descendant
W_SLOT_BROADER = 1.0  # query names a narrower entity, concept is stated for an ancestor
W_PREDICATE = 2.0
W_CONTEXT = 1.0
W_TEXT = 2.0

_ASCII_WORD = re.compile(r"^[a-z0-9_]+$")
_CJK = re.compile(r"[㐀-鿿]")


def _normalize_text(sentence: str) -> str:
    low = sentence.lower()
    words = [w for tok in re.findall(r"[A-Za-z][A-Za-z0-9_]*", sentence) for w in split_identifier(tok)]
    return low + " " + " ".join(words)


def _grams(text: str) -> set[str]:
    """ASCII words (split camel/snake) plus CJK character bigrams."""
    text = text or ""
    out = {w for tok in re.findall(r"[A-Za-z][A-Za-z0-9_]*", text) for w in split_identifier(tok) if len(w) > 1}
    for run in re.findall(r"[㐀-鿿]+", text):
        if len(run) == 1:
            out.add(run)
        out.update(run[i : i + 2] for i in range(len(run) - 1))
    return out


@dataclass
class VocabMatch:
    entities: set[str] = field(default_factory=set)
    predicates: set[str] = field(default_factory=set)
    contexts: set[str] = field(default_factory=set)
    surface: dict[str, str] = field(default_factory=dict)  # matched surface form -> term

    def to_dict(self) -> dict:
        return {
            "entities": sorted(self.entities),
            "predicates": sorted(self.predicates),
            "contexts": sorted(self.contexts),
            "surface": dict(sorted(self.surface.items())),
        }


def match_vocab(sentence: str, vocab: Vocabulary) -> VocabMatch:
    text = _normalize_text(sentence)
    forms: list[tuple[str, str, str]] = []  # (form, kind, term)
    for kind, terms in (("entity", vocab.entities), ("predicate", vocab.predicates)):
        for t in terms:
            for f in t.surface_forms():
                forms.append((f.lower(), kind, t.name))
    forms += [(c.lower(), "context", c) for c in vocab.contexts]
    forms.sort(key=lambda x: -len(x[0]))

    claimed = [False] * len(text)
    m = VocabMatch()
    for form, kind, name in forms:
        if not form:
            continue
        if _ASCII_WORD.match(form):
            pattern = re.compile(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])")
        else:
            pattern = re.compile(re.escape(form))
        for hit in pattern.finditer(text):
            span = range(hit.start(), hit.end())
            if any(claimed[i] for i in span):
                continue
            for i in span:
                claimed[i] = True
            {"entity": m.entities, "predicate": m.predicates, "context": m.contexts}[kind].add(name)
            m.surface[form] = name
    return m


def _ancestors(vocab: Vocabulary, entity: str) -> set[str]:
    out: set[str] = set()
    cur = vocab.entity(entity)
    while cur is not None and cur.hypernym and cur.hypernym not in out:
        out.add(cur.hypernym)
        cur = vocab.entity(cur.hypernym)
    return out


@dataclass
class Hit:
    concept: Concept
    score: float
    reasons: list[str]


def score_concepts(
    sentence: str,
    vocab: Vocabulary,
    concepts: dict[str, Concept],
    *,
    include_removed: bool = False,
) -> tuple[VocabMatch, list[Hit]]:
    vm = match_vocab(sentence, vocab)
    narrower: dict[str, str] = {}  # descendant -> queried entity
    broader: dict[str, str] = {}  # ancestor -> queried entity
    for e in vm.entities:
        for d in vocab.descendants(e) - {e}:
            narrower.setdefault(d, e)
        for a in _ancestors(vocab, e):
            broader.setdefault(a, e)
    qgrams = _grams(sentence)

    hits: list[Hit] = []
    for c in concepts.values():
        if c.lifecycle == "removed" and not include_removed:
            continue
        score = 0.0
        reasons: list[str] = []
        for role, name in (("subject", c.subject), ("object", c.object)):
            if not name:
                continue
            if name in vm.entities:
                score += W_SLOT_EXACT
                reasons.append(f"{role}={name}")
            elif name in narrower:
                score += W_SLOT_NARROWER
                reasons.append(f"{role}={name} (is-a {narrower[name]})")
            elif name in broader:
                score += W_SLOT_BROADER
                reasons.append(f"{role}={name} (broader than {broader[name]})")
        if c.predicate and c.predicate in vm.predicates:
            score += W_PREDICATE
            reasons.append(f"predicate={c.predicate}")
        if c.context and c.context in vm.contexts:
            score += W_CONTEXT
            reasons.append(f"context={c.context}")
        if qgrams:
            body = " ".join([c.statement, c.id.replace(".", " "), *map(str, c.modifiers.values())])
            overlap = qgrams & _grams(body)
            if overlap:
                frac = len(overlap) / len(qgrams)
                score += round(W_TEXT * frac, 3)
                reasons.append("text~" + ",".join(sorted(overlap)[:6]))
        if c.id in sentence:
            score += W_SLOT_EXACT
            reasons.append("id")
        if score > 0:
            hits.append(Hit(c, round(score, 3), reasons))
    hits.sort(key=lambda h: (-h.score, h.concept.id))
    return vm, hits


def describe_sites(ef: EdgeFile | None, symbols: dict[str, Symbol]) -> list[dict]:
    """Occurrences of a concept with their anchors resolved against the current code."""
    out = []
    for occ in ef.occurrences if ef else []:
        anchors = []
        for a in occ.anchors:
            sym = symbols.get(a.ref)
            item: dict = {"ref": a.ref, "state": a.state}
            if sym is None:
                item["resolved"] = False
            else:
                item["resolved"] = True
                item["lines"] = [sym.start_line, sym.end_line]
                item["drift"] = bool(a.fingerprint and a.fingerprint != sym.fingerprint)
            anchors.append(item)
        d: dict = {"id": occ.id, "anchors": anchors}
        if occ.variant:
            d["variant"] = occ.variant
        if occ.channel:
            d["channel"] = occ.channel
        out.append(d)
    return out


def query(repo, sentence: str, *, limit: int = 10, min_score: float = 0.5,
          include_removed: bool = False) -> dict:
    vocab = repo.load_vocab()
    concepts = repo.load_concepts()
    edges = repo.load_edges()
    symbols = repo.symbols(refresh=True)
    vm, hits = score_concepts(sentence, vocab, concepts, include_removed=include_removed)
    hits = [h for h in hits if h.score >= min_score][:limit]
    return {
        "query": sentence,
        "matched": vm.to_dict(),
        "count": len(hits),
        "concepts": [
            {
                "id": h.concept.id,
                "score": h.score,
                "reasons": h.reasons,
                "statement": h.concept.statement,
                "spo": [h.concept.subject, h.concept.predicate, h.concept.object],
                "modifiers": h.concept.modifiers,
                "lifecycle": h.concept.lifecycle,
                "level": h.concept.level,
                "version": h.concept.version,
                "occurrences": describe_sites(edges.get(h.concept.id), symbols),
            }
            for h in hits
        ],
    }
