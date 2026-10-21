from decimal import ROUND_HALF_UP, Decimal


class DiscountCalc:
    """Settlement-side member discount."""

    def __init__(self, rate: Decimal):
        self.rate = rate

    def apply(self, order_amount: Decimal, is_member: bool) -> Decimal:
        if not is_member or order_amount < Decimal("200"):
            return Decimal("0.00")
        discount = order_amount * self.rate
        return discount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calc_shipping_fee(weight_kg: Decimal) -> Decimal:
    return Decimal("8.00") if weight_kg <= 1 else Decimal("8.00") + (weight_kg - 1) * Decimal("2.00")
