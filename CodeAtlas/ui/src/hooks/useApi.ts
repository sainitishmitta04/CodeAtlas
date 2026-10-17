import { useCallback, useEffect, useRef, useState } from "react";
import { isAbort } from "../lib/errors";

export interface AsyncState<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  /** Re-run the request with the same inputs. */
  reload: () => void;
}

/**
 * Runs `fn` whenever `deps` change (and on reload), cancelling the previous request.
 * `fn === null` means "nothing to load" (state resets to idle).
 */
export function useApi<T>(fn: ((signal: AbortSignal) => Promise<T>) | null, deps: unknown[]): AsyncState<T> {
  const [state, setState] = useState<{ data: T | null; error: unknown; loading: boolean }>({
    data: null,
    error: null,
    loading: fn !== null,
  });
  const [nonce, setNonce] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    const run = fnRef.current;
    if (!run) {
      setState({ data: null, error: null, loading: false });
      return;
    }
    const ctrl = new AbortController();
    setState((s) => ({ data: s.data, error: null, loading: true }));
    run(ctrl.signal).then(
      (data) => {
        if (!ctrl.signal.aborted) setState({ data, error: null, loading: false });
      },
      (error) => {
        if (!ctrl.signal.aborted && !isAbort(error)) setState({ data: null, error, loading: false });
      },
    );
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, fn === null]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { ...state, reload };
}

/** A user-triggered request (button click): tracks loading/error and cancels on unmount. */
export function useAction<A extends unknown[], T>(fn: (signal: AbortSignal, ...args: A) => Promise<T>) {
  const [state, setState] = useState<{ data: T | null; error: unknown; loading: boolean }>({
    data: null,
    error: null,
    loading: false,
  });
  const ctrlRef = useRef<AbortController | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => () => ctrlRef.current?.abort(), []);

  const run = useCallback(async (...args: A): Promise<T | null> => {
    ctrlRef.current?.abort();
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    setState((s) => ({ data: s.data, error: null, loading: true }));
    try {
      const data = await fnRef.current(ctrl.signal, ...args);
      if (!ctrl.signal.aborted) setState({ data, error: null, loading: false });
      return data;
    } catch (error) {
      if (!ctrl.signal.aborted && !isAbort(error)) setState({ data: null, error, loading: false });
      return null;
    }
  }, []);

  const reset = useCallback(() => {
    ctrlRef.current?.abort();
    setState({ data: null, error: null, loading: false });
  }, []);
  const setData = useCallback((data: T | null) => setState({ data, error: null, loading: false }), []);

  return { ...state, run, reset, setData };
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}
