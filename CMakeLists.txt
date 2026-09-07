# openQ4 - CMake build
#
# A second build definition alongside meson.build. meson stays canonical: it is
# what CI, packaging, the release pipeline and every supported platform use, and
# this file follows it rather than leads it.
#
# Scope: Linux, SDL3 backend, GL renderer module. Windows and macOS are NOT
# ported and fail with a clear message rather than silently building something
# that differs from the meson output - the MSVC flag set, the macOS OpenAL
# provider policy, notarization and the packaging rules all live in meson.build
# and none of them are reproduced here.
#
# Both builds share the repository's own helper scripts for source discovery and
# code generation - tools/build/meson_sources.py, tools/build/list_sources.py,
# openq4_version.py, generate_savegame_compat_header.py, build_openq4_pack.py
# and generate_pak_header.py - so file lists and generated headers cannot drift
# between them. Compiler and link flags are the part that has to be kept in step
# by hand; they were taken from the meson build's own compile database.
#
#   cmake -B build -S . -G Ninja -DOPENQ4_GAMELIBS_REPO=../openQ4-game
#   cmake --build build

cmake_minimum_required(VERSION 3.20)
project(openQ4 VERSION 0.13.0 LANGUAGES C CXX)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
  message(FATAL_ERROR
    "The openQ4 CMake build currently covers Linux only. Use the meson build "
    "for Windows and macOS: see BUILDING.md. meson remains the canonical build "
    "on every platform, including Linux.")
endif()

find_package(Python3 COMPONENTS Interpreter REQUIRED)
find_package(PkgConfig REQUIRED)
find_package(Threads REQUIRED)

set(OPENQ4_GAMELIBS_REPO "${CMAKE_CURRENT_SOURCE_DIR}/../openQ4-game" CACHE PATH
    "Companion openQ4-game checkout")
option(OPENQ4_BUILD_DEDICATED "Build the dedicated server executable" ON)
option(OPENQ4_BUILD_GAMES "Build the game modules from the companion repository" ON)

get_filename_component(OPENQ4_GAMELIBS_REPO "${OPENQ4_GAMELIBS_REPO}" ABSOLUTE)
if(NOT EXISTS "${OPENQ4_GAMELIBS_REPO}/src/game")
  message(FATAL_ERROR
    "openQ4-game not found at ${OPENQ4_GAMELIBS_REPO}. Clone it beside this "
    "repository or set -DOPENQ4_GAMELIBS_REPO=/path/to/openQ4-game.")
endif()

set(ARCH_TAG x64)
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
  set(ARCH_TAG arm64)
endif()

set(GENERATED_DIR "${CMAKE_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${GENERATED_DIR}")

# ---------------------------------------------------------------------------
# Source discovery, through the repository's own scripts
# ---------------------------------------------------------------------------
function(openq4_emit_sources out_var emit_kind)
  execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/build/meson_sources.py"
            --host-system linux --platform-backend sdl3 --emit ${emit_kind} ${ARGN}
    OUTPUT_VARIABLE _raw RESULT_VARIABLE _rc OUTPUT_STRIP_TRAILING_WHITESPACE
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")
  if(NOT _rc EQUAL 0)
    message(FATAL_ERROR "meson_sources.py --emit ${emit_kind} failed (${_rc})")
  endif()
  string(REPLACE "\n" ";" _lines "${_raw}")
  set(_files)
  foreach(rel IN LISTS _lines)
    string(STRIP "${rel}" rel)
    if(rel)
      list(APPEND _files "${CMAKE_CURRENT_SOURCE_DIR}/${rel}")
    endif()
  endforeach()
  set(${out_var} "${_files}" PARENT_SCOPE)
endfunction()

function(openq4_list_sources out_var subdir)
  execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/build/list_sources.py"
            "${CMAKE_CURRENT_SOURCE_DIR}" "${subdir}" ${ARGN}
    OUTPUT_VARIABLE _raw RESULT_VARIABLE _rc OUTPUT_STRIP_TRAILING_WHITESPACE)
  if(NOT _rc EQUAL 0)
    message(FATAL_ERROR "list_sources.py ${subdir} failed (${_rc})")
  endif()
  string(REPLACE "\n" ";" _lines "${_raw}")
  set(_files)
  foreach(rel IN LISTS _lines)
    string(STRIP "${rel}" rel)
    if(rel)
      list(APPEND _files "${CMAKE_CURRENT_SOURCE_DIR}/${rel}")
    endif()
  endforeach()
  set(${out_var} "${_files}" PARENT_SCOPE)
