import type { TelemetryEvent, VehicleState, VehicleStatus } from "./types";
import { VEHICLE_STATUSES } from "./types";

export type FleetSource = "all" | "operational" | "benchmark";

export const VEHICLE_PAGE_SIZE = 25;

export interface FleetSummary {
  total: number;
  byStatus: Record<VehicleStatus, number>;
  averageBattery: number | null;
  activeFaults: number;
}

export function isBenchmarkVehicle(vehicle: Pick<VehicleState, "vehicle_id">): boolean {
  return vehicle.vehicle_id.toUpperCase().startsWith("BENCH-");
}

export function defaultFleetSource(vehicles: VehicleState[]): FleetSource {
  return vehicles.some((vehicle) => !isBenchmarkVehicle(vehicle))
    ? "operational"
    : "all";
}

export function filterBySource(
  vehicles: VehicleState[],
  source: FleetSource,
): VehicleState[] {
  if (source === "all") return vehicles;
  return vehicles.filter((vehicle) =>
    source === "benchmark"
      ? isBenchmarkVehicle(vehicle)
      : !isBenchmarkVehicle(vehicle),
  );
}

export function summarizeFleet(vehicles: VehicleState[]): FleetSummary {
  const byStatus = Object.fromEntries(
    VEHICLE_STATUSES.map((status) => [status, 0]),
  ) as Record<VehicleStatus, number>;

  for (const vehicle of vehicles) {
    byStatus[vehicle.status] += 1;
  }

  const batteryTotal = vehicles.reduce(
    (total, vehicle) => total + vehicle.battery_percentage,
    0,
  );

  return {
    total: vehicles.length,
    byStatus,
    averageBattery:
      vehicles.length > 0 ? batteryTotal / vehicles.length : null,
    activeFaults: vehicles.filter((vehicle) => vehicle.fault_code?.trim()).length,
  };
}

export function filterVehicles(
  vehicles: VehicleState[],
  search: string,
  status: VehicleStatus | "all",
  source: FleetSource = "all",
): VehicleState[] {
  const query = search.trim().toLocaleLowerCase();
  return filterBySource(vehicles, source).filter((vehicle) => {
    const matchesStatus = status === "all" || vehicle.status === status;
    const matchesSearch =
      query.length === 0 ||
      [vehicle.vehicle_id, vehicle.status, vehicle.fault_code ?? ""].some(
        (value) => value.toLocaleLowerCase().includes(query),
      );
    return matchesStatus && matchesSearch;
  });
}

export function chronologicalHistory(
  events: TelemetryEvent[],
): TelemetryEvent[] {
  return [...events].sort(
    (left, right) =>
      new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime(),
  );
}

export function formatTimestamp(value: string): string {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "Unknown time";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(timestamp);
}
