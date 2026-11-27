import {
  FormEvent,
  useState,
} from "react";

import { loginWithPhone } from "../api/auth";
import { useAuthStore } from "../store/authStore";
import {
  setSessionId,
} from "../store/sessionStore";


export function LoginPage() {
  const [phone, setPhone] =
    useState("");

  const [error, setError] =
    useState("");

  const [loading, setLoading] =
    useState(false);

  const signIn =
    useAuthStore(
      (state) => state.signIn
    );


  const submit = async (
    event: FormEvent
  ) => {
    event.preventDefault();

    const trimmed =
      phone.trim();

    if (!trimmed || loading) {
      return;
    }

    setLoading(true);
    setError("");

    try {
      const result =
        await loginWithPhone(
          trimmed
        );

      /*
       * Keep the authenticated session ID
       * in frontend runtime state.
       *
       * Do NOT persist this in localStorage.
       * The backend also provides the HttpOnly
       * authentication cookie.
       */
      setSessionId(
        result.session_id
      );

      /*
       * Store the normalized phone number
       * for the currently authenticated user.
       */
      signIn(
        result.phone
      );

    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not sign in. Please try again."
      );

    } finally {
      setLoading(false);
    }
  };


  return (
    <div className="login-screen">
      <form
        onSubmit={submit}
        className="login-card"
      >
        <div className="login-mark" aria-hidden="true">
          🛍️
        </div>

        <h1>
          Kapruka Concierge
        </h1>

        <p className="login-subtitle">
          Enter your phone number to
          continue. Your shopping memory,
          preferences and cart are kept
          separate for this phone number.
        </p>

        <label htmlFor="login-phone">
          Phone number
        </label>

        <input
          id="login-phone"
          type="tel"
          value={phone}
          onChange={(event) =>
            setPhone(
              event.target.value
            )
          }
          placeholder="0712345678 or +94712345678"
          autoComplete="tel"
          autoFocus
          disabled={loading}
        />

        {error && (
          <div className="form-error">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={
            loading ||
            !phone.trim()
          }
        >
          {loading
            ? "Signing in…"
            : "Continue"}
        </button>

        <p className="login-note">
          This is phone-number
          identification, not OTP
          verification. Add SMS OTP
          before production use if the
          phone number itself must be
          proof of ownership.
        </p>
      </form>
    </div>
  );
}
