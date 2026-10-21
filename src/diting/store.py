"""Repository-level storage: locating ``.diting/``, reading/writing the YAML source of truth,
scanning source files into a symbol table, and the rebuildable cache."""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import yaml

from .extract import Symbol, get_extractors
from .model import Concept, EdgeFile, SchemaError, Vocabulary

DITING_DIR = ".diting"
CACHE_DIR = ".cache"
SCHEMA_VERSION = 1

DEFAULT_CONFIG: dict = {
    "version": SCHEMA_VERSION,
    "languages": ["python"],
    "include": ["**/*.py"],
    "exclude": [
        ".git/**",
        ".diting/**",
        ".venv/**",
        "venv/**",
        "**/node_modules/**",
        "**/__pycache__/**",
        "build/**",
        "dist/**",
    ],
    # gate policy per concept level: block | warn | off
    "gate": {"critical": "block", "normal": "block", "info": "warn"},
}

_ALWAYS_SKIP_DIRS = {".git", ".diting", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache"}


class NotInitialized(RuntimeError):
    pass


def _dump(data: object) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)


def _load(path: Path) -> object:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _match(rel: str, patterns: list[str]) -> bool:
    for p in patterns:
        if fnmatch.fnmatch(rel, p):
            return True
        # make "**/x" also match top-level "x"
        if p.startswith("**/") and fnmatch.fnmatch(rel, p[3:]):
            return True
    return False


