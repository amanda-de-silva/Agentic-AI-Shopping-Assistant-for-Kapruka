/** Gift/self checkout UI with Kapruka-aware automatic delivery date resolution. */
import {
  useEffect,
  useRef,
  useState,
} from "react";

import {
  createCheckout,
  getDeliveryOptions,
  type CheckoutMode,
} from "../../api/orders";

import { useCart } from "../../hooks/useCart";


function todayIsoDate(): string {
  const now =
    new Date();

  const local =
    new Date(
      now.getTime() -
        now.getTimezoneOffset() * 60000
    );

  return local
    .toISOString()
    .slice(0, 10);
}


function formatDeliveryDate(
  isoDate: string
): string {
  const date =
    new Date(
      `${isoDate}T00:00:00`
    );

  if (
    Number.isNaN(
      date.getTime()
    )
  ) {
    return isoDate;
  }

  return date.toLocaleDateString(
    undefined,
    {
      year: "numeric",
      month: "short",
      day: "numeric",
    }
  );
}


export function CheckoutSummary() {
  const { cart } =
    useCart();

  const [mode, setMode] =
    useState<CheckoutMode>(
      "gift"
    );

  const [contactName, setContactName] =
    useState("");

  const [contactPhone, setContactPhone] =
    useState("");

  const [city, setCity] =
    useState("");

  const [address, setAddress] =
    useState("");

  const [deliveryDate, setDeliveryDate] =
    useState("");

  const [earliestDate, setEarliestDate] =
    useState<string | null>(
      null
    );

  const [canonicalCity, setCanonicalCity] =
    useState<string | null>(
      null
    );

  const [
    deliveryLoading,
    setDeliveryLoading,
  ] = useState(false);

  const [deliveryError, setDeliveryError] =
    useState<string | null>(
      null
    );

  const [senderName, setSenderName] =
    useState("");

  const [anonymousSender, setAnonymousSender] =
    useState(false);

  const [giftMessage, setGiftMessage] =
    useState("");

  const [loading, setLoading] =
    useState(false);

  const [error, setError] =
    useState<string | null>(
      null
    );

  const idempotencyKey =
    useRef(
      globalThis.crypto.randomUUID()
    );

  const isGift =
    mode === "gift";


  const productFingerprint =
    cart?.items
      .map(
        (item) =>
          `${item.product_id}:${item.quantity}`
      )
      .sort()
      .join("|") || "";


  const clearError = () => {
    setError(null);
  };


  useEffect(() => {
    let active = true;

    setEarliestDate(
      null
    );

    setCanonicalCity(
      null
    );

    setDeliveryError(
      null
    );

    const trimmedCity =
      city.trim();

    if (
      !cart ||
      cart.items.length === 0 ||
      trimmedCity.length < 2
    ) {
      setDeliveryLoading(
        false
      );

      setDeliveryDate("");

      return;
    }

    setDeliveryLoading(
      true
    );

    /*
     * Debounce city typing so we don't call Kapruka for every character.
     */
    const timeoutId =
      window.setTimeout(
        async () => {
          try {
            const result =
              await getDeliveryOptions(
                trimmedCity
              );

            if (!active) {
              return;
            }

            setCanonicalCity(
              result.canonical_city
            );

            setEarliestDate(
              result.earliest_date
            );

            if (
              result.earliest_date
            ) {
              /*
               * Reset the selected date to the newly confirmed earliest date
               * whenever the city/cart changes.
               */
              setDeliveryDate(
                result.earliest_date
              );
            } else {
              setDeliveryDate(
                ""
              );
            }

            setDeliveryError(
              result.message
            );

          } catch (err) {
            if (!active) {
              return;
            }

            setEarliestDate(
              null
            );

            setCanonicalCity(
              null
            );

            setDeliveryDate(
              ""
            );

            setDeliveryError(
              err instanceof Error
                ? err.message
                : "Could not determine the delivery date."
            );

          } finally {
            if (active) {
              setDeliveryLoading(
                false
              );
            }
          }
        },
        700
      );

    return () => {
      active = false;

      window.clearTimeout(
        timeoutId
      );
    };
  }, [
    city,
    productFingerprint,
  ]);


  const submit =
    async () => {
      if (
        !contactName.trim()
      ) {
        setError(
          isGift
            ? "Recipient name is required."
            : "Your name is required."
        );

        return;
      }

      if (
        !contactPhone.trim()
      ) {
        setError(
          isGift
            ? "Recipient phone number is required."
            : "Your phone number is required."
        );

        return;
      }

      if (
        !city.trim()
      ) {
        setError(
          "Delivery city is required."
        );

        return;
      }

      if (
        !address.trim()
      ) {
        setError(
          "Delivery address is required."
        );

        return;
      }

      if (
        deliveryLoading
      ) {
        setError(
          "Please wait while Kapruka checks the earliest delivery date."
        );

        return;
      }

      if (
        !earliestDate ||
        !deliveryDate
      ) {
        setError(
          deliveryError ||
            "A valid Kapruka delivery date could not be confirmed."
        );

        return;
      }

      if (
        deliveryDate <
        earliestDate
      ) {
        setError(
          `The selected date is earlier than Kapruka's earliest confirmed delivery date of ${formatDeliveryDate(
            earliestDate
          )}.`
        );

        return;
      }

      if (
        !cart ||
        cart.items.length === 0
      ) {
        setError(
          "Your cart is empty."
        );

        return;
      }

      if (
        isGift &&
        !anonymousSender &&
        !senderName.trim()
      ) {
        setError(
          "Enter a sender name or choose anonymous sender."
        );

        return;
      }

      setLoading(
        true
      );

      setError(
        null
      );

      try {
        const result =
          await createCheckout(
            {
              mode,

              recipient: {
                name:
                  contactName.trim(),

                phone:
                  contactPhone.trim(),
              },

              delivery: {
                address:
                  address.trim(),

                city:
                  canonicalCity ||
                  city.trim(),

                date:
                  deliveryDate,
              },

              ...(isGift &&
              !anonymousSender &&
              senderName.trim()
                ? {
                    sender: {
                      name:
                        senderName.trim(),
                    },
                  }
                : {}),

              ...(isGift &&
              giftMessage.trim()
                ? {
                    gift_message:
                      giftMessage.trim(),
                  }
                : {}),

              currency:
                cart.currency ||
                "LKR",
            },

            idempotencyKey.current
          );

        window.location.assign(
          result.checkout_url
        );

      } catch (err) {

        setError(
          err instanceof Error
            ? err.message
            : "Checkout failed. Please try again."
        );

      } finally {

        setLoading(
          false
        );
      }
    };


  if (
    !cart ||
    cart.items.length === 0
  ) {
    return (
      <p>
        Your cart is empty —
        go find something first!
      </p>
    );
  }


  const minDeliveryDate =
    earliestDate ||
    todayIsoDate();


  return (
    <div>
      <h2>
        Checkout
      </h2>

      {/* ====================================================== */}
      {/* Gift / Self selector                                  */}
      {/* ====================================================== */}

      <div
        role="radiogroup"
        aria-label="Checkout type"
        style={{
          display: "grid",

          gridTemplateColumns:
            "repeat(2, minmax(0, 1fr))",

          gap: 8,

          marginBottom: 20,

          padding: 6,

          borderRadius: 10,

          background:
            "rgba(76, 50, 126, 0.22)",
        }}
      >
        <button
          type="button"
          role="radio"
          aria-checked={
            isGift
          }
          onClick={() => {
            setMode("gift");

            clearError();
          }}
          disabled={
            loading
          }
          style={{
            border:
              isGift
                ? "1px solid #f4c400"
                : "1px solid transparent",

            borderRadius: 8,

            padding:
              "12px 10px",

            background:
              isGift
                ? "rgba(255, 194, 0, 0.14)"
                : "transparent",

            color:
              "inherit",

            cursor:
              loading
                ? "not-allowed"
                : "pointer",

            fontWeight: 700,
          }}
        >
          🎁 It's a gift
        </button>


        <button
          type="button"
          role="radio"
          aria-checked={
            !isGift
          }
          onClick={() => {
            setMode("self");

            clearError();
          }}
          disabled={
            loading
          }
          style={{
            border:
              !isGift
                ? "1px solid #f4c400"
                : "1px solid transparent",

            borderRadius: 8,

            padding:
              "12px 10px",

            background:
              !isGift
                ? "rgba(255, 194, 0, 0.14)"
                : "transparent",

            color:
              "inherit",

            cursor:
              loading
                ? "not-allowed"
                : "pointer",

            fontWeight: 700,
          }}
        >
          👤 It's for me
        </button>
      </div>


      {/* ====================================================== */}
      {/* Recipient / buyer                                      */}
      {/* ====================================================== */}

      <section
        style={{
          marginBottom: 20,
        }}
      >
        <h3>
          {
            isGift
              ? "Recipient Information"
              : "Your Information"
          }
        </h3>

        <div
          style={{
            display: "grid",

            gridTemplateColumns:
              "repeat(2, minmax(0, 1fr))",

            gap: 12,
          }}
        >
          <div
            style={{
              display: "flex",
              flexDirection:
                "column",

              gap: 6,
            }}
          >
            <label htmlFor="checkout-name">
              {
                isGift
                  ? "Recipient name"
                  : "My name"
              }
            </label>

            <input
              id="checkout-name"
              value={
                contactName
              }
              placeholder={
                isGift
                  ? "Recipient name"
                  : "Your name"
              }
              onChange={
                (event) => {
                  setContactName(
                    event.target.value
                  );

                  clearError();
                }
              }
              disabled={
                loading
              }
              autoComplete="name"
            />
          </div>


          <div
            style={{
              display: "flex",
              flexDirection:
                "column",

              gap: 6,
            }}
          >
            <label htmlFor="checkout-phone">
              {
                isGift
                  ? "Recipient phone"
                  : "My phone"
              }
            </label>

            <input
              id="checkout-phone"
              type="tel"
              value={
                contactPhone
              }
              placeholder="Phone number"
              onChange={
                (event) => {
                  setContactPhone(
                    event.target.value
                  );

                  clearError();
                }
              }
              disabled={
                loading
              }
              autoComplete="tel"
            />
          </div>
        </div>
      </section>


      {/* ====================================================== */}
      {/* Delivery                                               */}
      {/* ====================================================== */}

      <section
        style={{
          marginBottom: 20,
        }}
      >
        <h3>
          Delivery Details
        </h3>

        <div
          style={{
            display: "flex",
            flexDirection:
              "column",

            gap: 12,
          }}
        >
          <div
            style={{
              display: "flex",
              flexDirection:
                "column",

              gap: 6,
            }}
          >
            <label htmlFor="checkout-address">
              Delivery address
            </label>

            <textarea
              id="checkout-address"
              value={
                address
              }
              placeholder="Delivery address"
              rows={3}
              onChange={
                (event) => {
                  setAddress(
                    event.target.value
                  );

                  clearError();
                }
              }
              disabled={
                loading
              }
              autoComplete="street-address"
            />
          </div>


          <div
            style={{
              display: "grid",

              gridTemplateColumns:
                "repeat(2, minmax(0, 1fr))",

              gap: 12,
            }}
          >
            <div
              style={{
                display: "flex",
                flexDirection:
                  "column",

                gap: 6,
              }}
            >
              <label htmlFor="checkout-city">
                Delivery city
              </label>

              <input
                id="checkout-city"
                value={
                  city
                }
                placeholder="Delivery city"
                onChange={
                  (event) => {
                    setCity(
                      event.target.value
                    );

                    clearError();
                  }
                }
                disabled={
                  loading
                }
                autoComplete="address-level2"
              />

              {canonicalCity && (
                <small>
                  Kapruka city:{" "}
                  <strong>
                    {
                      canonicalCity
                    }
                  </strong>
                </small>
              )}

              {deliveryLoading && (
                <small>
                  Checking Kapruka delivery
                  availability…
                </small>
              )}

              {!deliveryLoading &&
                earliestDate && (
                  <small>
                    Earliest available:{" "}
                    <strong>
                      {
                        formatDeliveryDate(
                          earliestDate
                        )
                      }
                    </strong>
                  </small>
                )}

              {deliveryError && (
                <small
                  role="alert"
                  style={{
                    color:
                      "var(--color-warning)",
                  }}
                >
                  {
                    deliveryError
                  }
                </small>
              )}
            </div>


            <div
              style={{
                display: "flex",

                flexDirection:
                  "column",

                gap: 6,
              }}
            >
              <label htmlFor="checkout-date">
                Delivery date
              </label>

              <input
                id="checkout-date"
                type="date"
                value={
                  deliveryDate
                }
                min={
                  minDeliveryDate
                }
                onChange={
                  (event) => {
                    setDeliveryDate(
                      event.target.value
                    );

                    clearError();
                  }
                }
                disabled={
                  loading ||
                  deliveryLoading ||
                  !earliestDate
                }
              />

              {earliestDate && (
                <small>
                  The date cannot be earlier
                  than Kapruka's confirmed
                  earliest delivery date.
                </small>
              )}
            </div>
          </div>
        </div>
      </section>


      {/* ====================================================== */}
      {/* Gift-only fields                                       */}
      {/* ====================================================== */}

      {isGift && (
        <section
          style={{
            marginBottom: 20,
          }}
        >
          <h3>
            Gift Details
          </h3>

          <div
            style={{
              display: "flex",

              flexDirection:
                "column",

              gap: 12,
            }}
          >
            <div
              style={{
                display: "flex",

                flexDirection:
                  "column",

                gap: 6,
              }}
            >
              <label htmlFor="checkout-sender">
                Sender name
              </label>

              <input
                id="checkout-sender"
                value={
                  senderName
                }
                placeholder="Your name"
                onChange={
                  (event) => {
                    setSenderName(
                      event.target.value
                    );

                    clearError();
                  }
                }
                disabled={
                  loading ||
                  anonymousSender
                }
                autoComplete="name"
              />
            </div>


            <label
              style={{
                display: "flex",

                alignItems:
                  "center",

                gap: 8,

                cursor:
                  loading
                    ? "not-allowed"
                    : "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={
                  anonymousSender
                }
                onChange={
                  (event) => {
                    setAnonymousSender(
                      event.target.checked
                    );

                    clearError();
                  }
                }
                disabled={
                  loading
                }
              />

              Keep sender anonymous
            </label>


            <div
              style={{
                display: "flex",

                flexDirection:
                  "column",

                gap: 6,
              }}
            >
              <label htmlFor="checkout-message">
                Personal message
                (optional)
              </label>

              <textarea
                id="checkout-message"
                value={
                  giftMessage
                }
                placeholder="Write a personal message"
                rows={4}
                onChange={
                  (event) => {
                    setGiftMessage(
                      event.target.value
                    );

                    clearError();
                  }
                }
                disabled={
                  loading
                }
              />
            </div>
          </div>
        </section>
      )}


      {/* ====================================================== */}
      {/* Cart summary                                           */}
      {/* ====================================================== */}

      <section>
        {cart.items.map(
          (item) => (
            <div
              key={
                item.product_id
              }
              className="cart-item-row"
            >
              <span>
                {
                  item.name
                }{" "}
                ×{" "}
                {
                  item.quantity
                }
              </span>

              <span>
                {
                  item.currency
                }{" "}
                {
                  item.line_total.toLocaleString()
                }
              </span>
            </div>
          )
        )}

        <div
          style={{
            display: "flex",

            justifyContent:
              "space-between",

            marginTop: 12,

            fontSize: 16,
          }}
        >
          <strong>
            Total
          </strong>

          <strong>
            {
              cart.currency
            }{" "}
            {
              cart.subtotal.toLocaleString()
            }
          </strong>
        </div>
      </section>


      {error && (
        <p
          role="alert"
          style={{
            color:
              "var(--color-warning)",

            marginTop: 14,
          }}
        >
          {
            error
          }
        </p>
      )}


      <button
        type="button"
        className="checkout-cta"
        onClick={
          submit
        }
        disabled={
          loading ||
          deliveryLoading ||
          !earliestDate ||
          !deliveryDate
        }
      >
        {
          loading
            ? "Creating Kapruka checkout…"
            : deliveryLoading
              ? "Checking delivery…"
              : "Pay with Kapruka"
        }
      </button>
    </div>
  );
}