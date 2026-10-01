# Trạng thái hiện tại của project

Tài liệu này mô tả snapshot đã kiểm thử ngày `2026-08-21`. Tài liệu không coi
kế hoạch, score hay dữ liệu lịch sử là chức năng radio/SLA đã hoàn thành.

## 1. Mục tiêu và kiến trúc hiện tại

Project thử nghiệm điều phối nhiều PDU Session của một UE theo service intent:

```text
Ứng dụng UE độc lập
  -> traffic trên TUN của UE
  -> exporter đọc counter TUN và active/application probe
  -> Prometheus scrape và lưu metric
  -> Intent Engine chủ động query Prometheus
  -> chọn profile/slice
  -> Free5GMANO allocate hoặc reuse slice
  -> OAI UE establish PDU mới
  -> validate TUN, gateway và SLA
  -> ứng dụng bind sang TUN mới và ACK
  -> release PDU dịch vụ cũ khi không còn reference
```

Prometheus không gửi lệnh xuống Intent Engine. Intent Engine là daemon chủ động
đọc metric và ra quyết định. Free5GMANO quản lý vòng đời slice; OAI UE thực thi
PDU Session qua NAS. OCUDU đang được dùng làm gNB.

## 2. Các thành phần đã có

| Thành phần | Trạng thái | Vai trò |
|---|---|---|
| Open5GS 5GC trên Kubernetes | Có | AMF dùng chung; PCF/SMF/UPF theo slice |
| Free5GMANO và Kube5GNFVO | Có | Tạo/reuse/xóa network slice instance |
| OCUDU gNB | Có | gNB ZMQ kết nối AMF và OAI UE |
| OAI UE Duranta | Có patch riêng | Runtime establish/release nhiều PDU và giữ tên TUN mặc định ổn định |
| UE telemetry exporter | Có | PDU presence, byte rate, packet rate, packet size theo UE/PDU |
| Prometheus | Có | Pull metric từ exporter/5GC/Intent Engine |
| Intent Engine daemon/API | Có | Nhận intent, query metric, xếp hạng và điều phối |
| Application simulator | Có | Tạo UDP traffic thật, bind TUN, heartbeat và ACK binding |
| Grafana/Free5GMANO dashboard | Có | Lọc theo UE/PDU/service/intent/slice và hiển thị score/KPI |
| Slice idle reaper | Có, an toàn mặc định | Đối chiếu reference + PDU live + idle timer; chỉ `dry-run` |
| UPF QoS reconciler | Có | Kiểm tra và áp lại TBF 100/5/1 Mbps khi pod UPF đổi, không sửa template |

Hai service chạy nền trên VM:

- `ansa-session-metrics.service`: exporter tại cổng `9105`;
- `ansa-intent-engine.service`: API/worker tại cổng `9106`.

## 3. Chức năng đã kiểm thử

- OCUDU gNB đăng ký NGAP với AMF.
- OAI UE đăng ký 5G và tạo default PDU1 trên SST1.
- Runtime tạo và release PDU2/PDU3 mà không restart UE.
- PDU1, PDU2 và PDU3 đã từng đồng thời UP; cả ba ping được gateway tương ứng.
- Một intent có thể reuse slice đang healthy hoặc gọi Free5GMANO khi namespace
  đích chưa tồn tại.
- `concurrent` giữ nhiều PDU cho nhiều dịch vụ chạy song song.
- `migrate` thực hiện establish/validate/bind/ACK trước khi release PDU dịch vụ
  cũ.
- Default PDU1 được bảo vệ; chuyển khỏi default không xóa đường nền này.
- Metric và score được gắn `ue_id` và `pdu_session_id`, tránh trộn nhiều UE/PDU.
- Giới hạn admission hiện tại là tối đa ba PDU đồng thời cho mỗi UE.
- Bộ test hiện tại gồm 54 test `session_orchestrator` và 13 test
  `intent_flow_v2` (tổng 67 test).
- Registry tách khóa session theo `(ue_id, pdu_session_id)`; test đã chứng minh
  kết thúc UE1/PDU2 không làm mất reference UE2/PDU2.
- Session state được reconcile với TUN live; trạng thái cũ chuyển `LOST` sau
  radio restart và trở lại `ACTIVE` nếu đúng PDU của đúng UE xuất hiện lại.

## 3.1. Kết quả live gần nhất (2026-08-21)

- UE1 attach qua OCUDU/OAI ZMQ và nhận PDU1 `10.45.1.53`.
- Control intent tạo PDU2 `10.45.2.6`; sensor intent tạo PDU3 `10.45.3.6`.
- Cả ba PDU cùng UP, có NAS Establishment Accept, TUN/IP và ping gateway `0%`
  loss.
- Migration PDU2 -> PDU3 vào `AWAITING_APPLICATION_ACK` khi cả PDU cùng UP;
  sau ACK OAI mới gửi Release Request cho PDU2. PDU1 không bị release.
- Active probe của URLLC đo RTT `106.810 ms`, jitter `35.251 ms`, loss `0%`:
  connectivity PASS nhưng service SLA `DEGRADED`. mMTC đo RTT `94.074 ms`,
  jitter `16.327 ms`, loss `0%`: SLA lab `MET`.

## 3.2. Kiểm thử observe-first sau nâng cấp (2026-08-21)

