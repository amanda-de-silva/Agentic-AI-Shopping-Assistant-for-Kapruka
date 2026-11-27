/**
 * Order tracking page — hosts <OrderTracker />.
 */
import { OrderTracker } from "../components/checkout/OrderTracker";

export function OrderTrackingPage() {
  return (
    <div className="page">
      <OrderTracker />
    </div>
  );
}
