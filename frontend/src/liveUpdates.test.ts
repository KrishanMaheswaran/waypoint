import { afterEach, describe, expect, it, vi } from "vitest";
import { subscribeToVehicles } from "./liveUpdates";

afterEach(() => vi.unstubAllGlobals());
describe("vehicle EventSource", () => {
  it("uses native reconnect and refreshes on notifications and resync, then closes", () => {
    const handlers = new Map<string, () => void>();
    const close = vi.fn();
    const constructor = vi.fn(function () {
      return {
        addEventListener: (kind: string, handler: () => void) =>
          handlers.set(kind, handler),
        close,
      };
    });
    vi.stubGlobal("EventSource", constructor);
    const refresh = vi.fn();
    const unsubscribe = subscribeToVehicles(refresh);
    expect(constructor).toHaveBeenCalledWith("/api/events/vehicles");
    handlers.get("resync")!();
    handlers.get("vehicle")!();
    handlers.get("resync")!(); // reconnect has no replay; refresh the database
    expect(refresh).toHaveBeenCalledTimes(3);
    unsubscribe();
    expect(close).toHaveBeenCalledOnce();
  });
  it("keeps polling available when the browser refuses a stream", () => {
    vi.stubGlobal("EventSource", function () {
      throw new Error("blocked");
    });
    expect(() => subscribeToVehicles(vi.fn())()).not.toThrow();
  });
  it("supports browsers without EventSource", () => {
    vi.stubGlobal("EventSource", undefined);
    expect(() => subscribeToVehicles(vi.fn())()).not.toThrow();
  });
});
