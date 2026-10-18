import { workflowLabel } from "../services/format";
import type { DashboardPlan } from "../types/dashboard";
import { Panel } from "./Panel";
import { PlanSteps } from "./PlanSteps";
import { StatusBadge } from "./StatusBadge";

interface ActivePlanPanelProps {
  plan: DashboardPlan | null;
}

/** The plan Haven most recently proposed, and how far it has got. */
export function ActivePlanPanel({ plan }: ActivePlanPanelProps) {
  if (plan === null) {
    return (
      <Panel title="Active plan" className="panel-wide">
        <p className="empty">
          No plan yet. Ask Alexa+ to “get movie night ready” or say “we’re leaving”, and it will
          appear here.
        </p>
      </Panel>
    );
  }

  return (
    <Panel title="Active plan" subtitle={workflowLabel(plan.workflow)} className="panel-wide">
      <p className="plan-goal">“{plan.goal}”</p>
      <div className="badges">
        <StatusBadge status={plan.status} />
        <StatusBadge status={plan.execution_status} fallback="Not executed" />
      </div>
      <p className="muted">{plan.summary}</p>
      {plan.clarification_needed && plan.clarification_prompt ? (
        <div className="banner banner-warn" role="status">
          Haven asks: {plan.clarification_prompt}
        </div>
      ) : null}
      <PlanSteps steps={plan.steps} />
    </Panel>
  );
}
