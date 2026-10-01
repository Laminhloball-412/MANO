// SPDX-FileCopyrightText: Copyright (C) 2021-2026 Software Radio Systems Limited
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI
// Portions of this file may implement 3GPP specifications, which may be subject to additional licensing requirements.

#pragma once

#include "ocudu/ran/cu_cp_pdu_session.h"
#include "ocudu/ran/s_nssai.h"
#include <optional>
#include <string>
#include <string_view>

namespace ocudu::ocucp {

/// Formats a decoded NGAP QoS flow request for a stable, grep-friendly log entry.
std::string format_ngap_qos_flow_request(std::string_view                   operation,
                                         pdu_session_id_t                  pdu_session_id,
                                         const std::optional<s_nssai_t>&   s_nssai,
                                         const qos_flow_setup_request_item& qos_flow);

} // namespace ocudu::ocucp