@dataclass
class Repo:
    root: Path

    # ------------------------------------------------------------ locating
    @classmethod
    def find(cls, start: Path | str = ".") -> "Repo":
        cur = Path(start).resolve()
        for p in (cur, *cur.parents):
            if (p / DITING_DIR / "config.yaml").is_file():
                return cls(p)
        raise NotInitialized(f"no {DITING_DIR}/ found from {cur}; run `diting init` first")

    @property
    def dir(self) -> Path:
        return self.root / DITING_DIR

    @property
    def cache_dir(self) -> Path:
        return self.dir / CACHE_DIR

    @property
    def initialized(self) -> bool:
        return (self.dir / "config.yaml").is_file()

    def init_layout(self) -> list[str]:
        """Create the directory layout; never overwrite existing human-authored files."""
        created: list[str] = []
        for sub in ("concepts", "edges", "changesets"):
            d = self.dir / sub
            if not d.exists():
                d.mkdir(parents=True)
                (d / ".gitkeep").touch()
                created.append(f"{DITING_DIR}/{sub}/")
        files = {
            "config.yaml": DEFAULT_CONFIG,
            "vocab.yaml": Vocabulary().to_dict(),
        }
        for name, data in files.items():
            p = self.dir / name
            if not p.exists():
                p.write_text(_dump(data), encoding="utf-8")
                created.append(f"{DITING_DIR}/{name}")
        gi = self.dir / ".gitignore"
        if not gi.exists():
            gi.write_text(f"{CACHE_DIR}/\nvocab.candidates.yaml\n", encoding="utf-8")
            created.append(f"{DITING_DIR}/.gitignore")
        return created

    # -------------------------------------------------------------- config
    @property
    def config(self) -> dict:
        if not hasattr(self, "_config"):
            raw = _load(self.dir / "config.yaml") or {}
            cfg = {**DEFAULT_CONFIG, **raw}
            cfg["gate"] = {**DEFAULT_CONFIG["gate"], **(raw.get("gate") or {})}
            self._config = cfg
        return self._config

    # ---------------------------------------------------------- vocabulary
    def load_vocab(self) -> Vocabulary:
        p = self.dir / "vocab.yaml"
        return Vocabulary.from_dict(_load(p) if p.exists() else None)

    def save_vocab(self, vocab: Vocabulary) -> None:
        vocab.validate()
        (self.dir / "vocab.yaml").write_text(_dump(vocab.to_dict()), encoding="utf-8")

    # ------------------------------------------------------------ concepts
    def concept_path(self, concept_id: str) -> Path:
        parts = concept_id.split(".")
        return self.dir / "concepts" / Path(*parts[:-1]) / f"{parts[-1]}.yaml"

    def load_concepts(self) -> dict[str, Concept]:
        out: dict[str, Concept] = {}
        base = self.dir / "concepts"
        for p in sorted(base.rglob("*.yaml")) if base.exists() else []:
            data = _load(p)
            if not data:
                continue
            try:
                c = Concept.from_dict(data)
            except SchemaError as e:
                raise SchemaError(f"{p.relative_to(self.root)}: {e}") from e
            expected = self.concept_path(c.id)
            if p.resolve() != expected.resolve():
                raise SchemaError(
                    f"{p.relative_to(self.root)}: concept id {c.id!r} should live at "
                    f"{expected.relative_to(self.root)}"
                )
            if c.id in out:
                raise SchemaError(f"duplicate concept id {c.id!r}")
            out[c.id] = c
        return out

    def save_concept(self, c: Concept) -> Path:
        c.validate()
        p = self.concept_path(c.id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_dump(c.to_dict()), encoding="utf-8")
        return p

    # --------------------------------------------------------------- edges
    def edge_path(self, concept_id: str) -> Path:
        return self.dir / "edges" / f"{concept_id}.yaml"

    def load_edges(self) -> dict[str, EdgeFile]:
        out: dict[str, EdgeFile] = {}
        base = self.dir / "edges"
        for p in sorted(base.glob("*.yaml")) if base.exists() else []:
            data = _load(p)
            if not data:
                continue
            try:
                ef = EdgeFile.from_dict(data)
            except SchemaError as e:
                raise SchemaError(f"{p.relative_to(self.root)}: {e}") from e
            if p.stem != ef.concept:
                raise SchemaError(f"{p.relative_to(self.root)}: file name must equal concept id {ef.concept!r}")
            out[ef.concept] = ef
        return out

    def load_edge(self, concept_id: str) -> EdgeFile:
        p = self.edge_path(concept_id)
        return EdgeFile.from_dict(_load(p)) if p.exists() else EdgeFile(concept=concept_id)

    def save_edges(self, ef: EdgeFile) -> Path:
        p = self.edge_path(ef.concept)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_dump(ef.to_dict()), encoding="utf-8")
        return p

    # ------------------------------------------------------------- symbols
    def iter_source_files(self) -> Iterator[tuple[Path, str]]:
        include = self.config["include"]
        exclude = self.config["exclude"]
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in _ALWAYS_SKIP_DIRS)
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                rel = p.relative_to(self.root).as_posix()
                if _match(rel, include) and not _match(rel, exclude):
                    yield p, rel

    def scan(self, files: list[str] | None = None) -> dict[str, Symbol]:
        """Extract symbols. ``files`` restricts to the given repo-relative paths."""
        extractors = get_extractors(self.config["languages"])
        wanted = set(files) if files is not None else None
        symbols: dict[str, Symbol] = {}
        for p, rel in self.iter_source_files():
            if wanted is not None and rel not in wanted:
                continue
            for ex in extractors:
                if p.suffix in ex.suffixes:
                    for s in ex.extract(p, rel):
                        symbols[s.ref] = s
        return symbols

    # --------------------------------------------------------------- cache
    def write_cache(self, symbols: dict[str, Symbol]) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        p = self.cache_dir / "symbols.json"
        payload = {"version": SCHEMA_VERSION, "symbols": [s.to_dict() for s in symbols.values()]}
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return p

    def read_cache(self) -> dict[str, Symbol] | None:
        p = self.cache_dir / "symbols.json"
        if not p.exists():
            return None
        payload = json.loads(p.read_text(encoding="utf-8"))
        if payload.get("version") != SCHEMA_VERSION:
            return None
        return {s["path"] + "#" + s["qualname"]: Symbol.from_dict(s) for s in payload["symbols"]}

    def symbols(self, refresh: bool = False) -> dict[str, Symbol]:
        cached = None if refresh else self.read_cache()
        if cached is not None:
            return cached
        syms = self.scan()
        self.write_cache(syms)
        return syms
