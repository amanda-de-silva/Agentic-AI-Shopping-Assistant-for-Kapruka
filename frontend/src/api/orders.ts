/** Checkout, delivery-option, and authorized order tracking API calls. */
import { apiFetch } from "./client";

export type CheckoutMode =
  | "gift"
  | "self";

export interface OrderCreateResponse {
  order_id: string;
  kapruka_order_number: string | null;
  checkout_url: string;
  total: number | null;
  currency: string;
}

export interface OrderStatus {
  order_number: string;
  status: string;
  message?: string | null;
  recipient: string | null;
  items: Record<string, unknown>[];
  timeline: Record<string, unknown>[];
}

export interface CheckoutPayload {
  mode: CheckoutMode;

  recipient: {
    name: string;
    phone: string;
  };

  delivery: {
    address: string;
    city: string;
    date?: string;
  };

  sender?: {
    name: string;
  };

  currency?: string;

  gift_message?: string;
}

export interface DeliveryOptionsResponse {
  requested_city: string;
  canonical_city: string;
  earliest_date: string | null;
  checked_from: string;
  checked_until: string;
  days_checked: number;
  message: string | null;
}

export async function getDeliveryOptions(
  city: string
): Promise<DeliveryOptionsResponse> {
  return apiFetch<DeliveryOptionsResponse>(
    "/checkout/delivery-options",
    {
      method: "POST",

      body: JSON.stringify({
        city,
      }),
    }
  );
}

export async function createCheckout(
  payload: CheckoutPayload,
  idempotencyKey: string
): Promise<OrderCreateResponse> {
  return apiFetch<OrderCreateResponse>(
    "/checkout",
    {
      method: "POST",

      headers: {
        "Idempotency-Key":
          idempotencyKey,
      },

      body: JSON.stringify(
        payload
      ),
    }
  );
}

export async function getOrderStatus(
  orderNumber: string
): Promise<OrderStatus> {
  return apiFetch<OrderStatus>(
    `/orders/${encodeURIComponent(
      orderNumber
    )}`
  );
}