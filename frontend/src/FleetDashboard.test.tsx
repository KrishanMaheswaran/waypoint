import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FleetDashboard } from "./FleetDashboard";
import type { TelemetryEvent, VehicleState } from "./types";

const { getVehicles, getVehicleTelemetry } = vi.hoisted(() => ({
  getVehicles: vi.fn(),
  getVehicleTelemetry: vi.fn(),
}));

vi.mock("./api", () => ({ getVehicles, getVehicleTelemetry }));

const vehicle: VehicleState = {
  vehicle_id: "VF-1001",
  battery_percentage: 82,
  status: "ready",
  temperature: 23.4,
  fault_code: null,
  last_updated: "2026-10-29T18:00:00Z",
};

const secondVehicle: VehicleState = {
  vehicle_id: "VF-1002",
  battery_percentage: 37,
  status: "maintenance",
  temperature: 81,
  fault_code: "TEMP_HIGH",
  last_updated: "2026-10-29T18:01:00Z",
};

const benchmarkVehicle: VehicleState = {
  ...vehicle,
  vehicle_id: "BENCH-run-0001",
  battery_percentage: 12,
  status: "maintenance",
  temperature: 90,
  fault_code: "BENCH_TEST",
  last_updated: "2026-10-29T18:02:00Z",
};

const event: TelemetryEvent = {
  id: 10,
  event_id: "event-10",
  vehicle_id: vehicle.vehicle_id,
  battery_percentage: 82,
  status: "ready",
  temperature: 23.4,
  fault_code: null,
  timestamp: "2026-10-29T18:00:00Z",
};

const secondEvent: TelemetryEvent = {
  ...event,
  id: 11,
  event_id: "event-11",
  vehicle_id: secondVehicle.vehicle_id,
  battery_percentage: secondVehicle.battery_percentage,
  status: secondVehicle.status,
  temperature: secondVehicle.temperature,
  fault_code: secondVehicle.fault_code,
  timestamp: secondVehicle.last_updated,
};

beforeEach(() => {
  vi.clearAllMocks();
  getVehicles.mockResolvedValue([vehicle]);
  getVehicleTelemetry.mockImplementation((vehicleId: string) =>
    Promise.resolve([vehicleId === secondVehicle.vehicle_id ? secondEvent : event]),
  );
});

