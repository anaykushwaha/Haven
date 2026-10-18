import type { DashboardPlan } from "../types/dashboard";
import { Panel } from "./Panel";
import { StatusBadge } from "./StatusBadge";

interface VerificationPanelProps {
  plan: DashboardPlan | null;
}

/** After acting, Haven re-reads the home and checks it. This shows that check. */
export function VerificationPanel({ plan }: VerificationPanelProps) {
  const checked = plan?.steps.filter((step) => step.verification_status !== null) ?? [];

  return (
    <Panel
      title="Verification results"
      subtitle="Haven checks the home, not just its own report"
    >
      {plan === null || plan.verification_status === null ? (
        <p className="empty">Nothing has been executed yet, so there is nothing to verify.</p>
      ) : (
        <>
          <div className="badges">
            <StatusBadge status={plan.verification_status} />
          </div>
          {checked.length === 0 ? (
            <p className="muted">No step was checked.</p>
          ) : (
            <ul className="list">
              {checked.map((step) => (
                <li key={step.id} className="verification">
                  <div className="row">
                    <span className="grow">{step.title}</span>
                    <StatusBadge status={step.verification_status} />
                  </div>
                  <p className="muted">{step.verification_detail}</p>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Panel>
  );
}
