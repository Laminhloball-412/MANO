# Kiểm thử Intent-Based Slice Provisioning

## Mục tiêu

Sau khi hoàn thành tài liệu này, bạn có thể:

- gửi một yêu cầu dịch vụ ở mức nghiệp vụ;
- xem Technical SLA do policy catalog suy ra;
- kiểm tra lý do candidate được chọn hoặc bị loại;
- chạy dry-run mà không thay đổi hạ tầng;
- kiểm tra flow reuse hoặc allocate qua Free5GMANO;
- xem cấu hình và health của slice;
- kết nối gNB/UE thủ công trong bước kiểm thử riêng.

## Điều kiện ban đầu

- Máy MANO dùng địa chỉ `192.168.180.35`.
- Free5GMANO WebUI dùng cổng `8888`.
- Slice Intent API dùng cổng `8890`.
- Source project nằm tại `/home/ubuntu/mano`.
- Kubernetes và Free5GMANO đang hoạt động.
- Không chạy gNB hoặc UE trong các bước provisioning bên dưới.

## Phân biệt cấu hình với hiệu năng đo được

WebUI hiển thị ba mức evidence:

- `DECLARED`: giá trị lấy từ policy catalog hoặc descriptor.
- `ENFORCED`: hệ thống có cơ chế áp giới hạn hoặc QoS tương ứng.
- `BENCHMARKED`: giá trị đã được kiểm tra bằng traffic test có kiểm soát.

Ví dụ `peak_throughput_mbps=80` với evidence `DECLARED` chỉ có nghĩa plan yêu cầu và descriptor mô tả mức 80 Mbps. Giá trị đó không chứng minh UE đã đạt 80 Mbps và không phải cam kết vô tuyến end-to-end.

## Bước 1: Kiểm tra trạng thái Kubernetes

```bash
kubectl get pods -A
```

Kết quả mong đợi: pod Free5GMANO, MongoDB, shared core và các slice đang tồn tại ở trạng thái `Running`/`Ready`.

## Bước 2: Kiểm tra service

```bash
kubectl get svc -A
```

Kết quả mong đợi: service của Free5GMANO, WebUI, PCF, SMF và UPF xuất hiện đúng namespace đang chạy.

## Bước 3: Chạy test Intent API

```bash
cd /home/ubuntu/mano/ue-service-portal
```

```bash
python3 manage.py test portal.intent_based -v 2
```

Kết quả mong đợi: toàn bộ test translator, feasibility gate, lifecycle, provisioning và API đều `OK`.

## Bước 4: Chạy test Free5GMANO WebUI

```bash
cd /home/ubuntu/mano/free5gmano-webui
```

```bash
source .venv/bin/activate
```

```bash
python manage.py test intent_based_mvp -v 2
```

Kết quả mong đợi: test schema/proxy và sáu khu vực giao diện đều `OK`.

## Bước 5: Chạy test Auto Slice renderer

```bash
cd /home/ubuntu/mano/intent_flow_v2
```

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Kết quả mong đợi: test phân loại cũ, cấp slot/SD, render VNF/NSD/NRM và runtime SLA overlay đều `OK`.

## Bước 6: Chạy kiểm tra read-only của Auto Slice

```bash
python3 intent_flow.py doctor
```

Kết quả mong đợi: ba template eMBB, URLLC và mMTC có source package hợp lệ. Lệnh này không allocate hoặc delete slice.

## Bước 7: Xem inventory trước khi test

```bash
python3 intent_flow.py web-status
```

Ghi lại namespace, SST, SD và trạng thái của các slice hiện có để đối chiếu sau dry-run.

## Bước 8: Lấy Service Policy Catalog

```bash
curl -s http://127.0.0.1:8890/api/slice-intents/catalog/
```

Kết quả mong đợi: JSON chứa `catalog_version=2.0`, ba service type và ba template family.

## Bước 9: Gửi intent live video ở chế độ dry-run

```bash
curl -s -X POST http://127.0.0.1:8890/api/slice-intents/ -H 'Content-Type: application/json' -d '{"schema_version":"2.0","service_type":"live_video","quality":"high","scale":20,"priority":"high","duration_minutes":120,"execution_mode":"dry_run"}'
```

