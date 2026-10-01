# Vận hành Business Intent và NSSF dùng chung

Tài liệu này ghi lại cách triển khai, xác minh và khôi phục luồng Business Intent → Auto Slice → AMF/NSSF trên máy lab `192.168.180.35`.

## 1. Ổn định NRF trước khi triển khai NSSF

Baseline ngày 2026-09-04:

- Namespace: `share`.
- Deployment NRF: `6ef97a96-d17e-4f21-86d3-af2532ea1nrf-gtecu`.
- Service SBI: `nrf-svc.share.svc.cluster.local:7777`.
- ConfigMap gốc: `open5gs-nrf`.
- Bản sao trước thay đổi: `/home/ubuntu/mano/runtime/core-config-backups/open5gs-nrf-before-nssf.yaml`.
- Log trước thay đổi: `/home/ubuntu/mano/runtime/core-config-backups/nrf-before-nssf.log`.
- Pod, Service và Endpoint NRF đều tồn tại trước thay đổi.

Manifest quản lý thêm `global.max.peer: 1024`, đồng thời giữ nguyên MongoDB URI, PLMN `460/99`, SBI bind `0.0.0.0:7777` và địa chỉ quảng bá của NRF.

Khôi phục NRF:

```bash
kubectl apply -f /home/ubuntu/mano/runtime/core-config-backups/open5gs-nrf-before-nssf.yaml
kubectl rollout restart deployment/6ef97a96-d17e-4f21-86d3-af2532ea1nrf-gtecu -n share
kubectl rollout status deployment/6ef97a96-d17e-4f21-86d3-af2532ea1nrf-gtecu -n share --timeout=120s
```

Không tiếp tục triển khai NSSF nếu cửa sổ log NRF mới còn xuất hiện `No memory in client_pool` hoặc `NFProfile has no usable endpoint`.

Kết quả sau rollout:

- Deployment trở lại `1/1 Ready`.
- Discovery bằng HTTP/2 trả về đủ ba PCF: `slice-1`, `slice-2`, `slice-3`.
- Mỗi PCF có ít nhất một `ipEndPoints` sử dụng được.
- Cửa sổ log của pod NRF mới không còn hai lỗi nêu trên.
- Lần truy vấn đầu tiên thất bại với mã curl `7` vì `kubectl exec deployment/...` chọn nhầm pod cũ đang `Terminating`; truy vấn lại theo tên pod `Running` thành công. Các kiểm tra tự động sau bước này phải chọn pod ở trạng thái `Running` thay vì dựa vào Deployment shortcut trong lúc rollout.

## 2. NSSF dùng chung

NSSF chạy trong namespace `share` bằng các tài nguyên sau:

- Deployment `open5gs-nssf`;
- Service `nssf-svc.share.svc.cluster.local:7777`;
- ConfigMap `open5gs-nssf`;
- NRF `nrf-svc.share.svc.cluster.local:7777`.

Manifest quản lý nằm tại `/home/ubuntu/mano/intent_flow_v2/shared_core/open5gs-nssf.yaml`. Cấu hình ban đầu chứa SST/SD `1/000001`, `2/000001`, `3/000001`. Không tạo một NSSF riêng cho từng slice.

Kiểm tra nhanh:

```bash
kubectl get deployment,pod,svc,endpoints -n share | grep -E 'nssf|nrf'
cd /home/ubuntu/mano/intent_flow_v2
python3 intent_flow.py core-status
```

Chỉ chấp nhận khi AMF và NSSF đều `Available`, NRF báo NSSF `REGISTERED`, endpoint NSSF tồn tại và `verified=true`.

## 3. Đồng bộ S-NSSAI

Mọi slice active được reconciler ghi vào cả ConfigMap `open5gs-amf` và `open5gs-nssf`. Reconciler sao lưu hai ConfigMap trước khi thay đổi, chỉ restart deployment bị đổi và rollback cả hai nếu xác minh thất bại.

```bash
cd /home/ubuntu/mano/intent_flow_v2
python3 intent_flow.py core-reconcile
python3 intent_flow.py core-status
```

Hai lần reconcile liên tiếp trên cùng trạng thái phải có `changed=false`; đây là kiểm tra idempotence.

## 4. Business Intent trên WebUI

Mở `http://192.168.180.35:8888/Business_Intent/`. Trang hiển thị sáu bước:

1. Nhu cầu nghiệp vụ.
2. Hệ thống hiểu yêu cầu.
3. Cấu hình kỹ thuật.
4. Tiến trình tạo slice.
5. Đăng ký NSSF.
6. Sẵn sàng cho UE.

Nút **Phân tích yêu cầu** chỉ chạy dry-run. Nút **Xác nhận và triển khai** mới gửi `execution_mode=execute`.

## 5. Kết quả xác minh ngày 2026-09-04

- Intent thật: `slice-intent-f709e11d34bf`.
- Bản dịch: `interactive_control`, chất lượng `critical`, template `urllc`.
- Quyết định: tái sử dụng `slice-2` sau khi health SMF-UPF xác nhận lại PFCP.
- NSSI ID: `bab235bd-85b7-4773-9c31-b0bc19008586`.
- Cấu hình: SST `2`, SD `000001`, DNN `srsapn`, 5QI `7`, ARP `8`.
- Core: AMF và NSSF cùng chứa SST/SD `2/000001`; NSSF đăng ký NRF; endpoint `10.244.0.54:7777`.
- Kết quả: `READY_FOR_UE`.
- Radio/UE: `NOT_RUN_RADIO_UNAVAILABLE` trong lần xác minh này.

Một lần execute trước đó (`slice-intent-4c23726b20c5`) dừng an toàn vì `slice-2` thiếu bằng chứng PFCP hiện hành. Rolling restart riêng ba deployment trong `slice-2` tái lập PFCP; không xóa namespace và không sửa subscriber.

## 6. Ý nghĩa READY_FOR_UE

`READY_FOR_UE` xác nhận cấu hình core đã sẵn sàng: S-NSSAI có trong AMF/NSSF, NSSF đăng ký NRF, DNN và hướng dẫn PDU Session đã được xuất. Trạng thái này không chứng minh UE đã đăng ký, PDU Session đã thiết lập, PRB đã được cấp hay SLA vô tuyến đã được đo.

Muốn chứng minh end-to-end phải chạy riêng gNB/UE với SST `2`, SD `000001`, DNN `srsapn`, sau đó lưu log AMF registration, SMF PDU Session, UPF traffic và số đo radio.

## 7. Khôi phục

- Backup trước đợt triển khai WebUI/portal: `/home/ubuntu/mano/backups/business-intent-nssf-20260904T072714Z`.
- Backup NRF: `/home/ubuntu/mano/runtime/core-config-backups/open5gs-nrf-before-nssf.yaml`.
- Backup AMF/NSSF theo từng reconcile: xem `last_backup_dir` trong kết quả lệnh hoặc thư mục backup runtime.

Sau khi phục hồi file cấu hình, rollout đúng deployment tương ứng và chạy lại `python3 intent_flow.py core-status`.
