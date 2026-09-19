import type { Assessment } from "../api/types";
import { num } from "../utils/format";

type Tone = "ok" | "warn" | "bad";

function toneOf(status: string | undefined): Tone {
  const s = (status ?? "").toLowerCase();
  if (s.startsWith("replace") || s.startsWith("high")) return "bad";
  if (s.startsWith("moderate")) return "warn";
  return "ok";
}

/** Corrosion results: headline numbers and the per-location table. */
export function AssessmentView({ assessment }: { assessment: Assessment }) {
  const { table, attention, required_thickness: tMin } = assessment;
  return (
    <section aria-label="Corrosion assessment" data-testid="assessment">
      <div className="stats">
        <div className="stat">
          <span className="stat-label">Locations assessed</span>
          <span className="stat-value">{table.length}</span>
        </div>
        <div className={`stat attn${attention.length ? "" : " none"}`}>
          <span className="stat-label">Need attention</span>
          <span className="stat-value">{attention.length}</span>
          <span className="stat-sub">{attention.length ? attention.join(", ") : "All within limits"}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Minimum allowable thickness</span>
          <span className="stat-value">{tMin === null ? "—" : `${num(tMin, 2)} mm`}</span>
          <span className="stat-sub">from the SOP</span>
        </div>
      </div>

      <div className="table-wrap">
        <table data-testid="assessment-table">
          <thead>
            <tr>
              <th>Location</th>
              <th className="num">Previous (mm)</th>
              <th className="num">Current (mm)</th>
              <th className="num">Rate (mm/yr)</th>
              <th className="num">Life left (yr)</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {table.map((row) => {
              const tone = toneOf(row.status);
              const [head, ...rest] = (row.status ?? "").split(":");
              return (
                <tr key={row.location} className={tone === "bad" ? "attn-row" : ""}>
                  <td>{row.location}</td>
                  <td className="num">{num(row.previous_thickness, 2)}</td>
                  <td className="num">{num(row.current_thickness, 2)}</td>
                  <td className="num">{num(row.corrosion_rate, 3)}</td>
                  <td className="num">{num(row.remaining_life, 1)}</td>
                  <td className="status-cell" title={row.status}>
                    {row.status ? <span className={`status-tag ${tone}`}>{head}</span> : "—"}
                    {rest.length > 0 && <div className="muted small">{rest.join(":").trim()}</div>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
