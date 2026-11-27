/** Main chat pane with bilingual text and voice input. */

import {
  useEffect,
  useRef,
  useState,
} from "react";

import { useChatStore } from "../../store/chatStore";
import { useChatStream } from "../../hooks/useChatStream";
import { MessageBubble } from "./MessageBubble";
import { ThinkingIndicator } from "./ThinkingIndicator";
import { VoiceInput } from "./VoiceInput";

export function ChatWindow() {
  const {
    messages,
    isThinking,
  } = useChatStore();

  const {
    send,
  } = useChatStream();

  const [
    input,
    setInput,
  ] = useState("");

  const listRef =
    useRef<HTMLDivElement>(
      null
    );

  useEffect(() => {
    listRef.current?.scrollTo({
      top:
        listRef.current
          .scrollHeight,
      behavior: "smooth",
    });
  }, [
    messages,
    isThinking,
  ]);

  const submit = () => {
    const trimmed =
      input.trim();

    if (
      !trimmed ||
      isThinking
    ) {
      return;
    }

    send(trimmed);

    setInput("");
  };

  return (
    <div className="chat-window">
      <div
        className="message-list"
        ref={listRef}
      >
        {messages.length ===
          0 && (
          <div className="message-bubble assistant">
            Hi! I'm your Kapruka
            shopping assistant. Ask me
            to find something, check
            delivery to a city, remember
            a preference for someone, or
            track an order — I'll take it
            from there.
          </div>
        )}

        {messages.map(
          (message) => (
            <MessageBubble
              key={message.id}
              message={message}
            />
          )
        )}

        <ThinkingIndicator
          visible={isThinking}
        />
      </div>

      <div className="chat-input-bar">
        <VoiceInput
          value={input}
          onChange={
            setInput
          }
          disabled={
            isThinking
          }
        />

        <input
          value={input}
          disabled={
            isThinking
          }
          onChange={(
            event
          ) =>
            setInput(
              event.target.value
            )
          }
          onKeyDown={(
            event
          ) => {
            if (
              event.key ===
              "Enter"
            ) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder="Ask for anything — “find a nut-free cake for my wife”, “add it to cart”, “where's my order?”"
          aria-label="Message"
        />

        <button
          type="button"
          onClick={
            submit
          }
          disabled={
            isThinking ||
            !input.trim()
          }
        >
          {isThinking
            ? "Thinking…"
            : "Send"}
        </button>
      </div>
    </div>
  );
}