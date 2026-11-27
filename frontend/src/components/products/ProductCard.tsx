import { useEffect, useRef, useState } from "react";

import { getProductDetail, type ProductDetailResponse } from "../../api/products";
import { mutateCart } from "../../api/cart";
import { useCartStore } from "../../store/cartStore";
import { buildRecommendationExplanation } from "./recommendationInsights";

interface ProductCardProps {
  productId: string;
  name: string;
  price: number;
  currency?: string;
  imageUrl?: string | null;
  images?: string[] | null;
  productUrl?: string | null;
  inStock?: boolean | null;
  category?: string | null;
  description?: string | null;
  requestContext?: string;
  isCompared?: boolean;
  compareDisabled?: boolean;
  onToggleCompare?: () => void;
}

function formatPrice(price: number, currency: string): string {
  return `${currency} ${price.toLocaleString()}`;
}

function formatDetailValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string" || typeof value === "number") return String(value).trim();
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.map(formatDetailValue).filter(Boolean).join(", ");
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, entry]) => {
        const formatted = formatDetailValue(entry);
        return formatted ? `${key.replace(/_/g, " ")}: ${formatted}` : "";
      })
      .filter(Boolean)
      .join(" · ");
  }
  return "";
}

export function ProductCard({
  productId,
  name,
  price,
  currency = "LKR",
  imageUrl,
  images,
  productUrl,
  inStock,
  category,
  description,
  requestContext = "",
  isCompared = false,
  compareDisabled = false,
  onToggleCompare,
}: ProductCardProps) {
  const [imageFailed, setImageFailed] = useState(false);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [detailsOpen, setDetailsOpen] = useState(false);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [details, setDetails] = useState<ProductDetailResponse | null>(null);
  const [modalImageFailed, setModalImageFailed] = useState(false);
  const [whyOpen, setWhyOpen] = useState(false);
  const detailsRequestRef = useRef<Promise<ProductDetailResponse | null> | null>(null);

  const { setCart, openDrawer } = useCartStore();
  const image = imageUrl || images?.find(Boolean) || null;
  const detailImage = details?.image_url || details?.images?.find(Boolean) || image;
  const detailUrl = details?.url || productUrl;
  const detailName = details?.name || name;
  const detailCurrency = details?.currency || currency;
  const detailPrice = details?.price ?? price;
  const detailInStock = details?.in_stock ?? inStock;
  const detailDescription = details?.description || description || null;
  const shippingText = formatDetailValue(details?.shipping);
  const variantItems = details?.variants || [];

  const loadProductDetails = async (): Promise<ProductDetailResponse | null> => {
    if (details) return details;
    if (detailsRequestRef.current) return detailsRequestRef.current;

    setDetailsLoading(true);
    setDetailsError(null);

    const request = getProductDetail(productId)
      .then((productDetails) => {
        setDetails(productDetails);
        return productDetails;
      })
      .catch((err: unknown) => {
        setDetailsError(
          err instanceof Error
            ? err.message
            : "Could not load the full Kapruka product details.",
        );
        return null;
      })
      .finally(() => {
        detailsRequestRef.current = null;
        setDetailsLoading(false);
      });

    detailsRequestRef.current = request;
    return request;
  };

  useEffect(() => {
    if (!detailsOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDetailsOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [detailsOpen]);

  const handleView = async () => {
    setDetailsOpen(true);
    setDetailsError(null);
    await loadProductDetails();
  };

  const handleWhyThis = async () => {
    const nextOpen = !whyOpen;
    setWhyOpen(nextOpen);
    if (nextOpen) {
      setDetailsError(null);
      await loadProductDetails();
    }
  };

  const handleAddToCart = async () => {
    if (adding || inStock === false || details?.in_stock === false) return;
    setAdding(true);
    setAdded(false);
    setError(null);

    try {
      const cart = await mutateCart("add", productId, 1);
      setCart(cart);
      setAdded(true);
      setDetailsOpen(false);
      openDrawer();
    } catch (err) {
      const message = err instanceof Error
        ? err.message
        : "Could not add this product to your cart.";
      setError(message);
      if (detailsOpen) setDetailsError(message);
    } finally {
      setAdding(false);
    }
  };

  const explanation = buildRecommendationExplanation(requestContext, {
    name: detailName,
    price: detailPrice,
    currency: detailCurrency,
    description: detailDescription,
    category: details?.category || category,
    vendor: details?.vendor,
    inStock: detailInStock,
  });

  return (
    <>
      <div className="product-card">
        <div className="product-card-media">
          {image && !imageFailed ? (
            <img
              src={image}
              alt={name}
              loading="lazy"
              decoding="async"
              onError={() => setImageFailed(true)}
            />
          ) : (
            <div className="product-card-placeholder" aria-hidden="true"><span>🛍️</span></div>
          )}
          {inStock === false && <span className="product-card-stock out">Out of stock</span>}
          {inStock !== false && inStock != null && <span className="product-card-stock">In stock</span>}
        </div>

        <div className="product-card-body">
          <div className="product-card-name" title={name}>{name}</div>
          <div className="product-card-price">{formatPrice(price, currency)}</div>

          <div className="product-card-actions">
            <button
              type="button"
              className="product-card-view-button"
              onClick={handleView}
              aria-label={`View details for ${name}`}
            >
              View
            </button>
            <button
              type="button"
              className="product-card-add-button"
              onClick={handleAddToCart}
              disabled={adding || inStock === false}
            >
              {adding ? "Adding…" : added ? "Added ✓" : "Add to cart"}
            </button>
          </div>

          <div className="product-card-insight-actions">
            <button
              type="button"
              className="product-card-insight-button"
              onClick={handleWhyThis}
              aria-expanded={whyOpen}
              aria-controls={`why-product-${productId}`}
              disabled={detailsLoading}
            >
              {detailsLoading && whyOpen ? "Checking details…" : whyOpen ? "Hide why" : "Why this?"}
            </button>
            {onToggleCompare && (
              <button
                type="button"
                className={`product-card-insight-button ${isCompared ? "selected" : ""}`}
                onClick={onToggleCompare}
                aria-pressed={isCompared}
                disabled={compareDisabled}
              >
                {isCompared ? "✓ Comparing" : "Compare"}
              </button>
            )}
          </div>

          {whyOpen && (
            <div className="product-why-panel" id={`why-product-${productId}`} aria-live="polite">
              <div className="product-why-title">Why this recommendation?</div>
              {detailsLoading && <p className="product-why-muted">Checking the product details from Kapruka…</p>}
              {detailsError && <p className="product-why-muted">Full details could not be loaded. This explanation uses the information already available.</p>}
              <ul>
                {explanation.matchedRequestDetails.map((item, index) => <li key={`match-${index}`}>{item}</li>)}
                {explanation.priceContext && <li>{explanation.priceContext}</li>}
                {explanation.stockContext && <li>{explanation.stockContext}</li>}
                {explanation.fallback && <li>{explanation.fallback}</li>}
              </ul>
              <p className="product-why-footnote">{explanation.evidenceNote}</p>
            </div>
          )}

          {error && <div className="product-card-error">{error}</div>}
        </div>
      </div>

      {detailsOpen && (
        <div
          className="product-modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setDetailsOpen(false);
          }}
        >
          <section
            className="product-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby={`product-modal-title-${productId}`}
          >
            <button
              type="button"
              className="product-modal-close"
              aria-label="Close product details"
              onClick={() => setDetailsOpen(false)}
            >×</button>

            <div className="product-modal-layout">
              <div className="product-modal-media">
                {detailImage && !modalImageFailed ? (
                  <img src={detailImage} alt={detailName} onError={() => setModalImageFailed(true)} />
                ) : (
                  <div className="product-card-placeholder" aria-hidden="true"><span>🛍️</span></div>
                )}
              </div>

              <div className="product-modal-content">
                <h2 id={`product-modal-title-${productId}`}>{detailName}</h2>
                <div className="product-modal-price">{formatPrice(detailPrice, detailCurrency)}</div>
                <div className={`product-modal-stock ${detailInStock === false ? "out" : ""}`}>
                  {details?.stock_note || (detailInStock === false ? "Out of stock" : detailInStock === true ? "In stock" : "Stock status not specified")}
                </div>
                {detailsLoading && <p className="product-modal-state">Loading full product details from Kapruka…</p>}
                {detailsError && <p className="product-modal-error" role="alert">{detailsError}</p>}

                {detailDescription ? (
                  <div className="product-modal-section">
                    <h3>Description</h3>
                    <p className="product-modal-description">{detailDescription}</p>
                  </div>
                ) : !detailsLoading && details && (
                  <div className="product-modal-section">
                    <h3>Description</h3>
                    <p className="product-modal-description product-modal-muted">Kapruka did not provide a product description for this item.</p>
                  </div>
                )}

                <dl className="product-modal-metadata">
                  {details?.category && <div><dt>Category</dt><dd>{details.category}</dd></div>}
                  {details?.vendor && <div><dt>Vendor</dt><dd>{details.vendor}</dd></div>}
                  {details?.weight && <div><dt>Weight</dt><dd>{details.weight}</dd></div>}
                  {details?.product_id && <div><dt>Product ID</dt><dd>{details.product_id}</dd></div>}
                  {details?.ships_internationally != null && <div><dt>International shipping</dt><dd>{details.ships_internationally ? "Available" : "Not available"}</dd></div>}
                  {shippingText && <div><dt>Shipping details</dt><dd>{shippingText}</dd></div>}
                </dl>

                {variantItems.length > 0 && (
                  <div className="product-modal-section">
                    <h3>Options and variants</h3>
                    <ul className="product-modal-variants">
                      {variantItems.map((variant, index) => (
                        <li key={`${productId}-variant-${index}`}>{formatDetailValue(variant) || `Option ${index + 1}`}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="product-modal-actions">
                  {detailUrl ? (
                    <a className="product-modal-kapruka-link" href={detailUrl} target="_blank" rel="noreferrer">View on Kapruka ↗</a>
                  ) : (
                    <span className="product-modal-muted">Kapruka product link unavailable</span>
                  )}
                  <button
                    type="button"
                    className="product-card-add-button product-modal-add-button"
                    onClick={handleAddToCart}
                    disabled={adding || details?.in_stock === false || inStock === false}
                  >
                    {adding ? "Adding…" : added ? "Added ✓" : "Add to cart"}
                  </button>
                </div>
              </div>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
