#include "retry.hpp"

#include <array>
#include <cstdlib>
#include <iostream>
#include <random>
#include <optional>

namespace {

void require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "Test failed: " << message << '\n';
        std::exit(EXIT_FAILURE);
    }
}

}

int main() {
    require(waypoint::isSuccessfulHttpStatus(200), "HTTP 200 is successful");
    require(waypoint::isSuccessfulHttpStatus(201), "HTTP 201 is successful");
    require(waypoint::isSuccessfulHttpStatus(202), "HTTP 202 accepted is successful");
    require(!waypoint::isSuccessfulHttpStatus(400), "HTTP 400 is not successful");
    require(waypoint::shouldRetry(std::nullopt), "connection failures are retryable");
    require(waypoint::shouldRetry(429), "HTTP 429 is retryable");
    require(waypoint::shouldRetry(500), "HTTP 500 is retryable");
    require(waypoint::shouldRetry(503), "HTTP 503 is retryable");
    require(!waypoint::shouldRetry(200), "HTTP 200 is not retryable");
    require(!waypoint::shouldRetry(400), "HTTP 400 is not retryable");
    require(!waypoint::shouldRetry(422), "HTTP 422 is not retryable");

    const std::array<std::chrono::milliseconds, 6> expected_bounds{
        std::chrono::milliseconds{500},
        std::chrono::milliseconds{1000},
        std::chrono::milliseconds{2000},
        std::chrono::milliseconds{4000},
        std::chrono::milliseconds{8000},
        std::chrono::milliseconds{8000},
    };
    for (unsigned int index = 0; index < expected_bounds.size(); ++index) {
        require(
            waypoint::retryDelayUpperBound(index) == expected_bounds[index],
            "retry upper bound doubles and remains capped"
        );
    }

    std::mt19937 random(42);
    for (unsigned int retry_index = 0; retry_index < waypoint::max_retries; ++retry_index) {
        for (int sample = 0; sample < 100; ++sample) {
            const auto delay = waypoint::calculateRetryDelay(retry_index, random);
            require(delay.count() >= 0, "jittered retry delay is nonnegative");
            require(
                delay <= waypoint::retryDelayUpperBound(retry_index),
                "jittered retry delay does not exceed exponential upper bound"
            );
        }
    }
}
