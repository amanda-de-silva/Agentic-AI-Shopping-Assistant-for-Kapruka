/** Product detail lookup used to enrich cards and display full product details. */
import { apiFetch } from "./client";

export interface ProductDetailResponse {
  product_id: string;
  name: string;
  price: number;
  currency?: string;
  in_stock?: boolean | null;
  stock_note?: string | null;
  image_url?: string | null;
  images?: string[];
  url?: string | null;
  description?: string | null;
  category?: string | null;
  vendor?: string | null;
  weight?: string | null;
  ships_internationally?: boolean | null;
  variants?: unknown[];
  shipping?: Record<string, unknown> | string | null;
}

export async function getProductDetail(
  productId: string,
): Promise<ProductDetailResponse> {
  return apiFetch<ProductDetailResponse>(
    `/products/${encodeURIComponent(productId)}`,
  );
}
