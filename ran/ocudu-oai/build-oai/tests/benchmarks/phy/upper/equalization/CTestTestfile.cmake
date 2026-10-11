# CMake generated Testfile for 
# Source directory: /home/ubuntu/ran/ocudu-oai/tests/benchmarks/phy/upper/equalization
# Build directory: /home/ubuntu/ran/ocudu-oai/build-oai/tests/benchmarks/phy/upper/equalization
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
add_test(channel_equalizer_benchmark "channel_equalizer_benchmark" "-s" "-R" "1" "-T" "MMSE")
set_tests_properties(channel_equalizer_benchmark PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/benchmarks/phy/upper/equalization/CMakeLists.txt;8;add_test;/home/ubuntu/ran/ocudu-oai/tests/benchmarks/phy/upper/equalization/CMakeLists.txt;0;")
