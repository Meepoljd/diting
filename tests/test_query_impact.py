import subprocess

from diting.impact import parse_unified_diff

from .conftest import write
from .test_cli import run, setup_concept


def git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


def commit_all(root, msg="x"):
    git(root, "add", "-A")
    git(root, "commit", "-qm", msg)


def make_repo(capsys, shop):
    setup_concept(capsys, shop)
    git(shop, "init", "-q", "-b", "main")
    commit_all(shop, "init")


def edit(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new), encoding="utf-8")


# ---------------------------------------------------------------- query


def test_query_by_synonyms_returns_all_sites(capsys, shop):
    setup_concept(capsys, shop)
    for sentence in ("会员折扣怎么取整", "VIP discount rounding", "member 折扣"):
        code, out = run(capsys, shop, "query", sentence)
        assert code == 0
        assert [c["id"] for c in out["concepts"]] == ["trade.member_discount.rounding"], sentence
        occ = {o["id"]: o for o in out["concepts"][0]["occurrences"]}
        assert set(occ) == {"settlement", "refund", "web-display"}
        assert occ["web-display"]["variant"] == "展示时取整到元"
        assert occ["settlement"]["anchors"][0]["lines"] == [10, 14]


def test_query_hypernym_expansion(capsys, shop):
    setup_concept(capsys, shop)
    run(capsys, shop, "vocab", "add", "entity", "用户", "-s", "user")
    run(capsys, shop, "vocab", "add", "entity", "会员", "--hypernym", "用户")
    code, out = run(capsys, shop, "query", "用户 优惠")
    hit = out["concepts"][0]
    assert hit["id"] == "trade.member_discount.rounding"
    assert any("is-a 用户" in r for r in hit["reasons"])


def test_query_no_match(capsys, shop):
    setup_concept(capsys, shop)
    code, out = run(capsys, shop, "query", "库存扣减 inventory")
    assert code == 0 and out["count"] == 0


def test_query_skips_removed_concepts(capsys, shop):
    setup_concept(capsys, shop)
    run(capsys, shop, "concept", "add", "trade.old.rule", "会员旧折扣", "-S", "会员", "-O", "折扣",
        "--lifecycle", "removed")
    code, out = run(capsys, shop, "query", "会员折扣")
    assert [c["id"] for c in out["concepts"]] == ["trade.member_discount.rounding"]
    code, out = run(capsys, shop, "query", "会员折扣", "--include-removed")
    assert {c["id"] for c in out["concepts"]} == {"trade.member_discount.rounding", "trade.old.rule"}


# ---------------------------------------------------------------- diff parsing


def test_parse_unified_diff_lines_and_status():
    diff = """\
diff --git a/a.py b/a.py
index 1..2 100644
--- a/a.py
+++ b/a.py
@@ -2,3 +2,3 @@ def f():
     x = 1
-    return x
+    return x + 1
 # end
@@ -10,2 +10,0 @@
-gone = 1
--- not a header = 2
diff --git a/old.py b/new.py
similarity index 90%
rename from old.py
rename to new.py
diff --git a/n.py b/n.py
new file mode 100644
--- /dev/null
+++ b/n.py
@@ -0,0 +1,2 @@
+a = 1
+b = 2
"""
    files = {f.path: f for f in parse_unified_diff(diff)}
    a = files["a.py"]
    assert a.status == "modified" and a.added == {3} and a.removed == {3, 10, 11}
    assert 10 in a.gaps
    assert files["new.py"].status == "renamed" and files["new.py"].old_path == "old.py"
    assert files["n.py"].status == "added" and files["n.py"].added == {1, 2}


# ---------------------------------------------------------------- impact


def test_impact_flags_untouched_siblings(capsys, shop):
    make_repo(capsys, shop)
    edit(shop / "svc/refund/refund.py", "ROUND_HALF_UP)", "ROUND_DOWN)")
    code, out = run(capsys, shop, "impact")
    assert code == 0
    (c,) = out["concepts"]
    assert c["changed"] == ["refund"]
    assert c["review"] == ["settlement", "web-display"]
    assert out["needs_review"] == 1
    code, _ = run(capsys, shop, "impact", "--fail-on-review")
    assert code == 1


