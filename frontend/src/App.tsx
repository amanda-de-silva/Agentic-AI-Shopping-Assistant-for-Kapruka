import {
  BrowserRouter,
  Routes,
  Route,
  Link,
} from "react-router-dom";
import { LoginPage } from "./pages/LoginPage";
import { HomePage } from "./pages/HomePage";
import { CartPage } from "./pages/CartPage";
import { CheckoutPage } from "./pages/CheckoutPage";
import {
  OrderTrackingPage,
} from "./pages/OrderTrackingPage";
import { GiftComposerPage } from "./pages/GiftComposerPage";

import {
  CartDrawer,
} from "./components/cart/CartDrawer";

import {
  useCartStore,
} from "./store/cartStore";

import {
  useAuthStore,
} from "./store/authStore";

import {
  useCart,
} from "./hooks/useCart";

import {
  logoutCurrentUser,
} from "./api/auth";

import {
  clearSessionId,
} from "./store/sessionStore";


function CartHydrator() {
  /*
   * useCart() performs GET /cart whenever the authenticated
   * application is mounted.
   *
   * Therefore, when the same phone number logs in again,
   * the user's previous cart is fetched from Supabase.
   */
  useCart();

  return null;
}


function TopBar() {
  const {
    cart,
    openDrawer,
  } = useCartStore();

  const phone = useAuthStore(
    (state) => state.phone
  );

  const signOut = useAuthStore(
    (state) => state.signOut
  );


  const handleLogout = async () => {
    try {
      await logoutCurrentUser();

    } catch {
      /*
       * Even if the network request fails,
       * clear the client authentication state.
       */

    } finally {
      clearSessionId();

      signOut();

      window.location.reload();
    }
  };


  return (
    <div className="topbar">
      <Link to="/" className="topbar-brand">
        <span className="topbar-brand-mark" aria-hidden="true">
          🛍️
        </span>
        <span className="topbar-brand-name">
          Kapruka Concierge
        </span>
      </Link>

      <div className="topbar-actions">
        {phone && (
          <span className="topbar-phone">
            {phone}
          </span>
        )}

        <Link
          to="/gift-composer"
          className="pill-button"
        >
          Gift planner
        </Link>

        <Link
          to="/track"
          className="pill-button"
        >
          Track order
        </Link>

        <button
          className="pill-button accent"
          onClick={openDrawer}
        >
          Cart
          {cart &&
          cart.item_count > 0
            ? ` (${cart.item_count})`
            : ""}
        </button>

        <button
          className="pill-button"
          onClick={
            handleLogout
          }
        >
          Logout
        </button>
      </div>
    </div>
  );
}


function AuthenticatedApp() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <CartHydrator />

        <div className="main-column">
          <TopBar />

          <Routes>
            <Route
              path="/"
              element={<HomePage />}
            />

            <Route
              path="/cart"
              element={<CartPage />}
            />

            <Route
              path="/checkout"
              element={<CheckoutPage />}
            />

            <Route
              path="/track"
              element={
                <OrderTrackingPage />
              }
            />

            <Route
              path="/gift-composer"
              element={<GiftComposerPage />}
            />
          </Routes>
        </div>

        <CartDrawer />
      </div>
    </BrowserRouter>
  );
}


export default function App() {
  const authenticated =
    useAuthStore(
      (state) =>
        state.authenticated
    );

  if (!authenticated) {
    /*
     * Use your existing LoginPage here.
     */

    return <LoginPage />;
  }

  return (
    <AuthenticatedApp />
  );
}
