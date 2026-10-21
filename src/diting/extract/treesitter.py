"""Multi-language extractor built on tree-sitter.

One generic walker is driven by a declarative :class:`LangSpec` per language. Each spec says
which syntax nodes are *classes* (types, interfaces, traits, modules ...), which are
*functions* (functions, methods, constructors, function-valued variables ...), which nodes only
contribute a qualname segment (*containers*: Rust ``impl``, C++/TS namespaces ...), which leaves
are identifiers, and which are comments.

Hashing mirrors the Python extractor:

- ``fingerprint`` serializes the symbol's syntax tree (node types + leaf text), skipping
  comments and erasing the symbol's own name, so moves / renames / reformatting keep it;
- ``shape`` additionally erases every identifier leaf, so internal renames keep it.

Grammars are optional dependencies (``pip install "diting[go]"`` etc.). They are imported
lazily; a missing grammar raises :class:`~diting.extract.ExtractorError` with the install hint.
"""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from . import ExtractorError, Symbol

Node = Any  # tree_sitter.Node (kept untyped so this module imports without tree-sitter)


# --------------------------------------------------------------------------- spec model


@dataclass(frozen=True)
class Hit:
    """How a syntax node participates in the symbol tree."""

    role: str  # "class" | "function" | "container"
    name: str | None
    erase: tuple = ()  # nodes serialized as "_" (the symbol's own name, receiver type ...)
    scope: tuple[str, ...] = ()  # extra qualname segments, e.g. Go receiver, C++ ``Calc::``
    classlike: bool = False  # containers only: functions inside become methods


Resolver = Callable[[Node], "Hit | None"]


@dataclass(frozen=True)
class LangSpec:
    name: str
    extra: str  # pip extra providing the grammar
    module: str
    suffixes: tuple[str, ...]
    func: str = "language"
    suffix_funcs: tuple[tuple[str, str], ...] = ()  # per-suffix grammar function overrides
    classes: frozenset[str] = frozenset()
    functions: frozenset[str] = frozenset()
    containers: frozenset[str] = frozenset()
    classlike_containers: frozenset[str] = frozenset()
    comments: frozenset[str] = frozenset({"comment"})
    identifiers: frozenset[str] = frozenset({"identifier"})
    prefix: frozenset[str] = frozenset()  # preceding siblings that belong to the symbol
    wrappers: frozenset[str] = frozenset()  # parents that widen the line span (export, template)
    custom: dict[str, Resolver] = field(default_factory=dict)


# ------------------------------------------------------------------------------ helpers


def _txt(n: Node) -> str:
    return n.text.decode("utf-8", "replace")


_STRINGS = {"string", "string_literal", "interpreted_string_literal", "raw_string_literal"}


def _name_text(n: Node) -> str | None:
    """Readable name of a name node: strings lose quotes, qualified names keep the last part."""
    if n.type in _STRINGS:
        return _txt(n).strip("'\"`") or None
    if n.type in ("computed_property_name",):
        return None
    if n.type in ("scope_resolution", "qualified_name", "namespace_name", "scoped_identifier"):
        last = n.child_by_field_name("name")
        if last is None and n.named_children:
            last = n.named_children[-1]
        return _txt(last) if last is not None else None
    return _txt(n)


def _named(field_name: str, role: str, classlike: bool = False) -> Resolver:
    def resolve(node: Node) -> Hit | None:
        n = node.child_by_field_name(field_name)
        if n is None:
            return None
        name = _name_text(n)
        return Hit(role, name, (n,), classlike=classlike) if name else None

    return resolve


def _fixed(role: str, name: str | None, classlike: bool = False) -> Resolver:
    return lambda node: Hit(role, name, (), classlike=classlike)


def _with_body(inner: Resolver) -> Resolver:
    """C/C++ ``struct X`` is only a definition when it has a body."""
    return lambda node: inner(node) if node.child_by_field_name("body") is not None else None


# ---------------------------------------------------------------- language resolvers

# Go -------------------------------------------------------------------------------


