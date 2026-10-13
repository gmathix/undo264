# ==============================================================================
# undo264 — CMakeLists.txt,
# Generated with AI assistance with Claude Sonnet 5.5
# Inspired by edge264's Makefile
#
# Supported targets:   macOS, Linux, Windows (MinGW-w64), WebAssembly (Emscripten)
# Supported compilers: GNU GCC and LLVM Clang (AppleClang included)
#
# Parameters:
#   UNDO264_COMPILER   gcc | clang
#   CMAKE_BUILD_TYPE   Release (default) | Debug | RelWithDebInfo
#   USE_NATIVE_ARCH    ON (default): -march=native, for native builds on macOS/Linux/
#                      Windows only. Cross builds must pass their own -march through
#                      CMAKE_C_FLAGS, as with edge264's CFLAGS.
#   SANITIZE           comma-separated list passed to -fsanitize=, e.g. address,undefined
#   BUILD_TOOLS        ON (default): also build gen_rgb_video / compare_streams
#   COPY_MINGW_RUNTIME OFF (default): MinGW only, copy runtime DLLs next to the .exes
# ==============================================================================

cmake_minimum_required(VERSION 3.20)

# ---- Compiler selection ----------------------------------------------------
set(UNDO264_COMPILER "gcc" CACHE STRING "Compiler to build with: gcc | clang")
set_property(CACHE UNDO264_COMPILER PROPERTY STRINGS gcc clang)

if(NOT UNDO264_COMPILER MATCHES "^(gcc|clang)$")
    message(FATAL_ERROR "UNDO264_COMPILER='${UNDO264_COMPILER}' is invalid. Accepted values: gcc  clang")
endif()

if(NOT CMAKE_C_COMPILER AND NOT DEFINED ENV{CC} AND NOT CMAKE_TOOLCHAIN_FILE)
    if(UNDO264_COMPILER STREQUAL "auto")
        if(CMAKE_HOST_SYSTEM_NAME MATCHES "^(Linux|Windows)$")
            set(CMAKE_C_COMPILER gcc)
        endif()
    else()
        set(CMAKE_C_COMPILER ${UNDO264_COMPILER})
    endif()
endif()

project(undo264
        VERSION 1.0
        DESCRIPTION "A H.264 decoder written in C"
        LANGUAGES C
)

# ---- Compiler check ----------------------------------------------------------
if(MSVC OR NOT CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    message(FATAL_ERROR
            "undo264 requires GCC or Clang (found: ${CMAKE_C_COMPILER_ID}, ${CMAKE_C_COMPILER}). "
            "On Windows, put MinGW-w64's bin folder on the PATH, or pass "
            "-DCMAKE_C_COMPILER=<path to gcc.exe or clang.exe>. "
            "Use a fresh build directory if this one already picked another compiler.")
endif()

# ---- Target OS ---------------------------------------------------------------
if(EMSCRIPTEN)
    set(UNDO264_OS wasm)
elseif(CMAKE_SYSTEM_NAME STREQUAL "Darwin")
    set(UNDO264_OS macos)
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    set(UNDO264_OS linux)
elseif(WIN32)
    set(UNDO264_OS windows)
else()
    message(FATAL_ERROR
            "Unsupported target system '${CMAKE_SYSTEM_NAME}'. "
            "Supported: macOS, Linux, Windows (MinGW), WebAssembly (Emscripten)")
endif()

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)

# default to a sane build type if none is given (for makefile/ninja only)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE "Release" CACHE STRING "Build type" FORCE)
    set_property(CACHE CMAKE_BUILD_TYPE PROPERTY STRINGS Debug Release RelWithDebInfo)
endif()

# ---- User parameters ---------------------------------------------------------
option(USE_NATIVE_ARCH "Optimize for the host CPU (-march=native); native builds only" ON)
set(SANITIZE "" CACHE STRING "Comma-separated sanitizers for -fsanitize=, e.g. address,undefined")
option(BUILD_TOOLS "Build gen_rgb_video / compare_streams helper tools" ON)
option(COPY_MINGW_RUNTIME "MinGW: copy runtime DLLs next to the executables after building" OFF)

set(UNDO264_SOURCES
        src/main.c
        src/intra.c
        src/annexb.c
        src/ps.c
        src/decoder.c
        src/mb.c
        src/cavlc.c
        src/vlc.c
        src/picture.c
        src/dequant.c
        src/dpb.c
        src/loopfilter.c
        src/cabac.c
        src/util/logger.c
        src/tests/profiler.c
        src/dsp_init.c
        src/dsp_init.h
)

# ---- Compile flags -----------------------------------------------------------
add_library(undo264_warnings INTERFACE)
target_compile_options(undo264_warnings INTERFACE
        -Wall -Wextra -Wpedantic
        -flax-vector-conversions -Wno-override-init
        $<$<CONFIG:Release>:-O3>
        $<$<CONFIG:Debug,DebugASAN>:-O0 -g3>
)

# ---- Architecture ------------------------------------------------------------
if(UNDO264_OS STREQUAL "wasm")
    target_compile_options(undo264_warnings INTERFACE -msimd128)
