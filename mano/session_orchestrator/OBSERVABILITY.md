# Thiết kế đánh giá slice cho nhiều UE và nhiều slice

## 1. Đơn vị của một quyết định

Điểm phù hợp không thuộc riêng một slice và cũng không dùng chung cho mọi UE.
Mỗi bản ghi phải có khóa:

```text
(ue_id, service, flow_id, candidate_slice_id)
```

Ví dụ UE1 và UE2 cùng yêu cầu URLLC vẫn có điểm khác nhau vì metric, vị trí,
tải và PDU hiện tại có thể khác nhau. Nếu có `slice-2` và `slice-4` cùng thuộc
URLLC, mỗi UE phải được chấm riêng với cả hai slice này.

PDU Session được định danh bằng `(ue_id, pdu_session_id)`. Không được dùng số
lượng UE thay cho số PDU vì một UE có thể giữ nhiều PDU đồng thời.

## 2. Prometheus có tự nhận biết dịch vụ không?

Không. Throughput, RTT hay packet size không đủ để khẳng định ứng dụng là video,
robot hay telemetry. Hệ thống phải kết hợp ba nguồn:

1. **Service intent/SLA yêu cầu**: loại dịch vụ và ngưỡng người dùng cần.
2. **Metric trên default PDU**: mô tả nhu cầu và chất lượng đường hiện tại.
3. **Capability/runtime của candidate slice**: năng lực, health, tải và phần tài
   nguyên còn lại của từng slice instance.

Metric trên default PDU chỉ cho biết có nhu cầu chuyển hay không. Nó không chứng
minh candidate slice tốt. Sau khi thiết lập PDU mới, hệ thống phải đo lại trên
target PDU; chỉ chuyển traffic khi target đạt SLA trong cửa sổ ổn định.

### Slice chưa tồn tại và make-before-break

Dashboard phân biệt hai trạng thái của một candidate:

- `allocatable_absent_*`: namespace/pod chưa tồn tại nhưng capability profile
  vẫn phù hợp; Intent Engine được phép chọn để Free5GMANO allocate.
- `ineligible_health_or_capacity`: slice đã tồn tại nhưng không healthy hoặc
  đã hết capacity; không được thiết lập PDU mới vào slice đó.

Trong lab, mapping data-plane được giữ ổn định: eMBB dùng slot 1, URLLC dùng
slot 2 và mMTC dùng slot 3. Auto allocation phải tạo đúng slot vì PDU ID, TUN,
gateway và subscriber S-NSSAI đã được cấu hình theo mapping này.

Make-before-break là khoảng chồng lấn có kiểm soát: PDU mới được establish và
kiểm tra trong khi PDU cũ vẫn truyền được dữ liệu, sau đó traffic mới được
steer, traffic cũ được drain và PDU cũ mới bị release. Nếu ứng dụng thật sự cần
hai dịch vụ đồng thời thì giữ cả hai PDU là multi-PDU concurrency, không phải
một lần chuyển make-before-break. Slice chỉ được deallocate sau khi không còn
PDU tham chiếu và đã hết idle timeout.

## 3. Profile cố định và slice instance động

Nên giữ ba **service-class profile** eMBB, URLLC và mMTC làm baseline, nhưng không
hard-code rằng mỗi class chỉ có đúng một slice.

| Profile | Mục tiêu chính | Metric quan trọng |
|---|---|---|
| eMBB | băng thông lớn | throughput, capacity còn lại, loss |
| URLLC | trễ thấp, ổn định | RTT, jitter, loss, reliability |
| mMTC | nhiều thiết bị/bản tin nhỏ | message rate, packet size, session capacity |

Mỗi slice instance kế thừa profile rồi có override riêng: S-NSSAI, DNN, capacity,
baseline latency, max PDU, priority và cost. Các giá trị này phải được hiệu chỉnh
bằng benchmark của lab, không coi tên eMBB/URLLC/mMTC là bằng chứng hiệu năng.

Trong project, trường `service_class` tách loại dịch vụ khỏi tên instance. Vì
vậy có thể khai báo `urllc-primary`, `urllc-secondary` cùng class `urllc` nhưng
namespace, SD, gateway và PDU Session ID khác nhau.

## 4. Công thức suitability đề xuất

Trước tiên áp dụng eligibility gate. Candidate bị loại nếu không healthy, không
hỗ trợ DNN/S-NSSAI, hết capacity hoặc không đủ tài nguyên tối thiểu.

Với candidate hợp lệ:

```text
Score = 100 × (
    0.40 × RequirementFit
  + 0.25 × QoSHeadroom
  + 0.20 × HealthAndLoad
  + 0.15 × IntentAffinity
)
```

