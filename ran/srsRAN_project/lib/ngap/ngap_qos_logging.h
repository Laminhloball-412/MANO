/*
 * Copyright 2021-2026 Software Radio Systems Limited
 *
 * This file is part of srsRAN.
 *
 * srsRAN is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as
 * published by the Free Software Foundation, either version 3 of
 * the License, or (at your option) any later version.
 */

#pragma once

#include "srsran/cu_cp/cu_cp_types.h"
#include <string>
#include <string_view>

namespace srsran::srs_cu_cp {

std::string format_ngap_qos_flow_request(std::string_view                    operation,
                                         pdu_session_id_t                   pdu_session_id,
                                         const s_nssai_t&                   s_nssai,
                                         const qos_flow_setup_request_item& qos_flow);

} // namespace srsran::srs_cu_cp
