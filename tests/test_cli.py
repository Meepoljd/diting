import json

from diting.cli import main

from .conftest import write


def run(capsys, root, *argv):
    code = main([*argv, "--root", str(root), "--json"])
    out = capsys.readouterr().out
    return code, json.loads(out)


def setup_concept(capsys, shop):
    assert run(capsys, shop, "init")[0] == 0
    for argv in (
        ("vocab", "add", "context", "交易"),
        ("vocab", "add", "entity", "会员", "-s", "member", "-s", "VIP"),
        ("vocab", "add", "entity", "折扣", "-s", "discount"),
        ("vocab", "add", "predicate", "享受", "-s", "apply", "--class", "compute"),
    ):
        assert run(capsys, shop, *argv)[0] == 0
    code, out = run(capsys, shop, "concept", "add", "trade.member_discount.rounding",
                    "会员折扣金额四舍五入保留两位小数", "--context", "交易",
                    "-S", "会员", "-P", "享受", "-O", "折扣",
                    "-m", "condition=订单金额 >= 200", "-m", "manner=四舍五入保留两位",
                    "--level", "critical")
    assert code == 0 and out["warnings"] == []
    for ref, extra in (
        ("svc/pricing/discount.py#DiscountCalc.apply", ("-o", "settlement")),
        ("svc/refund/refund.py#refund_member_discount", ("-o", "refund")),
        ("web/price_label.py#PriceLabel.format_discount", ("-o", "web-display", "--variant", "展示时取整到元")),
    ):
        code, out = run(capsys, shop, "anchor", "add", "trade.member_discount.rounding", ref, *extra)
        assert code == 0, out


def test_init_creates_layout_and_candidates(capsys, shop):
    code, out = run(capsys, shop, "init")
    assert code == 0
    assert out["symbols"] >= 5
    assert ".diting/config.yaml" in out["created"]
    assert (shop / ".diting" / "vocab.candidates.yaml").exists()
    assert (shop / ".diting" / ".cache" / "symbols.json").exists()
    assert "discount" in out["candidates"]["entities"]
    # idempotent: second init creates nothing new
    code, out = run(capsys, shop, "init")
    assert out["created"] == []


def test_full_pr1_flow_and_lint_clean(capsys, shop):
    setup_concept(capsys, shop)
    code, out = run(capsys, shop, "status")
    assert out["concepts"] == 1 and out["occurrences"] == 3 and out["anchors"] == 3
    code, out = run(capsys, shop, "concept", "list")
    assert out["concepts"][0]["occurrences"] == 3
    code, out = run(capsys, shop, "lint")
    assert code == 0, out
    assert out["errors"] == 0


def test_anchor_unknown_symbol_gives_hint(capsys, shop):
    setup_concept(capsys, shop)
    code, out = run(capsys, shop, "anchor", "add", "trade.member_discount.rounding",
                    "svc/pricing/discount.py#apply")
    assert code == 2
    assert "DiscountCalc.apply" in out["error"]


def test_lint_detects_drift_and_lost_anchor(capsys, shop):
    setup_concept(capsys, shop)
    # logic change at one site -> drift warning
    p = shop / "svc/refund/refund.py"
    p.write_text(p.read_text().replace("ROUND_HALF_UP)", "ROUND_DOWN)").replace(
        "import ROUND_HALF_UP,", "import ROUND_DOWN, ROUND_HALF_UP,"), encoding="utf-8")
    code, out = run(capsys, shop, "lint")
    assert code == 0
    assert [f["code"] for f in out["findings"]] == ["anchor-drift"]
    # symbol disappears -> unresolved error, non-zero exit
    write(shop / "web/price_label.py", "def nothing():\n    pass\n")
    code, out = run(capsys, shop, "lint")
    assert code == 1
    assert "anchor-unresolved" in [f["code"] for f in out["findings"]]


def test_lint_ignores_pure_reformatting(capsys, shop):
    setup_concept(capsys, shop)
    p = shop / "svc/pricing/discount.py"
    p.write_text("\n\n# moved down\n" + p.read_text().replace("    def apply(", "    # note\n    def apply("),
                 encoding="utf-8")
    code, out = run(capsys, shop, "lint")
    assert code == 0 and out["findings"] == []


def test_concept_bad_vocab_warns_and_duplicate_blocked(capsys, shop):
    setup_concept(capsys, shop)
    code, out = run(capsys, shop, "concept", "add", "trade.coupon.stack", "优惠券可叠加",
                    "-S", "会员", "-P", "叠加", "-O", "优惠券")
    assert code == 0
    assert len(out["warnings"]) == 2
    code, out = run(capsys, shop, "concept", "add", "trade.coupon.stack", "x")
    assert code == 2 and "exists" in out["error"]


def test_not_initialized_error(capsys, tmp_path):
    code, out = run(capsys, tmp_path, "status")
    assert code == 2 and "diting init" in out["error"]