def test_impact_all_sites_changed_needs_no_review(capsys, shop):
    make_repo(capsys, shop)
    for rel in ("svc/refund/refund.py", "svc/pricing/discount.py", "web/price_label.py"):
        edit(shop / rel, "rounding=ROUND_HALF_UP)", "rounding=ROUND_HALF_EVEN)")
    code, out = run(capsys, shop, "impact")
    (c,) = out["concepts"]
    assert sorted(c["changed"]) == ["refund", "settlement", "web-display"]
    assert c["review"] == [] and out["needs_review"] == 0


def test_impact_cosmetic_change_is_ignored(capsys, shop):
    make_repo(capsys, shop)
    edit(shop / "svc/refund/refund.py", "    if not is_member", "    # explain\n    if not is_member")
    code, out = run(capsys, shop, "impact", "--fail-on-review")
    assert code == 0
    (c,) = out["concepts"]
    assert {o["id"]: o["status"] for o in c["occurrences"]}["refund"] == "cosmetic"
    assert c["review"] == []


def test_impact_staged_and_base_modes(capsys, shop):
    make_repo(capsys, shop)
    git(shop, "checkout", "-q", "-b", "feature")
    edit(shop / "svc/pricing/discount.py", 'Decimal("200")', 'Decimal("300")')
    git(shop, "add", "-A")
    code, out = run(capsys, shop, "impact", "--staged")
    assert out["concepts"][0]["changed"] == ["settlement"]
    git(shop, "commit", "-qm", "threshold")
    code, out = run(capsys, shop, "impact")  # nothing uncommitted
    assert out["concepts"] == []
    code, out = run(capsys, shop, "impact", "--base", "main")
    assert out["concepts"][0]["changed"] == ["settlement"]
    assert out["concepts"][0]["review"] == ["refund", "web-display"]


def test_impact_diff_file_and_deleted_symbol(capsys, shop, tmp_path):
    make_repo(capsys, shop)
    write(shop / "web/price_label.py", "def nothing():\n    pass\n")
    write(shop / "svc/new_mod.py", "def brand_new():\n    return 1\n")
    code, out = run(capsys, shop, "impact")
    (c,) = out["concepts"]
    assert {o["id"]: o["status"] for o in c["occurrences"]}["web-display"] == "missing"
    assert "svc/new_mod.py#brand_new" in out["unindexed"]
    # same change supplied as a diff file (no old-side text available)
    diff = subprocess.run(["git", "-C", str(shop), "diff"], capture_output=True, text=True).stdout
    p = tmp_path / "change.diff"
    p.write_text(diff, encoding="utf-8")
    code, out = run(capsys, shop, "impact", "--diff", str(p))
    assert out["concepts"][0]["changed"] == ["web-display"]


def test_impact_outside_git_needs_diff(capsys, shop):
    setup_concept(capsys, shop)
    code, out = run(capsys, shop, "impact")
    assert code == 2 and "--diff" in out["error"]


def test_impact_when_diting_root_is_a_subdirectory(capsys, shop):
    setup_concept(capsys, shop)
    outer = shop.parent
    git(outer, "init", "-q", "-b", "main")
    commit_all(outer, "init")
    edit(shop / "svc/refund/refund.py", "ROUND_HALF_UP)", "ROUND_DOWN)")
    code, out = run(capsys, shop, "impact")
    assert out["concepts"][0]["changed"] == ["refund"]
    diff = subprocess.run(["git", "-C", str(outer), "diff"], capture_output=True, text=True).stdout
    assert "a/shop/svc" in diff
    p = outer / "c.diff"
    p.write_text(diff, encoding="utf-8")
    code, out = run(capsys, shop, "impact", "--diff", str(p))
    assert out["concepts"][0]["changed"] == ["refund"]