def _go_method(node: Node) -> Hit | None:
    name = node.child_by_field_name("name")
    if name is None:
        return None
    t = None
    recv = node.child_by_field_name("receiver")
    for p in recv.named_children if recv is not None else ():
        if p.type == "parameter_declaration":
            t = p.child_by_field_name("type")
            break
    while t is not None and t.type in ("pointer_type", "generic_type", "parenthesized_type"):
        t = t.child_by_field_name("type") or (t.named_children[0] if t.named_children else None)
    if t is not None and t.type == "type_identifier":
        return Hit("function", _txt(name), (name, t), (_txt(t),))
    return Hit("function", _txt(name), (name,))


# C / C++ --------------------------------------------------------------------------

_DECL_WRAPPERS = {
    "function_declarator",
    "pointer_declarator",
    "reference_declarator",
    "parenthesized_declarator",
    "attributed_declarator",
}
_DECL_SKIP = {"parameter_list", "type_qualifier", "attribute_specifier", "ms_call_modifier", "ms_pointer_modifier"}


def _c_function(node: Node) -> Hit | None:
    d = node.child_by_field_name("declarator")
    while d is not None and d.type in _DECL_WRAPPERS:
        nxt = d.child_by_field_name("declarator")
        if nxt is None:
            nxt = next((c for c in d.named_children if c.type not in _DECL_SKIP), None)
        d = nxt
    if d is None:
        return None
    if d.type == "qualified_identifier":  # int Calc::apply(...) / N::M::f(...)
        scope: list[str] = []
        q = d
        while q is not None and q.type == "qualified_identifier":
            s = q.child_by_field_name("scope")
            if s is not None:
                if s.type == "template_type":
                    s = s.child_by_field_name("name") or s
                scope.append(_txt(s))
            q = q.child_by_field_name("name")
        if q is None:
            return None
        if q.type == "template_function":
            q = q.child_by_field_name("name") or q
        return Hit("function", _txt(q), (d,), tuple(scope))
    if d.type == "template_function":
        d = d.child_by_field_name("name") or d
    if d.type in ("identifier", "field_identifier", "destructor_name", "operator_name"):
        return Hit("function", _txt(d), (d,))
    return None


# C# -------------------------------------------------------------------------------


def _cs_destructor(node: Node) -> Hit | None:
    n = node.child_by_field_name("name")
    return Hit("function", "~" + _txt(n), (n,)) if n is not None else None


def _cs_operator(node: Node) -> Hit | None:
    op = node.child_by_field_name("operator")
    return Hit("function", "operator" + _txt(op), ()) if op is not None else None


# JavaScript / TypeScript ----------------------------------------------------------

_JS_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
_JS_MEMBER_NAMES = {"property_identifier", "private_property_identifier", "identifier", "string"}


def _js_declarator(node: Node) -> Hit | None:
    """``const f = () => ...`` is a function; ``const api = {...}`` / ``= class {}`` a scope."""
    name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
    if name is None or value is None or name.type != "identifier":
        return None
    if value.type in _JS_FUNCTION_VALUES:
        return Hit("function", _txt(name), (name,))
    if value.type == "class":
        return Hit("class", _txt(name), (name,))
    if value.type == "object":
        return Hit("container", _txt(name), (name,), classlike=True)
    return None


def _js_member(name_field: str) -> Resolver:
    """Class fields / object pairs whose value is a function."""

    def resolve(node: Node) -> Hit | None:
        n, v = node.child_by_field_name(name_field), node.child_by_field_name("value")
        if n is None or v is None or v.type not in _JS_FUNCTION_VALUES or n.type not in _JS_MEMBER_NAMES:
            return None
        name = _name_text(n)
        return Hit("function", name, (n,)) if name else None

    return resolve


# Rust -----------------------------------------------------------------------------


def _rust_type_name(t: Node | None) -> str | None:
    while t is not None:
        if t.type in ("type_identifier", "primitive_type"):
            return _txt(t)
        if t.type in ("generic_type", "reference_type"):
            t = t.child_by_field_name("type")
        elif t.type == "scoped_type_identifier":
            t = t.child_by_field_name("name")
        else:
            return None
    return None


def _rust_impl(node: Node) -> Hit | None:
    # `impl Calc {}` and `impl Trait for Calc {}` both scope their items under `Calc`
    return Hit("container", _rust_type_name(node.child_by_field_name("type")), (), classlike=True)


# Ruby -----------------------------------------------------------------------------


def _ruby_singleton_method(node: Node) -> Hit | None:
    n = node.child_by_field_name("name")
    return Hit("function", "self." + _txt(n), (n,)) if n is not None else None


