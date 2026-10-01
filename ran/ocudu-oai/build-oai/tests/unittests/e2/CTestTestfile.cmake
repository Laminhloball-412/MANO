# CMake generated Testfile for 
# Source directory: /home/ubuntu/ran/ocudu-oai/tests/unittests/e2
# Build directory: /home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/e2_asn1_packer_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/e2_setup_procedure_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/e2_entity_stop_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/e2_setup_routine_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/ric_reconnection_routine_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/ric_connection_loss_routine_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/ric_connection_setup_routine_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/e2_removal_procedure_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/e2_subscription_setup_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/e2_ric_control_procedure_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/e2ap_network_adapter_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/e2sm_kpm_meas_provider_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/e2sm_kpm_meas_provider_metrics_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/e2sm_kpm_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/e2_conn_upd_procedure_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/asn1_e2sm_ccc_test[1]_include.cmake")
add_test(e2sm_kpm_cu_cp_meas_provider_metrics_test.e2sm_kpm_cu_cp_returns_expected_rrc_metrics "/home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/e2/e2sm_kpm_meas_provider_metrics_test" "--gtest_filter=e2sm_kpm_cu_cp_meas_provider_metrics_test.e2sm_kpm_cu_cp_returns_expected_rrc_metrics")
set_tests_properties(e2sm_kpm_cu_cp_meas_provider_metrics_test.e2sm_kpm_cu_cp_returns_expected_rrc_metrics PROPERTIES  LABELS "MVP-GOV-E2-15.1;MVP-GOV-E2-15.2;MVP-GOV-E2-15.3;MVP-GOV-E2-16.1;MVP-GOV-E2-16.2;MVP-GOV-E2-17.1;MVP-GOV-E2-17.2;MVP-GOV-E2-4.1;MVP-GOV-E2-4.2" _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/e2/CMakeLists.txt;99;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/e2/CMakeLists.txt;0;")
set_directory_properties(PROPERTIES LABELS "e2ap")
