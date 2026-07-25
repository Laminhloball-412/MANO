# Template mới: slice-4 + UE4

Template này dùng cho trạng thái hiện tại có `slice-1`, `slice-2`, `slice-3`.
Lệnh `plan` đã xác nhận Auto Slice sẽ cấp:

- Namespace: `slice-4`
- Loại slice: `eMBB`
- S-NSSAI: `SST=1`, `SD=000002`
- IPv4 subnet: `10.45.4.0/24`
- IPv4 gateway: `10.45.4.1`

## 1. Intent tạo slice

File chạy thật: `slice-4-ue4-example.json`

```json
{
  "intent_id": "live-slice-4-ue4-20260724",
  "slice_type": "embb",
  "service": "UE4 high-bandwidth research traffic",
  "bandwidth_mbps": 100,
  "availability": "high",
  "description": "Create the next free eMBB slice for UE4; namespace and SD are allocated automatically."
}
```

Không ghi cứng `namespace`, `sst` và `sd` trong intent. Chạy `plan` trước và lấy
SST/SD thực tế trong kết quả để cấu hình UE, gNB, AMF và subscriber.

```bash
cd /home/ubuntu/mano/intent_flow_v2
python3 intent_flow.py plan intents/slice-4-ue4-example.json
python3 intent_flow.py create intents/slice-4-ue4-example.json
```

## 2. UE4 config

Tạo `/home/ubuntu/ran/srsRAN_4G/build/srsue/src/ue4.conf`:

```ini
[rf]
freq_offset = 0
tx_gain = 50
rx_gain = 40
srate = 11.52e6
nof_antennas = 1
device_name = zmq
device_args = tx_port=tcp://127.0.0.1:2401,rx_port=tcp://127.0.0.1:2400,base_srate=11.52e6

[rat.eutra]
dl_earfcn = 2850
nof_carriers = 0

[rat.nr]
bands = 3
nof_carriers = 1

[pcap]
enable = none
mac_filename = /tmp/ue4_mac.pcap
mac_nr_filename = /tmp/ue4_mac_nr.pcap
nas_filename = /tmp/ue4_nas.pcap

[log]
all_level = info
phy_lib_level = none
all_hex_limit = 32
filename = /tmp/ue4.log
file_max_size = -1

[usim]
mode = soft
algo = milenage
opc  = E8ED289DEBA952E4283B54E88E6183CA
k    = 465B5CE8B199B49FAA5F0A2EE238A6BC
imsi = 460990000000004
imei = 353490069873318

[slicing]
enable = true
nssai-sst = 1
nssai-sd = 000002

[rrc]
release = 15
ue_category = 4

[nas]
apn = srsapn
apn_protocol = ipv4

[gw]
netns = ue4
ip_devname = tun_srsue4
ip_netmask = 255.255.255.0

[gui]
enable = false
```

Chuẩn bị namespace:

```bash
sudo ip netns add ue4
sudo ip netns exec ue4 ip link set lo up
```

## 3. Subscriber dùng chung

Thêm subscriber vào WebUI tại `http://192.168.180.35:30001`:

- IMSI: `460990000000004`
- K: `465B5CE8B199B49FAA5F0A2EE238A6BC`
- OPC: `E8ED289DEBA952E4283B54E88E6183CA`
- DNN/APN: `srsapn`
- SST: `1`
- SD: `000002`

## 4. Shared AMF và gNB

Thêm vào `amf.plmn_support[].s_nssai` của ConfigMap `share/open5gs-amf`:

```yaml
- sst: 1
  sd: '000002'
```

Thêm vào `tai_slice_support_list` trong `gnb1.yaml`:

```yaml
- sst: 1
  sd: 2
```

Sau khi sửa AMF, restart deployment AMF và chờ N2 kết nối lại.

## 5. ZMQ relay

Flowgraph hiện tại chỉ có ba nhánh UE và đang hard-code các cổng 2100–2301.
Trước khi chạy UE4 phải thêm nhánh thứ tư:

- UE4 uplink source: `tcp://127.0.0.1:2401`
- UE4 downlink sink: `tcp://127.0.0.1:2400`
- Nối uplink UE4 vào đầu vào thứ tư của khối cộng tín hiệu gửi tới gNB.
- Nối tín hiệu downlink gNB qua path-loss của UE4 tới sink cổng 2400.

## 6. Thứ tự chạy

```text
ZMQ relay 4 UE
→ gNB
→ chờ N2 completed
→ UE1
→ chờ 3 giây
→ UE2
→ chờ 3 giây
→ UE3
→ chờ 3 giây
→ UE4
```

UE4 chỉ được coi là thành công khi log có đủ:

```text
RRC Connected
PDU Session Establishment successful
IP: 10.45.4.x
```
