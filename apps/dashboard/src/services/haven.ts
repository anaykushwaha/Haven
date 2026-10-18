import type { DashboardSnapshot } from "../types/dashboard";

// Empty by default: in dev the Vite proxy forwards /api to Haven (see
// vite.config.ts). Set VITE_HAVEN_API_URL to talk to a server directly.
const API_BASE: string = import.meta.env?.VITE_HAVEN_API_URL ?? "";

export class HavenApiError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "HavenApiError";
    this.status = status;
  }
}

/** Fetch the current read-only snapshot of Haven's state. */
export async function fetchSnapshot(signal?: AbortSignal): Promise<DashboardSnapshot> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/dashboard`, {
      signal,
      headers: { Accept: "application/json" },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new HavenApiError(
      "Cannot reach Haven. Start the server with: python scripts/run_server.py --dashboard-api",
    );
  }

  if (response.status === 404) {
    throw new HavenApiError(
      "Haven is running without the dashboard API. Restart it with --dashboard-api.",
      404,
    );
  }
  if (!response.ok) {
    throw new HavenApiError(`Haven returned HTTP ${response.status}.`, response.status);
  }

  return (await response.json()) as DashboardSnapshot;
}
