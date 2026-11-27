/**
 * Slide-over cart view, reflects cartStore in real time as ui_actions arrive.
 */
import { useNavigate } from "react-router-dom";
import { useCart } from "../../hooks/useCart";
import { CartItemRow } from "./CartItemRow";

export function CartDrawer() {
  const { cart, isDrawerOpen, closeDrawer, remove, updateQuantity } = useCart();
  const navigate = useNavigate();

  return (
    <div className={`cart-drawer ${isDrawerOpen ? "open" : "closed"}`}>
      <div className="cart-drawer-header">
        <strong>Your Cart</strong>
        <button onClick={closeDrawer} aria-label="Close cart">✕</button>
      </div>
      <div className="cart-items">
        {(!cart || cart.items.length === 0) && (
          <p className="cart-empty">
            Nothing here yet — ask me to find something and I'll add it for you.
          </p>
        )}
        {cart?.items.map((item) => (
          <CartItemRow
            key={item.product_id}
            item={item}
            onRemove={remove}
            onQuantityChange={updateQuantity}
          />
        ))}
      </div>
      {cart && cart.items.length > 0 && (
        <div className="cart-summary">
          <div className="cart-summary-row">
            <span>Subtotal</span>
            <strong>
              {cart.currency} {cart.subtotal.toLocaleString()}
            </strong>
          </div>
          <button className="checkout-cta" onClick={() => navigate("/checkout")}>
            Checkout
          </button>
        </div>
      )}
    </div>
  );
}