# ----------------------------------------------------------------------------- specs

_JS_COMMON = dict(
    functions=frozenset({"function_declaration", "generator_function_declaration", "method_definition"}),
    identifiers=frozenset(
        {
            "identifier",
            "property_identifier",
            "private_property_identifier",
            "shorthand_property_identifier",
            "shorthand_property_identifier_pattern",
            "type_identifier",
        }
    ),
    prefix=frozenset({"decorator"}),
    wrappers=frozenset({"lexical_declaration", "variable_declaration", "export_statement"}),
)
_JS_CUSTOM: dict[str, Resolver] = {
    "variable_declarator": _js_declarator,
    "field_definition": _js_member("property"),
    "public_field_definition": _js_member("name"),
    "pair": _js_member("key"),
}

_C_IDENTIFIERS = frozenset({"identifier", "field_identifier", "type_identifier"})

SPECS: dict[str, LangSpec] = {
    spec.name: spec
    for spec in (
        LangSpec(
            name="go",
            extra="go",
            module="tree_sitter_go",
            suffixes=(".go",),
            classes=frozenset({"type_spec", "type_alias"}),
            functions=frozenset({"function_declaration"}),
            identifiers=frozenset({"identifier", "field_identifier", "type_identifier", "package_identifier"}),
            wrappers=frozenset({"type_declaration"}),
            custom={"method_declaration": _go_method},
        ),
        LangSpec(
            name="java",
            extra="java",
            module="tree_sitter_java",
            suffixes=(".java",),
            classes=frozenset(
                {
                    "class_declaration",
                    "interface_declaration",
                    "enum_declaration",
                    "record_declaration",
                    "annotation_type_declaration",
                }
            ),
            functions=frozenset({"method_declaration", "constructor_declaration", "compact_constructor_declaration"}),
            comments=frozenset({"line_comment", "block_comment"}),
            identifiers=frozenset({"identifier", "type_identifier"}),
        ),
        LangSpec(
            name="kotlin",
            extra="kotlin",
            module="tree_sitter_kotlin",
            suffixes=(".kt", ".kts"),
            classes=frozenset({"class_declaration", "object_declaration"}),
            functions=frozenset({"function_declaration"}),
            comments=frozenset({"line_comment", "block_comment", "comment", "multiline_comment"}),
            identifiers=frozenset({"identifier", "simple_identifier", "type_identifier"}),
            custom={
                "companion_object": lambda n: _named("name", "class")(n) or Hit("class", "Companion"),
                "secondary_constructor": _fixed("function", "constructor"),
            },
        ),
        LangSpec(
            name="javascript",
            extra="javascript",
            module="tree_sitter_javascript",
            suffixes=(".js", ".mjs", ".cjs", ".jsx"),
            classes=frozenset({"class_declaration"}),
            custom=_JS_CUSTOM,
            **_JS_COMMON,
        ),
        LangSpec(
            name="typescript",
            extra="typescript",
            module="tree_sitter_typescript",
            func="language_typescript",
            suffix_funcs=((".tsx", "language_tsx"),),
            suffixes=(".ts", ".mts", ".cts", ".tsx"),
            classes=frozenset(
                {
                    "class_declaration",
                    "abstract_class_declaration",
                    "interface_declaration",
                    "enum_declaration",
                    "type_alias_declaration",
                }
            ),
            containers=frozenset({"internal_module", "module"}),
            custom={
                **_JS_CUSTOM,
                "method_signature": _named("name", "function"),
                "abstract_method_signature": _named("name", "function"),
            },
            **_JS_COMMON,
        ),
        LangSpec(
            name="rust",
            extra="rust",
            module="tree_sitter_rust",
            suffixes=(".rs",),
            classes=frozenset({"struct_item", "enum_item", "trait_item", "union_item"}),
            functions=frozenset({"function_item", "function_signature_item", "macro_definition"}),
            containers=frozenset({"mod_item"}),
            comments=frozenset({"line_comment", "block_comment"}),
            identifiers=frozenset({"identifier", "type_identifier", "field_identifier", "shorthand_field_identifier"}),
            prefix=frozenset({"attribute_item"}),
            custom={"impl_item": _rust_impl},
        ),
        LangSpec(
            name="csharp",
            extra="csharp",
            module="tree_sitter_c_sharp",
            suffixes=(".cs",),
            classes=frozenset(
                {
                    "class_declaration",
                    "interface_declaration",
                    "struct_declaration",
                    "enum_declaration",
                    "record_declaration",
                    "record_struct_declaration",
                }
            ),
            functions=frozenset(
                {"method_declaration", "constructor_declaration", "property_declaration", "local_function_statement"}
            ),
            custom={"destructor_declaration": _cs_destructor, "operator_declaration": _cs_operator},
        ),
        LangSpec(
            name="cpp",
            extra="cpp",
            module="tree_sitter_cpp",
            suffixes=(".cc", ".cpp", ".cxx", ".c++", ".hh", ".hpp", ".hxx", ".h++", ".h"),
            containers=frozenset({"namespace_definition"}),
            identifiers=_C_IDENTIFIERS | {"namespace_identifier"},
            wrappers=frozenset({"template_declaration"}),
            custom={
                "function_definition": _c_function,
                "class_specifier": _with_body(_named("name", "class")),
                "struct_specifier": _with_body(_named("name", "class")),
                "union_specifier": _with_body(_named("name", "class")),
                "enum_specifier": _with_body(_named("name", "class")),
            },
        ),
        LangSpec(
            name="c",
            extra="c",
            module="tree_sitter_c",
            suffixes=(".c", ".h"),
            identifiers=_C_IDENTIFIERS,
            custom={
                "function_definition": _c_function,
                "struct_specifier": _with_body(_named("name", "class")),
                "union_specifier": _with_body(_named("name", "class")),
                "enum_specifier": _with_body(_named("name", "class")),
            },
        ),
        LangSpec(
            name="ruby",
            extra="ruby",
            module="tree_sitter_ruby",
            suffixes=(".rb",),
            classes=frozenset({"class", "module"}),
            functions=frozenset({"method"}),
            identifiers=frozenset({"identifier", "constant", "instance_variable", "class_variable"}),
            custom={
                "singleton_method": _ruby_singleton_method,
                "singleton_class": _fixed("container", None, classlike=True),
            },
        ),
        LangSpec(
            name="php",
            extra="php",
            module="tree_sitter_php",
            func="language_php",
            suffixes=(".php",),
            classes=frozenset(
                {"class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"}
            ),
            functions=frozenset({"method_declaration", "function_definition"}),
            identifiers=frozenset({"name"}),
        ),
    )
}


