# Resolve pybind11: prefer one already installed in the active Python
# environment (`pip install pybind11`), otherwise fetch it from GitHub at
# configure time. Either path requires the machine building this target to
# have Python 3 (with headers) available; the core engine and demo do not.
find_package(pybind11 CONFIG QUIET)

if (NOT pybind11_FOUND)
    include(FetchContent)
    FetchContent_Declare(
        pybind11
        GIT_REPOSITORY https://github.com/pybind/pybind11.git
        GIT_TAG        v2.13.6
    )
    FetchContent_MakeAvailable(pybind11)
endif()

pybind11_add_module(motorsim_py bindings.cpp)
target_link_libraries(motorsim_py PRIVATE motorsim_engine)

# Drop the built extension module next to the Python package so
# `import motorsim_py` works without extra PYTHONPATH setup.
#
# Multi-config generators (Visual Studio / MSVC) normally append a
# per-config subdirectory -- e.g. python/Release/ -- to the output path,
# which would put motorsim_py.pyd somewhere `import motorsim_py` can't find
# it from the python/ folder. Set the per-config properties explicitly too
# so the module always lands directly in python/, regardless of generator
# or build type.
set_target_properties(motorsim_py PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/python
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/python
)
foreach(cfg DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
    set_target_properties(motorsim_py PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY_${cfg} ${CMAKE_SOURCE_DIR}/python
        RUNTIME_OUTPUT_DIRECTORY_${cfg} ${CMAKE_SOURCE_DIR}/python
    )
endforeach()
