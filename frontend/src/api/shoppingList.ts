/** API for searching one shopping-list entry at a time. */
import { apiFetch } from "./client";

export interface ShoppingListProduct {
  product_id: string;
  name: string;
  price: number;
  currency: string;
  image_url?: string | null;
  url?: string | null;
  in_stock?: boolean | null;
}

export interface ShoppingListSearchResponse {
  item_query: string;
  products: ShoppingListProduct[];
  shown_count: number;
  limit: number;
  message?: string | null;
}

// Share only concurrent identical searches. Completed results are not cached,
// so moving through the list later always gets a fresh Kapruka search.
const inFlight = new Map<string, Promise<ShoppingListSearchResponse>>();

export function searchShoppingListItem(
  itemQuery: string,
): Promise<ShoppingListSearchResponse> {
  const normalized = itemQuery.trim().replace(/\s+/g, " ");
  const key = normalized.toLocaleLowerCase();
  const existing = inFlight.get(key);
  if (existing) return existing;

  const request = apiFetch<ShoppingListSearchResponse>("/shopping-list/search", {
    method: "POST",
    body: JSON.stringify({ item_query: normalized }),
  }).finally(() => {
    if (inFlight.get(key) === request) inFlight.delete(key);
  });

  inFlight.set(key, request);
  return request;
}
