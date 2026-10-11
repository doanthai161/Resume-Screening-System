# App local, MongoDB/Redis trong Docker

Môi trường mới dùng `docker-compose.local.yml` độc lập. File này kế thừa các
service hạ tầng từ Compose chính nhưng dùng project `resume-screening-local`,
container và volume riêng. Không ghép thêm `docker-compose.dev.yml` khi chạy
môi trường này. Không dùng lại volume của Mongo standalone cũ.

| Thành phần | Container | Địa chỉ từ Windows |
| --- | --- | --- |
| MongoDB 7, replica set `rs0` | `resume_mongo_local` | `127.0.0.1:27019` |
| Redis 7 | `resume_redis_local` | `127.0.0.1:6381` |
| FastAPI | Chạy bằng Python local | `127.0.0.1:8000` |

## Một lệnh chuẩn bị Mongo/Redis trên Windows

Yêu cầu: đã cài Docker Desktop với Linux containers/Compose v2; `.env` có
`MONGO_USERNAME`, `MONGO_PASSWORD`, `MONGO_APP_USERNAME`, `MONGO_APP_PASSWORD`,
`MONGO_APP_DATABASE`, `REDIS_PASSWORD`. Backend đã có venv và dependency.

Chạy tại root repository, **trong cùng terminal PowerShell**:

```powershell
.\start-local.ps1
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Nếu đã activate venv, lệnh thứ hai có thể viết ngắn:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Script mở Docker Desktop nếu chưa chạy, đợi Linux Engine, tải image còn thiếu
(`mongo:7`, `redis:7-alpine`), chạy hạ tầng và đợi Mongo/Redis healthy cùng
initializer replica set hoàn tất. Không cần build image Backend trong chế độ
này. Script còn kiểm tra tài khoản ứng dụng Mongo trước khi báo `READY`.

Không sửa `.env`, không sửa/xóa Brevo hoặc credentials, không xóa volume và
không chạy API, MinerU hay worker. Database lấy từ `MONGO_APP_DATABASE` của
cấu hình Compose thực tế, không tự đổi tên database hay di chuyển dữ liệu cũ.
Healthcheck local đọc credentials qua biến môi trường, tránh lỗi shell khi
mật khẩu chứa khoảng trắng hoặc dấu nháy.

Script chỉ đặt `MONGODB_URI`, `MONGODB_DB_NAME`, `REDIS_URL`, `ENVIRONMENT`,
`DEBUG`, `REFRESH_COOKIE_SECURE` trong **phiên PowerShell hiện tại**. URI tự
URL-encode credentials và dùng các cổng host của Compose; không in mật khẩu.
Các giá trị này ưu tiên hơn `.env`, nên URI cũ trong `.env` không ảnh hưởng
Uvicorn được chạy tiếp trong cùng terminal. Terminal mới cần chạy lại script.

Nếu execution policy chặn script, cho phép trong phiên hiện tại rồi chạy lại:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\start-local.ps1
```

Không chạy launcher qua `powershell -File ...` hoặc double-click nếu muốn
Uvicorn tiếp theo kế thừa cấu hình: đó là process khác, không truyền được biến
môi trường về terminal cha. Gọi `.\start-local.ps1` trực tiếp như ví dụ trên,
không dot-source (`. .\start-local.ps1`).

Các tùy chọn:

```powershell
.\start-local.ps1 -Check                 # Chỉ kiểm tra cấu hình, không chạy Docker Desktop/container
.\start-local.ps1 -Pull                  # Chủ động cập nhật image trước khi chạy
.\start-local.ps1 -TimeoutSeconds 300    # Tăng thời gian chờ Engine/readiness
.\start-local.ps1 -Stop                  # Dừng hạ tầng, giữ nguyên volume
```

`-Stop` không đọc `.env` hay cần parse Compose, nên vẫn dùng được khi file bị
thiếu hoặc credentials vừa sửa chưa hợp lệ. Script chỉ dừng container có đúng
project label `resume-screening-local` và service `mongo`, `redis`, `mongo-key`,
`mongo-init-replica`; bỏ qua container one-off, API/worker và các project khác.
Nếu không có container tương ứng, lệnh kết thúc mà không thay đổi gì.

Output Docker được đọc bằng UTF-8, không phụ thuộc code page của terminal
Windows. Timeout áp dụng cả việc chờ process lẫn stdout/stderr; khi hết thời
gian, launcher không báo thành công và không chờ vô hạn một pipe còn mở.
Timeout không tự rollback/xóa container hoặc volume đã được Docker tạo.

Nếu Docker Engine trả HTTP 500 hoặc không khởi động, script báo lỗi và không
báo READY. Mở/restart Docker Desktop rồi thử lại; không reset dữ liệu hay dùng
`down -v`. Đổi credentials trong `.env` không tự đổi tài khoản Mongo đã tồn tại.

