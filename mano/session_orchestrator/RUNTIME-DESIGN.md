# Thiết kế runtime intent và multi-PDU

## 1. Phạm vi và thuật ngữ

Project dùng nhiều PDU Session độc lập của cùng UE để đưa traffic ứng dụng vào
S-NSSAI/DNN phù hợp. Đây là application-level orchestration; không tuyên bố là
triển khai đầy đủ thủ tục SSC mode 3 của 3GPP. SSC mode 3 trong TS 23.502,
clause 4.3.5.2, mô tả đổi PDU Session Anchor tới cùng DN. Project có thể chuyển
giữa các S-NSSAI khác nhau nên chỉ dùng nguyên tắc establish-before-release.

TS 23.501 cho phép UE đồng thời có nhiều PDU Session tới cùng hoặc khác Data
Network. TS 23.503 URSP ánh xạ traffic descriptor/application descriptor sang
S-NSSAI, DNN và SSC mode. OAI UE lab chưa thực thi URSP hoàn chỉnh, vì vậy
project mô phỏng enforcement ở application bằng socket bind vào TUN được chọn.

Prometheus dùng pull model. Exporter công bố metric; Intent Engine chủ động query
Prometheus. Prometheus không gọi ngược xuống Intent Engine.

Nguồn chuẩn:

- ETSI TS 123 501: https://www.etsi.org/deliver/etsi_ts/123500_123599/123501/18.12.00_60/ts_123501v181200p.pdf
- ETSI TS 123 502: https://www.etsi.org/deliver/etsi_ts/123500_123599/123502/17.07.00_60/ts_123502v170700p.pdf
- ETSI TS 123 503: https://www.etsi.org/deliver/etsi_ts/123500_123599/123503/18.10.00_60/ts_123503v181000p.pdf
- OCUDU OAI UE tutorial: https://docs.ocudu.org/tutorials/oaiue/
- Prometheus HTTP API: https://prometheus.io/docs/prometheus/latest/querying/api/

## 2. Các khối runtime

```text
Ứng dụng UE độc lập
  -> đăng ký flow_id và chạy trước trên default PDU
  -> socket bind vào TUN hiện được gán
  -> Application/UE Telemetry Agent
       - passive: byte/packet rate và packet size ước lượng
       - active probe: RTT/jitter/loss, được gắn nhãn riêng
  -> Prometheus scrape
  -> Intent Engine daemon
       - chỉ nhận intent gắn với flow đang chạy
       - chờ đủ observation window và query Prometheus theo flow_id
       - chọn capability profile và slice instance
  -> Session Coordinator
       - ensure/allocate slice
       - establish/reuse PDU
       - validate NAS/TUN/gateway/SLA
       - publish binding cho ứng dụng
       - release PDU khi không còn reference
  -> Slice Reaper
       - kiểm tra PDU/reference thật
       - idle timeout
       - deallocate slice không được bảo vệ
```

Telemetry, decision và execution có state riêng. Một lỗi Prometheus không được
làm release PDU đang hoạt động. Một lỗi target không được làm mất source PDU.

Flow chính chọn `explicit application descriptor + measured analytics`, không
dùng throughput để đoán dịch vụ khi ứng dụng đã cung cấp descriptor. Cách này
phản ánh URSP trong TS 23.503 mục 6.6.2: application/IP/domain/DNN/connection
descriptor được ánh xạ vào S-NSSAI/DNN/PDU; đồng thời PCF có thể dùng analytics
NWDAF làm input cho policy theo mục 6.1.1.3. Trong project, Intent API mô phỏng
application/AF input, Prometheus mô phỏng nguồn analytics, còn application bind
TUN mô phỏng phần URSP mà OAI UE lab chưa có.

Traffic inference chỉ là fallback có nhãn `intent_source=inferred`; nó không
được dùng làm flow demo chính và confidence thấp luôn giữ default.

## 3. Hai policy PDU

### `migrate`

Dùng khi intent mới thay thế intent cũ. Coordinator establish target, validate,
publish binding mới, chờ ứng dụng xác nhận đã mở flow mới, drain flow cũ, rồi
release source nếu reference count bằng 0. Socket TCP/UDP đang mở không tự di
chuyển; ứng dụng/proxy phải reconnect hoặc hỗ trợ QUIC/MPTCP.

Ngoại lệ an toàn: default PDU là đường nền/management được bảo vệ, nên ACK chỉ
kết thúc reference của ứng dụng cũ chứ không gửi NAS Release cho default PDU.
MBB thực sự dùng khi thay một PDU dịch vụ chuyên biệt bằng PDU dịch vụ khác.

### `concurrent`

Dùng khi nhiều dịch vụ chạy đồng thời. Target PDU được establish/reuse và giữ
song song với các PDU khác. Mỗi intent có binding riêng. Kết thúc một intent chỉ
giảm reference của PDU tương ứng; PDU chỉ release khi reference count bằng 0.

### `auto`

Không tự đoán “thay thế” hay “đồng thời” chỉ từ traffic. API phải cung cấp
`replaces_intent_id` nếu là thay thế. Có trường này thì chọn `migrate`; không có
thì chọn `concurrent`. Đây là hành vi an toàn vì release nhầm nguy hiểm hơn giữ
thừa một PDU trong thời gian ngắn.

## 4. Ma trận kịch bản

