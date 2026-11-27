import { apiFetch, BASE_URL } from "./client";
import { getSessionId, setSessionId } from "../store/sessionStore";

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

export interface ChatResponse {
  session_id: string;
  reply: string;
  ui_actions: UIAction[];
}

export async function sendChatMessage(message: string): Promise<ChatResponse> {
  const response = await apiFetch<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify({ session_id: getSessionId(), message }),
  });
  setSessionId(response.session_id);
  return response;
}

export async function streamChat(
  message: string,
  onToken: (token: string) => void,
  onDone: (final: ChatResponse) => void,
  onError: (err: Error) => void
): Promise<void> {
  try {
    const sessionId = getSessionId();
    const res = await fetch(`${BASE_URL}/chat/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(sessionId ? { "X-Session-Id": sessionId } : {}),
      },
      credentials: "include",
      body: JSON.stringify({ session_id: sessionId, message }),
    });
    const returnedSessionId = res.headers.get("X-Session-Id");
    if (returnedSessionId) setSessionId(returnedSessionId);
    if (!res.ok || !res.body) throw new Error(`Stream failed: ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let donePayload: ChatResponse | null = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split(/\r?\n\r?\n/);
      buffer = events.pop() || "";

      for (const raw of events) {
        const lines = raw.split(/\r?\n/);
        const eventType = lines.find((l) => l.startsWith("event:"))?.slice(6).trim();
        const dataLine = lines.find((l) => l.startsWith("data:"));
        if (!dataLine) continue;
        let data = dataLine.slice(5);
        if (data.startsWith(" ")) data = data.slice(1);

        if (eventType === "token") {
          onToken(data);
        } else if (eventType === "done") {
          donePayload = JSON.parse(data) as ChatResponse;
          setSessionId(donePayload.session_id);
        } else if (eventType === "error") {
          const parsed = JSON.parse(data) as { message?: string };
          onError(new Error(parsed.message || "The assistant hit an error."));
          return;
        }
      }
    }

    if (donePayload) onDone(donePayload);
    else onError(new Error("The chat stream ended without a completion event."));
  } catch (err) {
    onError(err as Error);
  }
}