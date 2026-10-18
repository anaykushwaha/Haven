import { formatTime, humanize } from "../services/format";
import type { DashboardActivity, DashboardPlanListItem } from "../types/dashboard";
import { Panel } from "./Panel";
import { StatusBadge } from "./StatusBadge";

interface ActivityPanelProps {
  plans: DashboardPlanListItem[];
  activity: DashboardActivity[];
}

/** Recent plans and the activity Haven chose to remember. */
export function ActivityPanel({ plans, activity }: ActivityPanelProps) {
  return (
    <Panel title="Timeline" subtitle="Recent plans and remembered activity">
      {plans.length === 0 && activity.length === 0 ? (
        <p className="empty">Nothing yet.</p>
      ) : null}

      {plans.length > 0 ? (
        <ul className="list">
          {plans.map((plan) => (
            <li key={plan.plan_id} className="row">
              <span className="time">{formatTime(plan.created_at)}</span>
              <span className="grow">{plan.goal}</span>
              <StatusBadge status={plan.execution_status ?? plan.plan_status} />
            </li>
          ))}
        </ul>
      ) : null}

      {activity.length > 0 ? (
        <>
          <h3>Remembered</h3>
          <ul className="list">
            {activity.map((item, index) => (
              <li key={`${item.occurred_at}-${index}`} className="row">
                <span className="time">{formatTime(item.occurred_at)}</span>
                <span className="grow">{item.summary}</span>
                <span className="muted">{humanize(item.activity_type)}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Panel>
  );
}
