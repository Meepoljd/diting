# shop-poly example

The same rule as [`../shop`](../shop) is implemented in three languages:

| Site | Language | Symbol |
|---|---|---|
| settlement | Go | `svc/pricing/discount.go#Calc.MemberDiscount` |
| refund | Java | `svc/refund/src/main/java/shop/refund/RefundService.java#RefundService.memberDiscount` |
| web-display | TypeScript | `web/src/priceLabel.ts#PriceLabel.formatDiscount` (registered variant: rounds to whole yuan) |

The rule is "a member discount is rounded half-up to 2 decimals for orders >= 200". `.diting/` indexes all three sites as one concept. When a change touches only one language, `diting check` blocks the other two.

Requires the grammars: `pip install "diting[go,java,typescript] @ git+https://github.com/Meepoljd/diting@main"`.
