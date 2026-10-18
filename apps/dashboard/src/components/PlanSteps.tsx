import type { DashboardStep } from "../types/dashboard";
import { StatusBadge } from "./StatusBadge";

interface PlanStepsProps {
  steps: DashboardStep[];
}

/** The plan's steps, each with what happened when it ran. */
export function PlanSteps({ steps }: PlanStepsProps) {
  if (steps.length === 0) {
    return <p className="muted">This plan has no steps to run.</p>;
  }

  return (
    <ol className="steps">
      {steps.map((step) => (
        <li key={step.id} className="step">
          <div className="step-head">
            <span className="step-order">{step.order}</span>
            <span className="grow step-title">{step.title}</span>
            {step.requires_confirmation ? <span className="badge badge-warn">Needs confirmation</span> : null}
            <StatusBadge status={step.execution_status} fallback="Pending" />
          </div>
          <p className="step-detail">{step.execution_detail ?? step.description}</p>
          {step.attempts !== null && step.attempts > 1 ? (
            <p className="step-note">Retried: ran {step.attempts} times.</p>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
