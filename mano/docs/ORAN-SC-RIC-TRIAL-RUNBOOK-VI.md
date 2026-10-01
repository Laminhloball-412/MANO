# Tích hợp Near-RT RIC và xApp với gNB srsRAN

## Mục tiêu

Tài liệu này mô tả bản tích hợp thử nghiệm Near-RT RIC vào hệ thống đang chạy trên máy lab. Sau khi hoàn thành, gNB srsRAN kết nối với O-RAN SC RIC qua E2, xApp nhận được E2SM-KPM Indication, và E2SM-RC sẵn sàng cho thử nghiệm điều khiển có giám sát.

Phạm vi hiện tại chỉ gồm RAN, RIC và xApp. NSSF và 5G Core không bị sửa trong lần tích hợp này.

## Kiến trúc đang chạy

```text
Dashboard :5000
    |
    +-- GNU Radio + ZMQ
    +-- srsRAN Project gNB
    |      +-- N2/N3 --> Open5GS 5GC
    |      +-- E2/SCTP --> O-RAN SC Near-RT RIC
    |                         +-- E2 Termination
    |                         +-- E2 Manager / Subscription Manager
    |                         +-- Python xApp runner
    +-- srsUE trong network namespace ue1
```

Các thành phần chính:

| Thành phần | Đường dẫn hoặc địa chỉ |
| --- | --- |
| Dashboard | `http://127.0.0.1:5000` trên máy lab |
| gNB | `/home/ubuntu/ran/srsRAN_project/build/apps/gnb/gnb` |
| Cấu hình gNB | `/home/ubuntu/ran/srsRAN_project/build/apps/gnb/gnb1.yaml` |
| srsUE | `/home/ubuntu/ran/srsRAN_4G/build/srsue/src/srsue` |
| O-RAN SC RIC | `/home/ubuntu/ran/oran-sc-ric` |
| RIC E2 endpoint | `10.0.2.10:36421/SCTP` |
| Địa chỉ E2 của host/gNB | `10.0.2.1` |
| E2 node ID hiện tại | `gnbd_460_099_00019b_0` |

RIC được triển khai từ commit `621ade26251f69a4ad079ba98bb708d8e5aeeb98` của repo `srsran/oran-sc-ric`.

## Phần đã áp dụng từ repo 5G-testbed-ANSA

Repo `newt2105/5G-testbed-ANSA` có kiến trúc gần với hệ thống này: srsRAN Project gNB, srsUE, ZMQ, O-RAN SC RIC, Open5GS và Grafana. Bản tích hợp sử dụng ba ý tưởng phù hợp từ repo đó:

1. Khối `e2` trong YAML của gNB để bật E2SM-KPM và E2SM-RC.
2. Khối `cell_cfg.slicing` để scheduler biết ba S-NSSAI đang được gNB phục vụ.
3. Mô hình xApp nhận KPM rồi đưa số liệu sang hệ giám sát.

Không sao chép nguyên stack InfluxDB/Grafana của repo tham khảo vì hệ thống hiện có Prometheus/Grafana. File `mon_xapp_db.py` trong repo tham khảo còn chứa thông tin InfluxDB cố định và phép kiểm tra biến UL/DL chưa nhất quán; cần sửa trước khi dùng trong production.

## Cấu hình E2 của gNB

`gnb1.yaml` đã được bổ sung cấu hình sau:

```yaml
cell_cfg:
  slicing:
    - sst: 1
      sd: 1
      sched_cfg:
        min_prb_policy_ratio: 1
        max_prb_policy_ratio: 100
    - sst: 2
      sd: 1
      sched_cfg:
        min_prb_policy_ratio: 1
        max_prb_policy_ratio: 100
    - sst: 3
      sd: 1
      sched_cfg:
        min_prb_policy_ratio: 1
        max_prb_policy_ratio: 100

e2:
  enable_du_e2: true
  enable_cu_cp_e2: false
  enable_cu_up_e2: false
  e2sm_kpm_enabled: true
  e2sm_rc_enabled: true
  e2sm_ccc_enabled: false
  addr: 10.0.2.10
  bind_addr: 10.0.2.1
  port: 36421
```

Các entry `cell_cfg.slicing` chỉ cấu hình scheduler phía RAN. Chúng không đăng ký slice vào NSSF và không cấp quyền slice cho subscriber.

## Khởi động RIC

```bash
cd /home/ubuntu/ran/oran-sc-ric
docker compose up -d
docker compose ps -a
```

Kết quả mong đợi: các container `ric_e2term`, `ric_e2mgr`, `ric_submgr`, `ric_dbaas`, `ric_rtmgr_sim`, `ric_appmgr` và `python_xapp_runner` ở trạng thái `Up`.

Kiểm tra địa chỉ bridge:

```bash
ip -br addr | grep 10.0.2.1
docker network inspect oran-sc-ric_ric_network \
  --format '{{range .IPAM.Config}}{{.Subnet}} {{.Gateway}}{{end}}'
```

## Khởi động một UE bằng dashboard cổng 5000

```bash
python3 - <<'PY'
import json
import urllib.request

request = urllib.request.Request(
    "http://127.0.0.1:5000/attach",
    data=json.dumps({"ue_ids": [1]}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=20) as response:
    print(response.status, response.read().decode())
PY
```

Kiểm tra trạng thái:

```bash
curl -sS http://127.0.0.1:5000/status | python3 -m json.tool
```

Kết quả thử nghiệm ngày 30/09/2026: UE1 đăng ký thành công và nhận địa chỉ `10.45.1.4` trên slice SST 1 / SD 000001.

## Kiểm tra E2 Setup

