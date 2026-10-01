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

#include "lib/ngap/ngap_qos_logging.h"
#include <gtest/gtest.h>

using namespace srsran;
using namespace srsran::srs_cu_cp;

TEST(ngap_qos_logging_test, formats_decoded_setup_qos_flow)
{
  qos_flow_setup_request_item flow;
  flow.qos_flow_id                                                  = uint_to_qos_flow_id(1);
  flow.qos_flow_level_qos_params.qos_desc                           = non_dyn_5qi_descriptor{uint_to_five_qi(9)};
  flow.qos_flow_level_qos_params.alloc_retention_prio.prio_level_arp = 8;
  flow.qos_flow_level_qos_params.alloc_retention_prio.may_trigger_preemption = false;
  flow.qos_flow_level_qos_params.alloc_retention_prio.is_preemptable         = false;

  const s_nssai_t s_nssai{slice_service_type{1}, slice_differentiator::create(1).value()};

  EXPECT_EQ(format_ngap_qos_flow_request("setup", uint_to_pdu_session_id(1), s_nssai, flow),
            "PDU session QoS requested: operation=setup psi=1 sst=1 sd=000001 qfi=1 five_qi=9 "
            "qos_characteristics=non-dynamic arp_priority=8 "
            "preemption_capability=shall-not-trigger-pre-emption "
            "preemption_vulnerability=not-pre-emptable gbr=no");
}
