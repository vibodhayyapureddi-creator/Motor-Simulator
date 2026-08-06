cmake_minimum_required(VERSION 3.15)
project(motorsim LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if (NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# --- Build options -----------------------------------------------------
# The core engine (engine/) always builds -- it has zero third-party
# dependencies (standard library only).
#
# MOTORSIM_BUILD_DEMO: a small standalone C++ executable that exercises the
# engine directly, with no Python/pybind11 involved. Handy for quickly
# checking the physics, and for building/testing the engine on a machine
# that doesn't have Python set up yet.
#
# MOTORSIM_BUILD_PYTHON_BINDINGS: builds the pybind11 extension module that
# the Python control layer (python/motorsim_app) imports. Requires either a
# `pip install pybind11` in the active Python environment, or network
# access for CMake's FetchContent to pull pybind11 from GitHub -- see
# bindings/CMakeLists.txt and README.md for details.
option(MOTORSIM_BUILD_DEMO "Build the standalone C++ demo executable" ON)
option(MOTORSIM_BUILD_PYTHON_BINDINGS "Build the pybind11 Python extension module" OFF)

add_subdirectory(engine)

if (MOTORSIM_BUILD_DEMO)
    add_subdirectory(demo)
endif()

if (MOTORSIM_BUILD_PYTHON_BINDINGS)
    add_subdirectory(bindings)
endif()