Kết quả mong đợi: HTTP `202` và JSON chứa `intent_id`, ví dụ `slice-intent-abc123`. Ghi lại ID thật.

## Bước 10: Theo dõi intent dry-run

Thay `slice-intent-abc123` bằng ID thật.

```bash
curl -s http://127.0.0.1:8890/api/slice-intents/slice-intent-abc123/
```

Kết quả mong đợi:

- `status=COMPLETED`;
- `stage=DRY_RUN`;
- Technical SLA có minimum/target/peak throughput;
- candidate không đạt hard constraint có `failed_checks`;
- plan có `deployment_configuration`;
- không có trường điều khiển radio hoặc thiết bị.

## Bước 11: Xem cấu hình dự kiến

```bash
curl -s http://127.0.0.1:8890/api/slice-intents/slice-intent-abc123/configuration/
```

Kết quả mong đợi: SST/SD dự kiến, DNN, 5QI, ARP, AMBR, throughput, latency target và `evidence_status`.

## Bước 12: Xác nhận dry-run không tạo tài nguyên

```bash
kubectl get namespaces
```

So sánh với Bước 7. Dry-run không được tạo namespace mới.

## Bước 13: Mở WebUI

Mở URL:

```text
http://192.168.180.35:8888/Intent_Attach/
```

Kết quả mong đợi: trang chỉ có sáu khu vực Service Intent, Technical SLA, Candidate Comparison, Allocation Lifecycle, Deployed Slice Configuration và Active Slice Inventory.

## Bước 14: Kiểm thử reuse

Chọn một intent có Technical SLA mà một slice operational hiện tại đáp ứng. Chuyển `Execution` sang `Execute`, sau đó xác nhận hộp thoại.

Kết quả mong đợi:

- decision action là `REUSE`;
- lifecycle kết thúc ở `READY`;
- không có namespace mới;
- configuration hiển thị namespace của instance được reuse.

## Bước 15: Kiểm thử allocate khi chưa có instance phù hợp

Chỉ thực hiện khi bạn đã xác nhận template đích được phép tạo và còn slot trống. Gửi intent `execute` từ WebUI hoặc API.

```bash
curl -s -X POST http://127.0.0.1:8890/api/slice-intents/ -H 'Content-Type: application/json' -d '{"schema_version":"2.0","service_type":"interactive_control","quality":"high","scale":10,"priority":"high","duration_minutes":60,"execution_mode":"execute"}'
```

Kết quả mong đợi: lifecycle đi qua `EXECUTING`, `ALLOCATING`, `INSPECTING` và `READY`.

## Bước 16: Kiểm tra pod của slice vừa tạo

Thay `slice-N` bằng namespace thật.

```bash
kubectl get pods -n slice-N
```

Kết quả mong đợi: PCF, SMF và UPF đều `Running` và Ready.

## Bước 17: Kiểm tra endpoint

```bash
kubectl get endpoints -n slice-N
```

Kết quả mong đợi: `pcf-svc`, `smf-svc` và `upf-svc` có address.

## Bước 18: Kiểm tra PFCP

Thay `SMF_POD` bằng tên pod thật.

```bash
kubectl logs -n slice-N SMF_POD --tail=500
```

Kết quả mong đợi: log mới nhất có `PFCP associated` và không có sự kiện de-association mới hơn.

## Bước 19: Xem NRM/runtime configuration đã render

Thay `INTENT_ID` bằng ID execute thật.

```bash
find /home/ubuntu/mano/intent_flow_v2/runs/INTENT_ID -maxdepth 3 -type f
```

Kết quả mong đợi: có package NRM runtime và `render-manifest.json`. Source package trong `base_templates` không bị sửa.

## Bước 20: Kết nối thủ công để kiểm thử end-to-end

Sau khi slice đã `READY`, dùng tài liệu cài đặt OCUDU/OAI UE của project để:

1. cấu hình subscriber cho S-NSSAI/DNN đã hiển thị;
2. cấu hình gNB quảng bá đúng S-NSSAI;
3. chạy gNB;
4. chạy OAI UE;
5. yêu cầu data session tương ứng;
6. đo traffic bằng công cụ kiểm thử riêng.

Provisioning flow không tự thực hiện sáu thao tác này. Nhờ đó lỗi allocate/core được tách khỏi lỗi radio, subscriber và thiết bị.

