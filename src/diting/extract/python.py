"""Python extractor built on the stdlib ``ast`` module (no native deps).

tree-sitter based extractors for other languages can implement the same ``Extractor``
protocol later; the index layer is language agnostic.
"""

from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path

from . import Symbol

_IDENT_FIELDS = {
    ast.Name: ("id",),
    ast.arg: ("arg",),
    ast.Attribute: ("attr",),
    ast.FunctionDef: ("name",),
    ast.AsyncFunctionDef: ("name",),
    ast.ClassDef: ("name",),
    ast.keyword: ("arg",),
    ast.alias: ("name", "asname"),
}


def _strip_docstring(node: ast.AST) -> ast.AST:
    body = getattr(node, "body", None)
    if (
        isinstance(body, list)
        and body
        and isinstance(body[0], ast.Expr)
        and isinstance(getattr(body[0], "value", None), ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        node.body = body[1:] or [ast.Pass()]
    return node


def _normalize(node: ast.AST, *, anonymize: bool) -> str:
    """Dump an AST without positions/docstrings; optionally erase identifiers.

    The symbol's own name is always erased: it is already captured by the ref, so a pure
    rename keeps the fingerprint and is handled like a move.
    """
    node = copy.deepcopy(node)
    if hasattr(node, "name"):
        node.name = "_"
    for n in ast.walk(node):
        _strip_docstring(n)
        if anonymize:
            for typ, fields in _IDENT_FIELDS.items():
                if isinstance(n, typ):
                    for f in fields:
                        if getattr(n, f, None) is not None:
                            setattr(n, f, "_")
    return ast.dump(node, annotate_fields=False, include_attributes=False)


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _identifiers(node: ast.AST) -> tuple[str, ...]:
    seen: set[str] = set()
    for n in ast.walk(node):
        for typ, fields in _IDENT_FIELDS.items():
            if isinstance(n, typ):
                for f in fields:
                    v = getattr(n, f, None)
                    if isinstance(v, str):
                        seen.add(v.split(".")[-1])
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.isidentifier():
            seen.add(n.value)
    return tuple(sorted(seen))


class PythonExtractor:
    language = "python"
    suffixes = (".py",)

    def extract(self, path: Path, rel: str) -> list[Symbol]:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return []
        return self.extract_text(text, rel)

    def extract_text(self, text: str, rel: str) -> list[Symbol]:
        try:
            tree = ast.parse(text, filename=rel)
        except (SyntaxError, ValueError):
            return []
        out: list[Symbol] = []

        def visit(node: ast.AST, prefix: str, in_class: bool) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    qual = f"{prefix}.{child.name}" if prefix else child.name
                    if isinstance(child, ast.ClassDef):
                        kind = "class"
                    else:
                        kind = "method" if in_class else "function"
                    out.append(
                        Symbol(
                            path=rel,
                            qualname=qual,
                            kind=kind,
                            # decorators belong to the symbol (they are part of its fingerprint)
                            start_line=min([child.lineno, *(d.lineno for d in child.decorator_list)]),
                            end_line=getattr(child, "end_lineno", child.lineno) or child.lineno,
                            fingerprint=_hash(_normalize(child, anonymize=False)),
                            shape=_hash(_normalize(child, anonymize=True)),
                            identifiers=_identifiers(child),
                        )
                    )
                    visit(child, qual, isinstance(child, ast.ClassDef))

        visit(tree, "", False)
        return out
