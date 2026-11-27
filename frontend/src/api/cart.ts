/** Cart API: existing single-item operations plus an explicit bundle-add endpoint. */
import { apiFetch } from "./client";
import type { CartView } from "../store/cartStore";

export async function getCart(): Promise<CartView> {
  return apiFetch<CartView>("/cart");
}

export async function mutateCart(
  action: "add" | "remove" | "update_quantity",
  productId: string,
  quantity = 1
): Promise<CartView> {
  return apiFetch<CartView>("/cart", {
    method: "PATCH",
    body: JSON.stringify({ action, product_id: productId, quantity }),
  });
}

export interface CartBatchItemInput {
  product_id: string;
  quantity?: number;
}

/** Add every product in a user-selected bundle as one cart action. */
export async function addCartItemsBatch(
  items: CartBatchItemInput[],
): Promise<CartView> {
  if (!items.length) {
    throw new Error("This gift bundle has no products to add.");
  }

  return apiFetch<CartView>("/cart/add-batch", {
    method: "POST",
    body: JSON.stringify({
      items: items.map((item) => ({
        product_id: item.product_id,
        quantity: item.quantity ?? 1,
      })),
    }),
  });
}
