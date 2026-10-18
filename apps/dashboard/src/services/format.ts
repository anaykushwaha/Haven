import type { DashboardDevice, StatusTone } from "../types/dashboard";

/** "AWAITING_CONFIRMATION" -> "Awaiting confirmation". */
export function humanize(value: string): string {
  const spaced = value.replace(/[_-]+/g, " ").toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function toneFor(status: string | null): StatusTone {
  switch (status) {
    case "COMPLETED":
    case "VERIFIED":
    case "SUCCEEDED":
      return "ok";
    case "AWAITING_CONFIRMATION":
    case "PARTIAL":
    case "UNVERIFIABLE":
      return "warn";
    case "FAILED":
    case "MISMATCH":
      return "bad";
    default:
      return "neutral";
  }
}

export function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function workflowLabel(workflow: string): string {
  switch (workflow) {
    case "movie_night":
      return "Movie night";
    case "leaving_home":
      return "Leaving home";
    case "tonight_planning":
      return "Tonight";
    default:
      return "Unrecognised";
  }
}

/** One short line describing what a device is doing right now. */
export function describeDevice(device: DashboardDevice): string {
  if (!device.online) {
    return "Offline";
  }

  const state = device.state;
  switch (device.device_type) {
    case "light":
      return state.power === "on" ? `On · ${String(state.brightness ?? "?")}%` : "Off";
    case "thermostat": {
      const target = `${String(state.target_celsius ?? "?")}°C`;
      return state.away === true
        ? `Away · ${target}`
        : `${humanize(String(state.mode ?? "heat"))} · ${target}`;
    }
    case "lock":
      return state.locked === true ? "Locked" : "Unlocked";
    case "security_system":
      return state.armed === true ? `Armed · ${String(state.mode ?? "away")}` : "Disarmed";
    case "tv":
      return state.power === "on" ? "On" : "Off";
    default:
      return "Online";
  }
}