describe("FleetDashboard", () => {
  it("shows loading state before live API results arrive", () => {
    getVehicles.mockReturnValue(new Promise(() => undefined));
    render(<FleetDashboard />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading vehicle data");
    expect(screen.getByText("Waiting for API")).toBeInTheDocument();
  });

  it("shows an explicit empty state when the API returns no vehicles", async () => {
    getVehicles.mockResolvedValue([]);
    render(<FleetDashboard />);

    expect(
      await screen.findByText("No vehicles reporting yet"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("img", { name: /history chart/ }),
    ).not.toBeInTheDocument();
  });

  it("renders API-derived summary, vehicle details, and timestamped history", async () => {
    render(<FleetDashboard />);

    expect(await screen.findByRole("button", { name: /VF-1001: view latest/ })).toBeInTheDocument();
    expect(screen.getAllByText("82.0%")).not.toHaveLength(0);
    expect(screen.getAllByText("23.4°C")).not.toHaveLength(0);
    expect(screen.getByRole("img", { name: /Battery history chart with 1 readings/ })).toBeInTheDocument();
    expect(screen.getByText("Recent telemetry")).toBeInTheDocument();
    expect(getVehicleTelemetry).toHaveBeenCalledWith(
      "VF-1001",
      expect.any(AbortSignal),
    );
  });

  it("supports searching, status filtering, and selecting a vehicle", async () => {
    const user = userEvent.setup();
    getVehicles.mockResolvedValue([vehicle, secondVehicle]);
    render(<FleetDashboard />);

    await screen.findByRole("button", { name: /VF-1001: view latest/ });
    await user.type(screen.getByRole("searchbox", { name: "Search vehicles" }), "TEMP_HIGH");
    expect(screen.getByRole("button", { name: /VF-1002: view latest/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /VF-1001: view latest/ })).not.toBeInTheDocument();

    await user.clear(screen.getByRole("searchbox", { name: "Search vehicles" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Filter by status" }), {
      target: { value: "maintenance" },
    });
    expect(screen.getByRole("button", { name: /VF-1002: view latest/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /VF-1001: view latest/ })).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "Filter by status" }), {
      target: { value: "all" },
    });
    await user.click(screen.getByRole("button", { name: /VF-1002: view latest/ }));
    await waitFor(() =>
      expect(getVehicleTelemetry).toHaveBeenLastCalledWith(
        "VF-1002",
        expect.any(AbortSignal),
      ),
    );
    expect(screen.getAllByText("TEMP_HIGH")).not.toHaveLength(0);
  });

  it("defaults to non-benchmark vehicles and applies the source to summaries and table", async () => {
    getVehicles.mockResolvedValue([benchmarkVehicle, vehicle, secondVehicle]);
    render(<FleetDashboard />);

    await screen.findByRole("button", { name: /VF-1001: view latest/ });
    expect(screen.getByRole("combobox", { name: "Filter by fleet source" })).toHaveValue("operational");
    expect(screen.getByText("2", { selector: ".stat-number" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /BENCH-run-0001: view latest/ })).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "Filter by fleet source" }), {
      target: { value: "benchmark" },
    });
    expect(await screen.findByRole("button", { name: /BENCH-run-0001: view latest/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /VF-1001: view latest/ })).not.toBeInTheDocument();
    expect(screen.getAllByText("1", { selector: ".stat-number" })[0]).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "Filter by fleet source" }), {
      target: { value: "all" },
    });
    expect(await screen.findByRole("button", { name: /VF-1001: view latest/ })).toBeInTheDocument();
    expect(screen.getByText("3", { selector: ".stat-number" })).toBeInTheDocument();
  });

  it("falls back to all vehicles with an explanation when only benchmark IDs exist", async () => {
    getVehicles.mockResolvedValue([benchmarkVehicle]);
    render(<FleetDashboard />);

    expect(await screen.findByText(/No non-benchmark vehicles are available/)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Filter by fleet source" })).toHaveValue("all");
    expect(screen.getByRole("button", { name: /BENCH-run-0001: view latest/ })).toBeInTheDocument();
    expect(screen.getAllByText("1", { selector: ".stat-number" })[0]).toBeInTheDocument();
  });

  it("paginates 25 rows at a time and resets pagination when search changes", async () => {
    const manyVehicles = Array.from({ length: 60 }, (_, index): VehicleState => ({
      ...vehicle,
      vehicle_id: `VF-${String(index + 1).padStart(4, "0")}`,
      status: index === 59 ? "maintenance" : "ready",
      fault_code: index === 59 ? "LAST_VEHICLE" : null,
    }));
    getVehicles.mockResolvedValue(manyVehicles);
    const user = userEvent.setup();
    render(<FleetDashboard />);

    await screen.findByRole("button", { name: /VF-0001: view latest/ });
    expect(screen.getAllByRole("row")).toHaveLength(26);
    expect(screen.getByText("Showing 1–25 of 60 vehicles")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getAllByRole("row")).toHaveLength(26);
    expect(screen.getByText("Showing 26–50 of 60 vehicles")).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox", { name: "Search vehicles" }), "VF-0060");
    expect(await screen.findByRole("button", { name: /VF-0060: view latest/ })).toBeInTheDocument();
    expect(screen.getByText("Showing 1–1 of 1 vehicles")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("shows a selected vehicle's latest state and loads its historical telemetry", async () => {
    const user = userEvent.setup();
    getVehicles.mockResolvedValue([vehicle, secondVehicle]);
    render(<FleetDashboard />);

    await screen.findByRole("button", { name: /VF-1001: view latest/ });
    await user.click(screen.getByRole("button", { name: /VF-1002: view latest/ }));
    const detail = await screen.findByRole("region", { name: "Details for VF-1002" });
    await waitFor(() =>
      expect(getVehicleTelemetry).toHaveBeenLastCalledWith(
        "VF-1002",
        expect.any(AbortSignal),
      ),
    );
    expect(within(detail).getAllByText("Maintenance")).not.toHaveLength(0);
    expect(within(detail).getByText("37.0%")).toBeInTheDocument();
    expect(within(detail).getAllByText("81.0°C")).not.toHaveLength(0);
    expect(within(detail).getByText("TEMP_HIGH")).toBeInTheDocument();
  });

  it("shows an empty message and zero count when source/search filters find no rows", async () => {
    const user = userEvent.setup();
    getVehicles.mockResolvedValue([vehicle]);
    render(<FleetDashboard />);

    await screen.findByRole("button", { name: /VF-1001: view latest/ });
    await user.type(screen.getByRole("searchbox", { name: "Search vehicles" }), "missing");
    expect(await screen.findByText("No vehicles match your search or filters.")).toBeInTheDocument();
    expect(screen.getByText("No vehicles match your search or filters.")).toBeInTheDocument();
    expect(screen.getByText("0", { selector: ".stat-number" })).toBeInTheDocument();
  });

  it("polls again after five seconds without overlapping an in-flight request", async () => {
    vi.useFakeTimers();
    let resolveInitial: (value: VehicleState[]) => void = () => undefined;
    getVehicles.mockReturnValue(
      new Promise<VehicleState[]>((resolve) => {
        resolveInitial = resolve;
      }),
    );
    const { unmount } = render(<FleetDashboard />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(getVehicles).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveInitial([vehicle]);
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_999);
    });
    expect(getVehicles).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(getVehicles).toHaveBeenCalledTimes(2);
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(getVehicles).toHaveBeenCalledTimes(2);
  });

  it("displays backend errors without hiding them in a success state", async () => {
    getVehicles.mockRejectedValue(new Error("Backend is unavailable"));
    render(<FleetDashboard />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Backend is unavailable",
    );
    expect(screen.getByText("No vehicles reporting yet")).toBeInTheDocument();
  });
});
