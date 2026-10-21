"""PR-3: sync / confirm / check / index --verify."""

import yaml

from .conftest import write
from .test_cli import run, setup_concept
from .test_query_impact import commit_all, edit, git

CID = "trade.member_discount.rounding"
REFUND = "svc/refund/refund.py"
SETTLE = "svc/pricing/discount.py"
WEB = "web/price_label.py"


def anchors(shop):
    data = yaml.safe_load((shop / ".diting/edges" / f"{CID}.yaml").read_text(encoding="utf-8"))
    return {o["id"]: o["anchors"][0] for o in data["occurrences"]}


def codes(out):
    return sorted({(f["code"], f.get("occurrence")) for f in out["findings"]})


def round_down(shop, rel):
    p = shop / rel
    edit(p, "rounding=ROUND_HALF_UP)", "rounding=ROUND_DOWN)")
    edit(p, "from decimal import ROUND_HALF_UP,", "from decimal import ROUND_DOWN, ROUND_HALF_UP,")


# ------------------------------------------------------------------- sync


def test_sync_move_to_other_file(capsys, shop):
    setup_concept(capsys, shop)
    src = (shop / REFUND).read_text(encoding="utf-8")
    (shop / REFUND).unlink()
    write(shop / "svc/after_sale/refunds.py", src)
    code, out = run(capsys, shop, "check", "--no-verify-index")
    assert code == 1
    assert ("anchor-moved", "refund") in codes(out)
    code, out = run(capsys, shop, "sync")
    assert code == 0
    ch = out["changes"]
    assert [(c["action"], c["new_ref"]) for c in ch] == [
        ("moved", "svc/after_sale/refunds.py#refund_member_discount")]
    a = anchors(shop)["refund"]
    assert a["path"] == "svc/after_sale/refunds.py" and a["state"] == "auto"
    code, out = run(capsys, shop, "check")
    assert code == 0, out
    assert out["findings"] == []


def test_sync_rename_is_a_move(capsys, shop):
    setup_concept(capsys, shop)
    edit(shop / REFUND, "def refund_member_discount(", "def claw_back_discount(")
    code, out = run(capsys, shop, "sync")
    assert [(c["action"], c["new_ref"]) for c in out["changes"]] == [
        ("moved", f"{REFUND}#claw_back_discount")]
    assert run(capsys, shop, "check")[0] == 0


def test_sync_dry_run_writes_nothing(capsys, shop):
    setup_concept(capsys, shop)
    before = (shop / ".diting/edges" / f"{CID}.yaml").read_text(encoding="utf-8")
    edit(shop / REFUND, "def refund_member_discount(", "def claw_back_discount(")
    code, out = run(capsys, shop, "sync", "--dry-run")
    assert out["dry_run"] is True and out["written"] == []
    assert out["counts"]["moved"] == 1
    assert (shop / ".diting/edges" / f"{CID}.yaml").read_text(encoding="utf-8") == before


def test_sync_body_change_suspect_then_confirm(capsys, shop):
    setup_concept(capsys, shop)
    round_down(shop, REFUND)
    code, out = run(capsys, shop, "sync")
    assert [(c["occurrence"], c["action"]) for c in out["changes"]] == [("refund", "suspect")]
    assert anchors(shop)["refund"]["state"] == "suspect"
    # reverting the code recovers the anchor
    edit(shop / REFUND, "rounding=ROUND_DOWN)", "rounding=ROUND_HALF_UP)")
    code, out = run(capsys, shop, "sync")
    assert [(c["occurrence"], c["action"]) for c in out["changes"]] == [("refund", "recovered")]
    assert anchors(shop)["refund"]["state"] == "verified"


