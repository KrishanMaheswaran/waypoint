import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useFleetData } from "./useFleetData";
const mocks = vi.hoisted(() => ({
  getVehicles: vi.fn(),
  getVehicleTelemetry: vi.fn(),
  subscribe: vi.fn(),
  close: vi.fn(),
}));
vi.mock("./api", () => mocks);
vi.mock("./liveUpdates", () => ({ subscribeToVehicles: mocks.subscribe }));
let notification: () => void;
beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  mocks.getVehicles.mockResolvedValue([]);
  mocks.subscribe.mockImplementation((refresh: () => void) => {
    notification = refresh;
    return mocks.close;
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
it("coalesces duplicate hints and keeps five-second reconciliation for missed notifications", async () => {
  const { unmount } = renderHook(() => useFleetData());
  await act(async () => {});
  expect(mocks.getVehicles).toHaveBeenCalledTimes(1);
  act(() => {
    notification();
    notification();
    notification();
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(mocks.getVehicles).toHaveBeenCalledTimes(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(mocks.getVehicles).toHaveBeenCalledTimes(3);
  unmount();
  expect(mocks.close).toHaveBeenCalledOnce();
  await vi.advanceTimersByTimeAsync(6000);
  expect(mocks.getVehicles).toHaveBeenCalledTimes(3);
});
it("never overlaps requests and collapses pending invalidations", async () => {
  let resolve!: (value: never[]) => void;
  mocks.getVehicles.mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  renderHook(() => useFleetData());
  act(() => {
    notification();
    notification();
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(mocks.getVehicles).toHaveBeenCalledTimes(1);
  await act(async () => {
    resolve([]);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(mocks.getVehicles).toHaveBeenCalledTimes(2);
});
it("polls after API errors while SSE is unavailable", async () => {
  mocks.getVehicles.mockRejectedValueOnce(new Error("offline"));
  const { result } = renderHook(() => useFleetData());
  await act(async () => {});
  expect(result.current.error).toBe("offline");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(mocks.getVehicles).toHaveBeenCalledTimes(2);
  expect(result.current.error).toBeNull();
});