- OAI UE attach lại qua OCUDU, default PDU1 nhận `10.45.1.54`; ping gateway
  `10.45.1.1` loss `0%`.
- Ứng dụng control chạy trên PDU1 trước khi có intent. Prometheus đã scrape
  `flow_id=ue1-control-observe-fixed` với ít nhất ba sample; sample dùng để
  quyết định có source `application` và tuổi khoảng `0.155 s`.
- Sau khi gắn intent, engine reuse slice-2, thiết lập PDU2 `10.45.2.8`, validate
  gateway, ứng dụng bind sang `oaitun_ue1p2` rồi ACK. PDU1 và PDU2 cùng UP và
  đều ping loss `0%` tại thời điểm kiểm tra.
- Flow sensor độc lập được đo trên PDU1, chọn slice-3 và chuyển sang PDU3
  `10.45.3.7`; gateway PDU3 ping loss `0%`.
- Migration sensor/PDU3 sang control/PDU2 kết thúc với flow mới `STEERED`,
  intent mới `ACTIVE`; intent cũ chỉ `ENDED` và PDU3 chỉ DOWN sau application
  ACK. Simulator ghi nhận switch tại `27.837 s`, không có send/bind error.
- Khi ứng dụng hết lease/thời lượng, PDU2/PDU3 đều được release; PDU1 được bảo
  vệ và vẫn ping được. Ba service nền và WebUI đều `active`.

Kết quả trên chứng minh orchestration và multi-PDU của một UE. Nó không chứng
minh URLLC cấp công nghiệp và không được dùng để tuyên bố multi-UE RF.

## 4. Phạm vi ba slice

Đúng: bản lab hiện tại mới mô hình hóa ba profile cơ bản:

1. `slice-1`: eMBB/default, SST `1`, SD `000001`, PDU1;
2. `slice-2`: URLLC, SST `2`, SD `000001`, PDU2;
3. `slice-3`: mMTC, SST `3`, SD `000001`, PDU3.

Đây là ba slice instance độc lập, không chỉ là ba nhãn. Chúng có namespace,
S-NSSAI, SMF/UPF/PCF, subnet/gateway và QoS subscription riêng. Tuy nhiên project
chưa tự sinh số lượng slice instance tùy ý trong cùng một service class. Các
profile hiện ánh xạ vào ba slot cố định; `allocate` nghĩa là dựng lại đúng slot
đang thiếu.

## 5. Những gì chưa hoàn thành

- Chưa triển khai URSP hoàn chỉnh trong UE; steering nằm ở tầng ứng dụng bằng
  cách bind socket vào địa chỉ/TUN được chọn.
- Chưa tự chuyển một TCP/UDP socket đang mở. Ứng dụng phải mở socket mới; muốn
  trong suốt cần proxy, QUIC, MPTCP hoặc policy/eBPF phù hợp.
- Chưa triển khai đầy đủ SSC mode 3 theo 3GPP; project áp dụng nguyên tắc
  establish-before-release ở tầng orchestration.
- Chưa có RAN scheduler/PRB isolation riêng cho eMBB, URLLC và mMTC.
- UPF TBF mới giới hạn downlink, chưa giới hạn uplink. Daemon tự reconcile sau
  pod restart nhưng đây vẫn là runtime enforcement, chưa nằm trong template NF.
- URLLC saturation benchmark chưa PASS; không được tuyên bố URLLC công nghiệp.
- `dry_run_deallocate` đang là `true`, nên idle slice chỉ được báo `dry-run`,
  không tự xóa thật.
- Runtime cố định `ue_ids: [1, 2, 3]`; exporter/API/dashboard và registry đã hỗ
  trợ, kiểm thử cách ly nhiều UE. Tuy nhiên tutorial OCUDU ZMQ hiện tại chỉ mô
  tả một OAI UE và ghi multi-UE emulation là use case sẽ bổ sung sau. Vì vậy
  UE2/UE3 chưa được tuyên bố attach RF đồng thời; cần OTA/COTS hoặc virtual RF
  multi-UE được OCUDU hỗ trợ để đóng khoảng trống này.

## 6. Quy tắc an toàn hiện tại

- Không sửa template gNB, UE, subscriber hoặc Kubernetes gốc khi chạy flow.
- Patch OAI được lưu riêng tại
  `session_orchestrator/patches/0001-oai-runtime-multipdu-mbb.patch`.
- Namespace `share` và `slice-1` được bảo vệ khỏi deallocation.
- Nếu target PDU fail TUN/gateway/SLA thì source PDU được giữ.
- Nếu ứng dụng chưa ACK trong chế độ `migrate`, source PDU dịch vụ chưa được
  release.
- Nếu Prometheus mất hoặc metric stale, engine không được đưa ra quyết định phá
  hủy dựa trên số liệu cũ.

## 7. Nguồn cấu hình và tài liệu liên quan

- Cấu hình lab: `session_orchestrator/config.lab.json`.
- Thiết kế runtime: `session_orchestrator/RUNTIME-DESIGN.md`.
- Metric/dashboard: `session_orchestrator/OBSERVABILITY.md`.
- Báo cáo QoS: `session_orchestrator/QOS-BENCHMARK-REPORT.md`.
- Runbook thủ công mới: `session_orchestrator/MANUAL-E2E-TEST-VI.md`.
- Ma trận kịch bản mới: `session_orchestrator/THREE-SLICE-SCENARIOS-VI.md`.
