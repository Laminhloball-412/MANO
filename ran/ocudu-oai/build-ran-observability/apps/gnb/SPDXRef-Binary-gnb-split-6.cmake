
			cmake_policy(SET CMP0011 NEW)
			cmake_policy(SET CMP0012 NEW)
			set(_install_root "$ENV{DESTDIR}/usr/local")
			if(NOT EXISTS "${_install_root}/bin/gnb")
				if(NOT FALSE)
					message(FATAL_ERROR "Cannot find bin/gnb")
				endif()
			else()
				file(SHA1 ${_install_root}/bin/gnb _sha1)
				list(APPEND SBOM_VERIFICATION_CODES ${_sha1})
				file(APPEND "/home/ubuntu/ran/ocudu-oai/build-ran-observability/sbom/sbom.spdx.in"
"
FileName: ./bin/gnb
SPDXID: SPDXRef-Binary-gnb-split-6
FileType: BINARY
FileChecksum: SHA1: ${_sha1}
LicenseConcluded: LicenseRef-OCUDU
LicenseInfoInFile: NOASSERTION
FileCopyrightText: NOASSERTION
Relationship: SPDXRef-Package-OCUDU-5G-CU-DU CONTAINS SPDXRef-Binary-gnb-split-6
"
				)
			endif()
			