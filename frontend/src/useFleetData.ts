import { useEffect, useState } from "react";
import { getVehicleTelemetry, getVehicles } from "./api";
import { defaultFleetSource, isBenchmarkVehicle } from "./fleet";
import type { TelemetryEvent, VehicleState } from "./types";

export const POLL_INTERVAL_MS = 5_000;

export interface FleetData {
  vehicles: VehicleState[];
  history: TelemetryEvent[];
  historyVehicleId: string | null;
  selectedVehicleId: string | null;
  selectVehicle: (vehicleId: string) => void;
  loading: boolean;
  error: string | null;
  lastUpdated: Date | null;
}

export function useFleetData(): FleetData {
  const [vehicles, setVehicles] = useState<VehicleState[]>([]);
  const [history, setHistory] = useState<TelemetryEvent[]>([]);
  const [historyVehicleId, setHistoryVehicleId] = useState<string | null>(null);
  const [selectedVehicleId, setSelectedVehicleId] = useState<string | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  useEffect(() => {
    let active = true;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;

    const refresh = async (): Promise<void> => {
      controller = new AbortController();
      try {
        const latestVehicles = await getVehicles(controller.signal);
        if (!active) return;
        setVehicles(latestVehicles);

        const currentSelection =
          selectedVehicleId &&
          latestVehicles.some(
            (vehicle) => vehicle.vehicle_id === selectedVehicleId,
          )
            ? selectedVehicleId
            : null;
        const preferredVehicle =
          defaultFleetSource(latestVehicles) === "all"
            ? latestVehicles[0]
            : latestVehicles.find((vehicle) => !isBenchmarkVehicle(vehicle));
        const activeVehicleId =
          currentSelection ?? preferredVehicle?.vehicle_id ?? null;
        const latestHistory = activeVehicleId
          ? await getVehicleTelemetry(activeVehicleId, controller.signal)
          : [];

        if (!active) return;
        setHistory(latestHistory);
        setHistoryVehicleId(activeVehicleId);
        setError(null);
        setLastUpdated(new Date());
      } catch (caught) {
        if (!active || (caught instanceof DOMException && caught.name === "AbortError")) {
          return;
        }
        setError(
          caught instanceof Error ? caught.message : "The API request failed.",
        );
      } finally {
        if (active) {
          setLoading(false);
          timeout = setTimeout(() => void refresh(), POLL_INTERVAL_MS);
        }
      }
    };

    void refresh();
    return () => {
      active = false;
      if (timeout !== undefined) clearTimeout(timeout);
      controller?.abort();
    };
  }, [selectedVehicleId]);

  const defaultVehicle =
    defaultFleetSource(vehicles) === "all"
      ? vehicles[0]
      : vehicles.find((vehicle) => !isBenchmarkVehicle(vehicle));

  return {
    vehicles,
    history,
    historyVehicleId,
    selectedVehicleId: selectedVehicleId ?? defaultVehicle?.vehicle_id ?? null,
    selectVehicle: setSelectedVehicleId,
    loading,
    error,
    lastUpdated,
  };
}
