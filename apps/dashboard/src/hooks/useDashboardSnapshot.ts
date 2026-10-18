import { useCallback, useEffect, useRef, useState } from "react";

import { fetchSnapshot, HavenApiError } from "../services/haven";
import type { DashboardSnapshot } from "../types/dashboard";

export interface DashboardState {
  snapshot: DashboardSnapshot | null;
  /** Set when the latest poll failed; `snapshot` still holds the last good one. */
  error: string | null;
  loading: boolean;
  paused: boolean;
  lastUpdated: Date | null;
  refresh: () => void;
  setPaused: (paused: boolean) => void;
}

/**
 * Poll Haven's read-only dashboard API.
 *
 * A failed poll keeps the last good snapshot on screen (marked stale by the
 * `error`) instead of blanking the dashboard mid-demo, and polling carries on
 * so it recovers by itself when the server comes back.
 */
export function useDashboardSnapshot(intervalMs = 2000): DashboardState {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [paused, setPaused] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);
  const inFlight = useRef<AbortController | null>(null);

  const refresh = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    if (paused) {
      return undefined;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      const controller = new AbortController();
      inFlight.current = controller;
      try {
        const next = await fetchSnapshot(controller.signal);
        if (cancelled) {
          return;
        }
        setSnapshot(next);
        setError(null);
        setLastUpdated(new Date());
      } catch (caught) {
        if (cancelled || (caught instanceof DOMException && caught.name === "AbortError")) {
          return;
        }
        setError(caught instanceof HavenApiError ? caught.message : "Unexpected error talking to Haven.");
      } finally {
        if (!cancelled) {
          setLoading(false);
          timer = setTimeout(poll, intervalMs);
        }
      }
    };

    void poll();

    return () => {
      cancelled = true;
      inFlight.current?.abort();
      if (timer !== undefined) {
        clearTimeout(timer);
      }
    };
  }, [intervalMs, paused, tick]);

  return { snapshot, error, loading, paused, lastUpdated, refresh, setPaused };
}
