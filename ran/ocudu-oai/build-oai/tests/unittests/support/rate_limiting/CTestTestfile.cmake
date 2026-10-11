# CMake generated Testfile for 
# Source directory: /home/ubuntu/ran/ocudu-oai/tests/unittests/support/rate_limiting
# Build directory: /home/ubuntu/ran/ocudu-oai/build-oai/tests/unittests/support/rate_limiting
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
add_test(token_bucket_test "token_bucket_test")
set_tests_properties(token_bucket_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/support/rate_limiting/CMakeLists.txt;8;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/support/rate_limiting/CMakeLists.txt;0;")
add_test(lockfree_token_bucket_test "lockfree_token_bucket_test")
set_tests_properties(lockfree_token_bucket_test PROPERTIES  _BACKTRACE_TRIPLES "/home/ubuntu/ran/ocudu-oai/tests/unittests/support/rate_limiting/CMakeLists.txt;13;add_test;/home/ubuntu/ran/ocudu-oai/tests/unittests/support/rate_limiting/CMakeLists.txt;0;")
set_directory_properties(PROPERTIES LABELS "support")
