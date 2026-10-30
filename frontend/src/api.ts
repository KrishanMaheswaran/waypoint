import type { TelemetryEvent, VehicleState } from "./types";

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();
const API_BASE_URL = (configuredBaseUrl || "/api").replace(/\/+$/, "");

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new Error("Unable to reach the Waypoint API. Check the backend connection.");
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        typeof body.detail === "string"
      ) {
        detail = body.detail;
      }
    } catch {
      // Keep the HTTP status message when the server does not return JSON.
    }
    throw new Error(detail);
  }

  return (await response.json()) as T;
}

export function getVehicles(signal?: AbortSignal): Promise<VehicleState[]> {
  return getJson<VehicleState[]>("/vehicles", signal);
}

export function getVehicleTelemetry(
  vehicleId: string,
  signal?: AbortSignal,
): Promise<TelemetryEvent[]> {
  return getJson<TelemetryEvent[]>(
    `/vehicles/${encodeURIComponent(vehicleId)}/telemetry`,
    signal,
  );
}