elseif(USE_NATIVE_ARCH AND NOT CMAKE_CROSSCOMPILING
        AND UNDO264_OS MATCHES "^(macos|linux|windows)$")
    include(CheckCCompilerFlag)
    check_c_compiler_flag(-march=native HAVE_MARCH_NATIVE)
    if(HAVE_MARCH_NATIVE)
        target_compile_options(undo264_warnings INTERFACE -march=native)
        if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|AMD64|x64")
            target_compile_definitions(undo264_warnings INTERFACE ARCH_X86=1)
        endif()
    else()
        message(WARNING "USE_NATIVE_ARCH requested but -march=native is unsupported; ignoring")
    endif()
endif()

# ---- Sanitizers --------------------------------------------------------------
add_library(undo264_sanitizers INTERFACE)
set(_san "${SANITIZE}")
if(NOT "${_san}" STREQUAL "" AND WIN32 AND CMAKE_C_COMPILER_ID STREQUAL "GNU")
    # MinGW-w64 GCC doesn't ship sanitizer runtimes
    message(WARNING "SANITIZE is not supported with MinGW GCC; ignoring")
    set(_san "")
endif()
if(NOT "${_san}" STREQUAL "")
    set(_san_flags "-fsanitize=${_san}")
    if(_san MATCHES "address")
        if(_san MATCHES "memory")
            message(FATAL_ERROR "SANITIZE: 'address' and 'memory' are mutually exclusive")
        endif()
        list(APPEND _san_flags -fno-omit-frame-pointer -g)   # readable stack traces
    endif()
    if(_san MATCHES "memory")
        list(APPEND _san_flags -fPIE)                        # reliable interception
    endif()
    target_compile_options(undo264_sanitizers INTERFACE ${_san_flags})
    target_link_options(undo264_sanitizers INTERFACE ${_san_flags})
endif()

# ---- Link flags --------------------------------------------------------------
add_library(undo264_link INTERFACE)
if(UNDO264_OS STREQUAL "wasm")
    # strict mode, growable memory, and direct host filesystem access under node
    target_link_options(undo264_link INTERFACE -sSTRICT=1 -sALLOW_MEMORY_GROWTH=1 -sNODERAWFS=1)
elseif(MINGW)
    # Link libgcc statically
    target_link_options(undo264_link INTERFACE -static-libgcc)
endif()

# libm: separate library everywhere except Emscripten, where it is built in
if(UNDO264_OS STREQUAL "wasm")
    set(UNDO264_LIBM "")
else()
    set(UNDO264_LIBM m)
endif()


function(undo264_copy_mingw_runtime target)
    if(NOT MINGW OR NOT COPY_MINGW_RUNTIME)
        return()
    endif()
    get_filename_component(_mingw_bin "${CMAKE_C_COMPILER}" DIRECTORY)
    foreach(_dll libwinpthread-1.dll libgcc_s_seh-1.dll libgcc_s_dw2-1.dll libgcc_s_sjlj-1.dll)
        if(EXISTS "${_mingw_bin}/${_dll}")
            add_custom_command(TARGET ${target} POST_BUILD
                    COMMAND ${CMAKE_COMMAND} -E copy_if_different
                    "${_mingw_bin}/${_dll}" "$<TARGET_FILE_DIR:${target}>"
                    VERBATIM)
        endif()
    endforeach()
endfunction()


# ---- Main target -------------------------------------------------------------
add_executable(undo264 ${UNDO264_SOURCES})
target_link_libraries(undo264 PRIVATE ${UNDO264_LIBM} undo264_warnings undo264_sanitizers undo264_link)
target_include_directories(undo264 PRIVATE src)
target_compile_definitions(undo264 PRIVATE
        $<$<CONFIG:Debug,DebugASAN>:SLICES_LOG=1>
)
if(WIN32)
    target_compile_definitions(undo264 PRIVATE WIN32)
endif()
if(UNIX)
    target_compile_definitions(undo264 PRIVATE UNIX)
endif()
undo264_copy_mingw_runtime(undo264)


# ---- Optional helper tools ---------------------------------------------------
if(BUILD_TOOLS)
    add_executable(gen_rgb_video src/tests/gen_rgb_video.c)
    target_link_libraries(gen_rgb_video PRIVATE ${UNDO264_LIBM} undo264_warnings undo264_link)
    undo264_copy_mingw_runtime(gen_rgb_video)

    add_executable(compare_streams src/tests/streamcomp.c)
    target_link_libraries(compare_streams PRIVATE undo264_warnings undo264_link)
    undo264_copy_mingw_runtime(compare_streams)
endif()


# ---- Install rules -----------------------------------------------------------
include(GNUInstallDirs)
install(TARGETS undo264 RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
if(UNDO264_OS STREQUAL "wasm")
    # emcc writes undo264.wasm next to the undo264.js glue file
    install(FILES "$<TARGET_FILE_DIR:undo264>/undo264.wasm" DESTINATION ${CMAKE_INSTALL_BINDIR})
endif()


# ---- Configuration summary (cf. `make help` in edge264) ------------------------
if(HAVE_MARCH_NATIVE)
    set(_native yes)
else()
    set(_native no)
endif()
message(STATUS "undo264 ${PROJECT_VERSION}: OS=${UNDO264_OS}"
        " compiler=${CMAKE_C_COMPILER_ID} (${CMAKE_C_COMPILER}) [UNDO264_COMPILER=${UNDO264_COMPILER}]"
        " build=${CMAKE_BUILD_TYPE} native=${_native}"
        " sanitize='${_san}' tools=${BUILD_TOOLS}")