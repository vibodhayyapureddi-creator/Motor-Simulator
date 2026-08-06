add_library(motorsim_engine STATIC
    src/DCMotor.cpp
    src/BLDCMotor.cpp
    src/Simulator.cpp
)

target_include_directories(motorsim_engine
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(motorsim_engine PUBLIC cxx_std_17)

# The bindings link this static library into a shared module
# (motorsim_py). On ELF platforms that requires position-independent code
# in every object, or the link fails with "relocation ... can not be used
# when making a shared object; recompile with -fPIC". Windows DLLs are
# always position independent, so this is a no-op for MSVC.
set_target_properties(motorsim_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)

if (MSVC)
    target_compile_options(motorsim_engine PRIVATE /W4)
else()
    target_compile_options(motorsim_engine PRIVATE -Wall -Wextra)
endif()
