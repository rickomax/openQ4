#!/usr/bin/env python3
"""Keeps the CMake build's platform flags in step with the meson build's.

openQ4 carries two build definitions. meson is canonical; CMake is a second one
alongside it. Their source lists and generated headers cannot drift, because both
call the same discovery and codegen scripts - but compiler and link flags are
written out separately in each, and nothing else checks that they agree.

That matters most where it can least be caught by building: the CMake Windows
and macOS paths were ported from meson.build without an MSVC or Xcode toolchain
to compile them. This test is what stands in for that missing compile. It does
not parse either build language; it pins the flags that change behaviour rather
than warning noise, and requires each to appear in both files.

A flag listed here is load-bearing. Removing one from either build should fail
this test rather than silently produce a differently-behaving binary.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GAME_LIBS_ROOT = Path(os.environ.get("OPENQ4_GAMELIBS_REPO", ROOT.parent / "openQ4-game")).resolve()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require_both(meson: str, cmake: str, needle: str, context: str) -> None:
    if needle not in meson:
        raise AssertionError(f"Missing {needle!r} in meson build ({context})")
    if needle not in cmake:
        raise AssertionError(f"Missing {needle!r} in CMake build ({context})")


# Flags whose absence changes generated code or runtime behaviour, not warnings.
SHARED_BEHAVIOUR = [
    # Fuses a*b+c into an FMA where the target has one. AArch64 always does and
    # the validated x86-64 SSE2 baseline does not, so leaving it on makes
    # identical physics and AI math diverge per architecture.
    "-ffp-contract=off",
    # The idTech4-era sources rely on implicit narrowing and pointer casts.
    "-fpermissive",
    "-fno-strict-aliasing",
    # UBSan's vptr metadata cannot be resolved across the runtime-loaded module
    # boundary while preserving each module's -z defs contract.
    "-fno-sanitize=vptr",
    "-fstack-protector-strong",
]

WINDOWS_BEHAVIOUR = [
    "/J",                       # default char to unsigned, which the SDK assumes
    "/Zc:strictStrings-",
    "/source-charset:windows-1252",
    "/execution-charset:windows-1252",
    "/we4302", "/we4311", "/we4312",   # pointer truncation stays an error
    "/we4101", "/we4189", "/we4267", "/we4324", "/we4505",
    "/wd4091", "/wd4305", "/wd4838", "/wd5033", "/wd5055",
]

MACOS_BEHAVIOUR = [
    "MACOS_X=1",
    "-Wl,-pie",
    "-Wl,-dead_strip",
    "-Wl,-headerpad_max_install_names",
    "USE_OPENAL_SOFT_INCLUDES=1",
    "@loader_path/Frameworks",
]

LINUX_BEHAVIOUR = [
    "ID_GL_HARDLINK",
    "-Wl,-z,relro",
    "-Wl,-z,now",
    "-Wl,-z,noexecstack",
    "-Wl,-z,defs",
    "linux_renderer_module.map",
]


def validate_engine() -> None:
    meson = read(ROOT / "meson.build")
    cmake = read(ROOT / "CMakeLists.txt")

    for flag in SHARED_BEHAVIOUR:
        require_both(meson, cmake, flag, "engine shared flags")
    for flag in WINDOWS_BEHAVIOUR:
        require_both(meson, cmake, flag, "engine Windows flags")
    for flag in MACOS_BEHAVIOUR:
        require_both(meson, cmake, flag, "engine macOS flags")
    for flag in LINUX_BEHAVIOUR:
        require_both(meson, cmake, flag, "engine Linux flags")

    # Windows link shape.
    require_both(meson, cmake, "/STACK:16777216,16777216", "engine Windows stack size")
    require_both(meson, cmake, "/FIXED:NO", "renderer module Windows link")
    require_both(meson, cmake, "/LARGEADDRESSAWARE", "renderer module Windows link")

    # Windows system libraries the engine needs; dropping one is a link failure
    # on a host this build cannot be compiled on.
    for lib in ("Dbghelp", "bcrypt", "Iphlpapi", "Winmm", "ws2_32", "shell32", "ole32", "opengl32"):
        require_both(meson, cmake, lib, "engine Windows system libraries")

    for framework in ("Cocoa", "OpenGL", "ApplicationServices"):
        require_both(meson, cmake, framework, "engine macOS frameworks")

    # The native Vulkan module. It is built by default and selected at runtime
    # by r_renderApi, whose fallback is fail-closed - so a build that quietly
    # omitted it would turn "r_renderApi vulkan" into a silent fallback to GL
    # rather than an error. Its defines decide how volk and VMA resolve entry
    # points, and getting them wrong is a runtime failure, not a build one.
    for flag in (
        "OPENQ4_RENDERER_VK_MODULE",
        "VMA_STATIC_VULKAN_FUNCTIONS=0",
        "VMA_DYNAMIC_VULKAN_FUNCTIONS=1",
        "VK_ENABLE_BETA_EXTENSIONS",
        "VK_USE_PLATFORM_WIN32_KHR",
        "macos_renderer_module.exp",
    ):
        require_both(meson, cmake, flag, "Vulkan renderer module")

    # The Vulkan module links the hook-resolving GLEW flavour, not the GL one:
    # its mixed front-end translation units still have GL call sites.
    if "glew_dedicated" not in meson:
        raise AssertionError("meson no longer builds the hook-resolving GLEW flavour")
    if "openq4_glew_dedicated" not in cmake:
        raise AssertionError("CMake no longer builds the hook-resolving GLEW flavour")

    # GLEW's two flavours. The dedicated one resolves through openQ4's own hook
    # so a GL-free target still links; GLAPI=extern strips the dllimport
    # decoration its stubs would otherwise collide with. meson defines the hook
    # inside the glew subproject and passes GLAPI=extern from the engine, so the
    # two halves are checked against different files.
    glew_meson = read(ROOT / "subprojects" / "glew" / "meson.build")
    require_both(glew_meson, cmake, "OPENQ4_GLEW_SDL3_LOADER", "dedicated GLEW flavour")
    require_both(meson, cmake, "GLAPI=extern", "dedicated GLEW flavour")

    # The CMake build must not claim to be verified where it is not.
    for marker in ("VERIFICATION STATUS", "NOT built"):
        if marker not in cmake:
            raise AssertionError(
                f"The CMake build no longer records its verification status ({marker!r} missing). "
                "Windows and macOS are ported but unbuilt; that has to stay written down.")


def validate_gamelibs() -> None:
    meson = read(GAME_LIBS_ROOT / "src" / "meson.build")
    cmake = read(GAME_LIBS_ROOT / "CMakeLists.txt")

    for flag in SHARED_BEHAVIOUR:
        require_both(meson, cmake, flag, "gamelibs shared flags")

    # The module load addresses and export control, per host.
    require_both(meson, cmake, "/BASE:0x180000000", "gamelibs Windows 64-bit load address")
    require_both(meson, cmake, "/BASE:0x20000000", "gamelibs Windows 32-bit load address")
    require_both(meson, cmake, "/FIXED:NO", "gamelibs Windows link")
    require_both(meson, cmake, "/LARGEADDRESSAWARE", "gamelibs Windows link")
    require_both(meson, cmake, "darwin_game_module.exp", "gamelibs macOS export list")
    require_both(meson, cmake, "linux_game_module.map", "gamelibs Linux export map")
    require_both(meson, cmake, "-Wl,-z,defs", "gamelibs Linux undefined-symbol contract")
    require_both(meson, cmake, "@loader_path/", "gamelibs macOS install name")

    # Two idlib archives, one per game flavour: precompiled.h resolves a
    # different Game_local.h under GAME_MPAPI, so a single archive put two
    # definitions of the same types into the multiplayer module.
    require_both(meson, cmake, "GAME_MPAPI", "gamelibs idlib flavours")
    for name in ("idLib", "idLibMP"):
        if name not in cmake:
            raise AssertionError(f"CMake build lost the {name} archive")

    # NDEBUG only in release, and _DEBUG never: defining it changed shared
    # struct layouts across the module boundary - srfTriangles_t grows
    # description[64], Heap.h grows the MemScopedTag stack - so a debug module
    # and a debug engine disagreed about the size of types they pass each other.
    # Comments are stripped first: both builds explain this in prose, and the
    # explanation must not be mistaken for the define itself.
    code_only = "\n".join(
        line for line in cmake.splitlines() if not line.lstrip().startswith("#")
    )
    # Match _DEBUG as a whole token: INLINE_DEBUG (the option) and _INLINEDEBUG
    # (a legitimate define) both contain it and are not what this forbids.
    if re.search(r"(?<![A-Za-z0-9_])_DEBUG(?![A-Za-z0-9_])", code_only):
        raise AssertionError("CMake gamelibs build must not define _DEBUG")
    if "NDEBUG" not in cmake:
        raise AssertionError("CMake gamelibs build lost its release-only NDEBUG")


def main() -> int:
    validate_engine()
    if GAME_LIBS_ROOT.is_dir():
        validate_gamelibs()
    else:
        print(
            f"cmake_meson_flag_parity: gamelibs skipped (no checkout at {GAME_LIBS_ROOT})",
            file=sys.stderr,
        )
    print("cmake_meson_flag_parity: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
