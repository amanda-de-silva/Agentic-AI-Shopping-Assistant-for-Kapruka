/**
 * Renders a single user/assistant message, including embedded product cards
 * and the isolated sequential Shopping List Flow.
 */
import type { ChatMessage } from "../../store/chatStore";
import { useChatStore } from "../../store/chatStore";
import { ProductGrid, type ProductGridItem } from "../products/ProductGrid";
import { MemoryRecallBadge } from "./MemoryRecallBadge";
import { ShoppingListFlow } from "./ShoppingListFlow";

const PRODUCT_LIST_MARKER = /^\s*(?:[-*•]\s+|\d+[.)]\s+)/;
const PRICE_IN_PRODUCT_LINE = /\b(?:LKR|Rs\.?|USD|EUR|GBP)\b\s*[\d,]+(?:\.\d{1,2})?|(?:US\$|[$€£])\s*[\d,]+(?:\.\d{1,2})?/i;

/** Remove duplicated product-name/price rows without rewriting the reply. */
function hideDuplicatedProductList(content: string): string {
  const filteredLines = content
    .split(/\r?\n/)
    .filter((line) => !(PRODUCT_LIST_MARKER.test(line) && PRICE_IN_PRODUCT_LINE.test(line)));

  return filteredLines
    .join("\n")
    .replace(/\n[\t ]*\n(?:[\t ]*\n)+/g, "\n\n")
    .trim();
}

export function MessageBubble({ message }: { message: ChatMessage }) {
  const messages = useChatStore((state) => state.messages);
  const messageIndex = messages.findIndex((item) => item.id === message.id);
  const requestContext = messageIndex >= 0
    ? [...messages.slice(0, messageIndex)].reverse().find((item) => item.role === "user")?.content ?? ""
    : "";

  const hasProductCards = Boolean(
    message.role === "assistant" && message.products && message.products.length > 0,
  );
  const visibleContent = hasProductCards
    ? hideDuplicatedProductList(message.content)
    : message.content;

  return (
    <div className={`message-row ${message.role}`}>
      {visibleContent && (
        <div className={`message-bubble ${message.role}`}>
          {visibleContent}
        </div>
      )}
      {!visibleContent && !hasProductCards && (
        <div className={`message-bubble ${message.role}`}>
          {"\u00A0"}
        </div>
      )}

      {message.memoryRecalled && message.memoryRecalled.length > 0 && (
        <MemoryRecallBadge notes={message.memoryRecalled} />
      )}

      {hasProductCards && (
        <ProductGrid
          products={message.products as unknown as ProductGridItem[]}
          requestContext={requestContext}
        />
      )}

      {message.shoppingListItems && message.shoppingListItems.length > 0 && (
        <ShoppingListFlow items={message.shoppingListItems} />
      )}
    </div>
  );
}