def test_sync_reshaped_and_lost(capsys, shop):
    setup_concept(capsys, shop)
    # rename + rename identifiers: same shape, different fingerprint
    p = shop / REFUND
    text = p.read_text(encoding="utf-8").replace("refund_member_discount", "claw_back").replace("paid", "amount_paid")
    p.write_text(text, encoding="utf-8")
    code, out = run(capsys, shop, "sync")
    ch = out["changes"]
    assert [(c["action"], c["new_ref"]) for c in ch] == [("reshaped", f"{REFUND}#claw_back")]
    assert anchors(shop)["refund"]["state"] == "suspect"
    code, out = run(capsys, shop, "check")
    assert code == 1 and ("anchor-changed", "refund") in codes(out)
    # confirming signs it off
    code, out = run(capsys, shop, "confirm", CID, "refund")
    assert code == 0 and out["updated"][0]["logic_changed"] is True
    # deletion -> lost
    write(shop / WEB, "def nothing():\n    pass\n")
    code, out = run(capsys, shop, "sync")
    assert [(c["occurrence"], c["action"]) for c in out["changes"]] == [("web-display", "lost")]
    code, out = run(capsys, shop, "check")
    assert code == 1 and ("anchor-lost", "web-display") in codes(out)


def test_confirm_rejects_unresolved_and_unknown(capsys, shop):
    setup_concept(capsys, shop)
    write(shop / WEB, "def nothing():\n    pass\n")
    code, out = run(capsys, shop, "confirm", CID, "web-display")
    assert code != 0 and "unresolved" in out["error"]
    code, out = run(capsys, shop, "confirm", CID, "nope")
    assert code != 0 and "unknown occurrence" in out["error"]
    code, out = run(capsys, shop, "confirm", "x.y.z")
    assert code != 0 and "unknown concept" in out["error"]


# ------------------------------------------------------------------ check


def test_check_clean(capsys, shop):
    setup_concept(capsys, shop)
    code, out = run(capsys, shop, "check")
    assert code == 0, out
    assert out["ok"] and out["index_consistent"] and out["findings"] == []
    assert all(a.get("basis") for a in anchors(shop).values())


def test_check_catches_missed_sibling(capsys, shop):
    setup_concept(capsys, shop)
    round_down(shop, REFUND)
    code, out = run(capsys, shop, "check")
    assert code == 1
    assert codes(out) == [("anchor-changed", "refund"),
                          ("sibling-review", "settlement"),
                          ("sibling-review", "web-display")]
    assert all(f["severity"] == "block" for f in out["findings"])
    sib = next(f for f in out["findings"] if f["occurrence"] == "web-display")
    assert "refund" in sib["message"] and "展示时取整到元" in sib["message"]

    # signing off only the changed site does not silence the siblings (even in a later commit)
    code, out = run(capsys, shop, "confirm", CID, "refund")
    assert code == 0 and out["pending_siblings"] == ["settlement", "web-display"]
    code, out = run(capsys, shop, "check")
    assert code == 1
    assert codes(out) == [("sibling-review", "settlement"), ("sibling-review", "web-display")]

    # the variant site is still correct as is -> confirm it; settlement gets the same change
    assert run(capsys, shop, "confirm", CID, "web-display")[0] == 0
    round_down(shop, SETTLE)
    code, out = run(capsys, shop, "check")
    assert code == 1
    assert ("anchor-changed", "settlement") in codes(out)
    assert run(capsys, shop, "confirm", CID, "settlement")[0] == 0
    # refund and web-display were signed off before settlement changed -> review them again
    code, out = run(capsys, shop, "check")
    assert codes(out) == [("sibling-review", "refund"), ("sibling-review", "web-display")]
    code, out = run(capsys, shop, "confirm", CID)
    assert out["pending_siblings"] == []
    code, out = run(capsys, shop, "check")
    assert code == 0, out


def test_concept_bump_makes_sites_stale(capsys, shop):
    setup_concept(capsys, shop)
    code, out = run(capsys, shop, "concept", "bump", CID, "--statement", "会员折扣金额向下取整保留两位小数")
    assert code == 0 and out["to"] == 2
    code, out = run(capsys, shop, "check")
    assert code == 1
    assert codes(out) == [("stale-occurrence", o) for o in ("refund", "settlement", "web-display")]
    run(capsys, shop, "confirm", CID)
    code, out = run(capsys, shop, "check")
    assert code == 0, out


