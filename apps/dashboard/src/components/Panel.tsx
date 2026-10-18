import type { ReactNode } from "react";

interface PanelProps {
  title: string;
  subtitle?: string;
  className?: string;
  children: ReactNode;
}

/** The titled card every dashboard section sits in. */
export function Panel({ title, subtitle, className, children }: PanelProps) {
  return (
    <section className={`panel ${className ?? ""}`.trim()} aria-label={title}>
      <header className="panel-header">
        <h2>{title}</h2>
        {subtitle ? <p className="panel-subtitle">{subtitle}</p> : null}
      </header>
      {children}
    </section>
  );
}