endfunction()

openq4_emit_sources(ENGINE_CLIENT_SOURCES engine --target-kind client --renderer module
                    --linux-x11-helpers true --include-game false)
# The Linux dedicated discovery deliberately passes no --renderer: it is a
# separate headless source set, not the static-renderer shape. Forcing
# --renderer static here pulled renderer sources in and left the link with 52
# undefined GLEW symbols.
openq4_emit_sources(ENGINE_DED_SOURCES engine --target-kind dedicated
                    --linux-x11-helpers false --include-game false)
openq4_emit_sources(IMAGETOOLS_SOURCES imagetools)
openq4_emit_sources(RENDER_GEO_SOURCES render_geo)
openq4_emit_sources(RENDERER_GL_SOURCES renderer_gl)
openq4_list_sources(BSE_SOURCES src/bse)
openq4_list_sources(IDLIB_SOURCES src/idlib src/idlib/math/Simd_AltiVec.cpp)

# ---------------------------------------------------------------------------
# Staged game sources
#
# The engine compiles against the companion repository's game headers, and the
# savegame-compatibility header is generated from the staged tree. Both builds
# use the same staging script and the same .tmp/gamelibs_stage location.
# ---------------------------------------------------------------------------
set(GAME_STAGE_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.tmp/gamelibs_stage")
execute_process(
  COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/build/stage_gamelibs.py"
          "${CMAKE_CURRENT_SOURCE_DIR}" "${OPENQ4_GAMELIBS_REPO}" "${GAME_STAGE_ROOT}"
  OUTPUT_VARIABLE _stage_out RESULT_VARIABLE _stage_rc OUTPUT_STRIP_TRAILING_WHITESPACE)
if(NOT _stage_rc EQUAL 0)
  message(FATAL_ERROR "stage_gamelibs.py failed (${_stage_rc})")
endif()
string(STRIP "${_stage_out}" GAME_STAGE_ROOT)

# ---------------------------------------------------------------------------
# Generated headers and paks
# ---------------------------------------------------------------------------
add_custom_command(
  OUTPUT "${GENERATED_DIR}/openq4_version_generated.h"
  COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/build/openq4_version.py"
          --source-root "${CMAKE_CURRENT_SOURCE_DIR}"
          --base-version "${PROJECT_VERSION}"
          --header-out "${GENERATED_DIR}/openq4_version_generated.h"
  DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/tools/build/openq4_version.py"
  COMMENT "Generating openq4_version_generated.h" VERBATIM)

add_custom_command(
  OUTPUT "${GENERATED_DIR}/openq4_savegame_compat_generated.h"
  COMMAND "${Python3_EXECUTABLE}"
          "${CMAKE_CURRENT_SOURCE_DIR}/tools/build/generate_savegame_compat_header.py"
          --project-root "${CMAKE_CURRENT_SOURCE_DIR}"
          --game-stage-root "${GAME_STAGE_ROOT}"
          --header-out "${GENERATED_DIR}/openq4_savegame_compat_generated.h"
  DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/tools/build/generate_savegame_compat_header.py"
  COMMENT "Generating openq4_savegame_compat_generated.h" VERBATIM)

# FileSystem.cpp includes openq4_paks_generated.h, so the paks are a build
# dependency of the engine rather than a packaging afterthought.
foreach(pak 0 1)
  add_custom_command(
    OUTPUT "${GENERATED_DIR}/pak${pak}.pk4"
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/build/build_openq4_pack.py"
            --pak-name "pak${pak}.pk4"
            --source-dir "${CMAKE_CURRENT_SOURCE_DIR}/content/baseoq4/pak${pak}"
            --out "${GENERATED_DIR}/pak${pak}.pk4"
            --stage-out "${CMAKE_BINARY_DIR}/baseoq4/pak${pak}.pk4"
    DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/tools/build/build_openq4_pack.py"
    COMMENT "Building pak${pak}.pk4" VERBATIM)
endforeach()

add_custom_command(
  OUTPUT "${GENERATED_DIR}/openq4_paks_generated.h"
  COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/build/generate_pak_header.py"
          --pak0 "${GENERATED_DIR}/pak0.pk4" --pak1 "${GENERATED_DIR}/pak1.pk4"
          --header-out "${GENERATED_DIR}/openq4_paks_generated.h"
  DEPENDS "${GENERATED_DIR}/pak0.pk4" "${GENERATED_DIR}/pak1.pk4"
          "${CMAKE_CURRENT_SOURCE_DIR}/tools/build/generate_pak_header.py"
  COMMENT "Generating openq4_paks_generated.h" VERBATIM)

add_custom_target(openq4_generated_headers DEPENDS
  "${GENERATED_DIR}/openq4_version_generated.h"
  "${GENERATED_DIR}/openq4_savegame_compat_generated.h"
  "${GENERATED_DIR}/openq4_paks_generated.h")

# ---------------------------------------------------------------------------
# Bundled dependencies
# ---------------------------------------------------------------------------
# GLEW is split in two on purpose. Everything compiles against its declarations
# through qgl.h in the precompiled header, but only the renderer module links
# the loader: a module-only client carries no GL, and the Linux dedicated server
# is a headless link set that must not pull in GLX at all. Linking the compiled
# object everywhere left the dedicated target with undefined glXGetProcAddressARB.
add_library(openq4_glew_headers INTERFACE)
target_include_directories(openq4_glew_headers INTERFACE
  "${CMAKE_CURRENT_SOURCE_DIR}/subprojects/glew/include")
target_compile_definitions(openq4_glew_headers INTERFACE GLEW_NO_GLU)

# The renderer's GLEW, resolving through openQ4's SDL3 loader hook.
add_library(openq4_glew STATIC "${CMAKE_CURRENT_SOURCE_DIR}/subprojects/glew/src/glew.c")
target_link_libraries(openq4_glew PUBLIC openq4_glew_headers)
target_compile_definitions(openq4_glew PRIVATE OPENQ4_GLEW_SDL3_LOADER)
set_target_properties(openq4_glew PROPERTIES POSITION_INDEPENDENT_CODE ON)

# Dedicated targets link no GL, but the engine still references GLEW's entry
# points. This variant resolves them through openQ4's target-owned hook, which
# the per-platform dedicated GL stubs implement without loading graphics code.
# GLAPI=extern strips the dllimport decoration so those stubs' plain GL 1.1
# definitions link cleanly.
add_library(openq4_glew_dedicated STATIC "${CMAKE_CURRENT_SOURCE_DIR}/subprojects/glew/src/glew.c")
target_link_libraries(openq4_glew_dedicated PUBLIC openq4_glew_headers)
target_compile_definitions(openq4_glew_dedicated PRIVATE OPENQ4_GLEW_SDL3_LOADER GLAPI=extern)
set_target_properties(openq4_glew_dedicated PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_library(openq4_stb_vorbis STATIC "${CMAKE_CURRENT_SOURCE_DIR}/subprojects/stb_vorbis/stb_vorbis.c")
target_include_directories(openq4_stb_vorbis PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/subprojects/stb_vorbis")
set_target_properties(openq4_stb_vorbis PROPERTIES POSITION_INDEPENDENT_CODE ON)

# SDL3 is vendored as a meson subproject; its own build system is CMake, so it
# is consumed directly here rather than re-described.
set(SDL_SHARED OFF CACHE BOOL "" FORCE)
set(SDL_STATIC ON CACHE BOOL "" FORCE)
set(SDL_TEST_LIBRARY OFF CACHE BOOL "" FORCE)
add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/subprojects/SDL3-3.4.10" sdl3 EXCLUDE_FROM_ALL)

pkg_check_modules(OPENAL REQUIRED IMPORTED_TARGET openal)
pkg_check_modules(X11DEPS REQUIRED IMPORTED_TARGET x11 xext)

# ---------------------------------------------------------------------------
# Shared compile settings
#
# Taken from the meson build's compile database. -ffp-contract=off is
# load-bearing for the same reason as in the game repository: it stops GCC and
# Clang fusing a*b+c into an FMA, which would make identical math diverge
# between architectures. -fno-sanitize=vptr keeps the module boundary's
# -z defs contract satisfiable under UBSan.
# ---------------------------------------------------------------------------
set(OPENQ4_COMMON_DEFINES
  __DOOM_DLL__ USE_OPENAL ID_GL_HARDLINK GLEW_NO_GLU
  _FILE_OFFSET_BITS=64 _FORTIFY_SOURCE=2 _GLIBCXX_ASSERTIONS=1)
set(OPENQ4_COMMON_FLAGS
  -Wall -fpermissive -ffp-contract=off -fno-strict-aliasing
  -fstack-protector-strong -fno-sanitize=vptr)
set(OPENQ4_SDL3_DEFINES USE_SDL3=1 OPENQ4_HAVE_X11_HELPERS=1)

set(OPENQ4_LINK_OPTIONS_HARDENING -Wl,-z,relro -Wl,-z,now -Wl,-z,noexecstack)

set(OPENQ4_INCLUDE_DIRS
  "${GENERATED_DIR}"
  "${CMAKE_CURRENT_SOURCE_DIR}"
  "${CMAKE_CURRENT_SOURCE_DIR}/src"
  "${GAME_STAGE_ROOT}/src/game")

function(openq4_apply_common target)
  target_include_directories(${target} PRIVATE ${OPENQ4_INCLUDE_DIRS})
  target_compile_definitions(${target} PRIVATE ${OPENQ4_COMMON_DEFINES})
  target_compile_options(${target} PRIVATE ${OPENQ4_COMMON_FLAGS})
  target_link_libraries(${target} PRIVATE openq4_glew_headers)
  set_target_properties(${target} PROPERTIES
    CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON
    C_STANDARD 11
    POSITION_INDEPENDENT_CODE ON)
  add_dependencies(${target} openq4_generated_headers)
  # precompiled.h is force-included into every translation unit; meson does this
  # through cpp_pch and CMake's PCH support has the same effect.
  target_precompile_headers(${target} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/idlib/precompiled.h")
endfunction()

# ---------------------------------------------------------------------------
# Static libraries
# ---------------------------------------------------------------------------
add_library(openq4_bse STATIC ${BSE_SOURCES})
openq4_apply_common(openq4_bse)
target_include_directories(openq4_bse PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/bse")

add_library(openq4_imagetools STATIC ${IMAGETOOLS_SOURCES})
openq4_apply_common(openq4_imagetools)

add_library(openq4_render_geo STATIC ${RENDER_GEO_SOURCES})
openq4_apply_common(openq4_render_geo)

# ---------------------------------------------------------------------------
# Renderer module
#
# The module carries its own idlib copy, compiled with engine flavour so its
# codegen and interface layout match the renderer sources. Without it the
# module's -z defs link cannot resolve idStr, Mem_Free and friends.
# ---------------------------------------------------------------------------
add_library(openq4_renderer_idlib STATIC ${IDLIB_SOURCES})
openq4_apply_common(openq4_renderer_idlib)

add_library(renderer_gl MODULE ${RENDERER_GL_SOURCES})
openq4_apply_common(renderer_gl)
target_compile_definitions(renderer_gl PRIVATE
  ${OPENQ4_SDL3_DEFINES}
  OPENQ4_RENDERER_MODULE OPENQ4_RENDERER_GL_MODULE OPENQ4_SDL3_CONTEXT_IMPL)
# Order matters for static archives: imagetools calls into idlib (BinaryImage
# uses idCrypto::SHA256), so it has to precede it on the link line. meson gets
# this for free with --start-group/--end-group.
target_link_libraries(renderer_gl PRIVATE
  openq4_imagetools openq4_render_geo openq4_renderer_idlib openq4_glew
  SDL3::SDL3-static GL ${CMAKE_DL_LIBS} m)
# Same hardening as the executables, plus the module's export map: only the
# renderer module entry points leave the .so, and -z defs catches a module
# referencing an engine symbol that no longer exists at link time rather than
# at load time. --allow-shlib-undefined is what lets -z defs coexist with the
# engine symbols this module resolves from its host at runtime.
target_link_options(renderer_gl PRIVATE
  ${OPENQ4_LINK_OPTIONS_HARDENING}
  -Wl,-z,defs -Wl,--allow-shlib-undefined
  "-Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/tools/build/linux_renderer_module.map")
set_target_properties(renderer_gl PROPERTIES
  OUTPUT_NAME "renderer-gl_${ARCH_TAG}" PREFIX ""
  CXX_VISIBILITY_PRESET hidden
  LINK_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/tools/build/linux_renderer_module.map")

# ---------------------------------------------------------------------------
# Executables
# ---------------------------------------------------------------------------
set(OPENQ4_LINK_OPTIONS ${OPENQ4_LINK_OPTIONS_HARDENING} -Wl,--no-undefined)

add_executable(openq4_client ${ENGINE_CLIENT_SOURCES})
openq4_apply_common(openq4_client)
# The module-only client compiles against GLEW declarations but links no GL:
# renderer-gl carries GLEW and libGL itself.
target_compile_definitions(openq4_client PRIVATE
  ${OPENQ4_SDL3_DEFINES} OPENQ4_RENDERER_MODULE_ONLY)
target_link_libraries(openq4_client PRIVATE
  openq4_bse openq4_imagetools openq4_render_geo
  SDL3::SDL3-static openq4_stb_vorbis PkgConfig::OPENAL PkgConfig::X11DEPS
  Threads::Threads ${CMAKE_DL_LIBS} m)
target_link_options(openq4_client PRIVATE ${OPENQ4_LINK_OPTIONS})
set_target_properties(openq4_client PROPERTIES
  OUTPUT_NAME "openQ4-client_${ARCH_TAG}" POSITION_INDEPENDENT_CODE ON)
add_dependencies(openq4_client renderer_gl)

if(OPENQ4_BUILD_DEDICATED)
  # The dedicated target drops SDL3 and the X11 helpers: it uses the same
  # compile-time declarations but a separate headless source and link set, so
  # its executable depends on no OpenAL, GL, SDL, Wayland or X11 at runtime.
  add_executable(openq4_ded ${ENGINE_DED_SOURCES})
  openq4_apply_common(openq4_ded)
  target_compile_definitions(openq4_ded PRIVATE ID_DEDICATED)
  target_link_libraries(openq4_ded PRIVATE
    openq4_bse openq4_imagetools openq4_render_geo openq4_stb_vorbis
    openq4_glew_dedicated Threads::Threads ${CMAKE_DL_LIBS} m)
  target_link_options(openq4_ded PRIVATE ${OPENQ4_LINK_OPTIONS})
  set_target_properties(openq4_ded PROPERTIES
    OUTPUT_NAME "openQ4-ded_${ARCH_TAG}" POSITION_INDEPENDENT_CODE ON)
endif()

# ---------------------------------------------------------------------------
# Game modules, from the companion repository's own CMake build
# ---------------------------------------------------------------------------
if(OPENQ4_BUILD_GAMES)
  if(EXISTS "${OPENQ4_GAMELIBS_REPO}/CMakeLists.txt")
    add_subdirectory("${OPENQ4_GAMELIBS_REPO}" gamelibs)
  else()
    message(WARNING
      "${OPENQ4_GAMELIBS_REPO} has no CMakeLists.txt, so the game modules are "
      "not built here. Build them with meson in that repository, or set "
      "-DOPENQ4_BUILD_GAMES=OFF to silence this.")
  endif()
endif()

message(STATUS "openQ4 (CMake)")
message(STATUS "  Arch        : ${ARCH_TAG}")
message(STATUS "  Build type  : ${CMAKE_BUILD_TYPE}")
message(STATUS "  GameLibs    : ${OPENQ4_GAMELIBS_REPO}")
message(STATUS "  Stage root  : ${GAME_STAGE_ROOT}")
