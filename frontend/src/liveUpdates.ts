import { API_BASE_URL } from "./api";

// Payloads are invalidations only. Always re-read state/history from the API.
export function subscribeToVehicles(refresh: () => void): () => void {
  if (typeof EventSource === "undefined") return () => undefined;
  let source: EventSource;
  try {
    source = new EventSource(`${API_BASE_URL}/events/vehicles`);
  } catch {
    // Browser security policy or an invalid configured URL must not disable polling.
    return () => undefined;
  }
  source.addEventListener("resync", refresh);
  source.addEventListener("vehicle", refresh);
  // EventSource reconnects automatically. Polling also covers missed notifications,
  // unsupported browsers and unavailable listeners. Never create a second retry loop.
  return () => source.close();
}
