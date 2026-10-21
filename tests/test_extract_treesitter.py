"""tree-sitter extractors: symbol discovery and hash invariants for every supported language.

Each grammar is optional; tests for a language are skipped when its grammar is not installed.
"""

from __future__ import annotations

import importlib.util

import pytest

from diting.extract import ExtractorError, get_extractors, language_suffixes, supported_languages
from diting.extract.treesitter import SPECS


def _extractor(lang: str):
    spec = SPECS[lang]
    if importlib.util.find_spec("tree_sitter") is None or importlib.util.find_spec(spec.module) is None:
        pytest.skip(f"grammar for {lang} not installed")
    return get_extractors([lang])[0]


# --------------------------------------------------------------------------- discovery

DISCOVERY = {
    "go": (
        "a.go",
        "package p\n\ntype Calc struct{ x int }\n\nfunc (c *Calc) Apply(a int) int {\n\treturn a * 2\n}\n\n"
        "func Top(x int) int { return x }\n\ntype I interface{ M() }\n",
        {"Calc": "class", "Calc.Apply": "method", "Top": "function", "I": "class"},
    ),
    "java": (
        "A.java",
        "class Calc {\n  int apply(int a){ return a*2; }\n  Calc(){}\n  int apply(String s){ return 1; }\n}\n"
        "interface I { void m(); }\nenum E {A}\nrecord R(int x){}\n",
        {"Calc": "class", "Calc.apply": "method", "Calc.Calc": "method", "Calc.apply~2": "method", "I.m": "method",
         "E": "class", "R": "class"},
    ),
    "kotlin": (
        "a.kt",
        "class Calc {\n    fun apply(a: Int): Int {\n        return a * 2\n    }\n    companion object {\n"
        "        fun make() = Calc()\n    }\n}\n\nobject O {\n    fun f() = 1\n}\n\nfun top(x: Int) = x\n",
        {"Calc": "class", "Calc.apply": "method", "Calc.Companion.make": "method", "O.f": "method",
         "top": "function"},
    ),
    "javascript": (
        "a.js",
        "class Calc { apply(a){ return a*2 } }\nfunction f(x){return x}\nexport const g = (y) => y+1;\n"
        "const api = { h(){ return 1 }, k: () => 2 };\n",
        {"Calc": "class", "Calc.apply": "method", "f": "function", "g": "function", "api.h": "method",
         "api.k": "method"},
    ),
    "typescript": (
        "a.ts",
        "export class Calc { apply(a: number): number { return a*2 } }\nfunction f(x: number){return x}\n"
        "const g = (y: number) => y+1;\ninterface I { m(): void }\nenum E {A}\ntype T = number;\n"
        "namespace NS { export function inner() {} }\n",
        {"Calc": "class", "Calc.apply": "method", "f": "function", "g": "function", "I.m": "method", "E": "class",
         "T": "class", "NS.inner": "function"},
    ),
    "rust": (
        "a.rs",
        "struct Calc { x: i32 }\nimpl Calc { fn apply(&self, a: i32) -> i32 { a*2 } }\n"
        "fn top(x: i32) -> i32 { x }\ntrait T { fn m(&self) {} }\nimpl T for Calc { fn m(&self) {} }\n"
        "mod inner { pub fn f() {} }\n",
        {"Calc": "class", "Calc.apply": "method", "top": "function", "T.m": "method", "Calc.m": "method",
         "inner.f": "function"},
    ),
    "csharp": (
        "a.cs",
        "namespace N {\n class Calc {\n  int Apply(int a){ return a*2; }\n  Calc(){}\n  ~Calc(){}\n }\n"
        " interface I {}\n struct S {}\n}\n",
        {"Calc": "class", "Calc.Apply": "method", "Calc.Calc": "method", "Calc.~Calc": "method", "I": "class",
         "S": "class"},
    ),
    "c": (
        "a.c",
        "struct Calc { int x; };\nint apply(int a){ return a*2; }\nstatic int *g(void){return 0;}\n",
        {"Calc": "class", "apply": "function", "g": "function"},
    ),
    "cpp": (
        "a.cpp",
        "class Calc { public: int apply(int a){ return a*2; } };\nint Calc::other(int a){ return a; }\n"
        "namespace n { int f(){return 1;} }\ntemplate<typename T> T id(T x){ return x; }\n",
        {"Calc": "class", "Calc.apply": "method", "Calc.other": "method", "n.f": "function", "id": "function"},
    ),
    "ruby": (
        "a.rb",
        "class Calc\n  def apply(a)\n    a*2\n  end\n  def self.make; end\nend\nmodule M\n  def f; end\nend\n"
        "def top(x) x end\n",
        {"Calc": "class", "Calc.apply": "method", "Calc.self.make": "method", "M.f": "method", "top": "function"},
    ),
    "php": (
        "a.php",
        "<?php\nclass Calc { public function apply($a){ return $a*2; } }\nfunction top($x){ return $x; }\n"
        "interface I {}\ntrait T {}\n",
        {"Calc": "class", "Calc.apply": "method", "top": "function", "I": "class", "T": "class"},
    ),
}


