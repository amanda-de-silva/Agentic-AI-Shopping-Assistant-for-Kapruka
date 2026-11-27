/** Hook for one-at-a-time SSE chat streams and deterministic UI action handling. */
import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { streamChat } from "../api/chat";
import { useChatStore } from "../store/chatStore";
import { useCartStore } from "../store/cartStore";
import type { CartView, UIAction } from "../store/cartStore";
import { parseShoppingListMessage } from "../utils/shoppingListParser";

let seq = 0;
const nextId = () => `msg_${Date.now()}_${seq++}`;

export function useChatStream() {
  const { addMessage, updateMessage, updateMessageExtras, isThinking, setThinking } = useChatStore();
  const { setCart, openDrawer } = useCartStore();
  const navigate = useNavigate();

  const handleUIActions = useCallback(
    (messageId: string, actions: UIAction[]) => {
      for (const action of actions) {
        if (action.type === "cart_updated") {
          setCart(action.payload as unknown as CartView);
        } else if (action.type === "navigate_checkout") {
          const checkoutUrl = (action.payload as { checkout_url?: string }).checkout_url;
          if (checkoutUrl) window.location.assign(checkoutUrl);
        } else if (action.type === "navigate_cart") {
          openDrawer();
        } else if (action.type === "order_status") {
          const orderNumber = (action.payload as { order_number?: string }).order_number;
          navigate(orderNumber ? `/track?order=${encodeURIComponent(orderNumber)}` : "/track");
        } else if (action.type === "memory_recalled") {
          const profileText = String((action.payload as { profile_text?: string }).profile_text || "");
          if (profileText) updateMessageExtras(messageId, { memoryRecalled: [profileText] });
        } else if (action.type === "products_shown") {
          const products = (action.payload as { products?: Record<string, unknown>[] }).products || [];
          updateMessageExtras(messageId, { products });
        }
      }
    },
    [navigate, openDrawer, setCart, updateMessageExtras],
  );

  const send = useCallback(
    (message: string) => {
      if (isThinking) return;

      // A clearly comma-separated product list is handled by the isolated
      // Shopping List Flow. Every other message continues through the existing
      // SSE chat/orchestrator path unchanged.
      const shoppingListItems = parseShoppingListMessage(message);
      if (shoppingListItems) {
        addMessage({ id: nextId(), role: "user", content: message });
        addMessage({
          id: nextId(),
          role: "assistant",
          content: `I’ll help you shop through these ${shoppingListItems.length} items one by one. For each item, you can select one or more products to add to your cart, or skip it and continue.`,
          shoppingListItems,
        });
        return;
      }

      const assistantMessageId = nextId();
      addMessage({ id: nextId(), role: "user", content: message });
      addMessage({ id: assistantMessageId, role: "assistant", content: "" });
      setThinking(true);

      let accumulated = "";
      void streamChat(
        message,
        (token) => {
          accumulated += token;
          updateMessage(assistantMessageId, accumulated);
        },
        (final) => {
          updateMessage(assistantMessageId, final.reply);
          handleUIActions(assistantMessageId, final.ui_actions as UIAction[]);
          setThinking(false);
        },
        (err) => {
          updateMessage(assistantMessageId, `Sorry, something went wrong: ${err.message}`);
          setThinking(false);
        },
      );
    },
    [addMessage, handleUIActions, isThinking, setThinking, updateMessage],
  );

  return { send, isThinking };
}
