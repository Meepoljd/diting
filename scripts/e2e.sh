#!/usr/bin/env bash
# End-to-end regression for diting: drives the real CLI through the full agent workflow on a
# throw-away git copy of examples/shop and asserts exit codes + JSON fields at every step.
#
#   bash scripts/e2e.sh                                  # uses `diting` from PATH
#   DITING="python -m diting" PYTHONPATH=src bash scripts/e2e.sh
#
# Exit status 0 = every scenario behaved as specified.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
read -r -a DT <<<"${DITING:-diting}"
PY="${PYTHON:-python3}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
REPO="$WORK/shop"
CID=trade.member_discount.rounding
STEP=0
OUT="$WORK/out.json"

say() { STEP=$((STEP + 1)); printf '\n[%02d] %s\n' "$STEP" "$*"; }
fail() { echo "E2E FAIL at step $STEP: $*" >&2; [ -f "$OUT" ] && cat "$OUT" >&2; exit 1; }

# dt <expected-exit> <args...> : run diting --json in $REPO, store stdout in $OUT, check exit code
dt() {
  local want=$1; shift
  local code=0
  (cd "$REPO" && "${DT[@]}" "$@" --json) >"$OUT" || code=$?
  [ "$code" = "$want" ] || fail "\`diting $*\` exited $code, expected $want"
}

# expect <python expression over `j` (parsed $OUT)> [message]
expect() {
  "$PY" - "$OUT" "$1" <<'EOF' || fail "${2:-assertion failed: $1}"
import json, sys
j = json.load(open(sys.argv[1], encoding="utf-8"))
codes = sorted({(f["code"], f.get("occurrence")) for f in j.get("findings", [])})
ok = eval(sys.argv[2], {"j": j, "codes": codes})
if not ok:
    print("  expected:", sys.argv[2], file=sys.stderr)
    sys.exit(1)
EOF
  echo "  ok: $1"
}

g() { git -C "$REPO" -c user.email=e2e@diting -c user.name=e2e "$@" >/dev/null; }
commit() { g add -A; g commit -qm "$1"; }

# sub <file> <old> <new> : literal replace, must match
sub() {
  "$PY" - "$REPO/$1" "$2" "$3" <<'EOF' || fail "pattern not found in $1: $2"
import sys
p, old, new = sys.argv[1:]
s = open(p, encoding="utf-8").read()
if old not in s:
    sys.exit(1)
open(p, "w", encoding="utf-8").write(s.replace(old, new))
EOF
}

round_down() {  # the rule change: half-up -> down
  sub "$1" "rounding=ROUND_HALF_UP)" "rounding=ROUND_DOWN)"
  sub "$1" "from decimal import ROUND_HALF_UP," "from decimal import ROUND_DOWN, ROUND_HALF_UP,"
}

# ------------------------------------------------------------------------- setup
say "copy examples/shop (without its index) into a fresh git repo"
mkdir -p "$REPO"
(cd "$ROOT/examples/shop" && tar --exclude=.diting --exclude=__pycache__ -cf - .) | (cd "$REPO" && tar -xf -)
g init -q -b main

say "init + vocabulary + concept + three anchors"
dt 0 init
expect 'j["symbols"] >= 5 and "discount" in j["candidates"]["entities"]'
dt 0 vocab add context 交易
dt 0 vocab add entity 会员 -s member -s VIP
dt 0 vocab add entity 折扣 -s discount
dt 0 vocab add predicate 享受 -s apply --class compute
dt 0 concept add "$CID" 会员折扣金额四舍五入保留两位小数 --context 交易 -S 会员 -P 享受 -O 折扣 \
  -m "condition=订单金额 >= 200" --level critical
expect 'j["warnings"] == []' "concept uses only vocabulary terms"
dt 0 anchor add "$CID" "svc/pricing/discount.py#DiscountCalc.apply" -o settlement
dt 0 anchor add "$CID" "svc/refund/refund.py#refund_member_discount" -o refund
dt 0 anchor add "$CID" "web/price_label.py#PriceLabel.format_discount" -o web-display --variant 展示时取整到元
dt 0 lint
expect 'j["errors"] == 0'
dt 0 skill
[ -s "$REPO/.diting/SKILL.md" ] || fail "SKILL.md not written"
dt 0 check
expect 'j["ok"] and j["findings"] == [] and j["index_consistent"]' "clean index passes the gate"
commit "index the rounding rule"

say "query: one sentence -> all three sites"
dt 0 query VIP discount rounding
expect '[c["id"] for c in j["concepts"]] == ["'"$CID"'"]'
expect '{o["id"] for o in j["concepts"][0]["occurrences"]} == {"settlement", "refund", "web-display"}'

# ------------------------------------------------- missed sibling change in a PR
say "PR changes the rule at refund only -> impact asks to review the siblings"
g checkout -qb feature/refund-only
round_down svc/refund/refund.py
commit "refund rounds down"
dt 0 impact --base main
expect 'j["concepts"][0]["changed"] == ["refund"]'
expect 'set(j["concepts"][0]["review"]) == {"settlement", "web-display"}'
dt 1 impact --base main --fail-on-review

