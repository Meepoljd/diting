"""Inverted index: a code change (unified diff) -> touched symbols -> concepts -> sibling sites.

The core question ``impact`` answers is: *"I changed this rule here — where else does the same
rule live that I did not touch?"*

Line mapping:

- added lines are mapped onto the **new** version of each file;
- removed lines are mapped onto the **old** version when it is available (``--base`` /
  ``--staged`` / default ``HEAD`` modes read it from git), otherwise onto the gap they leave
  in the new version (``--diff FILE`` mode);
- a symbol is *touched* if any changed line falls in its range (so a change inside
  ``DiscountCalc.apply`` touches both ``DiscountCalc.apply`` and ``DiscountCalc``).

A touched anchor whose fingerprint is unchanged (old vs new, or anchored vs current) is only a
*cosmetic* change (comments, formatting) and never triggers sibling review.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .extract import Symbol, get_extractors
from .store import Repo

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class GitError(RuntimeError):
    pass


# ------------------------------------------------------------------ diff model


@dataclass
class FileChange:
    old_path: str | None  # None for added files
    new_path: str | None  # None for deleted files
    added: set[int] = field(default_factory=set)  # new-side line numbers
    removed: set[int] = field(default_factory=set)  # old-side line numbers
    gaps: set[int] = field(default_factory=set)  # new-side lines adjacent to a deletion

    @property
    def status(self) -> str:
        if self.old_path is None:
            return "added"
        if self.new_path is None:
            return "deleted"
        if self.old_path != self.new_path:
            return "renamed"
        return "modified"

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


def _unquote(p: str) -> str:
    p = p.strip()
    if len(p) >= 2 and p[0] == p[-1] == '"':
        p = bytes(p[1:-1], "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
    return p


def _side(p: str, prefix: str) -> str | None:
    p = _unquote(p.split("\t", 1)[0])
    if p == "/dev/null":
        return None
    return p[len(prefix):] if p.startswith(prefix) else p


def parse_unified_diff(text: str) -> list[FileChange]:
    files: list[FileChange] = []
    cur: FileChange | None = None
    old_ln = new_ln = 0
    old_left = new_left = 0  # lines remaining in the current hunk
    for raw in text.splitlines():
        if old_left > 0 or new_left > 0:
            if raw.startswith("\\"):
                continue
            tag = raw[:1]
            if tag == "+":
                cur.added.add(new_ln)
                new_ln += 1
                new_left -= 1
            elif tag == "-":
                cur.removed.add(old_ln)
                cur.gaps.update({max(new_ln - 1, 1), new_ln})
                old_ln += 1
                old_left -= 1
            else:  # context (" " or an empty line from stripped whitespace)
                old_ln += 1
                new_ln += 1
                old_left -= 1
                new_left -= 1
            continue
        if raw.startswith("diff --git "):
            m = re.match(r'^diff --git ("?a/.+?"?) ("?b/.+"?)$', raw)
            cur = FileChange(old_path=_side(m.group(1), "a/") if m else None,
                             new_path=_side(m.group(2), "b/") if m else None)
            cur._header = True  # type: ignore[attr-defined]
            files.append(cur)
            continue
        if raw.startswith("--- "):
            if cur is None or not getattr(cur, "_header", False) or cur.added or cur.removed:
                cur = FileChange(old_path=None, new_path=None)
                files.append(cur)
            cur._header = False  # type: ignore[attr-defined]
            cur.old_path = _side(raw[4:], "a/")
            continue
        if cur is None:
            continue
        if raw.startswith("+++ "):
            cur.new_path = _side(raw[4:], "b/")
        elif raw.startswith("new file mode"):
            cur.old_path = None
        elif raw.startswith("deleted file mode"):
            cur.new_path = None
        elif raw.startswith("rename from "):
            cur.old_path = _unquote(raw[len("rename from "):])
        elif raw.startswith("rename to "):
            cur.new_path = _unquote(raw[len("rename to "):])
        else:
            m = _HUNK.match(raw)
            if m:
                old_ln, new_ln = int(m.group(1)), int(m.group(3))
                old_left = int(m.group(2)) if m.group(2) is not None else 1
                new_left = int(m.group(4)) if m.group(4) is not None else 1
                if new_left == 0:  # pure deletion: start points at the line *before* the gap
                    new_ln += 1
    return [f for f in files if f.old_path or f.new_path]


# ------------------------------------------------------------------ git access


def _git(root: Path, *args: str, check: bool = True) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError as e:  # pragma: no cover - git missing
        raise GitError("git executable not found") from e
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout if r.returncode == 0 else ""


def git_toplevel(root: Path) -> Path | None:
    out = _git(root, "rev-parse", "--show-toplevel", check=False).strip()
    return Path(out).resolve() if out else None


def resolve_base(root: Path, base: str) -> str:
    """Use the merge-base so that changes landed on the base branch meanwhile are ignored."""
    _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    mb = _git(root, "merge-base", base, "HEAD", check=False).strip()
    return mb or base


@dataclass
class DiffSource:
    label: str
    text: str
    old_text: Callable[[str], str | None] | None = None
    new_text: Callable[[str], str | None] | None = None


def _show(root: Path, spec: str) -> str | None:
    r = subprocess.run(["git", "-C", str(root), "show", spec], capture_output=True)
    if r.returncode != 0:
        return None
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _worktree(root: Path) -> Callable[[str], str | None]:
    def read(rel: str) -> str | None:
        p = root / rel
        try:
            return p.read_text(encoding="utf-8") if p.is_file() else None
        except (UnicodeDecodeError, OSError):
            return None

    return read


def git_diff_source(root: Path, *, base: str | None = None, staged: bool = False) -> DiffSource:
    """Diff of the diting root (paths relative to it) against ``base`` / the index / ``HEAD``.

    Without ``staged``, the working tree is compared and untracked files count as added.
    """
    if git_toplevel(root) is None:
        raise GitError(f"{root} is not inside a git work tree (use --diff FILE)")
    common = ["--no-color", "--no-ext-diff", "-M", "--relative"]
    if staged:
        has_head = bool(_git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False).strip())
        return DiffSource(
            label="git diff --cached",
            text=_git(root, "-c", "core.quotepath=false", "diff", "--cached", *common),
            old_text=(lambda rel: _show(root, f"HEAD:./{rel}")) if has_head else (lambda rel: None),
            new_text=lambda rel: _show(root, f":./{rel}"),
        )
    ref = resolve_base(root, base) if base else "HEAD"
    text = _git(root, "-c", "core.quotepath=false", "diff", ref, *common)
    reader = _worktree(root)
    untracked = _git(root, "-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard",
                     "--", ".").splitlines()
    for rel in untracked:
        body = reader(rel)
        if not body:
            continue
        lines = body.splitlines()
        text += (f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n--- /dev/null\n+++ b/{rel}\n"
                 f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{ln}\n" for ln in lines))
    label = f"git diff {base or 'HEAD'}"
    if base and ref != base:
        label += f" (merge-base {ref[:12]})"
    if untracked:
        label += f" + {len(untracked)} untracked"
    return DiffSource(label=label, text=text, old_text=lambda rel: _show(root, f"{ref}:./{rel}"), new_text=reader)


def file_diff_source(root: Path, diff_text: str, label: str) -> DiffSource:
    """A diff supplied by the user; strip a leading directory if paths are git-root relative."""
    changes_text = diff_text
    top = git_toplevel(root)
    if top is not None and top != root.resolve():
        prefix = root.resolve().relative_to(top).as_posix().rstrip("/") + "/"
        changes_text = re.sub(rf"(^diff --git a/|^--- a/|^\+\+\+ b/| b/|^rename from |^rename to ){re.escape(prefix)}",
                              r"\1", diff_text, flags=re.M)
    return DiffSource(label=label, text=changes_text, old_text=None, new_text=_worktree(root))


# --------------------------------------------------------------------- mapping


def _symbols_for(repo: Repo, rel: str, text: str | None) -> list[Symbol]:
    if text is None:
        return []
    for ex in get_extractors(repo.config["languages"]):
        if Path(rel).suffix in ex.suffixes:
            return ex.extract_text(text, rel)
    return []


def _touched(symbols: list[Symbol], lines: set[int]) -> dict[str, Symbol]:
    out: dict[str, Symbol] = {}
    for s in symbols:
        if any(s.start_line <= ln <= s.end_line for ln in lines):
            out[s.ref] = s
    return out


def _innermost(symbols: list[Symbol], lines: set[int]) -> set[str]:
    out: set[str] = set()
    for ln in lines:
        best = None
        for s in symbols:
            if s.start_line <= ln <= s.end_line and (best is None or s.end_line - s.start_line < best.end_line - best.start_line):
                best = s
        if best is not None:
            out.add(best.ref)
    return out


@dataclass
class SymbolChange:
    ref: str
    old: Symbol | None
    new: Symbol | None

    @property
    def kind(self) -> str:
        if self.old is not None and self.new is not None:
            return "cosmetic" if self.old.fingerprint == self.new.fingerprint else "logic"
        if self.new is None:
            return "missing"
        return "touched"  # no old version known; compared against the anchor later


def _source_suffixes(repo: Repo) -> tuple[str, ...]:
    return tuple(sfx for ex in get_extractors(repo.config["languages"]) for sfx in ex.suffixes)


@dataclass
class ChangeSet:
    files: list[FileChange]
    touched: dict[str, SymbolChange] = field(default_factory=dict)
    inner: set[str] = field(default_factory=set)
    module_level: list[str] = field(default_factory=list)
    present: dict[str, set[str]] = field(default_factory=dict)

    def change_of(self, ref: str) -> str | None:
        """'logic' | 'cosmetic' | 'missing' | 'touched' | None for an anchored ref."""
        ch = self.touched.get(ref)
        if ch is not None:
            return ch.kind
        path, q = ref.split("#", 1)
        if path in self.present and q not in self.present[path]:
            return "missing"
        return None


def collect_changes(repo: Repo, src: DiffSource) -> ChangeSet:
    """Map a diff onto symbols of the old and new versions of every changed source file.

    ``ChangeSet.present`` maps every changed (old-side) path to the qualnames that still exist
    afterwards, so anchors whose symbol vanished are reported as ``missing`` even when the old
    file text is unknown (``--diff`` mode).
    """
    files = parse_unified_diff(src.text)
    suffixes = _source_suffixes(repo)
    cs = ChangeSet(files=files)
    touched, inner, module_level = cs.touched, cs.inner, cs.module_level
    new_read = src.new_text or _worktree(repo.root)
    for fc in files:
        if not fc.path.endswith(suffixes):
            continue
        new_syms = _symbols_for(repo, fc.new_path, new_read(fc.new_path)) if fc.new_path else []
        old_syms: list[Symbol] = []
        if fc.old_path and src.old_text is not None:
            old_syms = _symbols_for(repo, fc.old_path, src.old_text(fc.old_path))
        old_by_q = {s.qualname: s for s in old_syms}
        new_by_q = {s.qualname: s for s in new_syms}
        have_old = src.old_text is not None
        # without the old file, deletions can only be located by the gap they leave
        new_lines = set(fc.added) if have_old else fc.added | fc.gaps

        for s in _touched(new_syms, new_lines).values():
            touched[s.ref] = SymbolChange(s.ref, old_by_q.get(s.qualname) if have_old else None, s)
        for s in _touched(old_syms, fc.removed).values():
            ref = f"{fc.new_path}#{s.qualname}" if fc.new_path else s.ref
            touched.setdefault(ref, SymbolChange(ref, s, new_by_q.get(s.qualname)))
            if s.ref != ref:  # file renamed: anchors may still point at the old path
                touched.setdefault(s.ref, SymbolChange(s.ref, s, None))

        inner |= _innermost(new_syms, new_lines)
        for ref in _innermost(old_syms, fc.removed):
            q = ref.split("#", 1)[1]
            inner.add(f"{fc.new_path}#{q}" if fc.new_path and q in new_by_q else ref)

        if fc.old_path:
            cs.present[fc.old_path] = {s.qualname for s in new_syms} if fc.new_path == fc.old_path else set()

        ranges = [(s.start_line, s.end_line) for s in new_syms]
        if fc.new_path and any(not any(a <= ln <= b for a, b in ranges) for ln in fc.added):
            module_level.append(fc.path)
    return cs


def impact(repo: Repo, src: DiffSource) -> dict:
    concepts = repo.load_concepts()
    edges = repo.load_edges()
    cs = collect_changes(repo, src)
    touched = cs.touched

    anchored: set[str] = set()
    report_concepts = []
    for cid, ef in sorted(edges.items()):
        c = concepts.get(cid)
        if c is None:
            continue
        occs = []
        any_logic = False
        for occ in ef.occurrences:
            status = "untouched"
            details = []
            for a in occ.anchors:
                anchored.add(a.ref)
                kind = cs.change_of(a.ref)
                if kind is None:
                    continue
                if kind == "touched":  # no old version: compare with what was anchored
                    new = touched[a.ref].new
                    kind = "cosmetic" if a.fingerprint and new and new.fingerprint == a.fingerprint else "logic"
                details.append({"ref": a.ref, "change": kind})
            kinds = {d["change"] for d in details}
            if "missing" in kinds:
                status = "missing"
            elif "logic" in kinds:
                status = "changed"
            elif kinds:
                status = "cosmetic"
            if status in ("changed", "missing"):
                any_logic = True
            item: dict = {"id": occ.id, "status": status, "anchors": [a.ref for a in occ.anchors]}
            if details:
                item["changes"] = details
            if occ.variant:
                item["variant"] = occ.variant
            occs.append(item)
        if not any(o["status"] != "untouched" for o in occs):
            continue
        review = [o["id"] for o in occs if o["status"] in ("untouched", "cosmetic")] if any_logic else []
        report_concepts.append({
            "id": c.id,
            "statement": c.statement,
            "level": c.level,
            "lifecycle": c.lifecycle,
            "version": c.version,
            "changed": [o["id"] for o in occs if o["status"] in ("changed", "missing")],
            "review": review,
            "occurrences": occs,
        })

    unindexed = sorted(r for r in cs.inner if r not in anchored and touched.get(r) is not None
                       and touched[r].kind != "cosmetic")
    return {
        "source": src.label,
        "files": [{"path": f.path, "status": f.status, "added": len(f.added), "removed": len(f.removed)}
                  for f in cs.files],
        "changed_symbols": sorted(cs.inner),
        "concepts": report_concepts,
        "needs_review": sum(1 for c in report_concepts if c["review"]),
        "unindexed": unindexed,
        "module_level_changes": cs.module_level,
    }
