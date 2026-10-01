// SPDX-FileCopyrightText: Copyright (C) 2021-2026 Software Radio Systems Limited
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI
// Portions of this file may implement 3GPP specifications, which may be subject to additional licensing requirements.

#include "apps/helpers/metrics/json_generators/du_high/scheduler.h"
#include "ocudu/scheduler/scheduler_metrics.h"
#include <gtest/gtest.h>

using namespace ocudu;

TEST(scheduler_json_metrics_test, serializes_exact_cell_capacity_and_per_ue_prb_usage)
{
  scheduler_metrics_report report;
  scheduler_cell_metrics&  cell = report.cells.emplace_back();
  cell.nof_prbs                 = 51;
  cell.nof_slots                = 1000;
  cell.nof_dl_slots             = 700;
  cell.nof_ul_slots             = 300;
  cell.dl_prb_symbols_available = 499800;
  cell.ul_prb_symbols_available = 214200;

  scheduler_ue_metrics& ue      = cell.ue_metrics.emplace_back();
  ue.tot_pdsch_prbs_used        = 120;
  ue.tot_pusch_prbs_used        = 80;
  ue.tot_pdsch_prb_symbols_used = 1440;
  ue.tot_pusch_prb_symbols_used = 480;

  const nlohmann::json json = app_helpers::json_generators::generate(report);

  EXPECT_EQ(json["cells"][0]["cell_metrics"]["nof_prbs"], 51);
  EXPECT_EQ(json["cells"][0]["cell_metrics"]["nof_slots"], 1000);
  EXPECT_EQ(json["cells"][0]["cell_metrics"]["nof_dl_slots"], 700);
  EXPECT_EQ(json["cells"][0]["cell_metrics"]["nof_ul_slots"], 300);
  EXPECT_EQ(json["cells"][0]["cell_metrics"]["dl_prb_symbols_available"], 499800);
  EXPECT_EQ(json["cells"][0]["cell_metrics"]["ul_prb_symbols_available"], 214200);
  EXPECT_EQ(json["cells"][0]["ue_list"][0]["dl_nof_prbs"], 120);
  EXPECT_EQ(json["cells"][0]["ue_list"][0]["ul_nof_prbs"], 80);
  EXPECT_EQ(json["cells"][0]["ue_list"][0]["dl_nof_prb_symbols"], 1440);
  EXPECT_EQ(json["cells"][0]["ue_list"][0]["ul_nof_prb_symbols"], 480);
}
