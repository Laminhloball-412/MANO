// SPDX-FileCopyrightText: Copyright (C) 2021-2026 Software Radio Systems Limited
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI
// Portions of this file may implement 3GPP specifications, which may be subject to additional licensing requirements.

#include "ngap_qos_logging.h"
#include "fmt/format.h"

using namespace ocudu;
using namespace ocudu::ocucp;

std::string ocudu::ocucp::format_ngap_qos_flow_request(std::string_view                    operation,
                                                        pdu_session_id_t                   pdu_session_id,
                                                        const std::optional<s_nssai_t>&    s_nssai,
                                                        const qos_flow_setup_request_item& qos_flow)
{
  const qos_flow_level_qos_parameters& qos_params = qos_flow.qos_flow_level_qos_params;
  const five_qi_t                      five_qi     = qos_params.qos_desc.get_5qi();

  std::string slice_fields;
  if (s_nssai.has_value()) {
    slice_fields = fmt::format(" sst={}", s_nssai->sst.value());
    slice_fields += s_nssai->sd.is_set() ? fmt::format(" sd={:06x}", s_nssai->sd.value()) : " sd=none";
  }

  const std::string five_qi_value =
      five_qi == five_qi_t::invalid ? "invalid" : fmt::format("{}", five_qi_to_uint(five_qi));

  return fmt::format(
      "PDU session QoS requested: operation={} psi={}{} qfi={} five_qi={} qos_characteristics={} "
      "arp_priority={} preemption_capability={} preemption_vulnerability={} gbr={}",
      operation,
      pdu_session_id_to_uint(pdu_session_id),
      slice_fields,
      qos_flow_id_to_uint(qos_flow.qos_flow_id),
      five_qi_value,
      qos_params.qos_desc.is_dyn_5qi() ? "dynamic" : "non-dynamic",
      qos_params.alloc_retention_prio.prio_level_arp.value(),
      qos_params.alloc_retention_prio.may_trigger_preemption ? "may-trigger-pre-emption"
                                                             : "shall-not-trigger-pre-emption",
      qos_params.alloc_retention_prio.is_preemptable ? "pre-emptable" : "not-pre-emptable",
      qos_params.gbr_qos_info.has_value() ? "yes" : "no");
}
