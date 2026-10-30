import { useEffect, useMemo, useState } from "react";
import {
  filterBySource,
  filterVehicles,
  formatTimestamp,
  summarizeFleet,
  VEHICLE_PAGE_SIZE,
} from "./fleet";
import { TrendChart } from "./TrendChart";
import { useFleetData } from "./useFleetData";
import { VEHICLE_STATUSES } from "./types";
import type { VehicleStatus } from "./types";
import type { FleetSource } from "./fleet";

const statusNames: Record<VehicleStatus, string> = {
  idle: "Idle",
  charging: "Charging",
  ready: "Ready",
  maintenance: "Maintenance",
};

function BatteryMeter({ value }: { value: number }) {
  const level = Math.min(100, Math.max(0, value));
  const tone = level < 20 ? "critical" : level < 45 ? "low" : "normal";
  return (
    <div
      className={`battery-meter ${tone}`}
      role="meter"
      aria-label="Battery"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(value)}
    >
      <span className="battery-track">
        <span className="battery-fill" style={{ width: `${level}%` }} />
      </span>
      <span className="battery-value">{Math.round(value)}%</span>
    </div>
  );
}

function StatusBadge({ status }: { status: VehicleStatus }) {
  return (
    <span className={`status-badge status-${status}`}>
      <span className="status-dot" aria-hidden="true" />
      {statusNames[status]}
    </span>
  );
}

