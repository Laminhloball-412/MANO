# Intent Flow V2 – khối Auto Slice độc lập

Khối này chạy bên ngoài source Free5GMANO. WebUI gọi CLI trong thư mục này để
tự động render, upload và allocate slice. Không import hoặc sửa flow mẫu cũ.

## Các file Python

### `intent_flow.py`

Đây là orchestrator chính.

- `Api`: gọi REST API Free5GMANO và kube5gnfvo; quản lý GenericTemplate,
  SliceTemplate, NSSI và NFVO instance.
- `StateStore`: khóa và lưu trạng thái riêng tại
  `state/intent-flow-state.json`, tránh hai yêu cầu đồng thời chọn cùng slot.
- `Plan`: dữ liệu kế hoạch gồm namespace, SST/SD, nguồn VNF/NSD/NRM và subnet.
- `classify_intent()`: phân loại eMBB, URLLC hoặc mMTC.
- `all_namespace_slots()`: đọc toàn bộ namespace có dạng `slice-N`.
- `namespace_has_workloads()`: kiểm tra namespace có pod/deployment workload.
- `namespace_slots()`: trả về các slot thực sự đang chạy workload.
- `orphan_namespaces()`: phát hiện namespace `slice-N` không còn workload.
- `occupied_slots()` / `choose_slot()`: tìm số `slice-N` trống nhỏ nhất.
- `make_plan()`: tạo kế hoạch read-only cho một intent.
- `active_snssai_pairs()`: đọc các cặp SST/SD đang được NSSI sử dụng.
- `first_free_sd()`: chọn SD trống nhỏ nhất trong từng SST; SD có thể trùng
  giữa các SST khác nhau.
- `validate_sources()`: kiểm tra VNF/NSD/NRM nguồn còn tồn tại và đã upload.
- `create_namespace()`: chuẩn bị namespace và NetworkAttachmentDefinition.
- `wait_ready()`: chờ toàn bộ pod của slice Ready.
- `runtime_checks()`: kiểm tra PFCP và SMF có chọn đúng PCF namespace.
- `create()`: flow chính download → render → upload VNF/NSD/NRM → tạo
  SliceTemplate → allocate NSSI → verify.
- `rollback_owned()`: rollback tài nguyên do lần create lỗi vừa tạo.
- `delete()`: delete NSSI, namespace và template do Auto Slice quản lý.
- `direct_nfvo_cleanup()`: terminate/delete NFVO instance khi API thường bị kẹt.
- `force_db_detach()`: phương án cuối, detach đúng NSSI UUID bằng Django ORM.
- `status()`: đối chiếu state, API và Kubernetes.
- `web_status()`: dữ liệu rút gọn cho trang Auto Slice.
- `doctor()`: kiểm tra read-only cả ba profile, không tạo slice.
- `parser()` / `main()`: các lệnh `plan`, `create`, `delete`, `status`,
  `web-status`, `doctor`.

### `renderer.py`

Chỉ làm việc với package descriptor:

- `_safe_extract()`: giải nén ZIP an toàn, chặn path traversal.
- `_zip_tree()`: đóng gói lại descriptor.
- `_patch_text()`: thay namespace, FQDN, subnet, gateway, SST và SD.
- `render_packages()`: sinh ID VNF/NSD mới, giữ hậu tố `pcf`, `smf`, `upf`,
  cập nhật NSD reference và tạo VNF/NSD ZIP.
- `render_nrm_package()`: tạo NRM ZIP riêng, đồng bộ SST/SD và SLA.
- `clean_failed_run()`: xóa thư mục render tạm khi create rollback.

### `tests/test_flow.py`

Unit test cho classification, SD theo từng SST, render VNF/NSD, hậu tố
`pcf/smf/upf` và render NRM. Không gọi create/allocate thật.

## Các file không phải Python

- `config.json`: endpoint và ba profile eMBB/URLLC/mMTC.
- `intents/*.json`: intent mẫu chạy CLI.
- `state/intent-flow-state.json`: state runtime, không được xóa khi đang dùng.
- `runs/<intent-id>/`: source và package đã render; giữ để audit/đối chiếu.
- `webui_overlay/`: bản source các thay đổi đã chép sang Free5GMANO WebUI.

Các file WebUI runtime:

- `ui_templates/auto_slice.html`: giao diện ba nút Auto Slice.
- `static/js/nm_js/auto_slice.js`: gọi API, refresh, create và delete.
- `ui_route/views.py`: thêm API status/create/delete gọi external orchestrator.
- `ui_route/urls.py`: route `/Auto_Slice/` và `/api/auto-slice/...`.

## Local base descriptors

Giao diện `/Auto_Slice/` chỉ có ba thao tác tạo eMBB, URLLC và mMTC. Flow đọc
ba ZIP nguồn cố định từ `base_templates/default/` trên máy MANO, vì vậy không
phụ thuộc UUID của GenericTemplate mẫu trong database Free5GMANO.

Trước mỗi lần tạo slice, hệ thống kiểm tra toàn bộ `vnfd_id` trong NSD phải
khớp chính xác với các `descriptor_id` trong VNF, sau đó render thử VNF/NSD/NRM.
Nếu nguồn sai hoặc thiếu thì việc tạo dừng trước mọi thao tác ghi. Các ZIP nguồn
không thuộc resource của một slice nên thao tác Delete Slice không xóa chúng.
- `ui_templates/bar.html`: thêm mục Auto Slice ở sidebar.

## Flow Create

```text
intent
  → classify
  → chọn namespace và SD trống
  → tải VNF/NSD/NRM nguồn
  → render ID + namespace + SST/SD + subnet
  → upload ba GenericTemplate mới
  → tạo SliceTemplate
  → allocate NSSI
  → chờ pod Ready
  → kiểm tra PFCP/PCF
  → lưu state
```

## Lệnh kiểm tra an toàn

```bash
cd /home/ubuntu/mano/intent_flow_v2
python3 intent_flow.py doctor
python3 intent_flow.py web-status
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

`doctor`, `web-status` và unit test không tạo hoặc xóa slice.

Sơ đồ chức năng và kịch bản kiểm thử đầy đủ nằm tại
[`PROJECT_TEST_PLAN.md`](PROJECT_TEST_PLAN.md).
