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

## Cấu hình

Giữ trong `.env` các biến `MONGO_USERNAME`, `MONGO_PASSWORD`,
`MONGO_APP_USERNAME`, `MONGO_APP_PASSWORD`, `MONGO_APP_DATABASE`, `REDIS_PASSWORD`.
Với môi trường mới đã tạo, tên database là `resume_screening_local`.
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

`.env` trên máy hiện tại đã được cập nhật URI thực, không cần sao chép placeholder.
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
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
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
.\venv\Scripts\python.exe -m app.worker --queue resume-parse
```

Xem [hướng dẫn MinerU và nghiệm thu](MINERU_ADAPTER.md). Với script E2E chạy trên
host, `PARSING_TEST_MONGODB_URI` dùng cùng URI local ở cổng **27019**, và
`--database resume_screening_local`. Không dùng môi trường dev này làm
`TEST_MONGODB_URI` cho toàn bộ integration suite; suite đó cần instance test riêng.
