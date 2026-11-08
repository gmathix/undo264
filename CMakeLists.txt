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
#   ENABLE_ASAN        OFF (default): AddressSanitizer
#   ENABLE_UBSAN       OFF (default): UndefinedBehaviorSanitizer
#   ENABLE_MSAN        OFF (default): MemorySanitizer (Clang + Linux only, exclusive with ASan)
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
set(FUZZ_ENGINE "-fsanitize=fuzzer" CACHE STRING
        "Fuzzing engine: -fsanitize=fuzzer, or $LIB_FUZZING_ENGINE under OSS-Fuzz/ClusterFuzzLite")


# default to a sane build type if none is given (for makefile/ninja only)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE "Release" CACHE STRING "Build type" FORCE)
    set_property(CACHE CMAKE_BUILD_TYPE PROPERTY STRINGS Debug Release RelWithDebInfo)
endif()

# ---- User parameters ---------------------------------------------------------
option(USE_NATIVE_ARCH "Optimize for the host CPU (-march=native); native builds only" ON)
option(ENABLE_ASAN  "Build with AddressSanitizer" OFF)
option(ENABLE_UBSAN "Build with UndefinedBehaviorSanitizer" OFF)
option(ENABLE_MSAN  "Build with MemorySanitizer (Clang on Linux only; exclusive with ASan)" OFF)
option(BUILD_TOOLS "Build gen_rgb_video / compare_streams helper tools" ON)
option(COPY_MINGW_RUNTIME "MinGW: copy runtime DLLs next to the executables after building" OFF)
option(BUILD_FUZZERS "Build the fuzz target (needs clang for libFuzzer)" OFF)
option(BUILD_REPLAY_TARGETS "Build replay_asan / replay_ubsan / replay_msan (one sanitizer each; Clang on Linux)" OFF)


set(UNDO264_SOURCES
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
        src/util/dump_info.c
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
        $<$<CONFIG:Debug>:-O0 -g3>
)

# ---- Architecture ------------------------------------------------------------
if(UNDO264_OS STREQUAL "wasm")
    target_compile_options(undo264_warnings INTERFACE -msimd128 -msse4.2 -mavx2)
    target_compile_definitions(undo264_warnings INTERFACE ARCH_X86=1 WASM)
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

set(_san "")
if(ENABLE_ASAN)
    list(APPEND _san address)
endif()
if(ENABLE_UBSAN)
    list(APPEND _san undefined)
endif()
if(ENABLE_MSAN)
    list(APPEND _san memory)
endif()

if(_san AND WIN32 AND CMAKE_C_COMPILER_ID STREQUAL "GNU")
    # MinGW-w64 GCC doesn't ship sanitizer runtimes
    message(WARNING "Sanitizers are not supported with MinGW GCC; ignoring")
    set(_san "")
endif()

if(ENABLE_ASAN AND ENABLE_MSAN)
    message(FATAL_ERROR "ENABLE_ASAN and ENABLE_MSAN are mutually exclusive")
endif()
if(ENABLE_MSAN)
    if(NOT CMAKE_C_COMPILER_ID MATCHES "Clang")
        message(FATAL_ERROR "ENABLE_MSAN needs Clang (use -DUNDO264_COMPILER=clang in a fresh build directory)")
    endif()
    if(NOT UNDO264_OS STREQUAL "linux")
        message(FATAL_ERROR "ENABLE_MSAN is Linux-only (not supported on ${UNDO264_OS})")
    endif()
endif()

list(JOIN _san "," _san_list)
if(NOT _san_list STREQUAL "")
    set(_san_flags "-fsanitize=${_san_list}")
    if(ENABLE_ASAN)
        list(APPEND _san_flags -fno-omit-frame-pointer -g)   # readable stack traces
    endif()
    if(ENABLE_MSAN)
        list(APPEND _san_flags -fPIE -fno-omit-frame-pointer -g)
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


# ---- Main Target ---------------------------------------------------
add_library(undo264_core STATIC ${UNDO264_SOURCES})
target_include_directories(undo264_core PUBLIC src)
target_link_libraries(undo264_core PUBLIC
        ${UNDO264_LIBM} undo264_warnings undo264_sanitizers undo264_link)
target_compile_definitions(undo264_core PUBLIC
        $<$<CONFIG:Debug>:SLICES_LOG=1>
        $<$<BOOL:${WIN32}>:WIN32>
        $<$<BOOL:${UNIX}>:UNIX>)

add_executable(undo264 src/main.c)
target_link_libraries(undo264 PRIVATE undo264_core)
undo264_copy_mingw_runtime(undo264)



if(BUILD_FUZZERS)
    add_executable(fuzz_decoder fuzzing/fuzz_target.c)
    separate_arguments(_fuzz_engine UNIX_COMMAND "${FUZZ_ENGINE}")
    target_link_libraries(fuzz_decoder PRIVATE undo264_core ${_fuzz_engine})

    target_compile_options(undo264_core PUBLIC -UNDEBUG)
endif()

# ---- Replay targets: one executable per sanitizer ----------------------------
# Each variant gets its own instrumented copy of the core library, since MSan
# needs every translation unit instrumented (mixing would give false positives).
function(undo264_add_replay_target suffix sanitizer)
    set(_compile_flags -fsanitize=${sanitizer} -fno-omit-frame-pointer -g)
    set(_link_flags    -fsanitize=${sanitizer})

    if(sanitizer STREQUAL "memory")
        list(APPEND _compile_flags -fsanitize-memory-track-origins=2)  # report where uninit values came from
    elseif(sanitizer STREQUAL "undefined")
        list(APPEND _compile_flags -fno-sanitize-recover=undefined)    # abort on the first UB, like ASan/MSan
    endif()

    set(_core undo264_core_${suffix})
    add_library(${_core} STATIC ${UNDO264_SOURCES})
    target_include_directories(${_core} PUBLIC src)
    target_link_libraries(${_core} PUBLIC
            ${UNDO264_LIBM} undo264_warnings undo264_link)
    target_compile_definitions(${_core} PUBLIC
            $<$<CONFIG:Debug,DebugASAN>:SLICES_LOG=1>
            $<$<BOOL:${WIN32}>:WIN32>
            $<$<BOOL:${UNIX}>:UNIX>)
    target_compile_options(${_core} PUBLIC ${_compile_flags} -UNDEBUG)
    target_link_options(${_core} PUBLIC ${_link_flags})

    add_executable(replay_${suffix} fuzzing/replay_main.c fuzzing/fuzz_target.c)
    target_link_libraries(replay_${suffix} PRIVATE ${_core})
endfunction()

if(BUILD_REPLAY_TARGETS)
    if(NOT CMAKE_C_COMPILER_ID MATCHES "Clang")
        message(FATAL_ERROR "BUILD_REPLAY_TARGETS needs Clang (MSan is Clang-only). "
                "Use -DUNDO264_COMPILER=clang in a fresh build directory.")
    endif()
    if(NOT UNDO264_OS STREQUAL "linux")
        message(FATAL_ERROR "BUILD_REPLAY_TARGETS is Linux-only (MemorySanitizer is not supported on ${UNDO264_OS}).")
    endif()

    undo264_add_replay_target(asan  address)
    undo264_add_replay_target(ubsan undefined)
    undo264_add_replay_target(msan  memory)
endif()



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
        " sanitize='${_san_list}' tools=${BUILD_TOOLS}")