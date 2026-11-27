import { create } from "zustand";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  memoryRecalled?: string[];
  products?: Record<string, unknown>[];
  /** Present only on the assistant message that hosts an item-by-item shopping list. */
  shoppingListItems?: string[];
}

interface ChatStore {
  messages: ChatMessage[];
  isThinking: boolean;
  addMessage: (message: ChatMessage) => void;
  updateMessage: (id: string, content: string) => void;
  updateMessageExtras: (
    id: string,
    extras: Partial<Pick<ChatMessage, "memoryRecalled" | "products" | "shoppingListItems">>,
  ) => void;
  setThinking: (thinking: boolean) => void;
  reset: () => void;
}

export const useChatStore = create<ChatStore>((set) => ({
  messages: [],
  isThinking: false,
  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),
  updateMessage: (id, content) =>
    set((state) => ({
      messages: state.messages.map((message) =>
        message.id === id ? { ...message, content } : message,
      ),
    })),
  updateMessageExtras: (id, extras) =>
    set((state) => ({
      messages: state.messages.map((message) =>
        message.id === id ? { ...message, ...extras } : message,
      ),
    })),
  setThinking: (thinking) => set({ isThinking: thinking }),
  reset: () => set({ messages: [], isThinking: false }),
}));