say "check --base main blocks the PR"
dt 1 check --base main
expect 'codes == [("anchor-changed", "refund"), ("sibling-review", "settlement"), ("sibling-review", "web-display")]'
expect 'all(f["severity"] == "block" for f in j["findings"])' "critical concept -> block"

say "signing off only the changed site does not unblock (state survives commits)"
dt 0 confirm "$CID" refund
expect 'j["pending_siblings"] == ["settlement", "web-display"]'
commit "confirm refund only"
dt 1 check
expect 'codes == [("sibling-review", "settlement"), ("sibling-review", "web-display")]'

say "agent applies the change to settlement, reviews the display variant, confirms all"
round_down svc/pricing/discount.py
dt 1 check
expect '("anchor-changed", "settlement") in codes'
dt 0 confirm "$CID" --note "settlement updated; display variant still rounds to whole yuan"
expect 'j["pending_siblings"] == []'
dt 0 check --base main
expect 'j["ok"] and j["findings"] == []'
commit "apply rounding change everywhere"
g checkout -q main
g merge -q --ff-only feature/refund-only

# ------------------------------------------------------------- refactors
say "move a function to another file -> anchor-moved, sync fixes it"
mkdir -p "$REPO/svc/after_sale"
git -C "$REPO" mv svc/refund/refund.py svc/after_sale/refunds.py
dt 1 check
expect 'codes == [("anchor-moved", "refund")]'
dt 0 sync
expect '[(c["action"], c["new_ref"]) for c in j["changes"]] == [("moved", "svc/after_sale/refunds.py#refund_member_discount")]'
dt 0 check
expect 'j["ok"] and j["findings"] == []'
commit "move refund"

say "rename a method -> sync follows it"
sub svc/pricing/discount.py "def apply(" "def member_discount("
dt 0 sync --dry-run
expect 'j["written"] == [] and j["counts"].get("moved") == 1'
dt 0 sync
expect '[c["new_ref"] for c in j["changes"]] == ["svc/pricing/discount.py#DiscountCalc.member_discount"]'
dt 0 check
expect 'j["ok"]'
commit "rename apply"

say "comment/format-only edit is not a logic change"
sub web/price_label.py "        # variant: display rounds to whole yuan" "        # variant: the label shows whole yuan only"
dt 0 check
expect 'j["findings"] == []'
commit "comment"

say "deleting a site -> anchor-lost blocks"
cp "$REPO/web/price_label.py" "$WORK/price_label.bak"
printf 'def nothing():\n    pass\n' >"$REPO/web/price_label.py"
dt 1 check
expect 'codes == [("anchor-lost", "web-display")]'
cp "$WORK/price_label.bak" "$REPO/web/price_label.py"
dt 0 check

# ------------------------------------------------------------ rule change
say "the rule itself changes -> concept bump makes every site stale"
dt 0 concept bump "$CID" --statement 会员折扣金额向下取整保留两位小数 -m "manner=向下取整保留两位"
expect 'j["to"] == 2'
dt 1 check
expect 'codes == [("stale-occurrence", "refund"), ("stale-occurrence", "settlement"), ("stale-occurrence", "web-display")]'
dt 0 confirm "$CID"
dt 0 check
expect 'j["ok"]'
commit "rule v2"

# ------------------------------------------------------------- gate policy
say "info-level concept only warns; gate off suppresses"
sub .diting/concepts/trade/member_discount/rounding.yaml "level: critical" "level: info"
round_down web/price_label.py
dt 0 check
expect 'j["ok"] and j["blocking"] == 0 and j["warnings"] == 3'
printf 'gate:\n  info: "off"\n' >>"$REPO/.diting/config.yaml"
dt 0 check
expect 'j["findings"] == [] and j["suppressed"] == 3'
g checkout -q -- .
g clean -qfd -e .diting/.cache

# ------------------------------------------------------------------ L0 index
say "incremental index == full rebuild; a corrupted cache is detected"
printf 'def helper(x):\n    return x * 2\n' >"$REPO/svc/helper.py"
dt 0 index --verify
expect 'j["consistent"] and j["extracted"] >= 1 and j["reused"] >= 1'
"$PY" - "$REPO/.diting/.cache/symbols.json" <<'EOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["files"]["svc/helper.py"]["symbols"][0]["fingerprint"] = "0000000000000000"
json.dump(d, open(p, "w"))
EOF
dt 1 index --verify
expect 'not j["consistent"] and j["diffs"][0]["ref"] == "svc/helper.py#helper"'
dt 0 index --full --verify

say "ci-template renders a valid workflow running check --base"
(cd "$REPO" && "${DT[@]}" ci-template) >"$WORK/wf.yml"
"$PY" - "$WORK/wf.yml" <<'EOF' || fail "invalid workflow"
import sys, yaml
wf = yaml.safe_load(open(sys.argv[1]))
steps = wf["jobs"]["diting-check"]["steps"]
assert steps[0]["with"]["fetch-depth"] == 0
assert any("diting check --base" in s.get("run", "") for s in steps)
EOF
echo "  ok: workflow"

printf '\nE2E PASS: %d steps\n' "$STEP"