# --------------------------------------------------------------------------- hashing


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _key(n: Node) -> tuple[int, int, str]:
    return (n.start_byte, n.end_byte, n.type)


def _serialize(nodes: Iterable[Node], spec: LangSpec, erase: set, *, anonymize: bool) -> str:
    """S-expression of node types and leaf text (iterative: deep trees must not recurse)."""
    parts: list[str] = []
    stack: list[Any] = list(reversed(list(nodes)))
    while stack:
        n = stack.pop()
        if isinstance(n, str):
            parts.append(n)
            continue
        t = n.type
        if t in spec.comments or n.is_missing:
            continue
        if _key(n) in erase:
            parts.append("_")
            continue
        if n.child_count == 0:
            if not n.is_named:
                parts.append(t)  # anonymous tokens: operators, keywords, punctuation
            elif anonymize and t in spec.identifiers:
                parts.append(f"{t}:_")
            else:
                parts.append(f"{t}:{_txt(n)}")
            continue
        parts.append("(" + t)
        stack.append(")")
        stack.extend(reversed(n.children))
    return " ".join(parts)


def _identifiers(node: Node, spec: LangSpec) -> tuple[str, ...]:
    seen: set[str] = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in spec.comments:
            continue
        if n.child_count == 0:
            if n.is_named and n.type in spec.identifiers:
                seen.add(_txt(n))
        else:
            stack.extend(n.children)
    return tuple(sorted(seen))


# ------------------------------------------------------------------------- extractor


