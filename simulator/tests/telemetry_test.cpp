#include "telemetry.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <random>
#include <string>

#include <nlohmann/json.hpp>

namespace {

void require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "Test failed: " << message << '\n';
        std::exit(EXIT_FAILURE);
    }
}

}

int main() {
    std::mt19937 random(42);
    const auto timestamp = std::chrono::system_clock::from_time_t(1793318400);
    const auto event = waypoint::generateTelemetry("WP-1001", random, timestamp);

    require(event.vehicle_id == "WP-1001", "vehicle ID is serialized into the generated event");
    require(event.battery_percentage >= 20.0 && event.battery_percentage <= 100.0, "battery stays in range");
    require(event.temperature >= 18.0 && event.temperature <= 38.0, "temperature stays in range");
    const std::array<std::string, 4> valid_statuses{"idle", "charging", "ready", "maintenance"};
    require(
        std::find(valid_statuses.begin(), valid_statuses.end(), event.status) != valid_statuses.end(),
        "status matches the API's allowed values"
    );
    require(!event.fault_code.has_value(), "fault code is optional");

    const auto payload = nlohmann::json::parse(waypoint::serializeTelemetry(event));
    require(payload.size() == 6, "JSON contains only the six telemetry schema fields");
    require(payload.at("vehicle_id") == "WP-1001", "JSON preserves vehicle ID");
    require(payload.at("battery_percentage") == event.battery_percentage, "JSON preserves battery percentage");
    require(payload.at("status") == event.status, "JSON preserves status");
    require(payload.at("temperature") == event.temperature, "JSON preserves temperature");
    require(payload.at("fault_code").is_null(), "JSON encodes an empty fault code as null");
    require(payload.at("timestamp") == "2026-10-30T00:00:00Z", "timestamp is formatted as UTC ISO 8601");
}
