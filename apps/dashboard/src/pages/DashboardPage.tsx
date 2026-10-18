import { ActivePlanPanel } from "../components/ActivePlanPanel";
import { ActivityPanel } from "../components/ActivityPanel";
import { ConnectionBanner } from "../components/ConnectionBanner";
import { HouseholdPanel } from "../components/HouseholdPanel";
import { RationalePanel } from "../components/RationalePanel";
import { SimulatorPanel } from "../components/SimulatorPanel";
import { VerificationPanel } from "../components/VerificationPanel";
import { formatTime } from "../services/format";
import { type DashboardState, useDashboardSnapshot } from "../hooks/useDashboardSnapshot";

type DashboardViewProps = Pick<
  DashboardState,
  "snapshot" | "error" | "loading" | "paused" | "lastUpdated" | "refresh" | "setPaused"
>;

/** The dashboard layout, given state. Kept free of hooks so it renders anywhere. */
export function DashboardView({
  snapshot,
  error,
  loading,
  paused,
  lastUpdated,
  refresh,
  setPaused,
}: DashboardViewProps) {
  const online = error === null && snapshot !== null;

  return (
    <main className="page">
      <header className="topbar">
        <div>
          <h1>Haven</h1>
          <p className="muted">A household agent that plans, acts, and checks its work.</p>
        </div>
        <div className="controls">
          <span className={`status ${online ? "status-ok" : "status-bad"}`}>
            {online ? "● Online" : "● Offline"}
          </span>
          {lastUpdated ? <span className="muted">Updated {formatTime(lastUpdated.toISOString())}</span> : null}
          <button type="button" onClick={() => setPaused(!paused)}>
            {paused ? "Resume" : "Pause"}
          </button>
          <button type="button" onClick={refresh} disabled={paused}>
            Refresh
          </button>
        </div>
      </header>

      <ConnectionBanner error={error} hasSnapshot={snapshot !== null} paused={paused} />

      {snapshot === null ? (
        <p className="empty">{loading ? "Connecting to Haven…" : "No data yet."}</p>
      ) : (
        <div className="grid">
          <ActivePlanPanel plan={snapshot.active_plan} />
          <RationalePanel plan={snapshot.active_plan} />
          <VerificationPanel plan={snapshot.active_plan} />
          <HouseholdPanel
            context={snapshot.context}
            rooms={snapshot.rooms}
            householdName={snapshot.household_name}
          />
          <SimulatorPanel
            devices={snapshot.devices}
            security={snapshot.security}
            mediaSessions={snapshot.media_sessions}
          />
          <ActivityPanel plans={snapshot.recent_plans} activity={snapshot.recent_activity} />
        </div>
      )}

      {snapshot !== null ? (
        <footer className="footer muted">
          Planner: {snapshot.planner_backend} · Memory: {snapshot.persistence_backend} · Read-only view
        </footer>
      ) : null}
    </main>
  );
}

/** Polls Haven and renders the dashboard. */
export function DashboardPage() {
  return <DashboardView {...useDashboardSnapshot()} />;
}