class TreeSitterExtractor:
    def __init__(self, spec: LangSpec):
        self.spec = spec
        self.language = spec.name
        self.suffixes = spec.suffixes
        try:
            import tree_sitter as ts

            grammar = importlib.import_module(spec.module)
        except ImportError as e:
            raise ExtractorError(
                f"language {spec.name!r} needs the tree-sitter grammar: "
                f"pip install \"diting[{spec.extra}] @ git+https://github.com/Meepoljd/diting@main\" "
                f"(or: pip install tree-sitter {spec.module.replace('_', '-')}); missing module: {e.name}"
            ) from e
        self._ts = ts
        self._grammar = grammar
        self._parsers: dict[str, Any] = {}

    def _parser(self, rel: str) -> Any:
        suffix = PurePosixPath(rel).suffix
        func = dict(self.spec.suffix_funcs).get(suffix, self.spec.func)
        parser = self._parsers.get(func)
        if parser is None:
            try:
                lang = self._ts.Language(getattr(self._grammar, func)())
                parser = self._ts.Parser(lang)
            except (ValueError, TypeError) as e:  # ABI mismatch between tree-sitter and the grammar
                raise ExtractorError(f"cannot load the {self.spec.name} grammar: {e}") from e
            self._parsers[func] = parser
        return parser

    def extract(self, path: Path, rel: str) -> list[Symbol]:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return []
        return self.extract_text(text, rel)

    # ------------------------------------------------------------------ walking
    def _classify(self, node: Node) -> Hit | None:
        spec, t = self.spec, node.type
        if t in spec.custom:
            return spec.custom[t](node)
        if t in spec.classes:
            return _named("name", "class")(node)
        if t in spec.functions:
            return _named("name", "function")(node)
        if t in spec.containers:
            return _named("name", "container", classlike=t in spec.classlike_containers)(node) or Hit(
                "container", None
            )
        return None

    def _span(self, node: Node) -> tuple[Node, list[Node]]:
        """Widen to single-declaration wrappers (``export``, ``const``, ``template<>``) and
        collect leading decorators / attributes that belong to the symbol."""
        span = node
        while True:
            parent = span.parent
            if parent is None or parent.type not in self.spec.wrappers:
                break
            if sum(1 for c in parent.named_children if c.type == span.type) != 1:
                break
            span = parent
        prefix: list[Node] = []
        sib = span.prev_named_sibling
        while sib is not None and (sib.type in self.spec.prefix or sib.type in self.spec.comments):
            if sib.type in self.spec.prefix:
                prefix.append(sib)
            sib = sib.prev_named_sibling
        prefix.reverse()
        return span, prefix

    def extract_text(self, text: str, rel: str) -> list[Symbol]:
        spec = self.spec
        tree = self._parser(rel).parse(text.encode("utf-8"))
        out: list[Symbol] = []
        taken: dict[str, int] = {}

        # (node, qualname prefix, functions here are methods)
        stack: list[tuple[Node, str, bool]] = [(c, "", False) for c in reversed(tree.root_node.named_children)]
        while stack:
            node, prefix, classlike = stack.pop()
            if node.type in spec.comments:
                continue
            hit = self._classify(node)
            child_prefix, child_classlike = prefix, classlike
            if hit is not None:
                segments = [*hit.scope, *([hit.name] if hit.name else [])]
                qual = ".".join([prefix, *segments] if prefix else segments)
                if hit.role == "container":
                    child_prefix, child_classlike = qual, hit.classlike
                else:
                    # overloads / getter+setter pairs share a name: later ones get ~2, ~3 ...
                    n = taken.get(qual, 0) + 1
                    taken[qual] = n
                    ref_qual = qual if n == 1 else f"{qual}~{n}"
                    if hit.role == "class":
                        kind = "class"
                    else:
                        kind = "method" if (classlike or hit.scope) else "function"
                    span, pre = self._span(node)
                    erase = {_key(e) for e in hit.erase}
                    hashed = [*pre, node]
                    out.append(
                        Symbol(
                            path=rel,
                            qualname=ref_qual,
                            kind=kind,
                            start_line=min(n.start_point[0] for n in [span, *pre]) + 1,
                            end_line=span.end_point[0] + 1,
                            fingerprint=_hash(_serialize(hashed, spec, erase, anonymize=False)),
                            shape=_hash(_serialize(hashed, spec, erase, anonymize=True)),
                            identifiers=_identifiers(node, spec),
                        )
                    )
                    child_prefix, child_classlike = ref_qual, hit.role == "class"
            for c in reversed(node.named_children):
                stack.append((c, child_prefix, child_classlike))
        return out
