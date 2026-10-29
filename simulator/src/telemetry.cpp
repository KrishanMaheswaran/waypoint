#include "telemetry.hpp"

#include <array>
#include <chrono>
#include <ctime>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <utility>

#include <nlohmann/json.hpp>

namespace waypoint {
namespace {

std::string generateEventId(std::mt19937& random) {
    std::array<unsigned char, 16> bytes{};
    std::uniform_int_distribution<unsigned int> byte_distribution(0, 255);
    for (auto& byte : bytes) {
        byte = static_cast<unsigned char>(byte_distribution(random));
    }
    bytes[6] = static_cast<unsigned char>((bytes[6] & 0x0f) | 0x40);
    bytes[8] = static_cast<unsigned char>((bytes[8] & 0x3f) | 0x80);

    std::ostringstream formatted;
    formatted << std::hex << std::setfill('0');
    for (std::size_t index = 0; index < bytes.size(); ++index) {
        if (index == 4 || index == 6 || index == 8 || index == 10) {
            formatted << '-';
        }
        formatted << std::setw(2) << static_cast<unsigned int>(bytes[index]);
    }
    return formatted.str();
}

std::string formatTimestamp(std::chrono::system_clock::time_point timestamp) {
    const auto time = std::chrono::system_clock::to_time_t(timestamp);
    std::tm utc_time{};
#if defined(_WIN32)
    gmtime_s(&utc_time, &time);
#else
    gmtime_r(&time, &utc_time);
#endif

    std::ostringstream formatted;
    formatted << std::put_time(&utc_time, "%Y-%m-%dT%H:%M:%SZ");
    return formatted.str();
}

}

TelemetryEvent generateTelemetry(
    std::string vehicle_id,
    std::mt19937& random,
    std::chrono::system_clock::time_point timestamp
) {
    if (vehicle_id.empty() || vehicle_id.size() > 100) {
        throw std::invalid_argument("vehicle_id must contain 1 to 100 characters");
    }

    static constexpr std::array statuses{"idle", "charging", "ready", "maintenance"};
    std::uniform_real_distribution<double> battery_distribution(20.0, 100.0);
    std::uniform_real_distribution<double> temperature_distribution(18.0, 38.0);
    std::uniform_int_distribution<std::size_t> status_distribution(0, statuses.size() - 1);

    return {
        generateEventId(random),
        std::move(vehicle_id),
        battery_distribution(random),
        statuses[status_distribution(random)],
        temperature_distribution(random),
        std::nullopt,
        timestamp,
    };
}

std::string serializeTelemetry(const TelemetryEvent& event) {
    const nlohmann::json payload{
        {"event_id", event.event_id},
        {"vehicle_id", event.vehicle_id},
        {"battery_percentage", event.battery_percentage},
        {"status", event.status},
        {"temperature", event.temperature},
        {"fault_code", event.fault_code ? nlohmann::json(*event.fault_code) : nlohmann::json(nullptr)},
        {"timestamp", formatTimestamp(event.timestamp)},
    };
    return payload.dump();
}

}