@pytest.mark.parametrize("lang", sorted(DISCOVERY))
def test_discovers_symbols(lang):
    rel, src, expected = DISCOVERY[lang]
    syms = {s.qualname: s for s in _extractor(lang).extract_text(src, rel)}
    for qual, kind in expected.items():
        assert qual in syms, f"{lang}: {qual} not in {sorted(syms)}"
        assert syms[qual].kind == kind, f"{lang}: {qual} is {syms[qual].kind}, expected {kind}"
        assert syms[qual].path == rel


def test_line_span_covers_wrappers_and_decorators():
    ex = _extractor("typescript")
    src = "export class A {\n  @log()\n  @memo\n  run(x: number) {\n    return x\n  }\n}\n"
    run = next(s for s in ex.extract_text(src, "a.ts") if s.qualname == "A.run")
    assert (run.start_line, run.end_line) == (2, 6)
    ex = _extractor("javascript")
    g = next(s for s in ex.extract_text("export const g = (y) =>\n  y + 1;\n", "a.js") if s.qualname == "g")
    assert (g.start_line, g.end_line) == (1, 2)


def test_tsx_uses_tsx_grammar():
    ex = _extractor("typescript")
    syms = {s.qualname for s in ex.extract_text("export function View() { return <div>hi</div> }\n", "v.tsx")}
    assert "View" in syms


# ------------------------------------------------------------------------ hash invariants

# Template placeholders: {N} symbol name, {V} an internal identifier, {OP} a comparison operator,
# {C} a comment line. The symbol under test is always named by {N}.
TEMPLATES = {
    "go": ("a.go", "package p\n\n{C}\nfunc {N}(amount int) int {\n\t{V} := amount * 2\n\tif {V} {OP} 200 {\n"
                   "\t\treturn {V}\n\t}\n\treturn amount\n}\n", "// note", "{N}"),
    "java": ("A.java", "class Calc {\n  {C}\n  int {N}(int amount) {\n    int {V} = amount * 2;\n"
                       "    if ({V} {OP} 200) { return {V}; }\n    return amount;\n  }\n}\n", "// note", "Calc.{N}"),
    "kotlin": ("a.kt", "class Calc {\n    {C}\n    fun {N}(amount: Int): Int {\n        val {V} = amount * 2\n"
                       "        if ({V} {OP} 200) {\n            return {V}\n        }\n        return amount\n"
                       "    }\n}\n", "// note", "Calc.{N}"),
    "javascript": ("a.js", "{C}\nexport function {N}(amount) {\n  const {V} = amount * 2;\n"
                           "  if ({V} {OP} 200) return {V};\n  return amount;\n}\n", "// note", "{N}"),
    "typescript": ("a.ts", "export class Calc {\n  {C}\n  {N}(amount: number): number {\n    const {V} = amount * 2;\n"
                           "    if ({V} {OP} 200) return {V};\n    return amount;\n  }\n}\n", "// note", "Calc.{N}"),
    "rust": ("a.rs", "struct Calc;\nimpl Calc {\n    {C}\n    fn {N}(&self, amount: i32) -> i32 {\n"
                     "        let {V} = amount * 2;\n        if {V} {OP} 200 { return {V}; }\n        amount\n"
                     "    }\n}\n", "// note", "Calc.{N}"),
    "csharp": ("a.cs", "class Calc {\n  {C}\n  int {N}(int amount) {\n    var {V} = amount * 2;\n"
                       "    if ({V} {OP} 200) return {V};\n    return amount;\n  }\n}\n", "// note", "Calc.{N}"),
    "c": ("a.c", "{C}\nint {N}(int amount) {\n  int {V} = amount * 2;\n  if ({V} {OP} 200) return {V};\n"
                 "  return amount;\n}\n", "/* note */", "{N}"),
    "cpp": ("a.cpp", "class Calc { public: int {N}(int amount); };\n{C}\nint Calc::{N}(int amount) {\n"
                     "  int {V} = amount * 2;\n  if ({V} {OP} 200) return {V};\n  return amount;\n}\n",
            "// note", "Calc.{N}"),
    "ruby": ("a.rb", "class Calc\n  {C}\n  def {N}(amount)\n    {V} = amount * 2\n    return {V} if {V} {OP} 200\n"
                     "    amount\n  end\nend\n", "# note", "Calc.{N}"),
    "php": ("a.php", "<?php\nclass Calc {\n  {C}\n  public function {N}($amount) {\n    ${V} = $amount * 2;\n"
                     "    if (${V} {OP} 200) { return ${V}; }\n    return $amount;\n  }\n}\n", "// note", "Calc.{N}"),
}


