import {
  useCallback,
  useEffect,
} from "react";

import {
  getCart,
  mutateCart,
} from "../api/cart";

import {
  useCartStore,
} from "../store/cartStore";

import {
  useAuthStore,
} from "../store/authStore";


export function useCart() {
  const cart =
    useCartStore(
      (state) => state.cart
    );

  const setCart =
    useCartStore(
      (state) => state.setCart
    );

  const isDrawerOpen =
    useCartStore(
      (state) =>
        state.isDrawerOpen
    );

  const openDrawer =
    useCartStore(
      (state) =>
        state.openDrawer
    );

  const closeDrawer =
    useCartStore(
      (state) =>
        state.closeDrawer
    );

  const authenticated =
    useAuthStore(
      (state) =>
        state.authenticated
    );

  const phone =
    useAuthStore(
      (state) =>
        state.phone
    );


  useEffect(() => {
    /*
     * Never load the previous cart while logged out.
     */
    if (!authenticated || !phone) {
      return;
    }

    let cancelled = false;

    getCart()
      .then((serverCart) => {
        if (!cancelled) {
          setCart(
            serverCart
          );
        }
      })
      .catch(() => {
        /*
         * Do not turn a cart hydration failure
         * into an application crash.
         */
      });

    return () => {
      cancelled = true;
    };
  }, [
    authenticated,
    phone,
    setCart,
  ]);


  const remove = useCallback(
    async (
      productId: string
    ) => {
      const updated =
        await mutateCart(
          "remove",
          productId
        );

      setCart(updated);
    },
    [setCart]
  );


  const updateQuantity =
    useCallback(
      async (
        productId: string,
        quantity: number
      ) => {
        const updated =
          await mutateCart(
            "update_quantity",
            productId,
            quantity
          );

        setCart(updated);
      },
      [setCart]
    );


  return {
    cart,
    isDrawerOpen,
    openDrawer,
    closeDrawer,
    remove,
    updateQuantity,
  };
}