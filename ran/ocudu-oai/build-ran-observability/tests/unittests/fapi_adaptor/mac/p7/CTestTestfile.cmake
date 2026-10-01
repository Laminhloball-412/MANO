# CMake generated Testfile for 
# Source directory: /home/ubuntu/ran/ocudu-oai/tests/unittests/fapi_adaptor/mac/p7
# Build directory: /home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/mac/p7
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
include("/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/mac/p7/fapi_to_mac_indications_fastpath_translator_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/mac/p7/mac_to_fapi_fastpath_translator_test[1]_include.cmake")
subdirs("pdu_translators")
set_directory_properties(PROPERTIES LABELS "fapi_adaptor")
