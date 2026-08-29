cmake_minimum_required(VERSION 3.8.2)
project(qimsdk_cpp_multistream_yolox_grid LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(TEST_TARGET qimsdk-cpp-multistream-yolox-grid)

add_executable(${TEST_TARGET}
  main.cc
)

target_link_libraries(${TEST_TARGET} PRIVATE
  qimsdk-app-builder
)

install(
  TARGETS ${TEST_TARGET}
  RUNTIME DESTINATION ${QIMSDK_BINDIR}
  PERMISSIONS OWNER_EXECUTE OWNER_WRITE OWNER_READ
              GROUP_EXECUTE GROUP_READ
              WORLD_EXECUTE WORLD_READ
)
