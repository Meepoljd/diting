from decimal import ROUND_HALF_UP, Decimal


def refund_member_discount(paid: Decimal, rate: Decimal, is_member: bool) -> Decimal:
    """Refund must claw back exactly the discount granted at settlement."""
    if not is_member or paid < Decimal("200"):
        return Decimal("0.00")
    return (paid * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
