# CMake generated Testfile for 
# Source directory: /home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/polar
# Build directory: /home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/phy/upper/channel_coding/polar
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
add_test(polar_chain_test_bch "polar_chain_test" "-n9" "-k56" "-e864" "-i0" "-s101" "-o1")
set_tests_properties(polar_chain_test_bch PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/polar/CMakeLists.txt;10;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/polar/CMakeLists.txt;0;")
add_test(polar_chain_test_dci "polar_chain_test" "-n9" "-k40" "-e100" "-i0" "-s101" "-o1")
set_tests_properties(polar_chain_test_dci PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/polar/CMakeLists.txt;11;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/polar/CMakeLists.txt;0;")
add_test(polar_chain_test_uci "polar_chain_test" "-n10" "-k20" "-e256" "-i1" "-s101" "-o1")
set_tests_properties(polar_chain_test_uci PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/polar/CMakeLists.txt;12;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/polar/CMakeLists.txt;0;")
add_test(polar_chain_test_uci2 "polar_chain_test" "-n10" "-k18" "-e45" "-i1" "-s101" "-o1")
set_tests_properties(polar_chain_test_uci2 PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/polar/CMakeLists.txt;13;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/polar/CMakeLists.txt;0;")
add_test(polar_chain_test_uci3 "polar_chain_test" "-n10" "-k18" "-e38" "-i1" "-s101" "-o1")
set_tests_properties(polar_chain_test_uci3 PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/polar/CMakeLists.txt;14;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/polar/CMakeLists.txt;0;")
add_test(polar_interleaver_test "polar_interleaver_test")
set_tests_properties(polar_interleaver_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/polar/CMakeLists.txt;18;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/phy/upper/channel_coding/polar/CMakeLists.txt;0;")
set_directory_properties(PROPERTIES LABELS "phy")
