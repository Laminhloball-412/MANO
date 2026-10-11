# CMake generated Testfile for 
# Source directory: /home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support
# Build directory: /home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/scheduler/support
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/scheduler/support/pucch_support_test[1]_include.cmake")
add_test(pdcch_type0_css_occassions_test "pdcch_type0_css_occassions_test")
set_tests_properties(pdcch_type0_css_occassions_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;7;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;0;")
add_test(pdsch_default_time_allocation_test "pdsch_default_time_allocation_test")
set_tests_properties(pdsch_default_time_allocation_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;11;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;0;")
add_test(pdsch_dmrs_symbol_mask_test "pdsch_dmrs_symbol_mask_test")
set_tests_properties(pdsch_dmrs_symbol_mask_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;15;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;0;")
add_test(pusch_default_time_allocation_test "pusch_default_time_allocation_test")
set_tests_properties(pusch_default_time_allocation_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;25;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;0;")
add_test(pusch_td_resource_indices_test "pusch_td_resource_indices_test")
set_tests_properties(pusch_td_resource_indices_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;29;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;0;")
add_test(pusch_dmrs_symbol_mask_test "pusch_dmrs_symbol_mask_test")
set_tests_properties(pusch_dmrs_symbol_mask_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;33;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;0;")
add_test(prbs_calculator_test "prbs_calculator_test")
set_tests_properties(prbs_calculator_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;37;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;0;")
add_test(dmrs_helpers_test "dmrs_helpers_test")
set_tests_properties(dmrs_helpers_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;41;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;0;")
add_test(mcs_calculator_test.cpp "mcs_calculator_test")
set_tests_properties(mcs_calculator_test.cpp PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;45;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;0;")
add_test(mcs_tbs_calculator_test "mcs_tbs_calculator_test")
set_tests_properties(mcs_tbs_calculator_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;55;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;0;")
add_test(pdcch_aggregation_level_calculator_test.cpp "pdcch_aggregation_level_calculator_test")
set_tests_properties(pdcch_aggregation_level_calculator_test.cpp PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;59;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/scheduler/support/CMakeLists.txt;0;")
set_directory_properties(PROPERTIES LABELS "sched")
