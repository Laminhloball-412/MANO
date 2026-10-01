# Báo cáo thử nghiệm QoS theo ba S-NSSAI

## Phạm vi

- IMSI thử nghiệm: `460990000000001`.
- Không đổi CPU/RAM, PRB scheduler, gNB template hoặc Kubernetes source template.
- Open5GS v2.7.7, OCUDU gNB và OAI UE runtime multi-PDU.
- Downlink benchmark giữa OAI TUN và địa chỉ gateway của đúng UPF.

## Cấu hình đã thử

| Slice | S-NSSAI | 5QI | ARP | Session-AMBR | UPF TBF downlink |
|---|---|---:|---:|---:|---:|
| eMBB | SST1/SD000001 | 8 | 8 | 100 Mbps | 100 Mbps |
| URLLC | SST2/SD000001 | 7 | 2 | 5 Mbps | 5 Mbps |
| mMTC | SST3/SD000001 | 9 | 12 | 1 Mbps | 1 Mbps |

Các số trong MongoDB phải dùng BSON `int32` (`NumberInt`). Ghi bằng JavaScript
`number` thông thường tạo BSON double, khiến UDR đọc 5QI bằng 0 và trả HTTP 400.

## Kết quả

- Cả PDU1/5QI8, PDU2/5QI7 và PDU3/5QI9 đều thiết lập thành công.
- Make-before-break sang PDU2 và PDU3 vẫn hoàn tất; PDU nguồn chỉ release sau
  khi target TUN/gateway/SLA traffic nhẹ hợp lệ.
- Session-AMBR 1 Mbps không được UPF cưỡng chế: mMTC đạt `9,06 Mbps` trước TBF.
- Sau TBF `1 Mbps`, mMTC đạt `1,03 Mbps` với burst 16 KB.
- eMBB đạt `11,19 Mbps`; giới hạn là radio/ZMQ chứ không phải AMBR 100 Mbps.
- URLLC traffic nhẹ đạt RTT khoảng `78–93 ms`, jitter khoảng `32–42 ms`, loss
  `0%`. UDP saturation làm OAI/ZMQ data path stall và iperf hard-timeout, nên
  chưa có số throughput benchmark hợp lệ.

## Kết luận

S-NSSAI và QoS subscription đã khác thật; bandwidth downlink khác thật khi dùng
TBF. Tuy nhiên đây chưa phải bảo đảm end-to-end: TBF chưa persistent qua pod
restart, uplink chưa được shape, URLLC saturation chưa PASS và RAN scheduler chưa
được phân tách. Không được dùng baseline URLLC hiện tại để tuyên bố đặc tính
URLLC công nghiệp.

## Backup và rollback

- Backup BSON: `/home/ubuntu/mano/runtime/backups/20260820T120702Z-three-slice-qos/subscribers.archive`.
- Reset subscriber: `deploy/qos/reset-subscriber-qos.js`.
- Reset UPF qdisc: `deploy/qos/reset-upf-downlink-limits.sh`.
