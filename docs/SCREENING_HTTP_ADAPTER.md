# Worker screening kết nối AI service HTTP

Ngày cập nhật: 2026-10-14. Đã triển khai contract và adapter HTTP ở BE, bổ sung
nguồn profile và dẫn chứng HR. **593 passed** là bằng chứng lịch sử của đợt
adapter ngày 2026-10-13; kết quả mới nhất nằm tại
[ISSUE_STATUS.md](reviews/ISSUE_STATUS.md).

Phạm vi là kết nối worker screening với một AI service riêng. Repository chưa
có server thực thi endpoint `POST /v1/screen`. Process
`app.extraction_service:app` chỉ cung cấp extraction qua `/v1/extract`; cấu hình
URL của process đó không tạo thêm chức năng chấm điểm.

## Luồng xử lý

```text
API BE tạo screening run có snapshot
  → Redis queue screening → worker claim/preflight
  → HTTP POST /v1/screen đến AI service đã cấu hình
  → kiểm tra response, identity và binding snapshot
  → BE kiểm tra quote, tính score/coverage
  → transaction complete run/result/application
```

Adapter gửi một HTTP request cho mỗi attempt; không tự retry HTTP bên trong
adapter. Retry tạm thời do workflow worker/Mongo quản lý, giữ cùng run và
snapshot, key HTTP và body. Deadline retry được lưu bền vững trong Mongo,
không publish lại ngay sau lỗi. Trước dispatch, preflight kiểm tra
lease/generation, CV/profile và application vẫn phù hợp với đầu vào đã đóng
băng.

AI service trả đánh giá theo từng tiêu chí của scorecard. Tổng điểm, coverage,
điểm biên và quyết định vẫn do BE tính; không nhận tổng điểm do model tự quyết
định làm nguồn kết quả chính thức.

## Contract HTTP

Request dùng:

```http
POST /v1/screen
Authorization: Bearer <SCREENING_SERVICE_TOKEN>
Idempotency-Key: <screening-run-id>
Content-Type: application/json
```

Key HTTP là ID của run trong Mongo, ổn định qua attempts/redelivery. Đây là key
giữa worker và AI service, khác với key command do frontend gửi vào API tạo run.

Contract được định nghĩa trong
[app/schemas/screening_http.py](../app/schemas/screening_http.py), bằng
`ScreeningServiceRequest` và `ScreeningServiceResponse`. Không tự suy ra body
từ toàn document Mongo hoặc schema của extraction.

JSON Schema để triển khai AI service được sinh trực tiếp từ hai model:

- [Request schema v1](contracts/screening-http-v1.request.schema.json).
- [Response schema v1](contracts/screening-http-v1.response.schema.json).

Ví dụ để bàn giao và thử parser của AI service:

- [Request tổng hợp](contracts/screening-http-v1.request.example.json).
- [Response tương ứng](contracts/screening-http-v1.response.example.json).

Hai ví dụ dùng ID 24 ký tự hex cố định, CV tổng hợp Việt/Anh từ bộ contract
extraction, model `custom`/`synthetic-screening-model` và một tiêu chí Python.
Hash nguồn giữ đúng raw text; input hash được tạo từ cấu hình command tổng hợp
theo cách BE tính hash. Không có run/profile/model tương ứng trong DB thật.
Score 80 là số minh họa, không phải kết quả gọi model hoặc kết luận chất lượng
AI. Ví dụ không chứa token/API key và không dùng như request production.

Khi sửa model, sinh lại cả hai artifact từ `model_json_schema()`; không sửa JSON
Schema riêng rồi để contract Python khác với tài liệu tích hợp.

Request gồm các field sau, đều do BE lấy từ run đã đóng băng:

| Field | Nội dung |
| --- | --- |
| `contract_version` | `"1.0"` |
| `run_id`, `company_id`, `application_id`, `resume_file_id` | Identity của command và tài nguyên |
| `input_hash` | Hash binding đầu vào của run |
| `source` | Toàn `ScreeningSourceSnapshot`: raw text/hash, parse revision, profile ID/version và nội dung trích xuất/provenance |
| `scorecard` | `scorecard_id`, `version`, `criteria`, `pass_threshold` từ scorecard snapshot |
| `model` | `ai_model_id`, `provider`, `model_id`, `version` từ metadata model snapshot |
| `prompt_version` | `"screening-v1"` |

Model metadata được lưu tại `run.config_snapshot["model"]`, prompt binding tại
`run.config_snapshot["screening_prompt_version"]`. AI service không tự lấy model,
profile hoặc scorecard mới nhất để thay các binding này.

BE không gửi JWT người dùng, mật khẩu, refresh cookie, API key upstream hoặc
toàn bộ `AIModel.config`. Request chỉ lấy các field được contract cho phép.
AI service quản lý credentials của provider tại môi trường riêng của service.

Response là JSON object đúng contract, gồm `contract_version`, `run_id`,
`input_hash`, `source_text_sha256`, `resume_profile_id`, `profile_version`,
`scorecard_id`, `scorecard_version`, `model`, `prompt_version` và `output`.
`model` phải echo đúng bốn field đã nhận. Worker đối chiếu từng binding với
request trước khi chấp nhận output. JSON đúng hình dạng nhưng binding khác run
vẫn bị từ chối.

`output` dùng `ScreeningOutput`: `criteria_evaluations`, strengths/weaknesses,
matched/missing skills và optional AI confidence. Contract không nhận các field
tổng điểm như `overall_score`, coverage hoặc decision do AI service trả thêm.

Đánh giá giữ ba trạng thái `evaluated`, `insufficient_evidence` và
`conflicting_evidence`. Hai trạng thái chưa giải quyết giữ score null, có lý
do. Evidence tuân theo
[SCREENING_PROFILE_BINDING.md](SCREENING_PROFILE_BINDING.md): text nguyên văn,
offsets Unicode được xác minh, quote không có offsets phải duy nhất, page null.
BE kiểm tra lại dẫn chứng trước khi commit kết quả.

Từ đợt MVP ngày 2026-10-14, `source` còn có `profile_origin` và metadata người/
lý do/thời điểm HR tạo revision, cùng `hr_annotations` đã được BE xác nhận.
Evidence mặc định `origin=cv` giữ nguyên quy tắc quote. Evidence `origin=hr`
phải trỏ `hr_fact_id` trong snapshot và có text bằng đúng value đã lưu;
không được có page/offsets. AI service không tạo hoặc sửa xác nhận HR.
Các field mới có default để đọc dữ liệu CV-only cũ, contract vẫn `1.0`;
service dùng schema allowlist cũ cần cập nhật trước khi xử lý profile HR.
JSON Schema/ví dụ đã được sinh lại từ model Python.

## Cấu hình và khởi động worker

Điền các giá trị vào `.env` trên máy chạy BE/worker; không gửi token trong chat
hoặc cho frontend:

```dotenv
SCREENING_API_URL=https://screening.example.com
SCREENING_SERVICE_TOKEN=<random-service-token-at-least-32-characters>
SCREENING_REQUEST_TIMEOUT_SECONDS=240
WORKER_JOB_TIMEOUT_SECONDS=900
SCREENING_WORKER_ADAPTER_FACTORY=app.workers.screening_adapter:create_adapter
```

URL trong ví dụ là placeholder, cần thay bằng base URL của AI service thực tế
đã cung cấp `/v1/screen`. Token cần ít nhất 32 ký tự ASCII, không có khoảng
trắng hoặc ký tự điều khiển. Token service là secret riêng, không dùng API
key của provider hoặc JWT access token làm token này. API key cloud chỉ được
điền trong cấu hình của AI service thực thi inference.

