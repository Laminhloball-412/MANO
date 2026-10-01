# CMake generated Testfile for 
# Source directory: /home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp
# Build directory: /home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/benchmarks/pdcp
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
add_test(pdcp_tx_benchmark_nea0 "pdcp_tx_benchmark" "-a0" "-R3")
set_tests_properties(pdcp_tx_benchmark_nea0 PROPERTIES  LABELS "tsan" _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;12;add_test;/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;0;")
add_test(pdcp_tx_benchmark_nea1 "pdcp_tx_benchmark" "-a1" "-R1")
set_tests_properties(pdcp_tx_benchmark_nea1 PROPERTIES  LABELS "tsan" _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;13;add_test;/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;0;")
add_test(pdcp_tx_benchmark_nea2 "pdcp_tx_benchmark" "-a2" "-R3")
set_tests_properties(pdcp_tx_benchmark_nea2 PROPERTIES  LABELS "tsan" _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;14;add_test;/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;0;")
add_test(pdcp_tx_benchmark_nea3 "pdcp_tx_benchmark" "-a3" "-R1")
set_tests_properties(pdcp_tx_benchmark_nea3 PROPERTIES  LABELS "tsan" _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;15;add_test;/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;0;")
add_test(pdcp_rx_benchmark_nea0 "pdcp_rx_benchmark" "-a0" "-R3")
set_tests_properties(pdcp_rx_benchmark_nea0 PROPERTIES  LABELS "tsan" _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;23;add_test;/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;0;")
add_test(pdcp_rx_benchmark_nea1 "pdcp_rx_benchmark" "-a1" "-R1")
set_tests_properties(pdcp_rx_benchmark_nea1 PROPERTIES  LABELS "tsan" _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;24;add_test;/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;0;")
add_test(pdcp_rx_benchmark_nea2 "pdcp_rx_benchmark" "-a2" "-R3")
set_tests_properties(pdcp_rx_benchmark_nea2 PROPERTIES  LABELS "tsan" _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;25;add_test;/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;0;")
add_test(pdcp_rx_benchmark_nea3 "pdcp_rx_benchmark" "-a3" "-R1")
set_tests_properties(pdcp_rx_benchmark_nea3 PROPERTIES  LABELS "tsan" _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;26;add_test;/home/ubuntu/ran/ocudu-oai/tests/benchmarks/pdcp/CMakeLists.txt;0;")
set_directory_properties(PROPERTIES LABELS "pdcp")