## Tiêu chí hoàn thành

- Business intent được dịch bằng catalog version xác định.
- Candidate vi phạm hard constraint không được chọn.
- Dry-run không thay đổi Free5GMANO hoặc Kubernetes.
- Reuse không tạo namespace mới.
- Allocate chỉ báo `READY` khi PCF/SMF/UPF, endpoint và PFCP đều hợp lệ.
- WebUI hiển thị cấu hình và evidence source.
- Không có thao tác tự động kết nối thiết bị trong provisioning flow.

## Kết quả kiểm thử lab ngày 24/08/2026

- Backend intent: 27/27 test đạt.
- WebUI proxy và giao diện: 6/6 test đạt.
- Renderer và lifecycle Free5GMANO: 16/16 test đạt.
- Dry-run `live_video/high`, 20 người xem dịch thành 20/80/80 Mbps,
  latency tối đa 100 ms, loss tối đa 1% và reliability tối thiểu 99.9%.
- Policy chọn đúng template eMBB trước một instance URLLC có thể reuse. Do
  `slice-1` thiếu bằng chứng PFCP operational tại thời điểm test, kết quả an
  toàn là đề xuất allocate eMBB mới, không reuse nhầm URLLC.
- Execute `massive_sensor/standard`, 100 thiết bị đã tạo `slice-3`, SST 3,
  SD `000001`; PCF/SMF/UPF, endpoint và PFCP đều operational.
- Gửi lại cùng intent cho kết quả `REUSE`, không tạo namespace thứ hai.
- Endpoint configuration trả đủ namespace `slice-3`, SST 3, SD `000001`,
  DNN `srsapn`, 5QI 9, ARP 8 và AMBR 0.2 Mbps.
- WebUI tải đủ ba instance `slice-1`, `slice-2`, `slice-3`; không có lỗi
  JavaScript trong lần kiểm tra.

Lưu ý: các thông số capability hiện mang nhãn `DECLARED`. Chúng là giá trị
policy dùng để thiết kế và sàng lọc slice, chưa phải bằng chứng SLA đã được
benchmark ngoài thực địa. `READY` trong flow này xác nhận hạ tầng core và PFCP,
không khẳng định UE hoặc dịch vụ radio đã được kết nối.

## Kết quả kiểm thử Business Intent và NSSF ngày 04/09/2026

- Auto Slice/NSSF: 29/29 test đạt.
- Portal Business Intent: 37/37 test đạt.
- Free5GMANO WebUI: 10/10 test đạt; `manage.py check` không có lỗi.
- Trang `/Business_Intent/` và trang cũ `/Intent_Attach/` đều trả HTTP 200.
- Dry-run `slice-intent-32cca3392c33` kết thúc `DRY_RUN`. Namespace giữ nguyên và resourceVersion của `open5gs-amf`/`open5gs-nssf` vẫn là `163099`/`4899393`.
- Execute `slice-intent-f709e11d34bf` chọn `interactive_control/critical`, tái sử dụng URLLC `slice-2`, NSSI `bab235bd-85b7-4773-9c31-b0bc19008586`.
- Lifecycle cuối: `REGISTERING_NSSF` → `VERIFYING_NSSF` → `READY_FOR_UE`.
- Cấu hình đầu ra: SST `2`, SD `000001`, DNN `srsapn`, 5QI `7`, ARP `8`, PDU Session type `IPv4v6`.
- `core-status` xác nhận AMF/NSSF đều Available, NSSF `REGISTERED` tại NRF, endpoint `10.244.0.54:7777`, `verified=true`.
- AMF và NSSF đều chứa chính xác S-NSSAI SST `2`, SD `000001`.
- Kiểm thử radio không chạy: `NOT_RUN_RADIO_UNAVAILABLE`. Vì vậy kết quả không tuyên bố `UE_ATTACHED`, không có bằng chứng cấp PRB và không coi các SLA `DECLARED` là số đo thực tế.

Lưu ý cập nhật: với flow mới, một slice được tái sử dụng chỉ kết thúc ở `READY_FOR_UE` sau khi xác minh lại AMF, NSSF và NRF. Trạng thái `READY` trong các kết quả cũ không còn là tiêu chí cuối cho Business Intent.
