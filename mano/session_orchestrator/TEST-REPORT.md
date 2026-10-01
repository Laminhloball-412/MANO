# Báo cáo kiểm thử Make-Before-Break

- Thời gian: 2026-08-20 04:00 UTC
- VM: `192.168.180.35`
- gNB: OCUDU source commit `f85247d73`
- UE: Duranta OAI branch `codex/multipdu-runtime`, commit `72d580c`
- Flow: `flow-5256bc74d6da`
- Kết quả: `COMPLETED`

Trình tự quan sát được:

1. UE đăng ký và thiết lập PDU1 trên SST1.
2. Traffic qua default PDU được đo và Prometheus scrape thành công.
3. Intent policy chọn `slice-2`; slice đã tồn tại nên được reuse.
4. OAI thiết lập PDU2 trên SST2 trong khi PDU1 vẫn active.
5. PDU2 vượt qua kiểm tra gateway và SLA.
6. Flow mới bind vào `oaitun_ue1p2`; flow cũ được drain hai giây.
7. UE gửi Release Request, nhận Release Command và gửi Release Complete cho PDU1.
8. `oaitun_ue1` chuyển DOWN trong khi `oaitun_ue1p2` tiếp tục hoạt động.

Metric cuối trên PDU đích:

- Throughput: `0.023575 Mbps`
- RTT: `59.591 ms`
- Jitter: `24.358 ms`
- Packet loss: `0%`
- Packet size: `120 bytes`
- Message rate: `9.956 messages/s`

Tất cả Prometheus target cấu hình sẵn đều UP. Namespace nguồn được đánh dấu
`protected`, vì vậy không có SMF/UPF/PCF nào bị deallocate. Các process radio do
test tạo ra đã được dừng sau khi thu thập bằng chứng. Template gNB, UE,
subscriber, Kubernetes và UE Portal gốc không bị chỉnh sửa.

Một lần thử trước trùng thời điểm AMF chạy lâu ngày bị OOMKill (`exit 137`).
Kubernetes tự khởi động lại pod; không sửa manifest hoặc memory limit của 5GC.
