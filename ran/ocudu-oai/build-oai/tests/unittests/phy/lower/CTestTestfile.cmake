# CMake generated Testfile for 
# Source directory: /home/ubuntu/ran/ocudu-oai/tests/unittests/phy/lower
# Build directory: /home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/phy/lower
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
add_test(lower_phy_test "lower_phy_test")
set_tests_properties(lower_phy_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/lower/CMakeLists.txt;13;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/lower/CMakeLists.txt;0;")
subdirs("modulation")
subdirs("processors")
set_directory_properties(PROPERTIES LABELS "phy")
