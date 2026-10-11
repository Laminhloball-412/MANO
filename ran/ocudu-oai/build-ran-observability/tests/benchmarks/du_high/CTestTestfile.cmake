# CMake generated Testfile for 
# Source directory: /home/ubuntu/ran/ocudu-oai/tests/benchmarks/du_high
# Build directory: /home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/benchmarks/du_high
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
add_test(du_high_benchmark "du_high_benchmark")
set_tests_properties(du_high_benchmark PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/benchmarks/du_high/CMakeLists.txt;21;add_test;/home/ubuntu/ran/ocudu-oai/tests/benchmarks/du_high/CMakeLists.txt;0;")
set_directory_properties(PROPERTIES LABELS "du_high|tsan|rtsan")
