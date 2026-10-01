# CMake generated Testfile for 
# Source directory: /home/ubuntu/ran/ocudu-oai/tests/unittests/fapi_adaptor
# Build directory: /home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
include("/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/precoding_matrix_table_generator_test[1]_include.cmake")
include("/home/ubuntu/ran/ocudu-oai/build-ran-observability/tests/unittests/fapi_adaptor/uci_part2_correspondence_test[1]_include.cmake")
subdirs("mac/p5")
subdirs("mac/p7")
subdirs("phy/p7")
set_directory_properties(PROPERTIES LABELS "fapi_adaptor")
