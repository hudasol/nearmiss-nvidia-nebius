import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import type { Reference } from "./types";

export interface AsyncState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/** Fetch on mount and whenever deps change; stale responses are ignored. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const seq = useRef(0);
  useEffect(() => {
    const my = ++seq.current;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => {
        if (my === seq.current) setData(d);
      })
      .catch((e: unknown) => {
        if (my === seq.current) setError(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e)));
      })
      .finally(() => {
        if (my === seq.current) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((x) => x + 1), []);
  return { data, error, loading, reload };
}

export const ReferenceContext = createContext<Reference | null>(null);
export const useReference = () => useContext(ReferenceContext);

export function useReferenceLoader(): Reference | null {
  const [ref, setRef] = useState<Reference | null>(null);
  useEffect(() => {
    api.reference().then(setRef).catch(() => setRef(null));
  }, []);
  return ref;
}
