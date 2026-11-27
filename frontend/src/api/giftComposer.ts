/** Read-only Gift Composer API. This endpoint never mutates the cart. */
import { apiFetch } from "./client";

export type DeliveryStatus = "not_checked" | "confirmed" | "unavailable" | "unknown";

export interface GiftComposerRequest {
  recipient: string;
  occasion: string;
  budget_lkr?: number;
  preferences?: string;
  /** Ignore saved recipient likes for this preview; saved allergies/dislikes still apply. */
  ignore_saved_preferences?: boolean;
  avoid?: string;
  allergies?: string;
  delivery_city?: string;
  delivery_date?: string;
}

export interface GiftProductOption {
  product_id: string;
  name: string;
  price: number;
  currency: string;
  category?: string | null;
  group: string;
  image_url?: string | null;
  images: string[];
  url?: string | null;
  description: string;
  in_stock?: boolean | null;
  reasons: string[];
  delivery_status: DeliveryStatus;
  delivery_message?: string | null;
}

export interface GiftProductPreview extends GiftProductOption {
  /** Up to 12 same-type alternatives; alternative entries do not contain nested options. */
  options?: GiftProductOption[];
}

export interface GiftBundlePreview {
  id: string;
  title: string;
  description: string;
  total: number;
  currency: string;
  products: GiftProductPreview[];
  delivery_status: DeliveryStatus;
  delivery_message?: string | null;
}

export interface GiftComposerResponse {
  status: "bundles_found" | "single_items_only" | "no_eligible_products";
  recipient: string;
  occasion: string;
  budget_lkr?: number | null;
  delivery_city?: string | null;
  delivery_date?: string | null;
  message: string;
  bundles: GiftBundlePreview[];
  standalone_options: GiftProductPreview[];
  delivery_checked: boolean;
}

export async function createGiftPreview(
  payload: GiftComposerRequest,
): Promise<GiftComposerResponse> {
  return apiFetch<GiftComposerResponse>("/gift-composer/preview", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
