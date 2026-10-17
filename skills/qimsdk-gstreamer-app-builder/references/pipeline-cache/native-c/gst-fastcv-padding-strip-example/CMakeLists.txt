cmake_minimum_required(VERSION 3.16)
project(GST-FASTCV-PADDING-STRIP-EXAMPLE LANGUAGES C CXX)

set(CMAKE_INCLUDE_CURRENT_DIR ON)

find_package(PkgConfig)

# Resolved via CMAKE_FIND_ROOT_PATH (set by the cmake class' toolchain file to
# the recipe sysroot) — no FASTCV_ROOT override needed for the bitbake build.
find_path(FASTCV_INCLUDE_DIR fastcv/fastcv.h)
find_library(FASTCV_LIB fastcvopt)

if(FASTCV_INCLUDE_DIR AND FASTCV_LIB)
  message(STATUS "FastCV found: ${FASTCV_LIB}")
  set(EXTRA_LIBS ${FASTCV_LIB})
  set(EXTRA_INCLUDES ${FASTCV_INCLUDE_DIR})
  set(EXTRA_COMPILE_DEFS USE_FASTCV_MEMCPY)
else()
    message(WARNING "FastCV not found — building with memcpy fallback")
endif()

# Get the pkgconfigs exported by the automake tools
pkg_check_modules(GST
  REQUIRED gstreamer-1.0>=${GST_VERSION_REQUIRED}
           gstreamer-app-1.0>=${GST_VERSION_REQUIRED}
           gstreamer-video-1.0>=${GST_VERSION_REQUIRED})

# GStreamer gst-fastcv-padding-strip-example
set(GST_EXAMPLE_BIN gst-fastcv-padding-strip-example)

add_executable(${GST_EXAMPLE_BIN}
  main.cc
)

target_compile_options(${GST_EXAMPLE_BIN} PRIVATE
  -Wall -Wextra -Werror
)

target_include_directories(${GST_EXAMPLE_BIN} PRIVATE
  ${GST_INCLUDE_DIRS}
  ${EXTRA_INCLUDES}
)

target_compile_definitions(${GST_EXAMPLE_BIN} PRIVATE
  ${EXTRA_COMPILE_DEFS}
)

target_link_libraries(${GST_EXAMPLE_BIN} PRIVATE
  ${GST_LIBRARIES}
  ${EXTRA_LIBS}
)

install(
  TARGETS ${GST_EXAMPLE_BIN}
  RUNTIME DESTINATION ${GST_PLUGINS_QTI_OSS_INSTALL_BINDIR}
  PERMISSIONS OWNER_EXECUTE OWNER_WRITE OWNER_READ
              GROUP_EXECUTE GROUP_READ
              WORLD_EXECUTE WORLD_READ
)
