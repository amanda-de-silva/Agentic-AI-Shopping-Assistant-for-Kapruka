/**
 * Runtime-only session id holder.
 *
 * Deliberately not persisted to localStorage/sessionStorage: the application must
 * ask for the user's phone number whenever the app is launched/reloaded.
 */

let sessionId: string | null = null;

export function getSessionId(): string | null {
  return sessionId;
}

export function setSessionId(id: string): void {
  sessionId = id;
}

export function clearSessionId(): void {
  sessionId = null;
}