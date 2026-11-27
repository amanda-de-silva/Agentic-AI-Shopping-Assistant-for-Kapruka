/**
 * Shows agent 'thinking' state while the orchestrator is working.
 */
export function ThinkingIndicator({ visible }: { visible: boolean }) {
  if (!visible) return null;
  return (
    <div className="thinking-indicator">
      <span className="thinking-dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      Thinking…
    </div>
  );
}
