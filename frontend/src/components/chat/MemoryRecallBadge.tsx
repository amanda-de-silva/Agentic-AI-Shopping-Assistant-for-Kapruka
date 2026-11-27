/**
 * Shows when a recipient/preference memory was recalled and used.
 */
export function MemoryRecallBadge({ notes }: { notes: string[] }) {
  return (
    <div className="memory-recall-badge" title={notes.join("; ")}>
      🧠 Remembered: {notes[0]}
      {notes.length > 1 ? ` +${notes.length - 1} more` : ""}
    </div>
  );
}
