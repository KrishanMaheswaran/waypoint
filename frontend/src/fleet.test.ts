import { describe, expect, it } from "vitest";
import {
  chronologicalHistory,
  defaultFleetSource,
  filterBySource,
  filterVehicles,
  isBenchmarkVehicle,
  summarizeFleet,
} from "./fleet";
import type { TelemetryEvent, VehicleState } from "./types";

const vehicles: VehicleState[] = [
  {
    vehicle_id: "VF-1001",
    battery_percentage: 80,
    status: "ready",
    temperature: 22.5,
    fault_code: null,
    last_updated: "2026-10-29T18:00:00Z",
  },
  {
    vehicle_id: "VF-1002",
    battery_percentage: 40,
    status: "charging",
    temperature: 27,
    fault_code: "TEMP_HIGH",
    last_updated: "2026-10-29T18:01:00Z",
  },
];

const benchmarkVehicle: VehicleState = {
  ...vehicles[0],
  vehicle_id: "BENCH-run-0001",
  status: "maintenance",
  battery_percentage: 20,
  fault_code: "TEST_FAULT",
};

const history: TelemetryEvent[] = [
  {
    id: 2,
    event_id: "event-2",
    vehicle_id: "VF-1001",
    battery_percentage: 80,
    status: "ready",
    temperature: 22.5,
    fault_code: null,
    timestamp: "2026-10-29T18:02:00Z",
  },
  {
    id: 1,
    event_id: "event-1",
    vehicle_id: "VF-1001",
    battery_percentage: 78,
    status: "charging",
    temperature: 24,
    fault_code: null,
    timestamp: "2026-10-29T18:00:00Z",
  },
];

describe("fleet transformations", () => {
  it("calculates totals, status counts, average battery, and current faults", () => {
    expect(summarizeFleet(vehicles)).toEqual({
      total: 2,
      byStatus: {
        idle: 0,
        charging: 1,
        ready: 1,
        maintenance: 0,
      },
      averageBattery: 60,
      activeFaults: 1,
    });
    expect(summarizeFleet([]).averageBattery).toBeNull();
  });

  it("filters by vehicle, status, and fault code without mutating data", () => {
    expect(filterVehicles(vehicles, "vf-1001", "all")).toEqual([vehicles[0]]);
    expect(filterVehicles(vehicles, "temp_high", "ready")).toEqual([]);
    expect(filterVehicles(vehicles, "temp_high", "all")).toEqual([vehicles[1]]);
    expect(vehicles.map((vehicle) => vehicle.vehicle_id)).toEqual([
      "VF-1001",
      "VF-1002",
    ]);
  });

  it("classifies benchmark IDs by the BENCH- prefix and defaults safely", () => {
    expect(isBenchmarkVehicle({ vehicle_id: "BENCH-run-0001" })).toBe(true);
    expect(isBenchmarkVehicle({ vehicle_id: "bench-run-0001" })).toBe(true);
    expect(isBenchmarkVehicle({ vehicle_id: "VF-BENCH-0001" })).toBe(false);
    expect(defaultFleetSource([benchmarkVehicle, vehicles[0]])).toBe("operational");
    expect(defaultFleetSource([benchmarkVehicle])).toBe("all");
    expect(filterBySource([benchmarkVehicle, vehicles[0]], "operational")).toEqual([
      vehicles[0],
    ]);
    expect(filterBySource([benchmarkVehicle, vehicles[0]], "benchmark")).toEqual([
      benchmarkVehicle,
    ]);
  });

  it("calculates fleet statistics from the selected source", () => {
    expect(summarizeFleet(filterBySource([benchmarkVehicle, ...vehicles], "operational"))).toMatchObject({
      total: 2,
      activeFaults: 1,
      averageBattery: 60,
    });
    expect(summarizeFleet(filterBySource([benchmarkVehicle], "operational")).total).toBe(0);
  });

  it("orders returned timestamped events chronologically without mutation", () => {
    expect(chronologicalHistory(history).map((event) => event.event_id)).toEqual([
      "event-1",
      "event-2",
    ]);
    expect(history[0].event_id).toBe("event-2");
  });
});
