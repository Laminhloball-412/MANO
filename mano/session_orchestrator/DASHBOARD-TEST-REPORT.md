# Báo cáo kiểm thử dashboard multi-PDU

- Thời gian: 2026-08-20 04:49–04:52 UTC
- Dashboard version: `6`
- Kết quả: `COMPLETED`
- Dashboard: `ANSA – UE Multi-PDU Intent & Slice Operations`
- Free5GMANO: `http://192.168.180.35:8888/dashboard/`
- Grafana: `http://192.168.180.35:30030/d/auto-slice-operations/`

Ba dịch vụ được chạy riêng từ default PDU và cho kết quả:

| Service | Flow | Slice chọn | Điểm | Kết quả PDU |
|---|---|---|---:|---|
| video | `flow-c6154edd3e2e` | slice-1/eMBB | 81,64 | giữ default PDU1 |
| control | `flow-3f851150a2ed` | slice-2/URLLC | 87,44 | tạo PDU2 rồi release PDU1 |
| sensor | `flow-567dc09a53df` | slice-3/mMTC | 85,43 | tạo PDU3 rồi release PDU1 |

PDU2 đạt RTT `74,992 ms`, jitter `32,445 ms`, loss `0%`. PDU3 đạt RTT
`70,786 ms`, jitter `30,437 ms`, loss `0%`. PDU1 chỉ được release sau khi PDU
đích đã có TUN/IP, ping được gateway và vượt cửa sổ kiểm tra SLA.

Đã kiểm tra trực quan iframe trong Free5GMANO: score, selected slice, flow state,
bảng PDU và sáu biểu đồ KPI render bình thường. Dashboard v6 thêm score component,
slice health, CPU/RAM load, capability và PDU-capacity usage. Cả `26` truy vấn
PromQL đều hợp lệ. Dashboard phân biệt UE count và PDU count, có filter UE/PDU/
service/flow/service class/slice và không đếm session lịch sử là session active.

Backup trước triển khai nằm tại:
`/home/ubuntu/mano/runtime/backups/20260820T114910Z-dashboard-v6`.
