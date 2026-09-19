import { Cpu } from "lucide-react";
import type { RouteInfo, Step } from "../api/types";

const TYPE_LABEL: Record<string, string> = {
  general: "General reasoning",
  coder: "Code",
  vision: "Images / scans",
};

/** The distinct (task type, model) pairs the router picked during a run, in order of first use. */
export function routesOf(steps: Step[]): RouteInfo[] {
  const seen = new Map<string, RouteInfo>();
  for (const step of steps) {
    const route = step.route;
    if (!route) continue;
    const key = `${route.task_type}|${route.model}`;
    if (!seen.has(key)) seen.set(key, route);
  }
  return [...seen.values()];
}

/** Shows that the system chose a model for each kind of work by itself, and which model actually ran. */
export function RoutingCard({ steps }: { steps: Step[] }) {
  const routes = routesOf(steps);
  if (routes.length === 0) return null;
  const standIn = routes.some((r) => r.substituted);
  return (
    <section className="card routing" aria-label="Model selection" data-testid="routing">
      <div className="routing-head">
        <Cpu size={16} aria-hidden />
        <h3 className="section-title">Automatic model selection</h3>
      </div>
      <p className="muted small">
        The router looked at each task and picked the model for that kind of work. Nothing was chosen by hand.
      </p>
      <div className="table-wrap">
        <table className="routing-table">
          <thead>
            <tr>
              <th>Kind of task</th>
              <th>Model the design calls for</th>
              <th>Model that ran here</th>
              <th>Why it was chosen</th>
            </tr>
          </thead>
          <tbody>
            {routes.map((route) => (
              <tr key={`${route.task_type}-${route.model}`}>
                <td>
                  <strong>{TYPE_LABEL[route.task_type] ?? route.task_type}</strong>
                </td>
                <td>
                  <code>{route.designed_model}</code>
                </td>
                <td>
                  <code>{route.model}</code>
                </td>
                <td className="muted small">{route.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {standIn && (
        <p className="muted small">
          This computer has no GPU, so smaller stand-in models run in place of the large ones the design names. On a
          workstation with a mid-range GPU the design models are used with no other change.
        </p>
      )}
    </section>
  );
}
