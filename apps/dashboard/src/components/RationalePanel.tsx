import type { DashboardPlan } from "../types/dashboard";
import { Panel } from "./Panel";

interface RationalePanelProps {
  plan: DashboardPlan | null;
}

/** Why Haven made this plan, in plain sentences built from facts it holds. */
export function RationalePanel({ plan }: RationalePanelProps) {
  return (
    <Panel title="Why Haven made this plan">
      {plan === null || plan.rationale.length === 0 ? (
        <p className="empty">Haven will explain its reasoning when it proposes a plan.</p>
      ) : (
        <ul className="rationale">
          {plan.rationale.map((line, index) => (
            <li key={index}>{line}</li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
