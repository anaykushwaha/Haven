import { describeDevice } from "../services/format";
import type { DashboardDevice, DashboardMediaSession, DashboardSecurity } from "../types/dashboard";
import { Panel } from "./Panel";
import { StatusBadge } from "./StatusBadge";

interface SimulatorPanelProps {
  devices: DashboardDevice[];
  security: DashboardSecurity;
  mediaSessions: DashboardMediaSession[];
}

const GROUPS: { type: string; label: string }[] = [
  { type: "tv", label: "Fire TV" },
  { type: "light", label: "Lights" },
  { type: "thermostat", label: "Thermostat" },
  { type: "lock", label: "Doors" },
  { type: "security_system", label: "Security" },
];

/** Raw simulator state, so you can see a plan's effect land on the "home". */
export function SimulatorPanel({ devices, security, mediaSessions }: SimulatorPanelProps) {
  const sessionFor = (deviceId: string) => mediaSessions.find((s) => s.device_id === deviceId);

  return (
    <Panel
      title="Simulator state"
      subtitle={`${security.armed ? "Armed" : "Disarmed"} · ${
        security.all_locked ? "all doors locked" : "a door is unlocked"
      }`}
    >
      {GROUPS.map(({ type, label }) => {
        const members = devices.filter((device) => device.device_type === type);
        if (members.length === 0) {
          return null;
        }
        return (
          <div key={type}>
            <h3>{label}</h3>
            <ul className="list">
              {members.map((device) => {
                const session = sessionFor(device.id);
                return (
                  <li key={device.id} className="row">
                    <span
                      className={`dot ${device.online ? "dot-ok" : "dot-bad"}`}
                      aria-hidden="true"
                    />
                    <span className="grow">{device.name}</span>
                    {session ? (
                      <span className="muted">
                        “{session.title}” <StatusBadge status={session.status.toUpperCase()} tone="neutral" />
                      </span>
                    ) : (
                      <span className="muted">{describeDevice(device)}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </Panel>
  );
}
