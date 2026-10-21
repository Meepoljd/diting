// Package pricing computes the payable amount at settlement.
package pricing

import "math"

// Calc applies member discounts at checkout.
type Calc struct {
	Rate float64
}

// MemberDiscount is the discount granted to a member, rounded half-up to cents.
func (c *Calc) MemberDiscount(amount float64, isMember bool) float64 {
	if !isMember || amount < 200 {
		return 0
	}
	return math.Round(amount*c.Rate*100) / 100
}
