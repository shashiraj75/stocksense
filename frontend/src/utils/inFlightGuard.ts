// A synchronous in-flight request guard, backed by a plain mutable ref-like
// object (e.g. the `.current` box a React `useRef` returns) rather than
// React state. React state updates are batched/scheduled, so two calls to
// an async handler within the same synchronous tick (a rapid double-click,
// or a click racing a keyboard activation) can both read a stale "not
// loading" value from their own render closure before either state update
// commits. A ref mutation is immediate and shared across both invocations,
// so this guard cannot be bypassed the same way — see
// ClosedTradeHorizonBlock's loadNextPage in app/paper-trading/page.tsx for
// the concrete call site (guarding its lazy "older closed trades" fetch).

export interface MutableFlag {
  current: boolean;
}

/**
 * Runs `fn` only if `flag.current` is not already true. Sets `flag.current`
 * to true synchronously, before `fn` is invoked (and therefore before any
 * `await` inside it can yield control back to the event loop), and always
 * resets it to false in a `finally` — including when `fn` throws or its
 * returned promise rejects, so a failed request never permanently locks
 * out later attempts.
 *
 * Returns the result of `fn()`, or `undefined` if the call was rejected
 * because a request was already in flight.
 */
export async function runIfNotInFlight<T>(
  flag: MutableFlag,
  fn: () => Promise<T>,
): Promise<T | undefined> {
  if (flag.current) return undefined;
  flag.current = true;
  try {
    return await fn();
  } finally {
    flag.current = false;
  }
}
