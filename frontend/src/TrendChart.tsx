import { chronologicalHistory } from "./fleet";
import type { TelemetryEvent } from "./types";

interface TrendChartProps {
  title: string;
  unit: string;
  field: "battery_percentage" | "temperature";
  events: TelemetryEvent[];
  color: string;
}

const WIDTH = 560;
const HEIGHT = 190;
const PAD_X = 20;
const PAD_Y = 24;

export function TrendChart({
  title,
  unit,
  field,
  events,
  color,
}: TrendChartProps) {
  const ordered = chronologicalHistory(events);
  const values = ordered.map((event) => event[field]);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 0;
  const span = max - min || Math.max(Math.abs(max) * 0.1, 1);
  const timeStart = ordered.length
    ? new Date(ordered[0].timestamp).getTime()
    : 0;
  const timeEnd = ordered.length
    ? new Date(ordered[ordered.length - 1].timestamp).getTime()
    : 0;
  const timeSpan = timeEnd - timeStart;
  const points = ordered.map((event, index) => {
    const time = new Date(event.timestamp).getTime();
    return {
      event,
      x:
        ordered.length < 2 || timeSpan === 0
          ? WIDTH / 2
          : PAD_X + ((time - timeStart) / timeSpan) * (WIDTH - PAD_X * 2),
      y:
        HEIGHT -
        PAD_Y -
        ((event[field] - min) / span) * (HEIGHT - PAD_Y * 2),
      index,
    };
  });
  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"}${point.x},${point.y}`)
    .join(" ");

  return (
    <section className="trend-card" aria-label={`${title} trend`}>
      <div className="trend-heading">
        <div>
          <h3>{title}</h3>
          <p>Historical readings</p>
        </div>
        <span className="trend-unit">{unit}</span>
      </div>
      {points.length === 0 ? (
        <div className="chart-empty">No telemetry history available yet.</div>
      ) : (
        <>
          <svg
            className="trend-svg"
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            role="img"
            aria-label={`${title} history chart with ${points.length} readings`}
          >
            <line
              x1={PAD_X}
              x2={WIDTH - PAD_X}
              y1={HEIGHT - PAD_Y}
              y2={HEIGHT - PAD_Y}
              className="chart-axis"
            />
            <line
              x1={PAD_X}
              x2={WIDTH - PAD_X}
              y1={PAD_Y}
              y2={PAD_Y}
              className="chart-grid"
            />
            {path && (
              <path
                d={path}
                fill="none"
                stroke={color}
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            )}
            {points.map(({ event, x, y, index }) => (
              <circle
                key={`${event.event_id}-${index}`}
                cx={x}
                cy={y}
                r="4"
                fill={color}
                stroke="#101720"
                strokeWidth="2"
              >
                <title>
                  {`${event[field]} ${unit} · ${new Date(event.timestamp).toLocaleString()}`}
                </title>
              </circle>
            ))}
          </svg>
          <div className="chart-range">
            <span>{new Date(ordered[0].timestamp).toLocaleString()}</span>
            <span>{new Date(ordered[ordered.length - 1].timestamp).toLocaleString()}</span>
          </div>
        </>
      )}
    </section>
  );
}
