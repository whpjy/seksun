cmake_minimum_required(VERSION 3.20)

project(occt_projector VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(
    OpenCASCADE REQUIRED
    COMPONENTS
        FoundationClasses
        ModelingData
        ModelingAlgorithms
        Visualization
        ApplicationFramework
        DataExchange
)

add_executable(occt-projector src/main.cpp)

target_include_directories(
    occt-projector
    PRIVATE ${OpenCASCADE_INCLUDE_DIR}
)

target_link_libraries(
    occt-projector
    PRIVATE
        TKHLR
        TKSTEP
        TKSTEPAttr
        TKSTEP209
        TKSTEPBase
        TKXSBase
        TKShHealing
        TKTopAlgo
        TKGeomAlgo
        TKBRep
        TKGeomBase
        TKG3d
        TKG2d
        TKMath
        TKernel
)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(occt-projector PRIVATE -Wall -Wextra -Wpedantic)
endif()

install(TARGETS occt-projector RUNTIME DESTINATION bin)
