/**
 * Sequential shopping-list UI. Each entry gets up to twelve results; the user
 * may select several products and add them together, or skip to the next entry.
 */
import { useEffect, useMemo, useState } from "react";
import { addCartItemsBatch } from "../../api/cart";
import {
  searchShoppingListItem,
  type ShoppingListProduct,
} from "../../api/shoppingList";
import { useCartStore } from "../../store/cartStore";
import "./ShoppingListFlow.css";

interface ShoppingListFlowProps {
  items: string[];
}

function formatPrice(price: number, currency = "LKR"): string {
  return `${currency} ${Number(price || 0).toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })}`;
}

export function ShoppingListFlow({ items }: ShoppingListFlowProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [products, setProducts] = useState<ShoppingListProduct[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addedCount, setAddedCount] = useState(0);
  const [skippedCount, setSkippedCount] = useState(0);
  const [completed, setCompleted] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const setCart = useCartStore((state) => state.setCart);
  const openCartDrawer = useCartStore((state) => state.openDrawer);

  const currentItem = items[currentIndex] || "";
  const selectedProducts = useMemo(
    () => products.filter((product) => selectedIds.includes(product.product_id)),
    [products, selectedIds],
  );
  const isLastItem = currentIndex >= items.length - 1;

  useEffect(() => {
    if (completed || !currentItem) return;

    let cancelled = false;
    setLoading(true);
    setAdding(false);
    setError(null);
    setProducts([]);
    setSelectedIds([]);

    void searchShoppingListItem(currentItem)
      .then((response) => {
        if (cancelled) return;
        setProducts((response.products || []).slice(0, 12));
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(
          cause instanceof Error
            ? cause.message
            : `Could not search for ${currentItem}. You can retry or skip this item.`,
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [currentItem, completed, retryCount]);

  const retryCurrentItem = () => {
    setError(null);
    setRetryCount((count) => count + 1);
  };

  const finishList = (shouldOpenCart: boolean) => {
    setCompleted(true);
    if (shouldOpenCart) openCartDrawer();
  };

  const skipCurrentItem = () => {
    setSkippedCount((count) => count + 1);
    if (isLastItem) {
      finishList(addedCount > 0);
      return;
    }
    setCurrentIndex((index) => index + 1);
  };

  const toggleProduct = (product: ShoppingListProduct) => {
    if (product.in_stock === false || adding) return;
    setSelectedIds((current) =>
      current.includes(product.product_id)
        ? current.filter((id) => id !== product.product_id)
        : current.length < 12
          ? [...current, product.product_id]
          : current,
    );
  };

  const addSelectedAndContinue = async () => {
    if (!selectedProducts.length || adding) return;

    setAdding(true);
    setError(null);
    try {
      const updatedCart = await addCartItemsBatch(
        selectedProducts.map((product) => ({
          product_id: product.product_id,
          quantity: 1,
        })),
      );
      setCart(updatedCart);

      const newlyAdded = selectedProducts.length;
      const totalAdded = addedCount + newlyAdded;
      setAddedCount(totalAdded);
      setSelectedIds([]);

      if (isLastItem) {
        finishList(true);
      } else {
        setCurrentIndex((index) => index + 1);
      }
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The selected products could not be added. Your cart has not intentionally been changed.",
      );
    } finally {
      setAdding(false);
    }
  };

  if (completed) {
    return (
      <section className="shopping-list-flow shopping-list-complete" aria-live="polite">
        <div className="shopping-list-complete-icon" aria-hidden="true">✓</div>
        <div>
          <h3>Shopping list completed</h3>
          <p>
            Added {addedCount} product{addedCount === 1 ? "" : "s"} to your cart.
            {skippedCount > 0 ? ` Skipped ${skippedCount} item${skippedCount === 1 ? "" : "s"}.` : ""}
            {addedCount > 0 ? " You can review your cart before checkout." : " No products were added to your cart."}
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="shopping-list-flow" aria-label="Shopping list product suggestions">
      <header className="shopping-list-flow-header">
        <div>
          <span className="shopping-list-eyebrow">SHOPPING LIST</span>
          <h3>Item {currentIndex + 1} of {items.length}: {currentItem}</h3>
          <p>Select one or more products, or skip this item to continue.</p>
        </div>
        <span className="shopping-list-added-count">{addedCount} added</span>
      </header>

      <ol className="shopping-list-progress" aria-label="Shopping list progress">
        {items.map((item, index) => (
          <li
            key={`${index}-${item}`}
            className={index < currentIndex ? "done" : index === currentIndex ? "active" : "pending"}
            title={item}
          >
            <span>{index + 1}</span>
            <small>{item}</small>
          </li>
        ))}
      </ol>

      {loading && (
        <div className="shopping-list-state" role="status">
          Searching Kapruka for “{currentItem}”…
        </div>
      )}

      {!loading && error && (
        <div className="shopping-list-error" role="alert">
          <span>{error}</span>
          <button type="button" onClick={retryCurrentItem} disabled={adding}>
            Retry search
          </button>
        </div>
      )}

      {!loading && !error && products.length === 0 && (
        <div className="shopping-list-state">
          No in-stock matches were found for “{currentItem}”. You can skip this item and continue.
        </div>
      )}

      {!loading && products.length > 0 && (
        <>
          <div className="shopping-list-results-summary">
            Showing {products.length} of up to 12 matches. Select multiple products if needed.
          </div>
          <div className="shopping-list-product-grid">
            {products.map((product) => {
              const selected = selectedIds.includes(product.product_id);
              const unavailable = product.in_stock === false;
              return (
                <article
                  key={product.product_id}
                  className={`shopping-list-product ${selected ? "selected" : ""} ${unavailable ? "unavailable" : ""}`}
                >
                  <div className="shopping-list-product-image">
                    {product.image_url ? (
                      <img src={product.image_url} alt={product.name} loading="lazy" />
                    ) : (
                      <span aria-label="No product image">🛍️</span>
                    )}
                    <span className={`shopping-list-stock ${unavailable ? "out" : ""}`}>
                      {unavailable ? "Out of stock" : product.in_stock === true ? "In stock" : "Stock not confirmed"}
                    </span>
                  </div>
                  <div className="shopping-list-product-body">
                    <label className="shopping-list-select">
                      <input
                        type="checkbox"
                        checked={selected}
                        disabled={unavailable || adding}
                        onChange={() => toggleProduct(product)}
                      />
                      <span>Select this product</span>
                    </label>
                    <h4>{product.name}</h4>
                    <strong className="shopping-list-price">{formatPrice(product.price, product.currency)}</strong>
                    {product.url && (
                      <a href={product.url} target="_blank" rel="noreferrer">
                        View on Kapruka ↗
                      </a>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        </>
      )}

      <footer className="shopping-list-flow-actions">
        <div className="shopping-list-selection-note">
          {selectedIds.length > 0
            ? `${selectedIds.length} product${selectedIds.length === 1 ? "" : "s"} selected`
            : "No products selected yet"}
        </div>
        <div className="shopping-list-action-buttons">
          <button
            type="button"
            className="shopping-list-skip-button"
            onClick={skipCurrentItem}
            disabled={loading || adding}
          >
            {isLastItem ? "Finish list" : "Skip this item"}
          </button>
          <button
            type="button"
            className="shopping-list-add-button"
            onClick={() => void addSelectedAndContinue()}
            disabled={loading || adding || selectedIds.length === 0}
          >
            {adding
              ? "Adding to cart…"
              : isLastItem
                ? `Add ${selectedIds.length} selected & finish`
                : `Add ${selectedIds.length} selected & next`}
          </button>
        </div>
      </footer>
    </section>
  );
}
