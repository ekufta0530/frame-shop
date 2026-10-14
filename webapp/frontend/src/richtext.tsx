import type { ReactNode } from 'react';

/** Method notes are plain text with **bold** spans (no HTML), so config can never inject markup. */
export function renderMethod(text: string): ReactNode[] {
  return text.split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 ? <b key={i}>{part}</b> : part));
}
