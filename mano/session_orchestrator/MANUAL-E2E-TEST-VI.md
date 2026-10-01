# Runbook thủ công observe-first từ đầu tới cuối

Đây là flow chính của project. Ứng dụng chạy trước trên default PDU, xuất metric
theo `flow_id`, sau đó intent mới được gửi. Mỗi khối dưới đây là một lệnh độc
lập; không dùng script gộp và không dùng subcommand legacy `run`.

## 1. Kiến trúc được kiểm thử

```text
UE attach -> default PDU1
-> application flow chạy và được đo
-> Prometheus scrape metric theo flow_id
-> application descriptor/intent được gửi
-> Intent Engine query Prometheus
-> select/allocate/reuse slice
-> establish + validate target PDU
-> application bind socket mới và ACK
-> concurrent hoặc drain/release source PDU
```

Service descriptor cung cấp nhu cầu ứng dụng; metric cung cấp trạng thái và nhu
cầu đo được. Project không dùng throughput đơn lẻ để khẳng định loại dịch vụ.

## 2. Kiểm tra hạ tầng

```bash
ssh ubuntu@192.168.180.35
```

```bash
kubectl get nodes
```

Kỳ vọng node `Ready`.

```bash
kubectl get pods -A
```

Kỳ vọng shared 5GC, Free5GMANO, Prometheus, Grafana và PCF/SMF/UPF của các slice
đang tồn tại đều Ready.

```bash
systemctl status ansa-session-metrics --no-pager
```

```bash
systemctl status ansa-intent-engine --no-pager
```

```bash
curl http://127.0.0.1:9106/health
```

```bash
curl http://127.0.0.1:30090/-/ready
```

## 3. Khởi động OCUDU gNB và OAI UE

Chỉ dừng process cũ nếu đúng là phiên lab của mình.

```bash
pgrep -a gnb
```

```bash
pgrep -a nr-uesoftmodem
```

Terminal 1, chạy OCUDU gNB nếu chưa chạy:

```bash
cd /home/ubuntu/ran/ocudu-oai/build-oai/apps/gnb_split_8
```

```bash
sudo ./gnb -c /home/ubuntu/ran/oai-ue-lab/templates/gnb-oai-tdd.yaml
```

Kỳ vọng NG Setup với AMF thành công.

Terminal 2, chạy OAI UE nếu chưa chạy:

```bash
cd /home/ubuntu/ran/openairinterface5g-duranta/cmake_targets/ran_build/build
```

```bash
sudo -E ./nr-uesoftmodem -O /home/ubuntu/ran/oai-ue-lab/private-one.conf -r 51 --numerology 1 --band 78 -C 3489420000 --ue-scan-carrier -E --uecap_file /home/ubuntu/ran/openairinterface5g-duranta/targets/PROJECTS/GENERIC-NR-5GC/CONF/uecap_ports1.xml --log_config.global_log_options level,nocolor,time --device.name oai_zmqdevif --zmq.[0].tx_channels tcp://0.0.0.0:2001 --zmq.[0].rx_channels tcp://127.0.0.1:2000 --telnetsrv --telnetsrv.shrmod ciUE --telnetsrv.listenaddr 127.0.0.1 --telnetsrv.listenport 9090
```

Kỳ vọng Registration Accept, PDU1 Establishment Accept và `oaitun_ue1`.

Terminal 3, xác nhận default PDU:

```bash
ip -br addr show oaitun_ue1
```

```bash
ping -I oaitun_ue1 -c 3 10.45.1.1
```

Kỳ vọng TUN có IP `10.45.1.x` và ping loss `0%`.

## 4. Test video giữ nguyên default eMBB

Terminal 4, chạy ứng dụng trước:

```bash
cd /home/ubuntu/mano
```

```bash
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json simulate --service video --ue-id 1 --flow-id ue1-video --duration 120 --interface oaitun_ue1 --gateway 10.45.1.1
```

Giữ lệnh chạy. Simulator đăng ký `ue1-video`, gửi traffic trên PDU1 và xuất
sample định kỳ. Nó chưa yêu cầu slice.

Terminal 3, xem flow đã được phát hiện:

```bash
curl http://127.0.0.1:9106/api/v1/flows/ue1-video
```

Kỳ vọng state `OBSERVING`, `sample_count` tăng và metric không phải dữ liệu của
phiên cũ.

```bash
curl http://127.0.0.1:30090/api/v1/query?query=ansa_application_flow_sample_count
```

Kỳ vọng có series `flow_id="ue1-video"`.

Gửi descriptor video sau khi flow đang chạy:

```bash
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json submit --service video --ue-id 1 --flow-id ue1-video --intent-source application --mode concurrent --lease-seconds 120
```

Kỳ vọng engine chọn eMBB/slice-1/PDU1, không tạo PDU trùng. Simulator ACK binding
PDU1 và tiếp tục chạy trên `oaitun_ue1`.

## 5. Test control từ default sang URLLC/PDU2

Terminal 4, ứng dụng control chạy trên default trước:

```bash
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json simulate --service control --ue-id 1 --flow-id ue1-control --duration 180 --interface oaitun_ue1 --gateway 10.45.1.1
```

Terminal 3, xác nhận observation:

```bash
curl http://127.0.0.1:9106/api/v1/flows/ue1-control
```

