/**
 * Single cart line item with qty control and remove button.
 */
import type { CartItem } from "../../store/cartStore";

interface Props {
  item: CartItem;
  onRemove: (productId: string) => void;
  onQuantityChange: (productId: string, quantity: number) => void;
}

export function CartItemRow({ item, onRemove, onQuantityChange }: Props) {
  return (
    <div className="cart-item-row">
      <div>
        <div className="cart-item-name">{item.name}</div>
        <div className="cart-item-price">
          {item.currency} {item.price.toLocaleString()} × {item.quantity}
        </div>
      </div>
      <div className="qty-stepper">
        <button
          type="button"
          className="qty-btn"
          onClick={() => onQuantityChange(item.product_id, item.quantity - 1)}
          aria-label="Decrease quantity"
        >
          −
        </button>
        <span className="qty-value">{item.quantity}</span>
        <button
          type="button"
          className="qty-btn"
          onClick={() => onQuantityChange(item.product_id, item.quantity + 1)}
          aria-label="Increase quantity"
        >
          +
        </button>
        <button
          type="button"
          className="remove-btn"
          onClick={() => onRemove(item.product_id)}
          title="Remove"
          aria-label="Remove item"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
