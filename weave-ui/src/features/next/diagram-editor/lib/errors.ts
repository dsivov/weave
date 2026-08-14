/**
 * What a reader may be shown when something throws (U19).
 *
 * The reported case was a dagre `TypeError` — *"Cannot set properties of
 * undefined (setting 'rank')"* — rendered inside an otherwise good sentence as
 * the reason a diagram would not open. A manager cannot act on that, and it is
 * not even about their diagram: it is about ours.
 *
 * **But not every caught error is noise.** `mermaid.render` throws diagnostics
 * written for the person who typed the source — *"Parse error on line 3 …
 * Expecting 'SEMI'"* — and replacing those with a generic apology would take
 * away the most useful thing on the screen. The distinction is not where the
 * error came from, it is **who it was written for**:
 *
 * * a parser diagnostic about the reader's input → show it;
 * * a runtime failure of our own code → log it, and say something actionable.
 *
 * So this is one function rather than a rule everyone remembers, and
 * `tests/test_the_editor_opens_what_the_viewer_renders.py` asserts that nothing
 * in this feature renders `.message` any other way.
 */

//: The shapes a JavaScript runtime failure takes. Not an attempt to classify
//: every error — anything matching these was written by an engine for a
//: developer, and that is enough to know it does not belong on a panel.
const RUNTIME_FAILURE = [
  /^\w*Error:/,                        // "TypeError: …" when the name is carried
  /Cannot (read|set) propert(y|ies)/,  // the dagre case, and most null derefs
  /is not a function/,
  /is not defined/,
  /undefined is not/,
  /at .*:\d+:\d+/,                     // a stack frame reached the message
]

/**
 * The message to show a reader, given something that was thrown.
 *
 * @param err       whatever `catch` received
 * @param fallback  what to say when the error was not written for a reader
 */
export function readableError(err: unknown, fallback: string): string {
  const message = err instanceof Error ? err.message : String(err ?? '')
  if (!message.trim()) return fallback
  if (RUNTIME_FAILURE.some((re) => re.test(message))) {
    // The detail is not lost — it goes where the person who can use it looks.
    console.error('diagram editor: runtime failure shown as', fallback, err)
    return fallback
  }
  return message
}
