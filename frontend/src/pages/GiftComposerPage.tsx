/** Read-only Gift Composer: preview diverse baskets without touching the cart. */
import { FormEvent, useState } from "react";
import {
  createGiftPreview,
  type GiftComposerRequest,
  type GiftComposerResponse,
  type GiftBundlePreview,
  type GiftProductPreview,
  type GiftProductOption,
  type DeliveryStatus,
} from "../api/giftComposer";
import { addCartItemsBatch } from "../api/cart";
import { useCartStore } from "../store/cartStore";
import "./GiftComposerPage.css";

function money(value: number, currency = "LKR"): string {
  return `${currency} ${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function statusLabel(status: DeliveryStatus): string {
  switch (status) {
    case "confirmed": return "Delivery checked";
    case "unavailable": return "Not available on this date";
    case "unknown": return "Delivery not confirmed";
    default: return "Delivery not checked";
  }
}

function ProductPreview({
  product,
  selectedOptionIndex = 0,
  onSelectOption,
}: {
  product: GiftProductPreview;
  selectedOptionIndex?: number;
  onSelectOption?: (option: GiftProductOption, index: number) => void;
}) {
  const image = product.image_url || product.images?.[0];
  const options = product.options || [];
  const canBrowse = Boolean(onSelectOption && options.length > 1);
  const safeIndex = options.length > 0
    ? Math.min(Math.max(selectedOptionIndex, 0), options.length - 1)
    : 0;

  const move = (direction: -1 | 1) => {
    if (!onSelectOption || options.length < 2) return;
    const nextIndex = (safeIndex + direction + options.length) % options.length;
    onSelectOption(options[nextIndex], nextIndex);
  };

  return (
    <article className="gift-preview-product">
      <div className="gift-preview-product-media">
        {image ? (
          <img src={image} alt={product.name} loading="lazy" />
        ) : (
          <div className="gift-preview-no-image" aria-label="No image available">🎁</div>
        )}
        {canBrowse && (
          <>
            <button
              type="button"
              className="gift-preview-carousel-arrow prev"
              aria-label={`Previous ${product.group} option`}
              title="Previous option"
              onClick={() => move(-1)}
            >
              ‹
            </button>
            <button
              type="button"
              className="gift-preview-carousel-arrow next"
              aria-label={`Next ${product.group} option`}
              title="Next option"
              onClick={() => move(1)}
            >
              ›
            </button>
            <span className="gift-preview-carousel-count">
              {safeIndex + 1} / {options.length}
            </span>
          </>
        )}
      </div>
      <div className="gift-preview-product-body">
        <span className="gift-preview-product-group">{product.group}</span>
        <h4>{product.name}</h4>
        <div className="gift-preview-product-price">{money(product.price, product.currency)}</div>
        <p className="gift-preview-stock">
          {product.in_stock === true ? "In stock on Kapruka" : product.in_stock === false ? "Currently out of stock" : "Stock status not confirmed"}
        </p>
        {product.reasons.length > 0 && (
          <ul className="gift-preview-reasons">
            {product.reasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        )}
        {product.description && (
          <details className="gift-preview-description">
            <summary>Product description</summary>
            <p>{product.description}</p>
          </details>
        )}
        {product.delivery_message && (
          <p className="gift-preview-delivery-note">{product.delivery_message}</p>
        )}
        {product.url ? (
          <a className="gift-preview-link" href={product.url} target="_blank" rel="noreferrer">
            View on Kapruka ↗
          </a>
        ) : (
          <span className="gift-preview-muted">Kapruka link unavailable</span>
        )}
      </div>
    </article>
  );
}

function DeliveryStatusPill({ status }: { status: DeliveryStatus }) {
  return <span className={`gift-preview-status ${status}`}>{statusLabel(status)}</span>;
}

export function GiftComposerPage() {
  const [recipient, setRecipient] = useState("");
  const [occasion, setOccasion] = useState("Birthday");
  const [budget, setBudget] = useState(15000);
  const [hasBudget, setHasBudget] = useState(true);
  const [preferences, setPreferences] = useState("");
  const [ignoreSavedPreferences, setIgnoreSavedPreferences] = useState(false);
  const [avoid, setAvoid] = useState("");
  const [allergies, setAllergies] = useState("");
  const [deliveryCity, setDeliveryCity] = useState("");
  const [deliveryDate, setDeliveryDate] = useState("");
  const [result, setResult] = useState<GiftComposerResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addingBundleId, setAddingBundleId] = useState<string | null>(null);
  const [selectedOptionIndexes, setSelectedOptionIndexes] = useState<Record<string, number>>({});
  const [cartFeedback, setCartFeedback] = useState<{
    bundleId: string;
    kind: "loading" | "success" | "error";
    message: string;
  } | null>(null);
  const setCart = useCartStore((state) => state.setCart);
  const openCartDrawer = useCartStore((state) => state.openDrawer);

  const handleSelectBundleOption = (
    bundleId: string,
    slotIndex: number,
    option: GiftProductOption,
    optionIndex: number,
  ) => {
    const key = `${bundleId}:${slotIndex}`;
    const deliveryMessage = "Delivery has not been rechecked for this customized combination.";

    // Defense in depth: the backend limits carousel options so every combination
    // fits the budget, but refuse an over-budget selection if stale or malformed
    // response data ever bypasses that rule.
    const activeBundle = result?.bundles.find((bundle) => bundle.id === bundleId);
    const budgetLimit = result?.budget_lkr;
    if (activeBundle && budgetLimit != null) {
      const proposedTotal = activeBundle.products.reduce(
        (sum, current, index) => sum + Number(index === slotIndex ? option.price : current.price || 0),
        0,
      );
      if (proposedTotal > Number(budgetLimit) + 0.005) {
        setError(
          `That option would bring the gift total to ${money(proposedTotal)}, above your ${money(Number(budgetLimit))} budget. Choose a lower-priced option.`,
        );
        return;
      }
    }
    setError(null);

    setResult((current) => {
      if (!current) return current;
      return {
        ...current,
        bundles: current.bundles.map((bundle) => {
          if (bundle.id !== bundleId) return bundle;

          const products = bundle.products.map((existing, index) => ({
            ...(index === slotIndex
              ? { ...option, options: existing.options || [] }
              : existing),
            // The selected combination is different from the server-checked
            // combination, so never keep presenting its old delivery result.
            delivery_status: "not_checked" as DeliveryStatus,
            delivery_message: deliveryMessage,
          }));

          const total = Math.round(
            products.reduce((sum, product) => sum + Number(product.price || 0), 0) * 100,
          ) / 100;

          return {
            ...bundle,
            products,
            total,
            delivery_status: "not_checked" as DeliveryStatus,
            delivery_message: deliveryMessage,
          };
        }),
      };
    });

    setSelectedOptionIndexes((current) => ({ ...current, [key]: optionIndex }));
    setCartFeedback(null);
  };

  const handleSelectStandaloneOption = (
    slotIndex: number,
    option: GiftProductOption,
    optionIndex: number,
  ) => {
    const key = `standalone:${slotIndex}`;
    setResult((current) => {
      if (!current) return current;
      const previous = current.standalone_options[slotIndex];
      if (!previous) return current;
      const standalone_options = [...current.standalone_options];
      standalone_options[slotIndex] = {
        ...option,
        options: previous.options || [],
        delivery_status: "not_checked",
        delivery_message: "Delivery has not been rechecked for this selected item.",
      };
      return { ...current, standalone_options };
    });
    setSelectedOptionIndexes((current) => ({ ...current, [key]: optionIndex }));
  };

  const handleAddBundleToCart = async (bundle: GiftBundlePreview) => {
    if (addingBundleId) return;

    if (!bundle.products.length) {
      setCartFeedback({
        bundleId: bundle.id,
        kind: "error",
        message: "This gift bundle has no products to add.",
      });
      return;
    }

    if (
      result?.budget_lkr != null &&
      bundle.products.reduce((sum, product) => sum + Number(product.price || 0), 0) > Number(result.budget_lkr) + 0.005
    ) {
      setCartFeedback({
        bundleId: bundle.id,
        kind: "error",
        message: `This bundle exceeds your ${money(Number(result.budget_lkr))} budget. Choose lower-priced options before adding it to the cart.`,
      });
      return;
    }

    if (bundle.products.some((product) => product.in_stock === false)) {
      setCartFeedback({
        bundleId: bundle.id,
        kind: "error",
        message: "At least one product is marked out of stock. Refresh the preview before adding this bundle.",
      });
      return;
    }

    setAddingBundleId(bundle.id);
    setCartFeedback({
      bundleId: bundle.id,
      kind: "loading",
      message: "Adding all bundle items to your cart…",
    });

    try {
      const updatedCart = await addCartItemsBatch(
        bundle.products.map((product) => ({
          product_id: product.product_id,
          quantity: 1,
        })),
      );
      setCart(updatedCart);
      setCartFeedback({
        bundleId: bundle.id,
        kind: "success",
        message: `All ${bundle.products.length} bundle items were added to your cart.`,
      });
      openCartDrawer();
    } catch (cause) {
      setCartFeedback({
        bundleId: bundle.id,
        kind: "error",
        message: cause instanceof Error
          ? cause.message
          : "The whole gift bundle could not be added. Your cart was not intentionally changed.",
      });
    } finally {
      setAddingBundleId(null);
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setResult(null);
    setSelectedOptionIndexes({});
    setCartFeedback(null);

    const payload: GiftComposerRequest = {
      recipient: recipient.trim(),
      occasion: occasion.trim(),
      ...(hasBudget && budget > 0 ? { budget_lkr: budget } : {}),
      preferences: preferences.trim(),
      ignore_saved_preferences: ignoreSavedPreferences,
      avoid: avoid.trim(),
      allergies: allergies.trim(),
      ...(deliveryCity.trim() ? { delivery_city: deliveryCity.trim() } : {}),
      ...(deliveryDate ? { delivery_date: deliveryDate } : {}),
    };

    if (!payload.recipient || !payload.occasion) {
      setError("Recipient and occasion are required.");
      return;
    }

    setLoading(true);
    try {
      setResult(await createGiftPreview(payload));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Gift preview could not be created.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="gift-composer-page">
      <header className="gift-composer-header">
        <div>
          <p className="gift-composer-eyebrow">KAPRUKA CONCIERGE · PHASE 2</p>
          <h1>Intelligent Gift Composer</h1>
          <p className="gift-composer-intro">
            Build one personalized gift plan with a swappable product card for each requested category.
            Browse alternatives on each card before adding the selected items to your cart.
          </p>
        </div>
        <span className="gift-composer-readonly"><span aria-hidden="true">◇</span> Preview only</span>
      </header>

      <section className="gift-composer-panel">
        <h2>Tell us about the gift</h2>
        <p className="gift-composer-panel-intro">
          Use a saved recipient name or relationship such as “mother” or “Amila”.
          Enter comma-separated product categories to receive one card per category. If you leave this blank,
          the planner suggests categories based on the occasion and eligible saved recipient preferences.
          Saved allergies and dislikes remain exclusions.
        </p>
        <form className="gift-composer-form" onSubmit={submit}>
          <label>
            Recipient <span className="required">*</span>
            <input value={recipient} onChange={(event) => setRecipient(event.target.value)} placeholder="e.g. mother or Amila" maxLength={100} required />
          </label>
          <label>
            Occasion <span className="required">*</span>
            <input value={occasion} onChange={(event) => setOccasion(event.target.value)} placeholder="e.g. birthday, anniversary" maxLength={100} required />
          </label>
          <div className="gift-composer-budget-field">
            <label className="gift-composer-checkbox-label">
              <input type="checkbox" checked={hasBudget} onChange={(event) => setHasBudget(event.target.checked)} />
              Set a total budget (LKR)
            </label>
            <input type="number" min="1" step="1" value={budget} disabled={!hasBudget} required={hasBudget} onChange={(event) => setBudget(Number(event.target.value))} aria-label="Total budget in LKR" />
          </div>
          <label>
            Recipient preferences <span className="optional">(optional, comma-separated)</span>
            <input value={preferences} onChange={(event) => setPreferences(event.target.value)} placeholder="e.g. cake, flowers, chocolate, snacks" maxLength={500} />
          </label>
          <label className="gift-composer-ignore-profile-label">
            <input
              id="gift-composer-ignore-profile-preferences"
              type="checkbox"
              checked={ignoreSavedPreferences}
              onChange={(event) => setIgnoreSavedPreferences(event.target.checked)}
            />
            Ignore saved recipient likes (use occasion-based suggestions if no categories are entered)
          </label>
          <p className="gift-composer-preference-help">
            Saved allergies and dislikes still apply even when saved likes are ignored.
          </p>
          <label>
            Avoid these items <span className="optional">(optional)</span>
            <input value={avoid} onChange={(event) => setAvoid(event.target.value)} placeholder="e.g. perfume, very sweet gifts" maxLength={300} />
          </label>
          <label>
            Allergies / dietary exclusions <span className="optional">(optional, comma-separated)</span>
            <input value={allergies} onChange={(event) => setAllergies(event.target.value)} placeholder="e.g. peanuts, chocolate" maxLength={300} />
          </label>
          <div className="gift-composer-delivery-fields">
            <label>
              Delivery city <span className="optional">(optional)</span>
              <input value={deliveryCity} onChange={(event) => setDeliveryCity(event.target.value)} placeholder="e.g. Ahungalla" maxLength={100} />
            </label>
            <label>
              Intended delivery date <span className="optional">(optional)</span>
              <input type="date" value={deliveryDate} onChange={(event) => setDeliveryDate(event.target.value)} />
            </label>
          </div>
          <p className="gift-composer-delivery-help">To check delivery feasibility, provide both a city and a date. A check is not a reservation or delivery guarantee.</p>
          {error && <p className="gift-composer-error" role="alert">{error}</p>}
          <button className="gift-composer-submit" type="submit" disabled={loading}>
            {loading ? "Preparing gift preview…" : "Build gift preview"}
          </button>
        </form>
      </section>

      {result && (
        <section className="gift-composer-results" aria-live="polite">
          <div className="gift-composer-results-heading">
            <div>
              <p className="gift-composer-eyebrow">YOUR GIFT PLAN</p>
              <h2>Ideas for {result.recipient}</h2>
              <p>{result.message}</p>
            </div>
            {result.budget_lkr != null && (
              <div className="gift-composer-total-budget">
                <span>Total budget</span>
                <strong>{money(result.budget_lkr)}</strong>
              </div>
            )}
          </div>
          <div className="gift-composer-safety-note">
            These are previews, not reservations or orders. Use “Add all items to cart” to add a bundle together; you can still review the cart before checkout. Basket totals are product subtotals; delivery charges are not included unless explicitly supplied by Kapruka.
          </div>

          {result.bundles.length > 0 && (
            <div className="gift-composer-bundles">
              {result.bundles.slice(0, 1).map((bundle) => (
                <article className="gift-composer-bundle" key={bundle.id}>
                  <header className="gift-composer-bundle-header">
                    <div>
                      <h3>{bundle.title}</h3>
                      <p>{bundle.description}</p>
                    </div>
                    <div className="gift-composer-bundle-total">
                      <span>Product total</span>
                      <strong>{money(bundle.total, bundle.currency)}</strong>
                    </div>
                  </header>
                  <div className="gift-composer-bundle-status">
                    <DeliveryStatusPill status={bundle.delivery_status} />
                    {bundle.delivery_message && <span>{bundle.delivery_message}</span>}
                  </div>
                  <div className="gift-composer-products">
                    {bundle.products.map((product, slotIndex) => (
                      <ProductPreview
                        key={`${bundle.id}:${slotIndex}`}
                        product={product}
                        selectedOptionIndex={selectedOptionIndexes[`${bundle.id}:${slotIndex}`] ?? 0}
                        onSelectOption={(option, optionIndex) => handleSelectBundleOption(
                          bundle.id,
                          slotIndex,
                          option,
                          optionIndex,
                        )}
                      />
                    ))}
                  </div>
                  <div className="gift-composer-bundle-actions">
                    <button
                      type="button"
                      className="gift-composer-add-bundle"
                      onClick={() => void handleAddBundleToCart(bundle)}
                      disabled={Boolean(addingBundleId) || bundle.products.length === 0 || bundle.products.some((product) => product.in_stock === false)}
                    >
                      {addingBundleId === bundle.id
                        ? "Adding bundle…"
                        : `Add all ${bundle.products.length} items to cart`}
                    </button>
                    {cartFeedback?.bundleId === bundle.id && (
                      <p
                        className={`gift-composer-cart-feedback ${cartFeedback.kind}`}
                        role={cartFeedback.kind === "error" ? "alert" : "status"}
                      >
                        {cartFeedback.message}
                      </p>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}

          {result.bundles.length === 0 && result.standalone_options.length === 0 && (
            <div className="gift-composer-empty">
              <h3>No gift options could be assembled</h3>
              <p>Try broadening preferences, increasing your budget, or reviewing the avoid/allergy terms. No cart changes were made.</p>
            </div>
          )}
        </section>
      )}
    </main>
  );
}
