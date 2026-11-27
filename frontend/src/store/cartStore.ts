/**
 * Zustand store: cart items, item count, total -- updated from ui_actions events.
 */
import { create } from "zustand";

export interface CartItem {
  product_id: string;
  name: string;
  price: number;
  currency: string;
  quantity: number;
  recipient_name?: string | null;
  gift_message?: string | null;
  line_total: number;
}

export interface CartView {
  session_id: string;
  items: CartItem[];
  item_count: number;
  subtotal: number;
  currency: string;
}

export interface UIAction {
  type:
    | "cart_updated"
    | "navigate_checkout"
    | "navigate_cart"
    | "order_status"
    | "memory_recalled"
    | "products_shown";
  payload: Record<string, unknown>;
}

interface CartStore {
  cart: CartView | null;
  isDrawerOpen: boolean;
  setCart: (cart: CartView) => void;
  openDrawer: () => void;
  closeDrawer: () => void;
}

export const useCartStore = create<CartStore>((set) => ({
  cart: null,
  isDrawerOpen: false,
  setCart: (cart) => set({ cart }),
  openDrawer: () => set({ isDrawerOpen: true }),
  closeDrawer: () => set({ isDrawerOpen: false }),
}));
