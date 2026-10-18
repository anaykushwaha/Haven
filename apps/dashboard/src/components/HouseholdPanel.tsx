import type { DashboardRoom, HouseholdContext } from "../types/dashboard";
import { Panel } from "./Panel";

interface HouseholdPanelProps {
  context: HouseholdContext;
  rooms: DashboardRoom[];
  householdName: string;
}

/** Who is home, which rooms are in use, and the facts Haven is planning with. */
export function HouseholdPanel({ context, rooms, householdName }: HouseholdPanelProps) {
  const home = context.people.filter((person) => person.home).length;

  return (
    <Panel title="Household" subtitle={`${householdName} · ${home} of ${context.people.length} home`}>
      <ul className="list">
        {context.people.map((person) => (
          <li key={person.id} className="row">
            <span className={`dot ${person.home ? "dot-ok" : "dot-off"}`} aria-hidden="true" />
            <span className="grow">{person.name}</span>
            <span className="muted">{person.home ? "Home" : "Away"}</span>
          </li>
        ))}
      </ul>

      <h3>Rooms</h3>
      <ul className="list">
        {rooms.map((room) => (
          <li key={room.id} className="row">
            <span className={`dot ${room.occupied ? "dot-ok" : "dot-off"}`} aria-hidden="true" />
            <span className="grow">{room.name}</span>
            <span className="muted">
              {room.occupied ? (room.current_activity ?? "Occupied") : "Free"}
            </span>
          </li>
        ))}
      </ul>

      <h3>What Haven knows</h3>
      <dl className="facts">
        <dt>Free time</dt>
        <dd>
          {context.available_minutes === null ? "Unknown" : `About ${context.available_minutes} min`}
        </dd>
        <dt>Preferred genres</dt>
        <dd>{context.preferred_genres.length > 0 ? context.preferred_genres.join(", ") : "None saved"}</dd>
        <dt>Preferred room</dt>
        <dd>
          {context.rooms.find((room) => room.id === context.preferred_room_id)?.name ?? "None saved"}
        </dd>
      </dl>
    </Panel>
  );
}
