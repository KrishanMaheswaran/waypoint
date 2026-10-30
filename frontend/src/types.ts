export const VEHICLE_STATUSES = [
  "idle",
  "charging",
  "ready",
  "maintenance",
] as const;

export type VehicleStatus = (typeof VEHICLE_STATUSES)[number];

export interface VehicleState {
  vehicle_id: string;
  battery_percentage: number;
  status: VehicleStatus;
  temperature: number;
  fault_code: string | null;
  last_updated: string;
}

export interface TelemetryEvent extends Omit<VehicleState, "last_updated"> {
  id: number;
  event_id: string;
  timestamp: string;
}
