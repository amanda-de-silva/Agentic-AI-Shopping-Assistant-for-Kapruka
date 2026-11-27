/**
 * Responsive product grid with optional, user-controlled product comparison.
 * Product selection/ranking remains upstream and is not modified here.
 */
import { useEffect, useMemo, useState } from "react";
import { getProductDetail, type ProductDetailResponse } from "../../api/products";
import { ProductCard } from "./ProductCard";

export interface ProductGridItem {
  product_id: string;
  name: string;
  price: number;
  currency?: string;
  image_url?: string | null;
  images?: string[] | null;
  url?: string | null;
  in_stock?: boolean | null;
  description?: string | null;
  category?: string | null;
  vendor?: string | null;
  weight?: string | null;
  shipping?: Record<string, unknown> | string | null;
}

const MAX_COMPARE_PRODUCTS = 4;

function hasImage(product: ProductGridItem): boolean {
  return Boolean(product.image_url || product.images?.some(Boolean));
}

function formatPrice(price: number, currency = "LKR"): string {
  return `${currency} ${price.toLocaleString()}`;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not provided";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.map(displayValue).filter((v) => v !== "Not provided").join(", ") || "Not provided";
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .map(([key, entry]) => {
        const formatted = displayValue(entry);
        return formatted === "Not provided" ? "" : `${key.replace(/_/g, " ")}: ${formatted}`;
      })
      .filter(Boolean);
    return entries.join(" · ") || "Not provided";
  }
  return "Not provided";
}

