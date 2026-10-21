"""Language extractors: turn source files into a flat list of anchorable symbols."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Protocol


@dataclass(frozen=True)
class Symbol:
    """An anchorable code unit (module, class, function, method).

    - ``qualname`` is the stable symbol path inside the file, e.g. ``DiscountCalc.apply``.
    - ``fingerprint`` hashes the normalized AST *including* identifiers (except the symbol's
      own name): unchanged when code only moves, is reformatted or the symbol is renamed.
    - ``shape`` hashes the normalized AST *without* identifiers: unchanged when only names
      inside the body change. Together they drive the re-anchoring decision tree.
    """

    path: str
    qualname: str
    kind: str  # module | class | function | method
    start_line: int
    end_line: int
    fingerprint: str
    shape: str
    identifiers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ref(self) -> str:
        return f"{self.path}#{self.qualname}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["identifiers"] = list(self.identifiers)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Symbol":
        d = dict(d)
        d["identifiers"] = tuple(d.get("identifiers", ()))
        return cls(**d)


class ExtractorError(Exception):
    """An extractor cannot run (unknown language, missing optional grammar, ABI mismatch)."""


class Extractor(Protocol):
    language: str
    suffixes: tuple[str, ...]

    def extract(self, path: Path, rel: str) -> list[Symbol]: ...


def supported_languages() -> list[str]:
    from .treesitter import SPECS

    return ["python", *SPECS]


def language_suffixes() -> dict[str, tuple[str, ...]]:
    """File suffixes per supported language (no grammar import needed)."""
    from .python import PythonExtractor
    from .treesitter import SPECS

    return {"python": PythonExtractor.suffixes, **{k: v.suffixes for k, v in SPECS.items()}}


def get_extractors(languages: Iterable[str]) -> list[Extractor]:
    from .python import PythonExtractor
    from .treesitter import SPECS, TreeSitterExtractor

    out: list[Extractor] = []
    for lang in languages:
        if lang == "python":
            out.append(PythonExtractor())
        elif lang in SPECS:
            out.append(TreeSitterExtractor(SPECS[lang]))
        else:
            raise ExtractorError(f"unsupported language: {lang} (supported: {', '.join(supported_languages())})")
    return out
