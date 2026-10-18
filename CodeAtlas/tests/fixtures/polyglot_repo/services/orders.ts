import { clean } from "../utils/helpers";

export class OrderService {
  place(item: string): string {
    return formatPrice(clean(item));
  }
}

export function formatPrice(value: string): string {
  return value.toUpperCase();
}