Kiểm tra SCTP:

```bash
ss -nA sctp
```

Phải có association từ `10.0.2.1` tới `10.0.2.10:36421` ở trạng thái `ESTAB`.

Kiểm tra log gNB:

```bash
grep -aE '\[E2-DU|RIC Subscription|E2SM-KPM' /tmp/gnb.log | tail -n 100
```

Các dòng xác nhận quan trọng:

```text
E2 connection to Near-RT-RIC on 10.0.2.10:36421 accepted
E2 Setup procedure successful
Received RIC Subscription Request
E2 Agent admitted actions
```

Node ID không dùng giá trị mặc định trong script mẫu. Node thực của lab là:

```text
gnbd_460_099_00019b_0
```

Có thể lấy node đang đăng ký từ RNIB/Redis:

```bash
cd /home/ubuntu/ran/oran-sc-ric
docker compose exec -T dbaas redis-cli --scan \
  --pattern '{e2Manager},RAN:*'
```

## Chạy xApp KPM

Chạy xApp đo throughput DRB:

```bash
cd /home/ubuntu/ran/oran-sc-ric
docker compose exec -T python_xapp_runner \
  python3 -u ./simple_mon_xapp.py \
  --e2_node_id gnbd_460_099_00019b_0 \
  --metrics=DRB.UEThpDl,DRB.UEThpUl
```

Chạy xApp đo tài nguyên PRB:

```bash
cd /home/ubuntu/ran/oran-sc-ric
docker compose exec -T python_xapp_runner \
  python3 -u ./simple_mon_xapp.py \
  --e2_node_id gnbd_460_099_00019b_0 \
  --metrics=RRU.PrbUsedDl,RRU.PrbUsedUl,RRU.PrbAvailDl,RRU.PrbAvailUl
```

Kết quả mong đợi:

```text
Successfully subscribed with Subscription ID
RIC Indication Received from gnbd_460_099_00019b_0
Metric: RRU.PrbAvailDl, Value: [52]
Metric: RRU.PrbAvailUl, Value: [52]
```

Trong lần thử hiện tại, KPM Indication đã đi hết từ gNB qua E2Term tới xApp. `DRB.UEThpDl/Ul` bằng 0 khi không có user-plane traffic hữu ích. Giá trị này không phải lỗi E2. Hãy chạy iperf hoặc dịch vụ thật qua PDU Session để đo throughput khác 0.

## Xem xApp E2SM-RC

RC đã được bật ở gNB và script xApp có sẵn. Trước khi gửi lệnh điều khiển, xem tham số:

```bash
cd /home/ubuntu/ran/oran-sc-ric
docker compose exec -T python_xapp_runner ./simple_rc_xapp.py --help
```

Không gửi lệnh RC tự động trong bài test đầu tiên. Lệnh RC thay đổi scheduler/RAN và cần một kịch bản đo trước/sau cùng điều kiện rollback rõ ràng.

## Xử lý lỗi thường gặp

### xApp trả HTTP 503

Log Subscription Manager:

```text
No E2 connection for ranName gnbd_001_001_00019b_0
```

Nguyên nhân là script mẫu dùng PLMN `001/001`, trong khi lab dùng `460/099`. Truyền đúng tham số:

```bash
--e2_node_id gnbd_460_099_00019b_0
```

### Cổng 8091 hoặc 4561 đã được dùng

Một xApp cũ vẫn còn chạy trong container. Dừng xApp bằng `Ctrl+C`. Nếu terminal đã mất, restart riêng runner:

```bash
cd /home/ubuntu/ran/oran-sc-ric
docker compose restart python_xapp_runner
```

### RIC Indication có giá trị 0

Xác nhận UE đã đăng ký và PDU Session đã tồn tại. Sau đó tạo user-plane traffic qua đúng TUN hoặc dịch vụ. Control-plane traffic và một TUN không truyền được tới gateway có thể không làm tăng DRB throughput.

## Dừng bài test

Dừng UE, gNB và GNU Radio qua API dashboard:

```bash
curl -sS -X POST http://127.0.0.1:5000/stop-scenario
```

Giữ RIC chạy để lần sau gNB tự kết nối lại. Nếu muốn dừng cả RIC:

```bash
cd /home/ubuntu/ran/oran-sc-ric
docker compose down
```

## Rollback cấu hình gNB

Backup được tạo trước khi tích hợp. Đường dẫn backup mới nhất nằm trong:

```bash
cat /home/ubuntu/mano/.last-ric-backup
```

Rollback:

```bash
curl -sS -X POST http://127.0.0.1:5000/stop-scenario
BACKUP_DIR=$(cat /home/ubuntu/mano/.last-ric-backup)
sudo -n cp "$BACKUP_DIR/gnb1.yaml" \
  /home/ubuntu/ran/srsRAN_project/build/apps/gnb/gnb1.yaml
cd /home/ubuntu/ran/oran-sc-ric
docker compose down
```

## Bằng chứng của lần thử

Artifact được lưu tại:

```text
/home/ubuntu/mano/test-artifacts/ric-trial-20260930T134033Z/
```

Các file chính:

- `kpm-xapp-final.log`: KPM DRB Indication.
- `kpm-cell-xapp.log`: KPM PRB Indication.
- `kpm-xapp-correct-node.log`: quá trình sửa node ID khi kiểm thử.

## Tài liệu tham khảo

- <https://github.com/srsran/oran-sc-ric>
- <https://docs.srsran.com/projects/project/en/latest/tutorials/source/near-rt-ric/source/index.html>
- <https://github.com/newt2105/5G-testbed-ANSA>
