#pragma once

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <optional>
#include <random>

namespace waypoint {

inline constexpr unsigned int max_retries = 3;
inline constexpr std::chrono::milliseconds retry_base_delay{500};
inline constexpr std::chrono::milliseconds retry_max_delay{8000};

inline bool isSuccessfulHttpStatus(int status) {
    return status >= 200 && status < 300;
}

inline bool shouldRetry(std::optional<int> http_status) {
    return !http_status.has_value() || *http_status == 429 || (*http_status >= 500 && *http_status < 600);
}

inline std::chrono::milliseconds retryDelayUpperBound(unsigned int retry_index) {
    auto delay = retry_base_delay;
    for (unsigned int index = 0; index < retry_index && delay < retry_max_delay; ++index) {
        delay = std::min(delay * 2, retry_max_delay);
    }
    return delay;
}

inline std::chrono::milliseconds calculateRetryDelay(
    unsigned int retry_index,
    std::mt19937& random
) {
    const auto upper_bound = retryDelayUpperBound(retry_index).count();
    std::uniform_int_distribution<std::int64_t> distribution(0, upper_bound);
    return std::chrono::milliseconds(distribution(random));
}

}