Kỳ vọng ít nhất hai sample và flow vẫn ở default interface.

Gửi control intent:

```bash
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json submit --service control --ue-id 1 --flow-id ue1-control --intent-source application --mode concurrent --lease-seconds 180
```

```bash
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json status
```

Kỳ vọng intent đi qua `WAITING_FOR_TELEMETRY -> DECIDING -> ESTABLISHING ->
VALIDATING -> AWAITING_APPLICATION_ACK -> ACTIVE`; flow đi qua
`OBSERVING -> INTENT_RECEIVED -> READY_FOR_BIND -> STEERED`. Sau Prometheus
scrape, target là slice-2/PDU2.

```bash
ip -br addr show
```

Kỳ vọng `oaitun_ue1` và `oaitun_ue1p2` đều có IP; simulator báo switch sang
`oaitun_ue1p2` và ACK chỉ sau khi bind socket mới thành công.

```bash
ping -I oaitun_ue1p2 -c 3 10.45.2.1
```

## 6. Test sensor đồng thời trên mMTC/PDU3

Giữ flow control chạy. Terminal 5, sensor bắt đầu trên default:

```bash
cd /home/ubuntu/mano
```

```bash
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json simulate --service sensor --ue-id 1 --flow-id ue1-sensor --duration 180 --interface oaitun_ue1 --gateway 10.45.1.1
```

Terminal 3, gửi sensor intent:

```bash
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json submit --service sensor --ue-id 1 --flow-id ue1-sensor --intent-source application --mode concurrent --lease-seconds 180
```

```bash
ip -br addr show
```

Kỳ vọng PDU1/PDU2/PDU3 đồng thời UP. Control flow dùng PDU2; sensor flow dùng
PDU3. Mỗi flow có metric và binding riêng.

```bash
ping -I oaitun_ue1p3 -c 3 10.45.3.1
```

## 7. Test make-before-break PDU2 sang PDU3

Bắt đầu với control intent ACTIVE trên PDU2 và không còn sensor concurrent cũ.
Ghi lại control `intent_id` từ lệnh status.

Terminal 5, dịch vụ thay thế chạy tạm trên source PDU2:

```bash
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json simulate --service sensor --ue-id 1 --flow-id ue1-sensor-migrate --duration 180 --interface oaitun_ue1p2 --gateway 10.45.2.1
```

Thay `intent-control-id` bằng ID thật:

```bash
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json submit --service sensor --ue-id 1 --flow-id ue1-sensor-migrate --intent-source application --mode migrate --replaces-intent-id intent-control-id --lease-seconds 180
```

```bash
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json status
```

Kỳ vọng target PDU3 ở `AWAITING_APPLICATION_ACK`/flow `READY_FOR_BIND` trong khi
PDU2 vẫn UP.

```bash
ip -br addr show
```

Kỳ vọng PDU1/PDU2/PDU3 cùng tồn tại. Simulator mở socket PDU3 rồi tự ACK.

Sau ACK và drain:

```bash
ip -br addr show
```

Kỳ vọng PDU2 DOWN; PDU1/PDU3 còn UP. Phải đối chiếu events và NAS log để chứng
minh thứ tự, không chỉ nhìn trạng thái cuối.

## 8. Kịch bản lỗi bắt buộc

Intent tham chiếu flow không tồn tại:

```bash
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json submit --service control --ue-id 1 --flow-id missing-flow --mode concurrent
```

Kỳ vọng lỗi `flow_id is not active`; không allocate slice.

UE ngoài phạm vi lab:

```bash
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json submit --service control --ue-id 4 --mode concurrent
```

Kỳ vọng JSON lỗi cho biết chỉ cho phép UE1, UE2, UE3.

## 9. Dashboard

Free5GMANO WebUI:

```text
http://192.168.180.35:8888/dashboard/
```

Grafana:

```text
http://192.168.180.35:30030/d/auto-slice-operations/
```

Dashboard phải hiển thị riêng active flow/`flow_id`, sample freshness,
application/passive metrics, intent source, target slice, capacity evidence và
SLA `MET`/`DEGRADED` sau validation.

## 10. Cleanup

Simulator mặc định DELETE flow khi hết thời lượng; API end intent và release PDU
chuyên biệt khi reference bằng 0. Có thể nhấn `Ctrl+C` ở terminal ứng dụng.

```bash
ip -br addr show
```

Kỳ vọng `oaitun_ue1` UP, PDU2/PDU3 DOWN sau drain.

```bash
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json status
```

Kỳ vọng flow kết thúc không còn được export như active; intent terminal chỉ còn
trong audit history. `dry_run_deallocate=true` nên slice idle chưa bị xóa thật.

## 11. Tiêu chí PASS

- Observe-first: flow và Prometheus metric tồn tại trước intent.
- Selection: intent gắn đúng `flow_id`, dùng sample fresh và chọn đúng profile.
- Steering: application bind target TUN rồi ACK; dashboard khớp Linux/5GC.
- Make-before-break: source và target cùng UP trước ACK; source chuyên biệt chỉ
  DOWN sau ACK/drain; default PDU1 luôn được bảo vệ.
- Multi-UE RF chỉ PASS khi AMF/gNB thực sự thấy nhiều IMSI/RNTI và mỗi UE có TUN
  riêng. Unit test nhãn/reference không thay thế bằng chứng radio.
