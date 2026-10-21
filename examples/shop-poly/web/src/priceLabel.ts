export class PriceLabel {
  constructor(private readonly rate: number) {}

  // shown on the product page: rounded to whole yuan (registered variant)
  formatDiscount(amount: number, isMember: boolean): string {
    if (!isMember || amount < 200) {
      return "";
    }
    return `-¥${Math.round(amount * this.rate)}`;
  }
}
