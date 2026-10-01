# CMake generated Testfile for 
# Source directory: /home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/ldpc
# Build directory: /home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/phy/upper/channel_coding/ldpc
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
add_test(ldpc_chain_test "ldpc_chain_test")
set_tests_properties(ldpc_chain_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/ldpc/CMakeLists.txt;8;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/ldpc/CMakeLists.txt;0;")
add_test(ldpc_rate_dematcher_test "ldpc_rate_dematcher_test")
set_tests_properties(ldpc_rate_dematcher_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/ldpc/CMakeLists.txt;12;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/ldpc/CMakeLists.txt;0;")
set_directory_properties(PROPERTIES LABELS "phy")
