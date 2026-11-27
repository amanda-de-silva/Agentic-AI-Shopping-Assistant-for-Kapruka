/**
 * Full-page cart view — hosts <CartDrawer /> content in page form.
 */
import { useNavigate } from "react-router-dom";
import { useCart } from "../hooks/useCart";
import { CartItemRow } from "../components/cart/CartItemRow";

export function CartPage() {
  const { cart, remove, updateQuantity } = useCart();
  const navigate = useNavigate();

  return (
    <div className="page">
      <h2>Your Cart</h2>
      {(!cart || cart.items.length === 0) && <p>Your cart is empty.</p>}
      {cart?.items.map((item) => (
        <CartItemRow key={item.product_id} item={item} onRemove={remove} onQuantityChange={updateQuantity} />
      ))}
      {cart && cart.items.length > 0 && (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 16, fontSize: 16 }}>
            <strong>Subtotal</strong>
            <strong>
              {cart.currency} {cart.subtotal.toLocaleString()}
            </strong>
          </div>
          <button className="checkout-cta" onClick={() => navigate("/checkout")}>
            Checkout
          </button>
        </>
      )}
    </div>
  );
}