def _sym(lang: str, *, name="apply", var="doubled", op=">=", comment=""):
    rel, tpl, _, qual = TEMPLATES[lang]
    src = tpl.replace("{N}", name).replace("{V}", var).replace("{OP}", op).replace("{C}", comment)
    syms = {s.qualname: s for s in _extractor(lang).extract_text(src, rel)}
    q = qual.replace("{N}", name)
    assert q in syms, f"{lang}: {q} not in {sorted(syms)}"
    return syms[q]


@pytest.mark.parametrize("lang", sorted(TEMPLATES))
def test_hash_invariants(lang):
    base = _sym(lang)
    comment = TEMPLATES[lang][2]

    reformatted_comment = _sym(lang, comment=comment)
    assert (reformatted_comment.fingerprint, reformatted_comment.shape) == (base.fingerprint, base.shape), (
        "comments must not change any hash"
    )

    renamed = _sym(lang, name="compute")
    assert (renamed.fingerprint, renamed.shape) == (base.fingerprint, base.shape), (
        "renaming the symbol itself must keep both hashes (so sync can follow it)"
    )

    inner = _sym(lang, var="twice")
    assert inner.fingerprint != base.fingerprint, "internal identifier rename changes the fingerprint"
    assert inner.shape == base.shape, "internal identifier rename keeps the shape"

    logic = _sym(lang, op=">")
    assert logic.fingerprint != base.fingerprint and logic.shape != base.shape, "logic change changes both"


@pytest.mark.parametrize("lang", sorted(TEMPLATES))
def test_whitespace_does_not_matter(lang):
    rel, tpl, _, qual = TEMPLATES[lang]
    src = tpl.replace("{N}", "apply").replace("{V}", "d").replace("{OP}", ">=").replace("{C}", "")
    ex = _extractor(lang)
    a = {s.qualname: s for s in ex.extract_text(src, rel)}
    b = {s.qualname: s for s in ex.extract_text("\n\n" + src.replace("\n", "\n\n"), rel)}
    q = qual.replace("{N}", "apply")
    assert a[q].fingerprint == b[q].fingerprint


# ---------------------------------------------------------------------------- registry


def test_registry_lists_all_languages():
    langs = supported_languages()
    assert langs[0] == "python"
    assert set(SPECS) <= set(langs)
    sfx = language_suffixes()
    assert sfx["python"] == (".py",) and ".go" in sfx["go"] and ".tsx" in sfx["typescript"]


def test_unknown_language_is_extractor_error():
    with pytest.raises(ExtractorError, match="unsupported language: cobol"):
        get_extractors(["cobol"])


def test_missing_grammar_gives_install_hint(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "tree_sitter_go", None)  # makes the import fail
    with pytest.raises(ExtractorError, match=r"diting\[go\]"):
        get_extractors(["go"])


def test_syntax_errors_do_not_crash():
    ex = _extractor("java")
    syms = ex.extract_text("class A { void ok() { return; }\n void broken( { }\n", "A.java")
    assert any(s.qualname.startswith("A") for s in syms)
