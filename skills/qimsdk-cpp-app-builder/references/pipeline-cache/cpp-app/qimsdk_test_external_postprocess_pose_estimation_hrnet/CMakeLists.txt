set(TEST_TARGET "qimsdk_test_external_postprocess_pose_estimation_hrnet")

add_executable(${TEST_TARGET}
  main.cc
)

target_link_libraries(${TEST_TARGET} PRIVATE
  qimsdk-app-builder
  qti-imsdk-test-utils
)

install(
  TARGETS ${TEST_TARGET}
  RUNTIME DESTINATION ${QIMSDK_BINDIR}
  PERMISSIONS OWNER_EXECUTE OWNER_WRITE OWNER_READ
              GROUP_EXECUTE GROUP_READ
              WORLD_EXECUTE WORLD_READ
)