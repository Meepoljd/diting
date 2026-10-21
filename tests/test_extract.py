from pathlib import Path

from diting.extract.python import PythonExtractor

from .conftest import write


def _syms(path: Path, rel: str = "m.py"):
    return {s.qualname: s for s in PythonExtractor().extract(path, rel)}


def test_extracts_nested_symbols(tmp_path):
    f = tmp_path / "m.py"
    write(f, """
        class A:
            def run(self):
                def inner():
                    return 1
                return inner()

        async def top(x):
            return x
    """)
    syms = _syms(f)
    assert set(syms) == {"A", "A.run", "A.run.inner", "top"}
    assert syms["A"].kind == "class"
    assert syms["A.run"].kind == "method"
    assert syms["top"].kind == "function"
    assert syms["A.run"].start_line == 2


def test_fingerprint_ignores_formatting_comments_docstrings(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    write(a, """
        def f(x):
            return round(x * 2, 2)
    """)
    write(b, """


        def f( x ):
            '''Docstring added.'''
            # a comment
            return round(
                x * 2,
                2,
            )
    """)
    assert _syms(a)["f"].fingerprint == _syms(b)["f"].fingerprint


def test_shape_survives_rename_but_fingerprint_does_not(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    write(a, """
        def apply_discount(amount, rate):
            return amount * rate
    """)
    write(b, """
        def compute_discount(total, ratio):
            return total * ratio
    """)
    sa, sb = _syms(a)["apply_discount"], _syms(b)["compute_discount"]
    assert sa.fingerprint != sb.fingerprint
    assert sa.shape == sb.shape


def test_logic_change_changes_both_hashes(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    write(a, "def f(x):\n    return x >= 200\n")
    write(b, "def f(x):\n    return x > 200\n")
    assert _syms(a)["f"].fingerprint != _syms(b)["f"].fingerprint
    assert _syms(a)["f"].shape != _syms(b)["f"].shape


def test_syntax_error_yields_no_symbols(tmp_path):
    f = tmp_path / "bad.py"
    write(f, "def broken(:\n")
    assert PythonExtractor().extract(f, "bad.py") == []