| ID | Kịch bản | Hành vi bắt buộc |
|---|---|---|
| S01 | UE chưa attach | Intent ở `WAITING_FOR_UE`, không allocate/release |
| S02 | UE chỉ có default PDU | Telemetry xuất attachment live, không tạo score giả |
| S03 | Ứng dụng chưa phát traffic | Passive throughput/rate bằng 0; active probe tách riêng |
| S04 | Một video intent | Reuse eMBB/default, không tạo PDU trùng |
| S05 | Một control intent | Ensure URLLC, establish PDU2, luôn giữ default PDU |
| S06 | Một sensor intent | Ensure mMTC, establish PDU3, luôn giữ default PDU |
| S07 | Video + control đồng thời | Giữ PDU1 và PDU2, binding riêng |
| S08 | Control + sensor đồng thời | Giữ PDU1/PDU2/PDU3 nếu UE đủ năng lực; nếu không phải admission-control |
| S09 | Control thay video/default | Establish và ACK PDU2; kết thúc intent cũ nhưng giữ PDU1 protected |
| S10 | Slice đích không tồn tại | Candidate `ABSENT`, được allocate đúng slot |
| S11 | Slice tồn tại và healthy | Reuse, không allocate lại |
| S12 | Slice tồn tại nhưng unhealthy | Không reuse/ghi đè; rollback hoặc chọn instance khác |
| S13 | Slice hết capacity | Candidate ineligible; không release source |
| S14 | Allocate timeout/fail | Intent `FAILED`, source và binding cũ còn nguyên |
| S15 | NAS Establish Reject | Rollback target, không release source |
| S16 | Có TUN nhưng gateway fail | Target không commit, rollback |
| S17 | SLA không ổn định | Target không commit; cần hysteresis/cooldown |
| S18 | Prometheus unavailable | Không quyết định phá hủy; retry với backoff |
| S19 | Metric stale | Không dùng score cũ làm current decision |
| S20 | UE restart | Reconcile TUN live; session registry cũ chuyển `LOST` |
| S21 | Daemon restart giữa transition | Resume/reconcile idempotent, không release mù |
| S22 | Hai request cùng UE đồng thời | Serialize theo UE lock |
| S23 | Nhiều UE cùng slice | Reference và capacity tính theo từng UE/PDU |
| S24 | Kết thúc một intent | Chỉ release PDU nếu không còn intent tham chiếu |
| S25 | Slice không còn PDU | Bắt đầu idle timer, chưa delete ngay |
| S26 | Có intent mới trong idle window | Hủy deallocation và reuse slice |
| S27 | Slice protected/default | Không deallocate dù idle |
| S28 | Delete Free5GMANO fail | Giữ trạng thái `DELETE_FAILED`, không giả báo thành công |
| S29 | Ứng dụng không ACK binding mới | Không release source; transition timeout/rollback |
| S30 | Socket dài hạn | Không hứa migration trong suốt; cần reconnect/proxy/QUIC/MPTCP |
| S31 | Ứng dụng chết không gửi END | Lease hết hạn, daemon end intent và giảm reference |
| S32 | PDU ID bị release rồi establish lại | Tên TUN ổn định theo default session ID, không theo vị trí mảng |
| S33 | UE chạm giới hạn PDU đã benchmark | Admission-control từ chối trước establish; không làm crash UE |
| S34 | Intent tới trước application flow | Từ chối; không dùng metric của flow khác |
| S35 | Flow chưa đủ sample/window | `WAITING_FOR_TELEMETRY`, chưa allocate |
| S36 | Flow metric stale | Không chọn slice; giữ source/default |
| S37 | Hai flow cùng UE | Tách bằng `flow_id`, mỗi flow có intent/binding riêng |
| S38 | Flow kết thúc | End intent, giảm reference và release PDU khi reference bằng 0 |

Trong lab có một lần OAI UE chạy không đủ quyền scheduling bị crash
`pthread_create(EAGAIN)` khi thêm PDU thứ ba. Khi chạy UE bằng quyền phù hợp,
PDU1/PDU2/PDU3 đã cùng UP và ping ba gateway không loss. Cấu hình vẫn đặt
`max_pdu_sessions_per_ue=3` làm admission limit; “nhiều slice tồn tại” không
được tự động xem là UE có khả năng giữ số PDU không giới hạn.

## 5. Metric và freshness

Passive metric lấy chênh lệch counter Linux của TUN theo chu kỳ:

- RX/TX throughput;
- RX/TX packet rate;
- average bytes per packet;
- interface/session presence.

RTT, jitter và loss không thể suy ra đúng chỉ từ byte counter. Chúng đến từ
active probe được cấu hình rõ hoặc từ instrumentation của ứng dụng. Metric phải
có `source=passive|active_probe|application` và timestamp. Intent Engine từ chối
metric quá `max_metric_age_seconds`.

Application instrumentation xuất metric theo bộ nhãn
`(flow_id, ue_id, service, interface)`. Mỗi flow lưu `first_sample_at`,
`last_sample_at` và `sample_count`. Lab yêu cầu tối thiểu hai sample và cửa sổ
quan sát ba giây trước khi engine query Prometheus. Series flow đã kết thúc
không còn được export như trạng thái hiện tại.

## 6. Điều kiện commit transition

Target chỉ được commit khi đồng thời đạt:

1. slice healthy;
2. NAS Establish Accept hoặc TUN/IP tương ứng xuất hiện;
3. SMF context và PFCP/UPF sẵn sàng nếu collector hỗ trợ;
4. ping gateway thành công;
5. SLA ổn định đủ số cửa sổ;
6. application ACK binding mới đối với mọi intent gắn `flow_id`.

Nếu thiếu bất kỳ điều kiện nào, source vẫn được giữ. Deallocation chỉ chạy sau
release thành công, reference count bằng 0 và hết idle timeout.