- `RequirementFit`: capability của slice có đáp ứng SLA dịch vụ không.
- `QoSHeadroom`: khoảng dư giữa metric dự kiến/đo gần nhất và ngưỡng SLA.
- `HealthAndLoad`: pod/NF/SMF/UPF healthy, PDU count, CPU/RAM và capacity còn lại.
- `IntentAffinity`: class của slice có khớp intent eMBB/URLLC/mMTC không.

Phiên bản hiện tại đã triển khai đúng trọng số trên. `RequirementFit` và
`QoSHeadroom` so sánh SLA của service class với capability profile; `HealthAndLoad`
lấy health pod, PDU count, CPU và RAM thật từ Kubernetes/Prometheus;
`IntentAffinity` bảo đảm class yêu cầu được ưu tiên. Traffic signature chỉ dùng
khi intent không rõ. Điểm vẫn là **điểm policy**, không phải xác suất hay cam kết
SLA; PDU đích phải vượt qua phép đo runtime trước khi steering.

Capability profile lab hiện dùng:

| Slice | Class | 5QI / ARP | AMBR/TBF DL | Benchmark DL | Baseline RTT/jitter/loss |
|---|---|---:|---:|---:|---|
| slice-1 | eMBB | 8 / 8 | 100 Mbps | 11,19 Mbps | 85 ms / 35 ms / 2% |
| slice-2 | URLLC | 7 / 2 | 5 Mbps | chưa hợp lệ | 90 ms / 40 ms / 0,5% |
| slice-3 | mMTC | 9 / 12 | 1 Mbps | 1,03 Mbps | 65 ms / 30 ms / 2% |

5QI/AMBR/ARP đã được Open5GS chấp nhận khi tạo session. Open5GS UPF của lab không
tự giới hạn bitrate theo Session-AMBR trong phép thử này, nên TBF trên `ogstun`
được dùng để cưỡng chế downlink. TBF hiện là runtime patch và sẽ mất khi UPF pod
khởi động lại. Tài nguyên CPU/RAM và RAN scheduler vẫn chưa thay đổi. URLLC vượt
qua traffic nhẹ nhưng saturation qua OAI/ZMQ bị stall, vì vậy không có benchmark
throughput hợp lệ và chưa được coi là SLA được bảo đảm.

## 5. Tránh dồn mọi UE vào một slice

Khi nhiều slice cùng class có điểm gần nhau, scheduler cần:

1. loại slice unhealthy hoặc hết capacity;
2. ưu tiên slice có load ratio thấp và headroom cao;
3. giữ hysteresis để UE không chuyển qua lại liên tục;
4. dùng admission reservation trong lúc tạo PDU để tránh hai UE cùng chiếm phần
   capacity cuối;
5. giới hạn số lần chuyển và có cooldown theo UE/service;
6. chỉ deallocate sau idle timeout khi không còn PDU và không còn reservation.

## 6. Metric và dashboard

- `ansa_intent_slice_score`: điểm theo UE, flow và candidate slice.
- `ansa_intent_slice_score_component`: bốn thành phần giải thích điểm.
- `ansa_intent_slice_selected_info`: slice được chọn cùng lý do.
- `ansa_pdu_session_info`: UE, PSI, S-NSSAI, DNN, TUN, source/target và trạng thái.
- `ansa_ue_*`: throughput, RTT, jitter, loss, packet size, message rate.
- `ansa_slice_health`, `ansa_slice_*_load_ratio`: health, CPU và RAM runtime.
- `ansa_slice_capacity_mbps`, `ansa_slice_max_pdu_sessions` và các metric
  `ansa_slice_baseline_*`: capability profile của từng slice instance.

Dashboard có filter UE, service, flow, PDU, service class và slice. Legend của
score phải hiện tối thiểu `UE · class · slice`. Khi chọn `All`, operator xem được
ma trận nhiều UE × nhiều candidate slice; khi chọn một UE, dashboard chỉ hiển thị
quyết định và PDU của UE đó.

`flow_id` có cardinality cao nên chỉ giữ trong khoảng thời gian phục vụ debug;
dashboard dài hạn nên dùng recording rule hoặc lưu decision history vào database.
Reason label phải thuộc tập mã hữu hạn, không đưa chuỗi lỗi tùy ý vào label.

## 7. Lộ trình triển khai

1. Đã có: per-UE/per-flow weighted score, multi-PDU state, nhiều candidate
   instance, capability profile, health/load exporter và eligibility gate.
2. Tiếp theo: benchmark từng profile, đưa kết quả đo vào baseline và cấu hình
   QoS/5QI/AMBR tương ứng trong 5GC/RAN.
3. Sau đó: reservation, hysteresis/cooldown và admission control nhiều UE.
4. Cuối cùng: replay nhiều UE đồng thời, đánh giá SLA và deallocate theo idle.

Không bắt buộc có RIC/xApp cho throughput/RTT/PDU metrics. RIC/xApp chỉ cần khi
đưa KPI RAN như PRB utilization, CQI, BLER hoặc handover vào quyết định.
