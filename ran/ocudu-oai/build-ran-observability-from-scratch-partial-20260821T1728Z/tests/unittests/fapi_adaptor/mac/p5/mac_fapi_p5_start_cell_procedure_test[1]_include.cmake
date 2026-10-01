if(EXISTS "/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/mac/p5/mac_fapi_p5_start_cell_procedure_test")
  if(NOT EXISTS "/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/mac/p5/mac_fapi_p5_start_cell_procedure_test[1]_tests.cmake" OR
     NOT "/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/mac/p5/mac_fapi_p5_start_cell_procedure_test[1]_tests.cmake" IS_NEWER_THAN "/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/mac/p5/mac_fapi_p5_start_cell_procedure_test" OR
     NOT "/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/mac/p5/mac_fapi_p5_start_cell_procedure_test[1]_tests.cmake" IS_NEWER_THAN "${CMAKE_CURRENT_LIST_FILE}")
    include("/usr/share/cmake-3.28/Modules/GoogleTestAddTests.cmake")
    gtest_discover_tests_impl(
      TEST_EXECUTABLE [==[/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/mac/p5/mac_fapi_p5_start_cell_procedure_test]==]
      TEST_EXECUTOR [==[]==]
      TEST_WORKING_DIR [==[/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/mac/p5]==]
      TEST_EXTRA_ARGS [==[]==]
      TEST_PROPERTIES [==[]==]
      TEST_PREFIX [==[]==]
      TEST_SUFFIX [==[]==]
      TEST_FILTER [==[]==]
      NO_PRETTY_TYPES [==[FALSE]==]
      NO_PRETTY_VALUES [==[FALSE]==]
      TEST_LIST [==[mac_fapi_p5_start_cell_procedure_test_TESTS]==]
      CTEST_FILE [==[/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/mac/p5/mac_fapi_p5_start_cell_procedure_test[1]_tests.cmake]==]
      TEST_DISCOVERY_TIMEOUT [==[15]==]
      TEST_XML_OUTPUT_DIR [==[]==]
    )
  endif()
  include("/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/mac/p5/mac_fapi_p5_start_cell_procedure_test[1]_tests.cmake")
else()
  add_test(mac_fapi_p5_start_cell_procedure_test_NOT_BUILT mac_fapi_p5_start_cell_procedure_test_NOT_BUILT)
endif()
