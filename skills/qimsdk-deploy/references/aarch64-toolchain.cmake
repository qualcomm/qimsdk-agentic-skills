# CMake toolchain file for the standalone aarch64 post-process module build.
#
# A post-process module links no IMSDK library -- qtimlpostprocess dlopen()s it and
# calls its extern "C" NewModule(). So there is nothing to find in a sysroot, and the
# distro cross-compiler is sufficient. Requires: g++-aarch64-linux-gnu.
#
# This file is copied into the build directory by workspace_setup_m.py (Mode M).
# Editing the copy has no effect on the next run; edit this template instead.

set(CMAKE_SYSTEM_NAME      Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

# CMAKE_C_COMPILER is set even though a module is C++ only. Module CMakeLists.txt files
# commonly declare project(... LANGUAGES C CXX); with C enabled and no C compiler named,
# CMake falls back to the host `cc` and the configure step silently mixes toolchains.
set(CMAKE_C_COMPILER   aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)

# _INIT rather than the plain variable: an artifact that does
#   set(CMAKE_CXX_FLAGS "...")
# would overwrite CMAKE_CXX_FLAGS wholesale and drop -march. *_INIT seeds the cache
# before the project's own flags are appended, so it survives.
set(CMAKE_C_FLAGS_INIT   "-march=armv8-a")
set(CMAKE_CXX_FLAGS_INIT "-march=armv8-a")

# Only ever search the target sysroot for libraries/includes, never the host's.
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM BEFORE)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
