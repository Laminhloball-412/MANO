/**
 * Copyright 2013-2023 Software Radio Systems Limited
 *
 * This file is part of srsRAN.
 *
 * srsRAN is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as
 * published by the Free Software Foundation, either version 3 of
 * the License, or (at your option) any later version.
 *
 * srsRAN is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * A copy of the GNU Affero General Public License can be found in
 * the LICENSE file in the top-level directory of this distribution
 * and at http://www.gnu.org/licenses/.
 *
 */

#ifndef SRSUE_GW_H
#define SRSUE_GW_H

#include "gw_metrics.h"
#include "srsran/common/buffer_pool.h"
#include "srsran/common/common.h"
#include "srsran/common/interfaces_common.h"
#include "srsran/common/threads.h"
#include "srsran/interfaces/ue_gw_interfaces.h"
#include "srsran/srslog/srslog.h"
#include "tft_packet_filter.h"
#include <atomic>
#include <map>
#include <mutex>
#include <net/if.h>
#include <netinet/in.h>

namespace srsue {

class stack_interface_gw;

struct gw_args_t {
  struct log_args_t {
    std::string gw_level;
    int         gw_hex_limit;
  } log;
  std::string netns;
  std::string tun_dev_name;
  std::string tun_dev_netmask;
};

struct gw_session_t {
  int32_t            tun_fd     = -1;
  int32_t            sock       = -1;
  uint32_t           ip_addr    = 0;
  uint8_t            session_id = 0;
  std::atomic<bool>  if_up      = {false}; // Đã đồng bộ kiểu atomic giống bản gốc
  struct ifreq       ifr        = {};
  std::string        tun_name;
  std::atomic<bool>  run_enable{false};
  std::thread        worker;
  uint8_t            eps_bearer_id = NOT_ASSIGNED;
};

class gw : public gw_interface_stack, public srsran::thread
{
public:
  gw(srslog::basic_logger& logger_);
  ~gw();
  int  init(const gw_args_t& args_, stack_interface_gw* stack);
  void stop();
  void add_bearer_mapping(uint32_t lcid, uint8_t pdu_session_id) override;
  void get_metrics(gw_metrics_t& m, const uint32_t nof_tti);

  // PDCP interface
  void write_pdu(uint32_t lcid, srsran::unique_byte_buffer_t pdu);
  void write_pdu_mch(uint32_t lcid, srsran::unique_byte_buffer_t pdu);

  // NAS interface
  int  setup_if_addr(uint32_t eps_bearer_id, uint8_t pdn_type, uint32_t ip_addr, uint8_t* ipv6_if_addr, char* err_str);
  int  deactivate_eps_bearer(const uint32_t eps_bearer_id);
  int  apply_traffic_flow_template(const uint8_t& eps_bearer_id, const LIBLTE_MME_TRAFFIC_FLOW_TEMPLATE_STRUCT* tft);
  void set_test_loop_mode(const test_loop_mode_state_t mode, const uint32_t ip_pdu_delay_ms);

  // RRC interface
  void add_mch_port(uint32_t lcid, uint32_t port);
  bool is_running();

private:
  static const int GW_THREAD_PRIO = -1;

  stack_interface_gw* stack = nullptr;

  gw_args_t args = {};

  std::atomic<bool> running    = {false};
  std::atomic<bool> run_enable = {false};
  int32_t           netns_fd   = 0;

  std::map<uint8_t, std::shared_ptr<gw_session_t>> sessions;
  std::map<uint32_t, int>                          lcid_to_tun_fd;
  std::map<uint32_t, uint8_t>                      lcid_to_session;
  std::map<uint8_t, uint32_t>                      session_to_lcid;

  static const int NOT_ASSIGNED          = -1;
  std::mutex       gw_mutex;

  srslog::basic_logger& logger;

  uint8_t  current_if_id[8];

  uint32_t                                       ul_tput_bytes = 0;
  uint32_t                                       dl_tput_bytes = 0;
  std::chrono::high_resolution_clock::time_point metrics_tp; // stores time when last metrics have been taken

  void run_thread();
  int setup_if_addr4(std::shared_ptr<gw_session_t> session, uint32_t ip_addr, char* err_str);
  int  setup_if_addr6(uint8_t* ipv6_if_id, char* err_str);
  bool find_ipv6_addr(struct in6_addr* in6_out);
  void del_ipv6_addr(struct in6_addr* in6p);

  void run_session(std::shared_ptr<gw_session_t> session);
  void stop_session(uint8_t session_id);
  int  init_if(std::shared_ptr<gw_session_t> session, char* err_str);
  // MBSFN
  int                mbsfn_sock_fd                   = 0;  // Sink UDP socket file descriptor
  struct sockaddr_in mbsfn_sock_addr                 = {}; // Target address
  uint32_t           mbsfn_ports[SRSRAN_N_MCH_LCIDS] = {}; // Target ports for MBSFN data

  // TFT
  tft_pdu_matcher tft_matcher;
};

} // namespace srsue

#endif // SRSUE_GW_H
