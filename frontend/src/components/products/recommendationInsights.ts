/**
 * Deterministic, evidence-grounded recommendation explanations.
 *
 * This deliberately does not ask an LLM to invent ranking reasons. It only
 * reports details that can be checked against the user's request and the
 * product information returned by Kapruka.
 */
export interface RecommendationEvidenceProduct {
  name: string;
  price: number;
  currency?: string;
  description?: string | null;
  category?: string | null;
  vendor?: string | null;
  inStock?: boolean | null;
}

export interface RecommendationExplanation {
  matchedRequestDetails: string[];
  priceContext: string | null;
  stockContext: string | null;
  fallback: string | null;
  evidenceNote: string;
}

const STOP_WORDS = new Set([
  "a", "an", "and", "are", "as", "at", "be", "best", "buy", "can",
  "could", "do", "for", "from", "find", "get", "give", "help", "i",
  "in", "is", "it", "me", "my", "of", "on", "option", "options",
  "or", "please", "recommend", "recommendation", "show", "some", "that",
  "the", "them", "there", "these", "this", "to", "want", "what", "which",
  "with", "would", "you", "your", "looking", "like", "suitable", "available",
  "kapruka", "product", "products", "item", "items", "under", "below",
  "above", "over", "less", "more", "than", "within", "maximum", "max",
  "budget", "price", "lkr", "rs", "rupees", "for", "mother", "father",
  "wife", "husband", "sister", "brother", "daughter", "son", "friend",
  "birthday", "anniversary", "gift", "present", "occasion", "buying", "need",
]);

/** Remove explicitly excluded/allergy terms so they aren't presented as positive matches. */
function keepPositivePreferences(request: string): string {
  return request
    .replace(/\ballergic\s+to\s+(?:the\s+)?[a-z][a-z -]*/gi, " ")
    .replace(/\ballergy\s+(?:to|for)\s+(?:the\s+)?[a-z][a-z -]*/gi, " ")
    .replace(/\b(?:without|free\s+from|free\s+of|avoid|excluding|exclude|not contain|does not contain|doesn't contain)\s+[a-z][a-z -]*/gi, " ")
    .replace(/\b(?:i\s+)?(?:do\s+not|don't|dont|cannot|can't)\s+(?:want|like|need|have)\s+[a-z][a-z -]*/gi, " ");
}

function getRequestTerms(request: string): string[] {
  const positiveRequest = keepPositivePreferences(request.toLowerCase());
  const tokens = positiveRequest.match(/[a-z][a-z'-]{2,}/g) ?? [];
  const seen = new Set<string>();
  const result: string[] = [];

  for (const token of tokens) {
    const normalized = token.replace(/^'+|'+$/g, "");
    if (normalized.length < 3 || STOP_WORDS.has(normalized) || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    result.push(normalized);
  }

  return result;
}

function containsWord(text: string, word: string): boolean {
  const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(?:^|[^a-z0-9])${escaped}(?:$|[^a-z0-9])`, "i").test(text);
}

function extractBudgetLimit(request: string): number | null {
  const pattern = /(?:under|below|less\s+than|not\s+more\s+than|up\s+to|maximum(?:\s+of)?|max(?:imum)?(?:\s+of)?|budget(?:\s+(?:is|of|up\s+to))?)\s*(?:(?:lkr|rs\.?|rupees)\s*)?([\d][\d,]*(?:\.\d{1,2})?)/i;
  const match = request.match(pattern);
  if (!match?.[1]) return null;
  const amount = Number(match[1].replace(/,/g, ""));
  return Number.isFinite(amount) && amount > 0 ? amount : null;
}

function formatMoney(amount: number, currency = "LKR"): string {
  return `${currency} ${amount.toLocaleString()}`;
}

export function buildRecommendationExplanation(
  request: string,
  product: RecommendationEvidenceProduct,
): RecommendationExplanation {
  const title = product.name || "";
  const description = product.description || "";
  const category = product.category || "";
  const evidenceText = `${title} ${description} ${category}`.toLowerCase();

  const matchedRequestDetails = getRequestTerms(request)
    .filter((term) => containsWord(evidenceText, term))
    .slice(0, 4)
    .map((term) => {
      const appearsInName = containsWord(title.toLowerCase(), term);
      const appearsInDescription = containsWord(description.toLowerCase(), term);
      const source = appearsInName && appearsInDescription
        ? "product name and description"
        : appearsInName
          ? "product name"
          : appearsInDescription
            ? "product description"
            : "product category";
      return `Your request mentions “${term}”, and the ${source} also mentions it.`;
    });

  const budget = extractBudgetLimit(request);
  let priceContext: string | null = null;
  const currency = product.currency || "LKR";

  if (budget !== null) {
    priceContext = product.price <= budget
      ? `The listed price (${formatMoney(product.price, currency)}) is within your stated limit of ${formatMoney(budget, currency)}.`
      : `The listed price (${formatMoney(product.price, currency)}) is above the budget limit of ${formatMoney(budget, currency)} you mentioned.`;
  }

  const stockContext = product.inStock === true
    ? "Kapruka's available product data currently reports this item as in stock."
    : product.inStock === false
      ? "Kapruka's available product data currently reports this item as out of stock."
      : null;

  const fallback = matchedRequestDetails.length === 0
    ? "The available product fields do not confirm a more specific match to the wording of your request. Review the product description and details before deciding."
    : null;

  return {
    matchedRequestDetails,
    priceContext,
    stockContext,
    fallback,
    evidenceNote: "This explanation uses only the request and product details available in the listing; missing attributes are not assumed.",
  };
}