export function FleetDashboard() {
  const {
    vehicles,
    history,
    historyVehicleId,
    selectedVehicleId,
    selectVehicle,
    loading,
    error,
    lastUpdated,
  } = useFleetData();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<VehicleStatus | "all">(
    "all",
  );
  const [sourceFilter, setSourceFilter] =
    useState<FleetSource>("operational");
  const [page, setPage] = useState(1);
  const operationalVehicles = useMemo(
    () => filterBySource(vehicles, "operational"),
    [vehicles],
  );
  const effectiveSource =
    sourceFilter === "operational" && operationalVehicles.length === 0
      ? "all"
      : sourceFilter;
  const sourceVehicles = useMemo(
    () => filterBySource(vehicles, effectiveSource),
    [vehicles, effectiveSource],
  );
  const summary = useMemo(() => summarizeFleet(sourceVehicles), [sourceVehicles]);
  const filteredVehicles = useMemo(
    () => filterVehicles(vehicles, search, statusFilter, effectiveSource),
    [vehicles, search, statusFilter, effectiveSource],
  );
  const pageCount = Math.max(
    1,
    Math.ceil(filteredVehicles.length / VEHICLE_PAGE_SIZE),
  );
  const currentPage = Math.min(page, pageCount);
  const pageVehicles = filteredVehicles.slice(
    (currentPage - 1) * VEHICLE_PAGE_SIZE,
    currentPage * VEHICLE_PAGE_SIZE,
  );
  const selectedVehicle =
    sourceVehicles.find((vehicle) => vehicle.vehicle_id === selectedVehicleId) ??
    sourceVehicles[0] ??
    null;
  const selectedHistory =
    historyVehicleId === selectedVehicle?.vehicle_id ? history : [];

  useEffect(() => {
    setPage(1);
  }, [search, statusFilter, sourceFilter]);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  useEffect(() => {
    if (selectedVehicle && selectedVehicle.vehicle_id !== selectedVehicleId) {
      selectVehicle(selectedVehicle.vehicle_id);
    }
  }, [selectVehicle, selectedVehicle, selectedVehicleId]);

  const resetFilters = (): void => {
    setPage(1);
  };
  const connectionState = error
    ? lastUpdated
      ? "stale"
      : "error"
    : lastUpdated
      ? "live"
      : loading
        ? "connecting"
        : "offline";
  const connectionLabel = {
    live: "LIVE",
    stale: "STALE",
    error: "API ERROR",
    connecting: "CONNECTING",
    offline: "OFFLINE",
  }[connectionState];

  return (
    <main className="app-shell">
      <aside className="sidebar" aria-label="Primary navigation">
        <a className="brand" href="#" aria-label="Waypoint fleet overview">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 32 32" fill="none">
              <path d="M7 23.5 16 6l9 17.5H7Z" stroke="currentColor" strokeWidth="2.2" />
              <circle cx="16" cy="19" r="3" fill="currentColor" />
            </svg>
          </span>
          <span className="brand-name">waypoint<span>.</span></span>
        </a>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          <a className="nav-item active" href="#fleet" aria-current="page">
            <span className="nav-icon" aria-hidden="true">▦</span>
            Fleet overview
          </a>
          <a className="nav-item" href="#vehicles">
            <span className="nav-icon" aria-hidden="true">⌁</span>
            Vehicles
          </a>
        </nav>
        <div className="sidebar-bottom">
          <span
            className={`connection-indicator connection-${connectionState}`}
            aria-hidden="true"
          />
          <div>
            <strong>API connection</strong>
            <span>{connectionLabel}</span>
          </div>
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <div className="breadcrumb"><span>Workspace</span><b>/</b> Fleet operations</div>
          <div className="topbar-right">
            <span className={`live-pill live-${connectionState}`}>
              <i aria-hidden="true" /> {connectionLabel}
            </span>
            <span className="topbar-date">
              {lastUpdated ? `Updated ${formatTimestamp(lastUpdated.toISOString())}` : "Waiting for API"}
            </span>
            <div className="avatar" aria-label="Fleet operator">FO</div>
          </div>
        </header>

        <div className="page-content" id="fleet">
          <section className="page-heading">
            <div>
              <p className="eyebrow">OPERATIONS CENTER <span>•</span> OVERVIEW</p>
              <h1>Fleet overview</h1>
              <p className="page-subtitle">Monitor vehicle health and telemetry across your fleet.</p>
            </div>
            <div className="refresh-indicator">
              <span className="refresh-icon" aria-hidden="true">↻</span>
              Auto-refresh <strong>5s</strong>
            </div>
          </section>

          {error && (
            <div className="alert-banner" role="alert">
              <span aria-hidden="true">!</span>
              <div><strong>Unable to refresh fleet data</strong><p>{error}</p></div>
            </div>
          )}

          <section className="stat-grid" aria-label="Fleet summary">
            <article className="stat-card">
              <div className="stat-top"><span>Total vehicles</span><span className="stat-icon blue">▦</span></div>
              <div className="stat-number">{loading && vehicles.length === 0 ? "—" : summary.total}</div>
              <div className="stat-foot"><span className="stat-indicator blue-dot" /> In selected source</div>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span>Ready for dispatch</span><span className="stat-icon green">↗</span></div>
              <div className="stat-number">{loading && vehicles.length === 0 ? "—" : summary.byStatus.ready}</div>
              <div className="stat-foot"><span className="stat-indicator green-dot" /> Current status</div>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span>Average battery</span><span className="stat-icon yellow">ϟ</span></div>
              <div className="stat-number">
                {loading && vehicles.length === 0
                  ? "—"
                  : summary.averageBattery === null
                    ? "—"
                    : `${summary.averageBattery.toFixed(1)}%`}
              </div>
              <div className="stat-foot"><span className="stat-indicator yellow-dot" /> Across active fleet</div>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span>Active faults</span><span className="stat-icon red">!</span></div>
              <div className="stat-number">{loading && vehicles.length === 0 ? "—" : summary.activeFaults}</div>
              <div className="stat-foot"><span className="stat-indicator red-dot" /> Vehicles reporting fault codes</div>
            </article>
          </section>

          <section className="status-strip" aria-label="Vehicle counts by status">
            {VEHICLE_STATUSES.map((status) => (
              <div className="status-count" key={status}>
                <StatusBadge status={status} />
                <strong>{loading && vehicles.length === 0 ? "—" : summary.byStatus[status]}</strong>
              </div>
            ))}
          </section>

          <section className="panel vehicle-panel" id="vehicles">
            <div className="panel-heading">
              <div>
                <h2>Vehicles <span className="count-badge">{summary.total}</span></h2>
                <p>Live status and latest reported telemetry</p>
              </div>
              <div className="table-controls">
                <label className="filter-field source-filter-field">
                  <span className="sr-only">Filter by fleet source</span>
                  <select
                    value={effectiveSource}
                    onChange={(event) => {
                      setSourceFilter(event.target.value as FleetSource);
                      resetFilters();
                    }}
                  >
                    <option value="all">All vehicles</option>
                    <option value="operational">Non-benchmark vehicles</option>
                    <option value="benchmark">Benchmark vehicles</option>
                  </select>
                </label>
                <label className="search-field">
                  <span className="search-icon" aria-hidden="true">⌕</span>
                  <span className="sr-only">Search vehicles</span>
                  <input
                    type="search"
                    placeholder="Search vehicles..."
                    value={search}
                    onChange={(event) => {
                      setSearch(event.target.value);
                      resetFilters();
                    }}
                  />
                </label>
                <label className="filter-field">
                  <span className="sr-only">Filter by status</span>
                  <select
                    value={statusFilter}
                    onChange={(event) => {
                      setStatusFilter(event.target.value as VehicleStatus | "all");
                      resetFilters();
                    }}
                  >
                    <option value="all">All statuses</option>
                    {VEHICLE_STATUSES.map((status) => (
                      <option value={status} key={status}>{statusNames[status]}</option>
                    ))}
                  </select>
                </label>
              </div>
            </div>

            {vehicles.length > 0 && operationalVehicles.length === 0 && (
              <p className="source-note" role="status">
                No non-benchmark vehicles are available. Showing all vehicles; benchmark IDs use the <code>BENCH-</code> prefix.
              </p>
            )}

            {loading && vehicles.length === 0 ? (
              <div className="table-message" role="status">Loading vehicle data…</div>
            ) : vehicles.length === 0 ? (
              <div className="table-message empty-state">
                <span className="empty-icon" aria-hidden="true">⌁</span>
                <strong>No vehicles reporting yet</strong>
                <p>Vehicle records will appear here after telemetry is received.</p>
              </div>
            ) : filteredVehicles.length === 0 ? (
              <div className="table-message">
                {sourceVehicles.length === 0
                  ? "No vehicles are available for this fleet source."
                  : "No vehicles match your search or filters."}
              </div>
            ) : (
              <>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Vehicle</th>
                        <th scope="col">Status</th>
                        <th scope="col">Battery</th>
                        <th scope="col">Temperature</th>
                        <th scope="col">Fault code</th>
                        <th scope="col">Last update</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pageVehicles.map((vehicle) => (
                        <tr
                          key={vehicle.vehicle_id}
                          className={selectedVehicle?.vehicle_id === vehicle.vehicle_id ? "selected-row" : ""}
                        >
                          <td>
                            <button
                              className="vehicle-link"
                              onClick={() => selectVehicle(vehicle.vehicle_id)}
                              aria-pressed={selectedVehicle?.vehicle_id === vehicle.vehicle_id}
                              aria-label={`${vehicle.vehicle_id}: view latest state and history`}
                            >
                              <span className="vehicle-symbol" aria-hidden="true">◈</span>
                              {vehicle.vehicle_id}
                            </button>
                          </td>
                          <td><StatusBadge status={vehicle.status} /></td>
                          <td><BatteryMeter value={vehicle.battery_percentage} /></td>
                          <td><span className="temperature-value">{vehicle.temperature.toFixed(1)}<small>°C</small></span></td>
                          <td>
                            {vehicle.fault_code
                              ? <span className="fault-code"><i />{vehicle.fault_code}</span>
                              : <span className="no-fault">—</span>}
                          </td>
                          <td className="timestamp-cell">{formatTimestamp(vehicle.last_updated)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="pagination" aria-label="Vehicle table pagination">
                  <span>
                    Showing {(currentPage - 1) * VEHICLE_PAGE_SIZE + 1}
                    –{Math.min(currentPage * VEHICLE_PAGE_SIZE, filteredVehicles.length)} of {filteredVehicles.length} vehicles
                  </span>
                  <div className="pagination-controls">
                    <button
                      type="button"
                      aria-label="Previous page"
                      onClick={() => setPage((current) => Math.max(1, current - 1))}
                      disabled={currentPage === 1}
                    >
                      Previous
                    </button>
                    <span aria-live="polite">Page {currentPage} of {pageCount}</span>
                    <button
                      type="button"
                      aria-label="Next page"
                      onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
                      disabled={currentPage === pageCount}
                    >
                      Next
                    </button>
                  </div>
                </div>
              </>
            )}
          </section>

          {selectedVehicle && (
            <section className="detail-section" aria-label={`Details for ${selectedVehicle.vehicle_id}`}>
              <div className="detail-heading">
                <div>
                  <p className="eyebrow">VEHICLE DETAIL</p>
                  <h2>{selectedVehicle.vehicle_id} <StatusBadge status={selectedVehicle.status} /></h2>
                  <p>Latest state and recent timestamped telemetry</p>
                </div>
                <div className="last-seen">
                  <span>LAST REPORTED</span>
                  <strong>{formatTimestamp(selectedVehicle.last_updated)}</strong>
                </div>
              </div>
              <div className="detail-metrics">
                <div className="detail-metric"><span>Battery level</span><strong>{selectedVehicle.battery_percentage.toFixed(1)}%</strong></div>
                <div className="detail-metric"><span>Temperature</span><strong>{selectedVehicle.temperature.toFixed(1)}°C</strong></div>
                <div className="detail-metric"><span>Fault code</span><strong className={selectedVehicle.fault_code ? "fault-text" : ""}>{selectedVehicle.fault_code ?? "None reported"}</strong></div>
              </div>
              <div className="chart-grid">
                <TrendChart
                  title="Battery"
                  unit="%"
                  field="battery_percentage"
                  events={selectedHistory}
                  color="#55a9ff"
                />
                <TrendChart
                  title="Temperature"
                  unit="°C"
                  field="temperature"
                  events={selectedHistory}
                  color="#a68bff"
                />
              </div>
              <section className="history-panel" aria-label="Recent telemetry history">
                <div className="history-heading"><h3>Recent telemetry</h3><span>{selectedHistory.length} events</span></div>
                {selectedHistory.length === 0 ? (
                  <p className="history-empty">No telemetry history available for this vehicle.</p>
                ) : (
                  <ol className="history-list">
                    {selectedHistory.slice(0, 5).map((event) => (
                      <li key={event.event_id}>
                        <span className="history-point" aria-hidden="true" />
                        <span className="history-time">{formatTimestamp(event.timestamp)}</span>
                        <StatusBadge status={event.status} />
                        <span>{event.battery_percentage.toFixed(1)}% battery</span>
                        <span>{event.temperature.toFixed(1)}°C</span>
                      </li>
                    ))}
                  </ol>
                )}
              </section>
            </section>
          )}

          <footer className="page-footer">
            <span>Waypoint Fleet Operations</span>
            <span>Data refreshes automatically every 5 seconds</span>
          </footer>
        </div>
      </div>
    </main>
  );
}
