# CMake generated Testfile for 
# Source directory: /home/ubuntu/ran/ocudu-oai/tests/unittests/phy/generic_functions
# Build directory: /home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/phy/generic_functions
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
add_test(dft_processor_test "dft_processor_test")
set_tests_properties(dft_processor_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/generic_functions/CMakeLists.txt;13;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/generic_functions/CMakeLists.txt;0;")
add_test(dft_processor_ci16_test "dft_processor_ci16_test")
set_tests_properties(dft_processor_ci16_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/generic_functions/CMakeLists.txt;19;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/generic_functions/CMakeLists.txt;0;")
subdirs("precoding")
set_directory_properties(PROPERTIES LABELS "phy")
