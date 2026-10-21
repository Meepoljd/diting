from decimal import ROUND_HALF_UP, Decimal


class PriceLabel:
    def format_discount(self, amount: Decimal, rate: Decimal, is_member: bool) -> str:
        if not is_member or amount < Decimal("200"):
            return ""
        # variant: display rounds to whole yuan
        shown = (amount * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return f"会员立减 ¥{shown}"
