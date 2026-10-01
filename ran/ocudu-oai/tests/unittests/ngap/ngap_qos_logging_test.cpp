// SPDX-FileCopyrightText: Copyright (C) 2021-2026 Software Radio Systems Limited
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI
// Portions of this file may implement 3GPP specifications, which may be subject to additional licensing requirements.

#include "lib/ngap/ngap_qos_logging.h"
#include <gtest/gtest.h>

using namespace ocudu;
using namespace ocudu::ocucp;

TEST(ngap_qos_logging_test, formats_decoded_setup_qos_flow)
{
  qos_flow_setup_request_item flow;
  flow.qos_flow_id                                               = uint_to_qos_flow_id(1);
  flow.qos_flow_level_qos_params.qos_desc                        = non_dyn_5qi_descriptor{uint_to_five_qi(2)};
  flow.qos_flow_level_qos_params.alloc_retention_prio.prio_level_arp = 4;
  flow.qos_flow_level_qos_params.alloc_retention_prio.may_trigger_preemption = false;
  flow.qos_flow_level_qos_params.alloc_retention_prio.is_preemptable         = false;

  const s_nssai_t s_nssai{slice_service_type{1}, slice_differentiator::create(1).value()};

  EXPECT_EQ(format_ngap_qos_flow_request("setup", uint_to_pdu_session_id(1), s_nssai, flow),
            "PDU session QoS requested: operation=setup psi=1 sst=1 sd=000001 qfi=1 five_qi=2 "
            "qos_characteristics=non-dynamic arp_priority=4 "
            "preemption_capability=shall-not-trigger-pre-emption "
            "preemption_vulnerability=not-pre-emptable gbr=no");
}
