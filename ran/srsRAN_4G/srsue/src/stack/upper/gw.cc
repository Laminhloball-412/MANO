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

#include "srsue/hdr/stack/upper/gw.h"
#include "srsran/common/standard_streams.h"
#include "srsran/interfaces/ue_pdcp_interfaces.h"
#include "srsran/upper/ipv6.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/if_tun.h>
#include <linux/ip.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <netinet/in.h>
#include <poll.h>
#include <cstring>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>
#include <vector>

namespace srsue {

gw::gw(srslog::basic_logger& logger_) : thread("GW"), logger(logger_), tft_matcher(logger) {}

void gw::run_session(std::shared_ptr<gw_session_t> session)
{
  while (session->run_enable) {
    struct pollfd tun_poll = {};
    tun_poll.fd            = session->tun_fd;
    tun_poll.events        = POLLIN;

    int poll_result = poll(&tun_poll, 1, 100);
    if (poll_result < 0) {
      if (errno == EINTR) {
        continue;
      }
      logger.error("PDU session %u TUN poll failed: %s", session->session_id, strerror(errno));
      break;
    }
    if (poll_result == 0) {
      continue;
    }
    if ((tun_poll.revents & (POLLERR | POLLHUP | POLLNVAL)) != 0) {
      if (session->run_enable) {
        logger.warning("PDU session %u TUN stopped (revents=0x%x)", session->session_id, tun_poll.revents);
      }
      break;
    }
    if ((tun_poll.revents & POLLIN) == 0) {
      continue;
    }

    srsran::unique_byte_buffer_t pdu = srsran::make_byte_buffer();
    if (!pdu) {
      logger.error("Could not allocate uplink PDU for session %u", session->session_id);
      usleep(100000);
      continue;
    }

    int n = read(session->tun_fd,
                 pdu->msg,
                 SRSRAN_MAX_BUFFER_SIZE_BYTES - SRSRAN_BUFFER_HEADER_OFFSET);
    if (n < 0) {
      if (errno == EINTR || errno == EAGAIN) {
        continue;
      }
      logger.error("PDU session %u TUN read failed: %s", session->session_id, strerror(errno));
      break;
    }
    if (n == 0) {
      continue;
    }

    pdu->N_bytes        = static_cast<uint32_t>(n);
    const uint8_t ipver = pdu->msg[0] >> 4U;
    uint32_t      packet_length = 0;
    if (ipver == 4 && pdu->N_bytes >= sizeof(struct iphdr)) {
      packet_length = ntohs(reinterpret_cast<struct iphdr*>(pdu->msg)->tot_len);
    } else if (ipver == 6 && pdu->N_bytes >= sizeof(struct ipv6hdr)) {
      packet_length = ntohs(reinterpret_cast<struct ipv6hdr*>(pdu->msg)->payload_len) + sizeof(struct ipv6hdr);
    } else {
      logger.warning(pdu->msg,
                     pdu->N_bytes,
                     "Dropping invalid uplink IP packet from PDU session %u",
                     session->session_id);
      continue;
    }
    if (packet_length != pdu->N_bytes) {
      logger.warning("Dropping incomplete uplink packet from PDU session %u (expected=%u, read=%u)",
                     session->session_id,
                     packet_length,
                     pdu->N_bytes);
      continue;
    }

    uint32_t lcid = 0;
    {
      std::lock_guard<std::mutex> lock(gw_mutex);
      auto                        mapping = session_to_lcid.find(session->session_id);
      if (mapping == session_to_lcid.end()) {
        logger.warning("No LCID mapping for PDU session %u; dropping uplink packet", session->session_id);
        continue;
      }
      lcid = mapping->second;
      ul_tput_bytes += pdu->N_bytes;
    }

    pdu->set_timestamp();
    logger.info(pdu->msg,
                pdu->N_bytes,
                "TX PDU. session=%u lcid=%u",
                session->session_id,
                lcid);
    stack->write_sdu(lcid, std::move(pdu));
  }

  logger.info("PDU session %u uplink worker stopped", session->session_id);
}

void gw::add_bearer_mapping(uint32_t lcid,
                              uint8_t  pdu_session_id)
{
  std::lock_guard<std::mutex> lock(gw_mutex);

  lcid_to_session[lcid] = pdu_session_id;
  session_to_lcid[pdu_session_id] = lcid;

  auto it = sessions.find(pdu_session_id);
  if (it != sessions.end()) {
    lcid_to_tun_fd[lcid] = it->second->tun_fd;
    logger.info("Bearer mapping: lcid=%d → session=%d tun_fd=%d",
                lcid, pdu_session_id, it->second->tun_fd);
  } else {
    logger.warning("Session %d not found for lcid=%d",
                   pdu_session_id, lcid);
  }
}

int gw::init(const gw_args_t& args_, stack_interface_gw* stack_)
{
  stack      = stack_;
  args       = args_;
  run_enable = true;

  logger.set_level(srslog::str_to_basic_level(args.log.gw_level));
  logger.set_hex_dump_max_size(args.log.gw_hex_limit);

  metrics_tp = std::chrono::high_resolution_clock::now();

  // MBSFN
  mbsfn_sock_fd = socket(AF_INET, SOCK_DGRAM, 0);
  if (mbsfn_sock_fd < 0) {
    logger.error("Failed to create MBSFN sink socket");
    return SRSRAN_ERROR;
  }
  if (fcntl(mbsfn_sock_fd, F_SETFL, O_NONBLOCK)) {
    logger.error("Failed to set non-blocking MBSFN sink socket");
    return SRSRAN_ERROR;
  }

  mbsfn_sock_addr.sin_family = AF_INET;
  if (inet_pton(mbsfn_sock_addr.sin_family, "127.0.0.1", &mbsfn_sock_addr.sin_addr.s_addr) != 1) {
    perror("inet_pton");
    return false;
  }

  return SRSRAN_SUCCESS;
}

gw::~gw()
{
  stop(); // Gọi stop() để dọn dẹp tập trung
}

void gw::stop()
{
  if (run_enable) {
    run_enable = false;
    
    // Giữ nguyên logic gốc: Dừng thread một cách an toàn
    if (running) {
      thread_cancel();
    }

    int cnt = 0;
    while (running && cnt < 100) {
      usleep(10000);
      cnt++;
    }
    if (running) {
      wait_thread_finish();
    }
  }

  // Giữ nguyên logic gốc: Đóng socket MCH (nếu có)
  if (mbsfn_sock_fd > 0) {
    close(mbsfn_sock_fd);
    mbsfn_sock_fd = 0;
  }

  std::vector<std::shared_ptr<gw_session_t>> sessions_to_stop;
  {
    std::lock_guard<std::mutex> lock(gw_mutex);
    for (auto& item : sessions) {
      if (item.second) {
        item.second->run_enable = false;
        sessions_to_stop.push_back(item.second);
      }
    }
    sessions.clear();
    lcid_to_tun_fd.clear();
    lcid_to_session.clear();
    session_to_lcid.clear();
  }

  for (auto& session : sessions_to_stop) {
    if (session->worker.joinable()) {
      session->worker.join();
    }
    if (session->tun_fd >= 0) {
      close(session->tun_fd);
      session->tun_fd = -1;
    }
    if (session->sock >= 0) {
      close(session->sock);
      session->sock = -1;
    }
    session->if_up = false;
  }
}

void gw::get_metrics(gw_metrics_t& m, const uint32_t nof_tti)
{
  std::lock_guard<std::mutex> lock(gw_mutex);

  std::chrono::duration<double> secs = std::chrono::high_resolution_clock::now() - metrics_tp;

  double dl_tput_mbps_real_time = (dl_tput_bytes * 8 / (double)1e6) / secs.count();
  double ul_tput_mbps_real_time = (ul_tput_bytes * 8 / (double)1e6) / secs.count();

  // Use the provided TTI counter to compute rate for metrics interface
  m.dl_tput_mbps = (nof_tti > 0) ? ((dl_tput_bytes * 8 / (double)1e6) / (nof_tti / 1000.0)) : 0.0;
  m.ul_tput_mbps = (nof_tti > 0) ? ((ul_tput_bytes * 8 / (double)1e6) / (nof_tti / 1000.0)) : 0.0;

  logger.debug("gw_rx_rate_mbps=%4.2f (real=%4.2f), gw_tx_rate_mbps=%4.2f (real=%4.2f)",
               m.dl_tput_mbps,
               dl_tput_mbps_real_time,
               m.ul_tput_mbps,
               ul_tput_mbps_real_time);

  // reset counters and store time
  metrics_tp    = std::chrono::high_resolution_clock::now();
  dl_tput_bytes = 0;
  ul_tput_bytes = 0;
}

/*******************************************************************************
  PDCP interface
*******************************************************************************/
void gw::write_pdu(uint32_t lcid, srsran::unique_byte_buffer_t pdu)
{
  if (!pdu || pdu->N_bytes == 0) {
    logger.warning("Empty downlink PDU for lcid=%u", lcid);
    return;
  }

  // Giữ lại log latency và thống kê throughput như bản gốc
  logger.info(pdu->msg, pdu->N_bytes, "RX PDU. Stack latency: %ld us", pdu->get_latency_us().count());
  const uint8_t ipver = pdu->msg[0] >> 4U;
  if ((ipver == 4 && pdu->N_bytes < sizeof(struct iphdr)) ||
      (ipver == 6 && pdu->N_bytes < sizeof(struct ipv6hdr)) || (ipver != 4 && ipver != 6)) {
    logger.warning("Invalid downlink IP packet for lcid=%u; dropping", lcid);
    return;
  }

  std::lock_guard<std::mutex> lock(gw_mutex);
  auto                        mapping = lcid_to_session.find(lcid);
  if (mapping == lcid_to_session.end()) {
    logger.error("No PDU session mapping for lcid=%u; dropping downlink packet", lcid);
    return;
  }
  auto session = sessions.find(mapping->second);
  if (session == sessions.end() || !session->second || session->second->tun_fd < 0) {
    logger.error("PDU session %u for lcid=%u has no valid TUN; dropping downlink packet", mapping->second, lcid);
    return;
  }

  int n = write(session->second->tun_fd, pdu->msg, pdu->N_bytes);
  if (n < 0) {
    logger.error("Failed downlink write to session=%u lcid=%u: %s", mapping->second, lcid, strerror(errno));
  } else if (static_cast<uint32_t>(n) != pdu->N_bytes) {
    logger.warning("Partial downlink write to session=%u lcid=%u (expected=%u, wrote=%d)",
                   mapping->second,
                   lcid,
                   pdu->N_bytes,
                   n);
  } else {
    dl_tput_bytes += pdu->N_bytes;
  }
}

void gw::write_pdu_mch(uint32_t lcid, srsran::unique_byte_buffer_t pdu)
{

}

/*******************************************************************************
  NAS interface
*******************************************************************************/
int gw::setup_if_addr(uint32_t eps_bearer_id,
                       uint8_t  pdn_type,
                       uint32_t ip_addr,
                       uint8_t* ipv6_if_addr,
                       char*    err_str)
{
  uint8_t session_id = static_cast<uint8_t>(eps_bearer_id);

  // Multi-session IPv6 needs a session-aware IPv6 configuration path. Reject it
  // explicitly instead of reporting success without configuring an address.
  if (pdn_type != LIBLTE_MME_PDN_TYPE_IPV4) {
    logger.error("PDU session %u requested unsupported IPv6/IPv4v6 data plane", session_id);
    return SRSRAN_ERROR_CANT_START;
  }

  stop_session(session_id);

  // Tạo session mới
  auto new_session       = std::make_shared<gw_session_t>();
  new_session->session_id = session_id;

  {
    std::lock_guard<std::mutex> lock(gw_mutex);
    sessions[session_id] = new_session;
  }

  // Setup IP (ngoài lock)
  if (pdn_type == LIBLTE_MME_PDN_TYPE_IPV4 ||
      pdn_type == LIBLTE_MME_PDN_TYPE_IPV4V6) {
    int err = setup_if_addr4(new_session, ip_addr, err_str);
    if (err != SRSRAN_SUCCESS) {
      stop_session(session_id);
      return err;
    }
  }

  // Spawn thread riêng cho session này
  new_session->run_enable = true;
  new_session->worker = std::thread(
    [this, session = new_session]() {
      run_session(session);
    }
  );

  return SRSRAN_SUCCESS;
}

int gw::deactivate_eps_bearer(const uint32_t eps_bearer_id)
{
  logger.info("Deactivating EPS bearer / PDU session %d", eps_bearer_id);
  tft_matcher.delete_tft_for_eps_bearer(eps_bearer_id);
  stop_session(static_cast<uint8_t>(eps_bearer_id));
  return SRSRAN_SUCCESS;
}

void gw::stop_session(uint8_t session_id)
{
  std::shared_ptr<gw_session_t> session;
  {
    std::lock_guard<std::mutex> lock(gw_mutex);
    auto                        item = sessions.find(session_id);
    if (item == sessions.end()) {
      return;
    }
    session             = item->second;
    session->run_enable = false;
    sessions.erase(item);

    for (auto map_it = lcid_to_session.begin(); map_it != lcid_to_session.end();) {
      if (map_it->second != session_id) {
        ++map_it;
        continue;
      }
      lcid_to_tun_fd.erase(map_it->first);
      map_it = lcid_to_session.erase(map_it);
    }
    session_to_lcid.erase(session_id);
  }

  if (session && session->worker.joinable()) {
    session->worker.join();
  }
  if (session && session->tun_fd >= 0) {
    close(session->tun_fd);
    session->tun_fd = -1;
  }
  if (session && session->sock >= 0) {
    close(session->sock);
    session->sock = -1;
  }
  if (session) {
    session->if_up = false;
  }
}

bool gw::is_running()
{
  return running;
}

int gw::apply_traffic_flow_template(const uint8_t& erab_id, const LIBLTE_MME_TRAFFIC_FLOW_TEMPLATE_STRUCT* tft)
{
  return tft_matcher.apply_traffic_flow_template(erab_id, tft);
}

void gw::set_test_loop_mode(const test_loop_mode_state_t mode, const uint32_t ip_pdu_delay_ms)
{
  logger.error("UE test loop mode not supported");
}

/*******************************************************************************
  RRC interface
*******************************************************************************/
void gw::add_mch_port(uint32_t lcid, uint32_t port)
{
  if (lcid > 0 && lcid < SRSRAN_N_MCH_LCIDS) {
    mbsfn_ports[lcid] = port;
  }
}

/********************/
/*    GW Receive    */
/********************/
void gw::run_thread()
{
  // TODO: Sẽ implement logic Uplink đa kênh (polling) ở phần tiếp theo
  while (run_enable) {
    usleep(10000); 
  }
}
/**************************/
/* TUN Interface Helpers  */
/**************************/
int gw::init_if(std::shared_ptr<gw_session_t> s, char* err_str)
{
  if (s->if_up) {
    return SRSRAN_ERROR_ALREADY_STARTED;
  }

  // Tên TUN động theo session_id
  s->tun_name = args.tun_dev_name + std::to_string(s->session_id);

  // Vào netns (giữ nguyên logic cũ)
  if (!args.netns.empty()) {
    std::string netns("/run/netns/");
    netns += args.netns;
    int netns_fd = open(netns.c_str(), O_RDONLY);
    if (netns_fd == -1) {
      err_str = strerror(errno);
      return SRSRAN_ERROR_CANT_START;
    }
    if (setns(netns_fd, CLONE_NEWNET) == -1) {
      err_str = strerror(errno);
      close(netns_fd);
      return SRSRAN_ERROR_CANT_START;
    }
    close(netns_fd);
  }

  // Tạo TUN device
  s->tun_fd = open("/dev/net/tun", O_RDWR);
  if (s->tun_fd < 0) {
    err_str = strerror(errno);
    return SRSRAN_ERROR_CANT_START;
  }

  memset(&s->ifr, 0, sizeof(s->ifr));
  s->ifr.ifr_flags = IFF_TUN | IFF_NO_PI;
  strncpy(s->ifr.ifr_ifrn.ifrn_name,
          s->tun_name.c_str(),
          IFNAMSIZ - 1);

  if (ioctl(s->tun_fd, TUNSETIFF, &s->ifr) < 0) {
    err_str = strerror(errno);
    close(s->tun_fd);
    s->tun_fd = -1;
    return SRSRAN_ERROR_CANT_START;
  }

  if (fcntl(s->tun_fd, F_SETFL, O_NONBLOCK) < 0) {
    logger.error("Failed to set PDU session %u TUN non-blocking: %s", s->session_id, strerror(errno));
    close(s->tun_fd);
    s->tun_fd = -1;
    return SRSRAN_ERROR_CANT_START;
  }

  // Bring up interface
  s->sock = socket(AF_INET, SOCK_DGRAM, 0);
  if (s->sock < 0) {
    logger.error("Failed to create control socket for PDU session %u: %s", s->session_id, strerror(errno));
    return SRSRAN_ERROR_CANT_START;
  }
  if (ioctl(s->sock, SIOCGIFFLAGS, &s->ifr) < 0) {
    logger.error("Failed to read TUN flags for PDU session %u: %s", s->session_id, strerror(errno));
    return SRSRAN_ERROR_CANT_START;
  }
  s->ifr.ifr_flags |= IFF_UP | IFF_RUNNING;
  if (ioctl(s->sock, SIOCSIFFLAGS, &s->ifr) < 0) {
    logger.error("Failed to bring up TUN for PDU session %u: %s", s->session_id, strerror(errno));
    return SRSRAN_ERROR_CANT_START;
  }

  s->if_up = true;
  return SRSRAN_SUCCESS;
}

int gw::setup_if_addr4(std::shared_ptr<gw_session_t> session, uint32_t ip_addr, char* err_str)
{
  if (ip_addr == 0) {
    logger.error("Network assigned an invalid IPv4 address to PDU session %u", session->session_id);
    return SRSRAN_ERROR_CANT_START;
  }

  if (ip_addr != session->ip_addr) {
    if (!session->if_up) {
      // Gọi init_if mới có truyền session_id
      if (init_if(session, err_str)) {
        logger.error("init_if failed for session %u", session->session_id);
        return SRSRAN_ERROR_CANT_START;
      }
    }

    if (session->sock >= 0) {
      close(session->sock);
      session->sock = -1;
    }

    // Setup the IP address sử dụng socket và ifr riêng của session
    session->sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (session->sock < 0) {
      logger.error("Failed to create IPv4 control socket for PDU session %u: %s",
                   session->session_id,
                   strerror(errno));
      return SRSRAN_ERROR_CANT_START;
    }
    session->ifr.ifr_addr.sa_family = AF_INET;
    ((struct sockaddr_in*)&session->ifr.ifr_addr)->sin_addr.s_addr = htonl(ip_addr);
    
    if (0 > ioctl(session->sock, SIOCSIFADDR, &session->ifr)) {
      err_str = strerror(errno);
      logger.debug("Failed to set socket address: %s", err_str);
      return SRSRAN_ERROR_CANT_START;
    }

    session->ifr.ifr_netmask.sa_family = AF_INET;
    if (inet_pton(session->ifr.ifr_netmask.sa_family,
                  args.tun_dev_netmask.c_str(),
                  &((struct sockaddr_in*)&session->ifr.ifr_netmask)->sin_addr.s_addr) != 1) {
      logger.error("Invalid tun_dev_netmask: %s", args.tun_dev_netmask.c_str());
      srsran::console("Invalid tun_dev_netmask: %s\n", args.tun_dev_netmask.c_str());
      perror("inet_pton");
      return SRSRAN_ERROR_CANT_START;
    }

    if (0 > ioctl(session->sock, SIOCSIFNETMASK, &session->ifr)) {
      err_str = strerror(errno);
      logger.debug("Failed to set socket netmask: %s", err_str);
      return SRSRAN_ERROR_CANT_START;
    }

    session->ip_addr = ip_addr;
  }
  return SRSRAN_SUCCESS;
}

int gw::setup_if_addr6(uint8_t* ipv6_if_id, char* err_str)
{
  logger.error("IPv6 setup is not implemented for multi-PDU TUN sessions");
  return SRSRAN_ERROR_CANT_START;
}

bool gw::find_ipv6_addr(struct in6_addr* in6_out)
{
  int               n, rtattrlen, fd = -1;
  unsigned int      if_index;
  struct rtattr *   rta, *rtatp;
  struct nlmsghdr*  nlmp;
  struct ifaddrmsg* rtmp;
  struct in6_addr*  in6p;
  char              buf[2048];
  struct {
    struct nlmsghdr  n;
    struct ifaddrmsg r;
    char             buf[1024];
  } req;

  logger.debug("Trying to obtain IPv6 addr of %s interface", args.tun_dev_name.c_str());

  // Get Interface Index
  if_index = if_nametoindex(args.tun_dev_name.c_str());
  if (if_index == 0) {
    logger.error("Could not find interface index");
    goto err_out;
  }

  // Open NETLINK socket
  fd = socket(PF_NETLINK, SOCK_DGRAM, NETLINK_ROUTE);
  if (fd < 0) {
    logger.error("Error openning NETLINK socket -- %s", strerror(errno));
    goto err_out;
  }

  // We use RTM_GETADDR to get the ip address from the kernel
  memset(&req, 0, sizeof(req));
  req.n.nlmsg_len   = NLMSG_LENGTH(sizeof(struct ifaddrmsg));
  req.n.nlmsg_flags = NLM_F_REQUEST | NLM_F_MATCH;
  req.n.nlmsg_type  = RTM_GETADDR;

  // AF_INET6 is used to signify the kernel to fetch only ipv6 entires.
  req.r.ifa_family = AF_INET6;

  // Fill up all the attributes for the rtnetlink header.
  // The length is important. 16 signifies we are requesting IPv6 addresses
  rta          = (struct rtattr*)(((char*)&req) + NLMSG_ALIGN(req.n.nlmsg_len));
  rta->rta_len = RTA_LENGTH(16);

  // Time to send and recv the message from kernel
  n = send(fd, &req, req.n.nlmsg_len, 0);
  if (n < 0) {
    logger.error("Error sending NETLINK message to kernel -- %s", strerror(errno));
    goto err_out;
  }

  n = recv(fd, buf, sizeof(buf), 0);
  if (n < 0) {
    logger.error("Error receiving from NETLINK socket");
    goto err_out;
  }

  if (n == 0) {
    logger.error("Nothing received from NETLINK Socket");
    goto err_out;
  }

  // Parse the reply
  for (nlmp = (struct nlmsghdr*)buf; NLMSG_OK(nlmp, n); nlmp = NLMSG_NEXT(nlmp, n)) {
    // Chack NL message type
    if (nlmp->nlmsg_type == NLMSG_DONE) {
      logger.error("Reach end of NETLINK message without finding IPv6 address.");
      goto err_out;
    }
    if (nlmp->nlmsg_type == NLMSG_ERROR) {
      logger.error("NLMSG_ERROR in NETLINK reply");
      goto err_out;
    }
    logger.debug("NETLINK message type %d", nlmp->nlmsg_type);

    // Get IFA message
    rtmp      = (struct ifaddrmsg*)NLMSG_DATA(nlmp);
    rtatp     = (struct rtattr*)IFA_RTA(rtmp);
    rtattrlen = IFA_PAYLOAD(nlmp);
    for (; RTA_OK(rtatp, rtattrlen); rtatp = RTA_NEXT(rtatp, rtattrlen)) {
      // We are looking IFA_ADDRESS rt_attribute type.
      // For more info on the different types see man(7) rtnetlink.
      if (rtatp->rta_type == IFA_ADDRESS) {
        in6p = (struct in6_addr*)RTA_DATA(rtatp);
        if (if_index == rtmp->ifa_index) {
          for (int i = 0; i < 16; i++) {
            in6_out->s6_addr[i] = in6p->s6_addr[i];
          }
          goto out;
        }
      }
    }
  }

err_out:
  if (fd > 0) {
    close(fd);
  }
  return false;
out:
  close(fd);
  return true;
}

void gw::del_ipv6_addr(struct in6_addr* in6p)
{
  int          status, fd = -1;
  unsigned int if_index;
  struct {
    struct nlmsghdr  n;
    struct ifaddrmsg ifa;
    char             buf[1024];
  } req;

  // Get Interface Index
  if_index = if_nametoindex(args.tun_dev_name.c_str());
  if (if_index == 0) {
    logger.error("Could not find interface index");
    goto out;
  }

  // Open netlink socket
  fd = socket(PF_NETLINK, SOCK_DGRAM, NETLINK_ROUTE);
  if (fd < 0) {
    logger.error("Error openning NETLINK socket -- %s", strerror(errno));
    goto out;
  }

  // We use RTM_DELADDR to delete the ip address from the interface
  memset(&req, 0, sizeof(req));
  req.n.nlmsg_len   = NLMSG_LENGTH(sizeof(struct ifaddrmsg));
  req.n.nlmsg_type  = RTM_DELADDR;
  req.n.nlmsg_flags = NLM_F_REQUEST;

  req.ifa.ifa_family    = AF_INET6;
  req.ifa.ifa_prefixlen = 64;
  req.ifa.ifa_index     = if_index; // set the tun_srsue index
  req.ifa.ifa_scope     = 0;

  // Add RT atribute
  struct rtattr* rta;
  rta           = (struct rtattr*)(((char*)&req.n) + NLMSG_ALIGN(req.n.nlmsg_len));
  rta->rta_type = IFA_LOCAL;
  rta->rta_len  = RTA_LENGTH(16);
  memcpy(RTA_DATA(rta), in6p, 16);
  req.n.nlmsg_len = NLMSG_ALIGN(req.n.nlmsg_len) + rta->rta_len;

  status = send(fd, &req, req.n.nlmsg_len, 0);
  if (status < 0) {
    logger.error("Error sending NETLINK message");
    goto out;
  }

out:
  if (fd >= 0) {
    close(fd);
  }
}
} // namespace srsue
