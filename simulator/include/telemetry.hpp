#pragma once

#include <chrono>
#include <optional>
#include <random>
#include <string>

namespace waypoint {

struct TelemetryEvent {
    std::string vehicle_id;
    double battery_percentage;
    std::string status;
    double temperature;
    std::optional<std::string> fault_code;
    std::chrono::system_clock::time_point timestamp;
};

TelemetryEvent generateTelemetry(
    std::string vehicle_id,
    std::mt19937& random,
    std::chrono::system_clock::time_point timestamp
);

std::string serializeTelemetry(const TelemetryEvent& event);

}