Test launcher độc lập, không cần Docker hay Pester:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests/local/test_start_local.ps1
```

## Cấu hình thủ công (không dùng launcher)

Giữ trong `.env` các biến `MONGO_USERNAME`, `MONGO_PASSWORD`,
`MONGO_APP_USERNAME`, `MONGO_APP_PASSWORD`, `MONGO_APP_DATABASE`, `REDIS_PASSWORD`.
Ví dụ dưới đây dùng database mới `resume_screening_local`; nếu đã có dữ liệu,
giữ đúng tên database/tài khoản đã khởi tạo.
App local dùng URI sau, thay placeholder bằng credentials tương ứng (URL-encode
username/password có ký tự đặc biệt):

```dotenv
ENVIRONMENT=development
DEBUG=true
REFRESH_COOKIE_SECURE=false
MONGO_APP_DATABASE=resume_screening_local
MONGODB_DB_NAME=resume_screening_local
MONGODB_URI="mongodb://<MONGO_APP_USERNAME>:<MONGO_APP_PASSWORD>@127.0.0.1:27019/resume_screening_local?authSource=resume_screening_local&replicaSet=rs0&directConnection=true"
REDIS_URL="redis://:<REDIS_PASSWORD>@127.0.0.1:6381/0"
```

`.env` không được đồng bộ khi pull code; hãy thay placeholder bằng giá trị thực.
Credentials chỉ dùng để khởi tạo lần đầu; sửa `.env` không đổi password trong
Mongo đã có dữ liệu. `directConnection=true` tránh client trên host tìm đến
hostname `mongo:27017` chỉ có trong mạng Docker.

## Chạy và dừng

Chạy từ root repository, với Docker Desktop đang hoạt động:

```powershell
docker compose -f docker-compose.local.yml config --quiet
docker compose -f docker-compose.local.yml up -d
docker compose -f docker-compose.local.yml ps -a
```

`mongo-key` và `mongo-init-replica` kết thúc `Exited (0)` là bình thường.
Mongo/Redis phải healthy. Khi hạ tầng sẵn sàng, chạy app bằng venv hiện có:

```powershell
$env:DEBUG='true'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Biến môi trường của terminal được ưu tiên hơn `.env`. Lệnh đặt `DEBUG` ở trên
bảo đảm Swagger được bật cho phiên chạy local, kể cả khi terminal đã có
`DEBUG=release` từ công cụ khác.

Swagger: <http://127.0.0.1:8000/docs>; readiness: <http://127.0.0.1:8000/ready>.
API tự tạo superuser theo `FIRST_SUPERUSER_*` nếu `CREATE_FIRST_SUPERUSER=true`
và database chưa có user. Đăng nhập bằng tài khoản này để tạo dữ liệu dev.

Nếu startup báo `node belongs to a set named 'None'`, Mongo tại cổng 27019
chưa báo replica set `rs0`. Kiểm tra Docker Desktop đã chạy, sau đó chạy lại
`docker compose -f docker-compose.local.yml up -d` và `ps -a`.
Đợi `mongo-init-replica` kết thúc với mã 0, rồi kiểm tra `/ready` sau khi chạy API.
Mongo healthy xác nhận tiến trình đã bật replica set; initializer mới xác nhận
`rs0` đã bầu PRIMARY. Không bỏ `replicaSet=rs0` khỏi URI để né lỗi vì các workflow
cần transaction. Không xóa volume để xử lý lỗi khởi động này.

Dừng app bằng Ctrl+C. Dừng riêng hạ tầng, giữ dữ liệu:

```powershell
docker compose -f docker-compose.local.yml stop
```

Volume mới gồm `resume-screening-local_mongo_data`,
`resume-screening-local_mongo_key`, `resume-screening-local_redis_data`.
Volume/container cũ của project `resume-screening-system` không được sửa/xóa.

## Parsing CV

Để cài/chạy MinerU 4 trên Windows bằng venv riêng, xem
[service MinerU local](../services/mineru/README.md). Môi trường Basic dùng CPU;
môi trường đã nghiệm thu hiện dùng Standard với ONNX và VLM llama.cpp.
Giữ `MINERU_PDF_TIER=standard` và `MINERU_FALLBACK_TIER=standard` trong `.env`.

MinerU và worker chạy riêng. Nếu MinerU chạy trên cùng Windows, đặt
`MINERU_API_URL=http://127.0.0.1:8001` cùng API key tương ứng trong `.env`.
Khởi động MinerU trước, sau đó mở terminal tại root và chạy:

```powershell
$env:WORKER_ADAPTER_FACTORY='app.workers.mineru_adapter:create_adapter'
.\.venv\Scripts\python.exe -m app.worker --queue resume-parse
```

Xem [hướng dẫn MinerU và nghiệm thu](MINERU_ADAPTER.md). Với script E2E chạy trên
host, `PARSING_TEST_MONGODB_URI` dùng cùng URI local ở cổng **27019**, và
`--database resume_screening_local`. Không dùng môi trường dev này làm
`TEST_MONGODB_URI` cho toàn bộ integration suite; suite đó cần instance test riêng.