Worker yêu cầu `WORKER_JOB_TIMEOUT_SECONDS` lớn hơn
`SCREENING_REQUEST_TIMEOUT_SECONDS`. Khi thiếu URL/token hoặc timeout không đủ,
factory fail trước khi tạo HTTP client hoặc claim job; không trả kết quả mẫu
để giả lập screening thành công.

Production cho HTTP nội bộ với host `screener`, `localhost` hoặc địa chỉ
loopback, ví dụ `127.0.0.1`/`::1`.
Endpoint bên ngoài phải dùng HTTPS. URL không chứa username/password, query
secret hoặc fragment; adapter không theo redirect để tránh chuyển token/CV
đến địa chỉ response chỉ định.

Khi BE/worker chạy trên host, dùng base URL host của AI service. Khi chạy trong
Docker, URL phải truy cập được từ container; `127.0.0.1` là chính container,
không phải máy host hoặc một container service khác.

Compose bật riêng worker bằng profile `screening`, hoặc bật nhóm worker với
profile `workers` sau khi các adapter tương ứng đã được cấu hình:

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile screening up -d --build
```

Khi chạy worker trên host, chọn factory cho process hiện tại:

```powershell
$env:WORKER_ADAPTER_FACTORY='app.workers.screening_adapter:create_adapter'
.\venv\Scripts\python.exe -m app.worker --queue screening
```

AI service được chạy/cung cấp riêng; Compose không tự triển khai server scorer
chỉ vì worker đã có adapter. `SCREENING_API_URL` mặc định rỗng, phải cấu hình
tường minh; worker không tự dùng endpoint của extractor.

API tạo/poll screening và đọc result vẫn giữ URL hiện có. Các quyền, tenant,
branch và điều kiện profile hiện tại tiếp tục áp dụng theo
[workflow screening](SCREENING_PROFILE_BINDING.md).

Run có `source_snapshot` nhưng thiếu model/prompt metadata của HTTP contract mới
cũng bị từ chối permanent trước dispatch. Adapter không đọc model live hoặc
`model_config` để bổ sung binding cho command cũ. Historical result vẫn đọc
được. Khi worker hợp lệ nhận run cũ, nó đánh dấu failed terminal vì input thiếu
metadata, trước khi gửi HTTP. Run đang giữ lease cần chờ worker/recovery xử lý;
không yêu cầu client tự sửa status/snapshot. Sau terminal, HR tạo run mới với
key mới nếu cần đánh giá theo contract hiện tại.

Response tạo/poll run bổ sung `next_retry_at` tùy chọn, serialize theo UTC khi
có giá trị. `null` hoặc field thiếu
trong document cũ nghĩa là không có deadline trì hoãn. Không cần backfill dữ
liệu lịch sử hoặc thay body của HTTP contract để dùng cơ chế retry mới.

## Lỗi, giới hạn và retry

| Trường hợp | Cách xử lý |
| --- | --- |
| Timeout, lỗi transport, HTTP 408/429/5xx | Lỗi tạm thời; worker retry có deadline Mongo/backoff trong giới hạn attempts của run |
| Auth/config bị service từ chối hoặc HTTP khác 200 ngoài nhóm tạm thời | Lỗi permanent; cần sửa cấu hình/nguyên nhân trước khi tạo run mới |
| Redirect | Không theo redirect; từ chối response |
| Body quá giới hạn, JSON hỏng/duplicate keys/nesting bất thường | Từ chối output; không log response body |
| Field lạ, sai binding run/profile/source/scorecard/model/prompt | Từ chối output permanent; không commit kết quả |
| Quote không khớp nguồn hoặc source/profile/application đã đổi | BE từ chối; completion 422 là permanent |
| Lease/generation đã mất | Không chấp nhận result stale; giữ cơ chế recovery theo Mongo |

Với lỗi retryable và còn attempts, run trở lại `queued`, lưu `next_retry_at`
trong Mongo theo backoff 5/10/20/40 giây rồi tối đa 60 giây. Default có 3
attempts, nên sau lỗi lần đầu/lần hai dùng deadline 5/10 giây; lỗi attempt cuối
trở thành terminal. Request không được claim trước deadline, kể cả Redis
redelivery đến sớm hoặc worker restart.

Maintenance chỉ publish run đã đến hạn; không publish ngay trong `fail_screening`.
Thời điểm chạy thực tế có thể muộn hơn `next_retry_at` do polling maintenance
(mặc định 60 giây) hoặc hạ tầng chưa sẵn sàng. Field này là thời điểm sớm nhất
được retry, không phải cam kết inference bắt đầu đúng giây đó.

Claim mới xóa deadline sau khi đã đến hạn; completion hoặc failure terminal
cũng giữ deadline null. Retry không đổi run ID, key HTTP hoặc nội dung snapshot.
Client cần poll run cũ, không tạo command/key mới để vượt backoff.

Adapter giới hạn request ở **6.500.000 bytes**, response ở **2.200.000 bytes** và
timeout tổng, đọc response có giới hạn thay vì tải body vô hạn. Response cần
Content-Type `application/json` hoặc `application/...+json`; Content-Encoding
chỉ được vắng mặt hoặc `identity`, không
chấp nhận body nén. Lỗi được biểu diễn bằng mã cố định; không đưa raw response,
CV, token, connection string hoặc thông điệp provider vào log/API.

Timeout hoặc mất kết nối không chứng minh AI service chưa chạy inference.
AI service cần thực thi idempotency phía server với key run ID và cùng input
binding để replay không tạo công việc khác. Header ổn định của BE không tự bảo
đảm model/cloud chỉ bị gọi một lần; provider vẫn có thể tính phí cho request
đã timeout.

## Việc còn cần ở AI service

1. Cài server riêng nhận `/v1/screen` theo contract v1 và xác thực service token.
2. Kiểm tra input/identity, giới hạn body/concurrency và thực thi idempotency.
3. Dựng prompt theo source/profile/scorecard đã đóng băng; coi CV là dữ liệu,
   không thực thi chỉ dẫn nằm trong CV.
4. Gọi provider bằng credentials nội bộ, trả đánh giá từng tiêu chí và echo
   binding đúng request. Không tự sửa thông tin thiếu hoặc chọn nguồn mới.
5. Chạy corpus có nhãn HR để kiểm tra quote, trạng thái null/mâu thuẫn và mức
   điểm. Nghiệm thu E2E worker → AI service → provider → BE riêng với tests mock.

## Bằng chứng lịch sử đợt adapter 2026-10-13

Bản cuối: `python -m pytest -q --disable-warnings --maxfail=1` đạt **593 passed**,
không skipped, với `TEST_MONGODB_URI` của replica set test riêng tại loopback
cổng 27131. Có **95 unit cases mới** cho adapter/config và **14 integration
cases mới** cho HTTP worker/binding/backoff. Lượt chạy riêng **62 integration
screening passed** và **472 unit passed** nằm trong tổng suite, không cộng lại.

Compile `app`/`tests`, kiểm tra JSON Schema/ví dụ, `git diff --check` và Compose
production/dev với profile `screening` đạt. Mongo test đã được dừng sau kiểm
thử; không xóa volume hoặc thay đổi database ứng dụng. Không build/smoke image
Docker mới trong đợt này; warnings dependency/deprecation còn tồn tại.

Bằng chứng và lần chạy trung gian được ghi tại
[ISSUE_STATUS.md](reviews/ISSUE_STATUS.md). Kết quả **484 passed** thuộc đợt nối
profile ngày 2026-10-12, không phải kết quả adapter HTTP này. Tests
MockTransport/ASGI và Redis giả lập không xác nhận endpoint AI service thực tế,
chất lượng model hoặc retry/idempotency của provider đã đạt.
