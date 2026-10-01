# Ba slice, cách tính điểm và các kịch bản

## 1. Ba slice khác nhau như thế nào

| Thuộc tính | eMBB/default | URLLC | mMTC |
|---|---:|---:|---:|
| Namespace | `slice-1` | `slice-2` | `slice-3` |
| S-NSSAI | SST1/SD000001 | SST2/SD000001 | SST3/SD000001 |
| PDU Session ID | 1 | 2 | 3 |
| UE TUN | `oaitun_ue1` | `oaitun_ue1p2` | `oaitun_ue1p3` |
| Gateway | `10.45.1.1` | `10.45.2.1` | `10.45.3.1` |
| 5QI | 8 | 7 | 9 |
| ARP priority | 8 | 2 | 12 |
| Session-AMBR | 100 Mbps | 5 Mbps | 1 Mbps |
| UPF downlink TBF | 100 Mbps | 5 Mbps | 1 Mbps |
| PDU capacity policy | 64 | 32 | 1000 |
| Message-rate policy | 2000/s | 5000/s | 10000/s |
| Bảo vệ khỏi xóa | Có | Không | Không |

Giá trị ARP nhỏ hơn có priority cao hơn, vì vậy URLLC dùng ARP 2. Các giá trị
trên tạo khác biệt thật ở S-NSSAI, core path, QoS subscription và downlink
shaping. Chúng chưa tạo khác biệt PRB/scheduler ở RAN.

Kết quả benchmark hiện có:

- eMBB: khoảng `11.19 Mbps`, đang bị giới hạn bởi radio/ZMQ trước mức AMBR;
- mMTC: khoảng `1.03 Mbps` sau khi áp TBF 1 Mbps;
- URLLC: traffic nhẹ RTT khoảng `78–93 ms`, jitter `32–42 ms`, loss `0%`;
- URLLC saturation chưa có kết quả hợp lệ do OAI/ZMQ stall.

Sau khi UPF pod restart, QoS reconciler của Intent Engine kiểm tra và áp lại
qdisc. Dashboard hiển thị riêng configured/enforced/benchmarked; chỉ coi giới
hạn được cưỡng chế khi metric `ansa_slice_downlink_enforcement_up` bằng `1`.

## 2. Service intent ánh xạ vào class

| Tên dịch vụ | Class ưu tiên |
|---|---|
| `video`, `streaming` | eMBB |
| `control`, `robot` | URLLC |
| `sensor`, `telemetry` | mMTC |

Intent tường minh có ưu tiên cao hơn suy đoán từ traffic. Nếu tên dịch vụ không
khớp bảng trên, engine mới dùng signature tốt nhất từ throughput, RTT, jitter,
loss, message rate và packet size.

Metric của traffic hiện tại chỉ mô tả nhu cầu/chất lượng đường đang dùng. Nó
không tự chứng minh ứng dụng là video, robot hay sensor.

## 3. Công thức suitability score

Mỗi candidate được tính:

```text
score = (
    0.35 * requirement_fit
  + 0.20 * qos_headroom
  + 0.20 * health_load
  + 0.15 * intent_affinity
  + 0.10 * evidence_confidence
)
```

- `requirement_fit`: capability profile đáp ứng yêu cầu class tới mức nào;
- `qos_headroom`: phần dư so với throughput/RTT/jitter/loss/message rate yêu cầu;
- `health_load`: pod health, PDU occupancy, CPU và RAM còn trống;
- `intent_affinity`: bằng 1 khi service class của slice đúng class được yêu cầu.
- `evidence_confidence`: độ tin cậy của capability, tăng khi bandwidth thực sự
  được enforce và có benchmark; capability khai báo không được coi là đo thật.

Effective capacity lấy giá trị bảo thủ nhất trong capability đã cấu hình, TBF
đang enforce và benchmark hợp lệ. Ví dụ eMBB khai báo/enforce 100 Mbps nhưng
benchmark mới đạt 11.19 Mbps thì engine dùng 11.19 Mbps khi tính headroom.

Candidate không healthy hoặc hết PDU capacity nhận score 0 và không được chọn.
Slice chưa tồn tại vẫn có thể là candidate `allocatable_absent`; khi thắng,
engine gọi Free5GMANO dựng đúng namespace/slot rồi mới establish PDU.

Với ba profile hiện tại, score chủ yếu chọn một trong ba class. Để tối ưu giữa
nhiều slice cùng class sau này cần nhiều instance/profile cùng class, benchmark
riêng và metric capacity/load riêng cho từng instance.

