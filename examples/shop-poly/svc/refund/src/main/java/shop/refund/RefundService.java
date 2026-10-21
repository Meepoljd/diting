package shop.refund;

import java.math.BigDecimal;
import java.math.RoundingMode;

public class RefundService {
    /** Refund must claw back exactly the discount granted at settlement. */
    public BigDecimal memberDiscount(BigDecimal paid, BigDecimal rate, boolean isMember) {
        if (!isMember || paid.compareTo(new BigDecimal("200")) < 0) {
            return BigDecimal.ZERO;
        }
        return paid.multiply(rate).setScale(2, RoundingMode.HALF_UP);
    }
}
