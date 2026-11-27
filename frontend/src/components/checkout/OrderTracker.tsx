/** Displays authorized order status/timeline. */
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getOrderStatus, type OrderStatus } from "../../api/orders";

export function OrderTracker() {
  const [params] = useSearchParams();
  const [orderNumber, setOrderNumber] = useState(params.get("order") || "");
  const [status, setStatus] = useState<OrderStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const lookup = async (number = orderNumber.trim()) => {
    if (!number) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getOrderStatus(number);
      setStatus(result);
    } catch (err) {
      setError((err as Error).message);
      setStatus(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const initialOrder = params.get("order");
    if (initialOrder) void lookup(initialOrder);
    // Intentionally run only for URL changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  return (
    <div>
      <h2>Track your order</h2>
      <div className="inline-form-row">
        <input
          placeholder="Order number"
          value={orderNumber}
          onChange={(e) => setOrderNumber(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void lookup()}
          aria-label="Order number"
        />
        <button onClick={() => void lookup()} disabled={loading}>
          {loading ? "Looking up…" : "Track"}
        </button>
      </div>
      {error && <p className="form-error">{error}</p>}
      {status && (
        <div>
          <p className="order-status-line">
            Status: <span className="order-status-value">{status.status}</span>
          </p>
          {status.message && <p className="order-status-line">{status.message}</p>}
          {status.recipient && <p className="order-status-line">Recipient: {status.recipient}</p>}
          {status.timeline.length > 0 && (
            <div className="order-timeline">
              {status.timeline.map((step, i) => (
                <div key={i} className="order-timeline-step">{String(step.event || step.status || JSON.stringify(step))}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
