cmake_minimum_required(VERSION 3.20)
project(waypoint_simulator LANGUAGES CXX)

if(POLICY CMP0135)
    cmake_policy(SET CMP0135 NEW)
endif()

include(FetchContent)

FetchContent_Declare(
    nlohmann_json
    URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz
)
FetchContent_Declare(
    cpp-httplib
    URL https://github.com/yhirose/cpp-httplib/archive/refs/tags/v0.18.1.tar.gz
)
FetchContent_MakeAvailable(nlohmann_json cpp-httplib)

add_library(telemetry
    src/telemetry.cpp
)
target_include_directories(telemetry PUBLIC include)
target_compile_features(telemetry PUBLIC cxx_std_17)
target_link_libraries(telemetry PUBLIC nlohmann_json::nlohmann_json)

add_executable(waypoint-simulator
    src/main.cpp
)
target_link_libraries(waypoint-simulator PRIVATE telemetry httplib::httplib)

include(CTest)
if(BUILD_TESTING)
    add_executable(telemetry-tests
        tests/telemetry_test.cpp
    )
    target_link_libraries(telemetry-tests PRIVATE telemetry)
    add_test(NAME telemetry-tests COMMAND telemetry-tests)

    add_executable(retry-tests
        tests/retry_test.cpp
    )
    target_include_directories(retry-tests PRIVATE include)
    target_compile_features(retry-tests PRIVATE cxx_std_17)
    add_test(NAME retry-tests COMMAND retry-tests)
endif()
