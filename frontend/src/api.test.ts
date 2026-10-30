import { afterEach, describe, expect, it, vi } from "vitest";
import { getVehicleTelemetry, getVehicles } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("API client", () => {
  it("requests vehicle state from the existing endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("[]", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(getVehicles()).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/vehicles",
      expect.objectContaining({
        method: "GET",
        headers: { Accept: "application/json" },
      }),
    );
  });

  it("encodes a vehicle ID in the existing history endpoint path", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("[]", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getVehicleTelemetry("VF/1001");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/vehicles/VF%2F1001/telemetry",
    );
  });

  it("surfaces API errors instead of replacing them with live-looking data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Database unavailable" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(getVehicles()).rejects.toThrow("Database unavailable");
  });
});
