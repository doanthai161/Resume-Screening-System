# MinerU local trên Windows

Service này chạy riêng với BE, dùng MinerU **4.0.10** và V1 API mà adapter hiện
tại yêu cầu. Không cài dependency MinerU vào `venv` của BE. Không cần clone source
MinerU vào repository.

Chạy từ root dự án với Python trong `venv` và `uv` đã cài:

```powershell
.\services\mineru\install.ps1
.\services\mineru\prepare-models.ps1 -Tier standard
.\services\mineru\start.ps1 -Tier standard
```

Venv, cache, model và uploads của MinerU nằm trong `.tmp/`, đã được gitignore
và nằm ngoài build context BE. Download có thể tốn nhiều thời gian; chạy lại
cùng lệnh để dùng cache nếu mạng gián đoạn. Script start kiểm tra model trước
khi mở server, dùng model local và chỉ bind `127.0.0.1:8001`.

Kiểm tra service:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/v1/health
```

Basic dùng ONNX CPU cho OCR/layout; có thể chậm. Standard dùng thêm VLM,
cần tải thêm model và đánh giá RAM/GPU trước khi dùng. Server tier là năng lực
triển khai: `basic` phục vụ Basic và Flash cho DOCX; `standard` phục vụ thêm
Standard/Advanced. Đây không phải hai tên đồng nghĩa với tier của mỗi request.
Xem [tiers](https://opendatalab.github.io/MinerU/usage/tiers/),
[models](https://opendatalab.github.io/MinerU/usage/model_source/) và
[V1 API](https://opendatalab.github.io/MinerU/usage/http_api/).

Trong `.env` của BE local:

```dotenv
MINERU_API_URL=http://127.0.0.1:8001
WORKER_ADAPTER_FACTORY=app.workers.mineru_adapter:create_adapter
MINERU_PDF_TIER=standard
MINERU_FALLBACK_TIER=standard
```

Lần nghiệm thu ngày 2026-10-02 dùng Standard: cả năm CV mẫu đạt kiểm tra, bao
gồm dấu tiếng Việt. Basic trước đó thiếu hai cụm tiếng Việt trong CV scan nên
không được tính là đạt yêu cầu tiếng Việt. Để thử Basic trên máy ít tài nguyên,
truyền `-Tier basic` vào prepare/start và đặt cả hai biến tier của BE thành
`basic`; cần đánh giá lại chất lượng trên corpus thực tế.

Giữ terminal MinerU chạy, mở terminal khác tại root để chạy worker:

```powershell
.\venv\Scripts\python.exe -m app.worker --queue resume-parse
```

Worker cần Mongo replica set, Redis và cấu hình uploads giống API. Hướng dẫn
hạ tầng: [LOCAL_DEVELOPMENT.md](../../docs/LOCAL_DEVELOPMENT.md).
Giữ API, MinerU và worker cùng chạy khi nghiệm thu
[năm CV mẫu](../../docs/MINERU_ADAPTER.md#real-end-to-end-acceptance).
Không chạy worker screening khi chưa có adapter AI.

Nhấn Ctrl+C ở terminal tương ứng để dừng. V1 API có resource index trong RAM;
restart MinerU làm upload/job/file ID cũ mất hiệu lực. BE đã retry chu trình
upload/job mới khi resource trả 404; dữ liệu và trạng thái run chính vẫn ở Mongo.

Các service đã được khởi động nền trong lần thiết lập hiện tại; không mở thêm
process trên cùng cổng. PID launcher nằm ở `.tmp/mineru-runtime/api-wrapper.pid`,
PID worker nằm ở `.tmp/parse-worker.pid`. Log tương ứng ở
`.tmp/mineru-runtime/api.*.log` và `.tmp/parse-worker.*.log`.
Khi khởi động lại máy, chạy hạ tầng Docker rồi mở terminal API, MinerU và worker
bằng các lệnh trên. Không dùng `taskkill /IM python.exe` để dừng hàng loạt process.