export function ProductGrid({
  products,
  requestContext = "",
}: {
  products: ProductGridItem[];
  requestContext?: string;
}) {
  const [enriched, setEnriched] = useState<Record<string, Partial<ProductGridItem>>>({});
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [compareNotice, setCompareNotice] = useState<string | null>(null);
  const [comparisonDetails, setComparisonDetails] = useState<Record<string, ProductDetailResponse>>({});

  useEffect(() => {
    let cancelled = false;
    const missing = products.filter((product) => product.product_id && !hasImage(product));

    if (!missing.length) return () => { cancelled = true; };

    void Promise.all(missing.map(async (product) => {
      try {
        const detail = await getProductDetail(product.product_id);
        return [product.product_id, {
          image_url: detail.image_url,
          images: detail.images,
          url: detail.url || product.url,
          description: detail.description,
          category: detail.category || product.category,
          vendor: detail.vendor,
          weight: detail.weight,
          shipping: detail.shipping,
          in_stock: detail.in_stock ?? product.in_stock,
        }] as const;
      } catch {
        return null;
      }
    })).then((entries) => {
      if (cancelled) return;
      setEnriched((current) => {
        const next = { ...current };
        for (const entry of entries) if (entry) next[entry[0]] = { ...next[entry[0]], ...entry[1] };
        return next;
      });
    });

    return () => { cancelled = true; };
  }, [products]);

  const mergedProducts = useMemo(
    () => products.map((product) => ({ ...product, ...(enriched[product.product_id] || {}) })),
    [products, enriched],
  );
  const selectedProducts = mergedProducts.filter((product) => compareIds.includes(product.product_id));

  useEffect(() => {
    if (!compareOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setCompareOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [compareOpen]);

  const toggleCompare = (product: ProductGridItem) => {
    setCompareNotice(null);

    if (compareIds.includes(product.product_id)) {
      setCompareIds(compareIds.filter((id) => id !== product.product_id));
      return;
    }

    if (compareIds.length >= MAX_COMPARE_PRODUCTS) {
      setCompareNotice(`Compare up to ${MAX_COMPARE_PRODUCTS} products at a time. Remove one to add another.`);
      return;
    }

    setCompareIds([...compareIds, product.product_id]);
  };

  const openComparison = async () => {
    if (selectedProducts.length < 2) return;
    setCompareOpen(true);
    setCompareError(null);

    const missing = selectedProducts.filter((product) => !comparisonDetails[product.product_id]);
    if (!missing.length) return;

    setCompareLoading(true);
    const entries = await Promise.all(missing.map(async (product) => {
      try {
        return [product.product_id, await getProductDetail(product.product_id)] as const;
      } catch {
        return null;
      }
    }));

    const successful = entries.filter((entry): entry is readonly [string, ProductDetailResponse] => entry !== null);
    setComparisonDetails((current) => {
      const next = { ...current };
      for (const [id, detail] of successful) next[id] = detail;
      return next;
    });
    if (successful.length < missing.length) {
      setCompareError("Some product details could not be loaded. The comparison still shows the information already available.");
    }
    setCompareLoading(false);
  };

  if (!products.length) return null;

  return (
    <>
      <div className="product-grid" aria-label="Recommended products">
        {mergedProducts.map((product) => {
          const isCompared = compareIds.includes(product.product_id);
          return (
            <ProductCard
              key={product.product_id}
              productId={product.product_id}
              name={product.name}
              price={product.price}
              currency={product.currency}
              imageUrl={product.image_url}
              images={product.images}
              productUrl={product.url}
              inStock={product.in_stock}
              category={product.category}
              description={product.description}
              requestContext={requestContext}
              isCompared={isCompared}
              compareDisabled={!isCompared && compareIds.length >= MAX_COMPARE_PRODUCTS}
              onToggleCompare={() => toggleCompare(product)}
            />
          );
        })}
      </div>

      {compareIds.length > 0 && (
        <div className="product-compare-tray" aria-live="polite">
          <div className="product-compare-tray-copy">
            <strong>{compareIds.length} of {MAX_COMPARE_PRODUCTS} selected</strong>
            <span>{compareIds.length < 2 ? "Select at least two products to compare." : "Compare price, description, availability and listing details."}</span>
            {compareNotice && <span className="product-compare-notice">{compareNotice}</span>}
          </div>
          <div className="product-compare-tray-actions">
            <button type="button" className="product-card-view-button" onClick={() => { setCompareIds([]); setCompareNotice(null); }}>
              Clear
            </button>
            <button type="button" className="product-card-add-button" onClick={openComparison} disabled={compareIds.length < 2}>
              Compare products
            </button>
          </div>
        </div>
      )}

      {compareOpen && (
        <div
          className="product-modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setCompareOpen(false);
          }}
        >
          <section className="product-comparison-modal" role="dialog" aria-modal="true" aria-labelledby="product-comparison-title">
            <div className="product-comparison-header">
              <div>
                <h2 id="product-comparison-title">Compare products</h2>
                <p>Only information available from the Kapruka listing is shown.</p>
              </div>
              <button type="button" className="product-modal-close comparison-close" aria-label="Close comparison" onClick={() => setCompareOpen(false)}>×</button>
            </div>

            {compareLoading && <p className="product-modal-state">Loading current Kapruka product details…</p>}
            {compareError && <p className="product-modal-muted" role="status">{compareError}</p>}

            <div className="product-comparison-scroll">
              <table className="product-comparison-table">
                <thead>
                  <tr>
                    <th scope="col">Detail</th>
                    {selectedProducts.map((product) => {
                      const detail = comparisonDetails[product.product_id];
                      return (
                        <th scope="col" key={product.product_id}>
                          <div className="comparison-product-heading">
                            {(detail?.image_url || detail?.images?.find(Boolean) || product.image_url || product.images?.find(Boolean)) && (
                              <img src={detail?.image_url || detail?.images?.find(Boolean) || product.image_url || product.images?.find(Boolean) || undefined} alt="" />
                            )}
                            <strong>{detail?.name || product.name}</strong>
                            <span>{formatPrice(detail?.price ?? product.price, detail?.currency || product.currency || "LKR")}</span>
                            {((detail?.url || product.url) && (
                              <a href={detail?.url || product.url || undefined} target="_blank" rel="noreferrer">View on Kapruka ↗</a>
                            ))}
                          </div>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th scope="row">Availability</th>
                    {selectedProducts.map((product) => {
                      const available = comparisonDetails[product.product_id]?.in_stock ?? product.in_stock;
                      return <td key={product.product_id}>{available === true ? "In stock" : available === false ? "Out of stock" : "Not specified"}</td>;
                    })}
                  </tr>
                  <tr>
                    <th scope="row">Category</th>
                    {selectedProducts.map((product) => <td key={product.product_id}>{displayValue(comparisonDetails[product.product_id]?.category || product.category)}</td>)}
                  </tr>
                  <tr>
                    <th scope="row">Description</th>
                    {selectedProducts.map((product) => <td key={product.product_id} className="comparison-description">{displayValue(comparisonDetails[product.product_id]?.description || product.description)}</td>)}
                  </tr>
                  <tr>
                    <th scope="row">Vendor</th>
                    {selectedProducts.map((product) => <td key={product.product_id}>{displayValue(comparisonDetails[product.product_id]?.vendor || product.vendor)}</td>)}
                  </tr>
                  <tr>
                    <th scope="row">Weight</th>
                    {selectedProducts.map((product) => <td key={product.product_id}>{displayValue(comparisonDetails[product.product_id]?.weight || product.weight)}</td>)}
                  </tr>
                  <tr>
                    <th scope="row">Shipping details</th>
                    {selectedProducts.map((product) => <td key={product.product_id}>{displayValue(comparisonDetails[product.product_id]?.shipping || product.shipping)}</td>)}
                  </tr>
                  <tr>
                    <th scope="row">Action</th>
                    {selectedProducts.map((product) => (
                      <td key={product.product_id}>
                        <button type="button" className="product-compare-remove" onClick={() => setCompareIds((current) => current.filter((id) => id !== product.product_id))}>Remove</button>
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
            <div className="product-comparison-footer">
              <span>Specifications not provided by Kapruka are labelled as unavailable.</span>
              <button type="button" className="product-card-add-button" onClick={() => setCompareOpen(false)}>Done</button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
