/**
 * Checkout page — hosts <CheckoutSummary />, triggered by the 'navigate_checkout' ui_action.
 */
import { CheckoutSummary } from "../components/checkout/CheckoutSummary";

export function CheckoutPage() {
  return (
    <div className="page">
      <CheckoutSummary />
    </div>
  );
}