def test_gate_levels(capsys, shop):
    setup_concept(capsys, shop)
    cpath = shop / ".diting/concepts/trade/member_discount/rounding.yaml"
    c = yaml.safe_load(cpath.read_text(encoding="utf-8"))
    c["level"] = "info"
    cpath.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), encoding="utf-8")
    round_down(shop, REFUND)
    code, out = run(capsys, shop, "check")
    assert code == 0 and out["ok"] and out["blocking"] == 0 and out["warnings"] == 3
    assert {f["severity"] for f in out["findings"]} == {"warn"}

    cfg = shop / ".diting/config.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    data["gate"] = {"info": "off"}
    cfg.write_text(yaml.safe_dump(data), encoding="utf-8")
    code, out = run(capsys, shop, "check")
    assert code == 0 and out["findings"] == [] and out["suppressed"] == 3


def test_check_draft_concept_not_gated(capsys, shop):
    setup_concept(capsys, shop)
    cpath = shop / ".diting/concepts/trade/member_discount/rounding.yaml"
    c = yaml.safe_load(cpath.read_text(encoding="utf-8"))
    c["lifecycle"] = "draft"
    cpath.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), encoding="utf-8")
    round_down(shop, REFUND)
    assert run(capsys, shop, "check")[0] == 0


def test_check_with_base_includes_impact(capsys, shop):
    setup_concept(capsys, shop)
    git(shop, "init", "-q", "-b", "main")
    commit_all(shop, "init")
    git(shop, "checkout", "-qb", "feature")
    round_down(shop, REFUND)
    commit_all(shop, "refund rounds down")
    code, out = run(capsys, shop, "check", "--base", "main")
    assert code == 1
    imp = out["impact"]["concepts"][0]
    assert imp["id"] == CID and set(imp["review"]) == {"settlement", "web-display"}
    assert ("sibling-review", "settlement") in codes(out)


def test_check_base_flags_anchor_without_basis(capsys, shop):
    """Legacy anchors (no basis) are still caught when a diff source is given."""
    setup_concept(capsys, shop)
    epath = shop / ".diting/edges" / f"{CID}.yaml"
    data = yaml.safe_load(epath.read_text(encoding="utf-8"))
    for o in data["occurrences"]:
        for a in o["anchors"]:
            a.pop("basis", None)
    epath.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    git(shop, "init", "-q", "-b", "main")
    commit_all(shop, "init")
    round_down(shop, REFUND)
    code, out = run(capsys, shop, "check")
    assert codes(out) == [("anchor-changed", "refund")]
    code, out = run(capsys, shop, "check", "--base", "main")
    assert code == 1
    assert ("sibling-review", "settlement") in codes(out)


# ------------------------------------------------------------------ index


def test_index_incremental_and_verify(capsys, shop):
    setup_concept(capsys, shop)
    code, out = run(capsys, shop, "index", "--full")
    assert code == 0 and out["extracted"] == out["files"]
    round_down(shop, REFUND)
    write(shop / "svc/new_mod.py", "def f(x):\n    return x + 1\n")
    (shop / WEB).unlink()
    code, out = run(capsys, shop, "index", "--verify")
    assert code == 0, out
    st = out
    assert st["extracted"] == 2 and st["dropped"] == 1 and st["reused"] == st["files"] - 2
    assert out["consistent"] is True


def test_index_verify_detects_corrupt_cache(capsys, shop):
    import json

    setup_concept(capsys, shop)
    run(capsys, shop, "index")
    cache = shop / ".diting/.cache/symbols.json"
    payload = json.loads(cache.read_text(encoding="utf-8"))
    entry = payload["files"][REFUND]
    entry["symbols"][0]["fingerprint"] = "deadbeef"
    cache.write_text(json.dumps(payload), encoding="utf-8")
    code, out = run(capsys, shop, "index", "--verify")
    assert code == 1 and out["consistent"] is False
    assert out["diffs"][0]["ref"] == f"{REFUND}#refund_member_discount"
