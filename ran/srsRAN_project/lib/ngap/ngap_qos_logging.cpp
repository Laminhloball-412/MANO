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

#include "ngap_qos_logging.h"
#include "fmt/format.h"

using namespace srsran;
using namespace srsran::srs_cu_cp;

std::string srsran::srs_cu_cp::format_ngap_qos_flow_request(
    std::string_view operation,
    pdu_session_id_t pdu_session_id,
    const s_nssai_t& s_nssai,
    const qos_flow_setup_request_item& qos_flow)
{
  const qos_flow_level_qos_parameters& qos_params = qos_flow.qos_flow_level_qos_params;
  const five_qi_t five_qi = qos_params.qos_desc.get_5qi();
  const std::string five_qi_value =
      five_qi == five_qi_t::invalid ? "invalid" : fmt::format("{}", five_qi_to_uint(five_qi));

  return fmt::format(
      "PDU session QoS requested: operation={} psi={} sst={} sd={:06x} qfi={} five_qi={} "
      "qos_characteristics={} arp_priority={} preemption_capability={} "
      "preemption_vulnerability={} gbr={}",
      operation,
      pdu_session_id_to_uint(pdu_session_id),
      s_nssai.sst.value(),
      s_nssai.sd.value(),
      qos_flow_id_to_uint(qos_flow.qos_flow_id),
      five_qi_value,
      qos_params.qos_desc.is_dyn_5qi() ? "dynamic" : "non-dynamic",
      qos_params.alloc_retention_prio.prio_level_arp.value(),
      qos_params.alloc_retention_prio.may_trigger_preemption ? "may-trigger-pre-emption"
                                                             : "shall-not-trigger-pre-emption",
      qos_params.alloc_retention_prio.is_preemptable ? "pre-emptable" : "not-pre-emptable",
      qos_params.gbr_qos_info.has_value() ? "yes" : "no");
}
