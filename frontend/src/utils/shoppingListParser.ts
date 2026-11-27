/** Recognize an obvious comma-separated shopping list without intercepting chat. */

const MAX_LIST_ENTRIES = 20;

const PREFIX_PATTERNS = [
  /^(?:my\s+)?shopping\s+list\s*[:\-]\s*/i,
  /^(?:items|products|list)\s*[:\-]\s*/i,
  /^(?:please\s+)?(?:i\s+want\s+to\s+buy|i\s+need\s+to\s+buy|i\s+need|i\s+want|can\s+you\s+(?:find|get|buy)|please\s+(?:find|get|buy)|find|buy|get|add\s+to\s+cart)\s*[:\-]?\s*/i,
];

const CONVERSATIONAL_FRAGMENT = /\b(?:because|although|however|which|who|when|where|why|please|i|we|you|my|our|your|they|he|she|could|would|should|recommend|help|think|prefer|likes|love|loves|want|needs?)\b/i;

function cleanEntry(value: string): string {
  return value
    .replace(/^\s*(?:[-*•]\s*|\d+[.)]\s*)/, "")
    .replace(/^and\s+/i, "")
    .replace(/[\s.]+$/, "")
    .trim();
}

/**
 * Returns item phrases only for clearly list-shaped input (2–20 comma/newline
 * separated product phrases). Ordinary sentences remain on the normal chat path.
 * Duplicate entries are preserved because they can represent repeated purchases.
 */
export function parseShoppingListMessage(message: string): string[] | null {
  const original = message.trim();
  if (!original || original.includes("?") || original.length > 1600) return null;

  let remaining = original;
  let explicitListLabel = false;
  let explicitShoppingCommand = false;

  for (let index = 0; index < PREFIX_PATTERNS.length; index += 1) {
    const pattern = PREFIX_PATTERNS[index];
    if (pattern.test(remaining)) {
      remaining = remaining.replace(pattern, "");
      explicitListLabel = index < 2;
      explicitShoppingCommand = index === 2;
      break;
    }
  }

  if (remaining.includes("?")) return null;

  const entries = remaining
    .split(/[,;\n]+/)
    .map(cleanEntry)
    .filter(Boolean);

  if (entries.length < 2 || entries.length > MAX_LIST_ENTRIES) return null;
  if (entries.some((entry) => entry.length < 2 || entry.length > 120)) return null;

  // Reject sentence-like requests unless the customer clearly labelled the
  // content as a shopping list. An explicit shopping verb still has to leave
  // clean, product-like item phrases after its prefix is removed.
  if (!explicitListLabel && entries.some((entry) => CONVERSATIONAL_FRAGMENT.test(entry))) {
    return null;
  }
  if (explicitShoppingCommand && entries.some((entry) => CONVERSATIONAL_FRAGMENT.test(entry))) {
    return null;
  }

  return entries;
}
