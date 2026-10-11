# CMake generated Testfile for 
# Source directory: /home/ubuntu/ran/ocudu-oai/tests/integrationtests/du_high
# Build directory: /home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/integrationtests/du_high
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
include("/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/integrationtests/du_high/du_high_test[1]_include.cmake")
add_test(mac_test_mode_adapter_test "mac_test_mode_adapter_test")
set_tests_properties(mac_test_mode_adapter_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/integrationtests/du_high/CMakeLists.txt;41;add_test;/home/ubuntu/ran/ocudu-oai/tests/integrationtests/du_high/CMakeLists.txt;0;")
subdirs("ntn")
set_directory_properties(PROPERTIES LABELS "du_high;integrationtest")
