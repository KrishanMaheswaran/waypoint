#include "telemetry.hpp"
#include "retry.hpp"

#include <charconv>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <thread>
#include <system_error>

#include <httplib.h>

namespace {

volatile std::sig_atomic_t running = 1;

void handleSignal(int) {
    running = 0;
}

struct Options {
    std::string api_url = "http://localhost:8000";
    std::size_t vehicle_count = 5;
    std::chrono::seconds interval{2};
};

void printUsage(const char* executable) {
    std::cout << "Usage: " << executable
              << " [--url URL] [--vehicles COUNT] [--interval SECONDS]\n"
                 "Defaults: --url http://localhost:8000 --vehicles 5 --interval 2\n";
}

std::uint64_t parsePositiveInteger(const std::string& value, const std::string& name) {
    std::uint64_t parsed = 0;
    const auto result = std::from_chars(value.data(), value.data() + value.size(), parsed);
    if (result.ec != std::errc{} || result.ptr != value.data() + value.size() || parsed == 0) {
        throw std::invalid_argument(name + " must be a positive integer");
    }
    return parsed;
}

Options parseOptions(int argc, char* argv[]) {
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--help") {
            printUsage(argv[0]);
            std::exit(0);
        }
        if (argument != "--url" && argument != "--vehicles" && argument != "--interval") {
            throw std::invalid_argument("unknown argument: " + argument);
        }
        if (index + 1 >= argc) {
            throw std::invalid_argument("missing value for " + argument);
        }

        const std::string value = argv[++index];
        if (argument == "--url") {
            options.api_url = value;
        } else if (argument == "--vehicles") {
            const auto count = parsePositiveInteger(value, "vehicle count");
            if (count > 10000) {
                throw std::invalid_argument("vehicle count must not exceed 10000");
            }
            options.vehicle_count = static_cast<std::size_t>(count);
        } else {
            const auto interval = parsePositiveInteger(value, "interval");
            if (interval > 86400) {
                throw std::invalid_argument("interval must not exceed 86400 seconds");
            }
            options.interval = std::chrono::seconds(interval);
        }
    }
    return options;
}

void waitUntil(std::chrono::steady_clock::time_point deadline) {
    while (running && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
}

}

int main(int argc, char* argv[]) {
    Options options;
    try {
        options = parseOptions(argc, argv);
    } catch (const std::invalid_argument& error) {
        std::cerr << "Error: " << error.what() << '\n';
        printUsage(argv[0]);
        return 2;
    }

    std::signal(SIGINT, handleSignal);
    std::signal(SIGTERM, handleSignal);

    httplib::Client client(options.api_url);
    client.set_connection_timeout(5, 0);
    client.set_read_timeout(5, 0);
    client.set_write_timeout(5, 0);

    std::random_device seed_source;
    std::seed_seq seed{
        seed_source(),
        seed_source(),
        seed_source(),
        seed_source(),
    };
    std::mt19937 random(seed);
    std::cout << "Sending telemetry to " << options.api_url << "/telemetry for "
              << options.vehicle_count << " vehicles every " << options.interval.count()
              << " seconds. Press Ctrl+C to stop.\n";

    while (running) {
        const auto next_cycle = std::chrono::steady_clock::now() + options.interval;
        const auto timestamp = std::chrono::system_clock::now();
        for (std::size_t index = 1; index <= options.vehicle_count && running; ++index) {
            const auto vehicle_id = "WP-" + std::to_string(1000 + index);
            const auto event = waypoint::generateTelemetry(vehicle_id, random, timestamp);
            const auto request_body = waypoint::serializeTelemetry(event);
            for (unsigned int retry = 0; running; ++retry) {
                const auto response = client.Post("/telemetry", request_body, "application/json");
                if (response && waypoint::isSuccessfulHttpStatus(response->status)) {
                    std::cout << "POST /telemetry " << vehicle_id << " event "
                              << event.event_id << ": HTTP " << response->status << '\n';
                    break;
                }

                const std::optional<int> http_status = response
                    ? std::optional<int>(response->status)
                    : std::nullopt;
                if (!waypoint::shouldRetry(http_status) || retry >= waypoint::max_retries) {
                    std::cerr << "POST /telemetry failed for " << vehicle_id << " event "
                              << event.event_id;
                    if (response) {
                        std::cerr << ": HTTP " << response->status << " " << response->body;
                    } else {
                        std::cerr << ": " << httplib::to_string(response.error());
                    }
                    std::cerr << " (after " << retry << " retries)\n";
                    break;
                }

                const auto delay = waypoint::calculateRetryDelay(retry, random);
                std::cerr << "POST /telemetry transient failure for " << vehicle_id << " event "
                          << event.event_id << "; retry " << (retry + 1) << "/"
                          << waypoint::max_retries << " in " << delay.count() << " ms\n";
                waitUntil(std::chrono::steady_clock::now() + delay);
            }
        }
        if (running) {
            waitUntil(next_cycle);
        }
    }

    std::cout << "\nSimulator stopped.\n";
    return 0;
}
