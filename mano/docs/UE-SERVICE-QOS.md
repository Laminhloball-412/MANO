# Cấu hình và kiểm tra QoS dịch vụ cho UE

Business Intent lưu chính sách PCC cho đúng thuê bao, S-NSSAI, DNN và luồng dịch vụ. Sau đó cần tạo PDU session mới và đọc bằng chứng NGAP để biết QoS thực tế được báo hiệu cho UE. `CORE_CONFIGURED` chỉ xác nhận chính sách đã được ghi và đọc lại trong core.

## Thao tác trên Business Intent

Mở [Business Intent](http://192.168.180.35:8888/Business_Intent/). Thử từng UE một để đối chiếu phiên và lưu lượng rõ ràng.

1. Nhập yêu cầu dịch vụ, quy mô và thời gian. Với robot, dùng mô tả có “robot control”; kiểm tra lại profile trong bản phân tích.
2. Điền **UE IMSIs** bằng IMSI của thuê bao đã tồn tại. Nhiều IMSI phân cách bằng dấu phẩy, tối đa 16; giữ nguyên số 0 đầu chuỗi. Không nhập IMSI ví dụ thay cho thuê bao thật.
3. Điền **Service server IP** bằng địa chỉ **IPv4 thực của máy chủ dịch vụ**, chọn **UDP**, nhập **Service server port = 5000** nếu ứng dụng thực sự dùng cổng này. Luật khớp địa chỉ/cổng máy chủ ở cả hai chiều. Adapter hiện chỉ hỗ trợ PDU IPv4.
4. Chọn **Reuse compatible slice or create** để tái sử dụng slice phù hợp; chọn **Create a new slice** để yêu cầu cấp slice mới. Chế độ mới không được âm thầm đổi thành tái sử dụng.
5. Bấm **Analyze requirement**, kiểm tra IMSI, địa chỉ/cổng, allocation, default QoS, service QoS và AMBR. Bất kỳ sửa đổi nào cũng cần phân tích lại. Chỉ phân tích có thể để trống toàn bộ bốn trường phạm vi dịch vụ; không gửi phạm vi thiếu một phần.
6. Bấm **Confirm and provision**. Xem `APPLYING_QOS`, `VERIFYING_QOS` và kết quả đọc lại. Khi có `RECONNECT_REQUIRED`, chủ động tạo lại PDU session của UE thử nghiệm; ứng dụng không tự ngắt phiên đang hoạt động.

Nếu thuê bao được chỉ định chưa có đúng S-NSSAI/DNN, adapter bổ sung quyền cho đúng đích trong cùng lần cập nhật có kiểm tra phiên bản (CAS). Thuê bao phải tồn tại và có S-NSSAI mặc định; không tạo thuê bao hay sửa thông tin xác thực, không đổi mặc định. Giới hạn tối đa 8 S-NSSAI/thuê bao và 4 DNN/S-NSSAI. Wildcard SD, DNN prefix/case alias hoặc đích trùng lặp gây lỗi thay vì chọn gần đúng.

Nếu cấp slice bị timeout và chưa có định danh đủ để liên hệ kết quả với yêu cầu, kiểm tra allocator thủ công trước khi gửi lại. Một slice vừa xuất hiện có thể thuộc yêu cầu khác.

## Đọc trạng thái và bằng chứng

| Kết quả | Ý nghĩa |
| --- | --- |
| Requested default/service QoS | Giá trị yêu cầu; chưa chứng minh đã áp dụng cho phiên UE. |
| `DECLARED_NOT_ENFORCED` | Chưa có xác nhận ghi và đọc lại chính sách cho phạm vi này. |
| `CORE_CONFIGURED` + policy readback verified | Chính sách core được lưu và đọc lại đúng. |
| `RECONNECT_REQUIRED` | Cần PDU session mới để nhận chính sách tĩnh đã lưu. |
| `NOT_OBSERVED` / radio `NOT_VERIFIED` | Chưa có bằng chứng phiên/radio trong kết quả job. |

Mốc kiểm tra đầu tiên: job `f3787fba85da` đã đạt `CORE_CONFIGURED`, với robot service **5QI 3 / ARP 2 / GFBR 0,25 Mbps / MFBR 0,5 Mbps**; default session **5QI 7 / ARP 4**, Session-AMBR **0,75 Mbps**. Lần thử một UE lúc 20:07 ngày 04/09/2026 đã xác nhận QFI 2 / 5QI 3 cho UDP/5000 và QFI 1 / 5QI 7 cho UDP/5001 ở cả UL/DL; cả hai flow được RAN chấp nhận và mỗi port có 12/12 echo. Xem [báo cáo và giới hạn phép thử](../test-artifacts/pcc-services-20260904/REPORT-VI.md).

GFBR/MFBR của profile đối xứng được triển khai cho UL và DL. Session-AMBR có phạm vi một PDU session; không coi nó là thông lượng đã đo. SMF cấp QFI; không suy QFI từ số 5QI.

## Lấy log trên máy lab

Dùng namespace trong kết quả job, sau đó chọn đúng pod/container từ danh sách. Các giá trị trong dấu `<...>` dưới đây phải thay bằng tên thực tế.

```bash
kubectl get pods -n <namespace>
kubectl logs -n <namespace> <pcf-pod> --all-containers --timestamps --since=15m > pcf-qos.log
kubectl logs -n <namespace> <smf-pod> --all-containers --timestamps --since=15m > smf-qos.log
kubectl logs -n <namespace> <upf-pod> --all-containers --timestamps --since=15m > upf-qos.log
```

PCF cho biết xử lý policy; SMF cho biết tạo phiên, QoS rules và PFCP; UPF hỗ trợ kiểm tra PDR/FAR/QER và lưu lượng. Nếu `kubectl logs` thiếu nội dung, xem cấu hình logger trong đúng container: một số tiến trình ghi file bên trong pod. Không mặc định có một đường dẫn log chung; xác định đường dẫn từ cấu hình rồi sao chép file đó để phân tích.

Lấy thêm **log NGAP của gNB đang phục vụ UE**, trong khoảng thời gian tạo phiên mới. Cần bản ghi Setup/Modify Request và Response chứa transfer; log tóm tắt thiếu transfer có thể không giải mã được. Lưu nguồn log, thời gian, RAN UE ID và PDU session ID. Nếu không đủ dữ liệu liên hệ cùng một phiên, chưa kết luận QoS của UE.

## Xem QoS được báo hiệu cho UE

CLI đọc file đã thu thập, không kết nối hay thay đổi lab:

```bash
/home/ubuntu/mano/free5gmano-webui/.venv/bin/python /home/ubuntu/mano/ue-service-portal/tools/inspect_ue_qos.py <gnb-ngap-log> --json
/home/ubuntu/mano/free5gmano-webui/.venv/bin/python /home/ubuntu/mano/ue-service-portal/tools/inspect_ue_qos.py <gnb-ngap-log> --ran-ue-id <ran-id> --pdu-session-id <pdu-id>
```

Trình giải mã transfer cần dependency `pycrate`. Dùng RAN UE ID/PDU session ID đọc từ log; không thay bằng IMSI. Báo cáo là bằng chứng lịch sử của file đầu vào, không phải trạng thái live.

Đối chiếu NGAP Request để đọc QFI, 5QI, ARP, AMBR và GFBR/MFBR được báo hiệu; đối chiếu Response của **cùng phiên** để xác nhận flow được RAN chấp nhận hoặc bị từ chối. Cuối cùng tạo lưu lượng đúng máy chủ UDP/5000 và một luồng đối chứng không khớp bộ lọc, kiểm tra QFI trên GTP-U/capture phù hợp. Chỉ kết luận lưu lượng được áp dụng khi có bằng chứng này; policy readback hoặc NGAP Request riêng lẻ chưa đủ.

## Cài dependency cho adapter

Trên lab dùng Python của service portal:

```bash
/home/ubuntu/mano/free5gmano-webui/.venv/bin/python -m pip install -r /home/ubuntu/mano/ue-service-portal/requirements-qos.txt
/home/ubuntu/mano/free5gmano-webui/.venv/bin/python -m pip install pycrate==0.8.1
```

Adapter ghim PyMongo 4.10.1 để làm việc với MongoDB 4.0 hiện có trên lab; đọc [bảng tương thích MongoDB](https://www.mongodb.com/docs/drivers/compatibility/?driver-language=python&python-driver-framework=pymongo) trước khi đổi driver/server. Biến `CORE_MONGO_URI` cho phép cấu hình kết nối; nếu bỏ trống, adapter tìm ClusterIP của `mongodb-svc` namespace `share`. Backup subscription nằm trong `ue-service-portal/runtime/qos-backups/`, không chứa khóa xác thực.

## Lệnh xem ngay kết quả đã thử

```bash
/home/ubuntu/mano/free5gmano-webui/.venv/bin/python /home/ubuntu/mano/ue-service-portal/tools/inspect_ue_qos.py /home/ubuntu/mano/test-artifacts/pcc-services-20260904/pcc-20260904T130704Z-645437/gnb-detail.log --ran-ue-id 0 --pdu-session-id 1
```

File này là phiên đã kết thúc. Để xem phiên mới, dùng log gNB của chính lần kết nối đó. Chạy lại một UE theo [hướng dẫn bản OAI mới](../test-artifacts/pcc-services-20260904/OAI-UE-BUILD.md), rồi lấy đường dẫn trong `latest-run-path.txt` ở thư mục test. UE có thể đồng thời dùng nhiều 5QI: đọc theo QFI/traffic, không gán một 5QI duy nhất cho cả UE.

Catalog hiện chọn robot `interactive_control` là 5QI 3; `video_call` là 5QI 2. Cần xem bản dịch trước khi provision để biết profile nào thực sự được yêu cầu. Một 5QI không tự nhận diện ứng dụng hay bảo đảm SLA.