## 4. Hai chế độ sử dụng PDU

### `concurrent`

Dùng khi hai dịch vụ cùng hoạt động. Ví dụ control và sensor cùng chạy thì giữ:

- PDU1/default cho đường nền;
- PDU2 cho control/URLLC;
- PDU3 cho sensor/mMTC.

Kết thúc một intent chỉ release PDU chuyên biệt nếu không còn intent nào tham
chiếu PDU đó.

### `migrate`

Dùng khi dịch vụ mới thay dịch vụ cũ. Phải truyền `replaces_intent_id`. Trình tự:

1. giữ PDU nguồn;
2. establish PDU đích;
3. validate TUN/IP và gateway/SLA;
4. publish binding mới;
5. ứng dụng mở socket trên TUN mới và gửi ACK;
6. drain;
7. release PDU dịch vụ cũ nếu reference bằng 0.

Default PDU1 được bảo vệ nên chuyển default → PDU2/PDU3 sẽ giữ PDU1. Bằng chứng
make-before-break rõ nhất là chuyển PDU chuyên biệt PDU2 → PDU3: tại thời điểm
trước ACK cả PDU2 và PDU3 phải cùng UP, sau ACK/drain PDU2 mới DOWN.

### `auto`

- Có `replaces_intent_id`: hành xử như `migrate`.
- Không có `replaces_intent_id`: hành xử như `concurrent`.

## 5. Ma trận kịch bản chính

| Kịch bản | Kết quả kỳ vọng |
|---|---|
| UE chưa attach | Intent `WAITING_FOR_UE`; không allocate/release |
| Chỉ attach, chưa có app | PDU1 UP; throughput/rate có thể 0; không dùng metric cũ làm traffic mới |
| Video | Reuse PDU1/eMBB; không tạo PDU trùng |
| Control | Ensure `slice-2`, tạo/reuse PDU2; PDU1 vẫn UP |
| Sensor | Ensure `slice-3`, tạo/reuse PDU3; PDU1 vẫn UP |
| Control + sensor | PDU1/PDU2/PDU3 đồng thời UP nếu chưa vượt admission limit |
| PDU2 → PDU3 migrate | PDU3 UP và ACK trước; sau drain PDU2 DOWN; PDU1 vẫn UP |
| Slice đích bị xóa | Dashboard không crash; candidate báo absent; engine allocate lại đúng slot |
| Slice unhealthy | Candidate ineligible; không release source |
| NAS reject hoặc không có TUN | Target rollback/fail; source vẫn hoạt động |
| Gateway/SLA fail | Không commit binding; source vẫn hoạt động |
| Prometheus down/metric stale | Chờ telemetry/retry; không release dựa trên dữ liệu cũ |
| Ứng dụng không ACK | `AWAITING_APPLICATION_ACK`; source chưa release |
| App chết không gửi END | Lease hết hạn; engine thu hồi reference |
| PDU không còn reference | Release PDU; bắt đầu idle timer cho slice |
| Slice idle | Hiện báo `dry-run`; chưa xóa thật vì cấu hình an toàn |
| Default/share idle | Luôn protected, không deallocate |
| PDU thứ tư | Admission control từ chối vì giới hạn hiện là 3 PDU/UE |
| UE2 chưa attach | Intent UE2 `WAITING_FOR_UE`; UE1 không bị tác động |
| UE1/UE2 cùng PDU2 | Reference tách theo `1:2` và `2:2`; END một UE không release UE kia |
| UE4 gửi intent | API trả lỗi vì lab chỉ cho phép UE1, UE2, UE3 |

## 6. Dấu hiệu PASS cho một transition

Một transition chỉ PASS khi có đủ bằng chứng:

1. candidate/slice đích healthy hoặc allocate thành công;
2. NAS tạo PDU mới và TUN/IP tương ứng xuất hiện;
3. ping đúng gateway qua đúng TUN thành công;
4. target ổn định đủ cửa sổ cấu hình;
5. app bind socket mới thành công;
6. app ACK nếu là `migrate`;
7. source chuyên biệt chỉ release sau các bước trên;
8. PDU1/default không bị release.

Dashboard/registry là bằng chứng điều phối, nhưng nên đối chiếu thêm `ip`, ping,
UE NAS log, SMF/UPF pod và metric Prometheus.
