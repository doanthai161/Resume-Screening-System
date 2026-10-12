# Phần 3 — Trích xuất hồ sơ

Ngày cập nhật: 2026-10-12. Đã triển khai code từ parse result đến profile có cấu
trúc. Chất lượng trích xuất bằng model thật còn cần nghiệm thu sau khi có tài
khoản/API key; inference trong regression tests là HTTP test double, không phải
bằng chứng AI hiểu CV đúng.

```text
CV PDF/DOCX/scan → MinerU/native → raw_text trong Mongo
  → POST extraction-runs → Redis resume-extract → worker
  → HTTP extractor /v1/extract → model cloud
  → đối chiếu schema/hash/quote → transaction Mongo → profile có phiên bản
```

## Thành phần đã có

- `app/extraction_service.py`: process HTTP riêng; dùng cùng image và contract BE.
  Không kết nối DB/Redis hoặc chạy model GPU trong process này.
- `app/workers/extraction_adapter.py`: adapter gọi extractor có service token;
  giới hạn thời gian/body và phân loại lỗi retryable/permanent.
- `app/workers/extraction_prompt.py`: prompt Việt/Anh, xử lý dữ liệu thiếu/mâu
  thuẫn, yêu cầu quote nguyên văn, không làm theo chỉ dẫn trong CV.
- `app/services/extraction_service.py`: API policy, idempotency, claim/heartbeat,
  completion/failure; quote/hash được kiểm tra lại trước khi ghi DB.
- `ResumeExtractionRun`: input snapshot, parse revision, model và prompt version,
  lease/attempt, thời điểm retry và lỗi đã được lược bỏ nội dung nhạy cảm.
- `ResumeProfile`: kết quả giữ trạng thái/dẫn chứng, version, run ID,
  model/prompt/extractor version và `review_required`.

Mỗi worker xử lý một job tại một thời điểm. Service có concurrency hữu hạn,
không xếp hàng request vô hạn trong RAM. Khi busy trả 429; timeout/lỗi tạm thời
được retry tối đa 3 attempt với backoff trong Mongo. Model trả JSON sai có một
lần sửa lại trong mỗi request. Do đó tối đa 6 lần gọi model cho một run có 3
attempt; retry sau timeout có thể phát sinh phí dù request cũ không trả kết quả.

Không tự chạy extraction sau parse; HR/client tạo extraction run khi parsing
hoàn tất. Đây là bước riêng, không gắn thêm inference vào transaction parsing.

## Cấu hình model cloud

Model gateway cần hỗ trợ Chat Completions, JSON object mode, temperature 0,
và GET `/models` trả `{"data":[{"id":"model-name"}]}`. `/ready` kiểm tra đúng
model đã đăng ký; `/live` chỉ kiểm tra process. `ready` không chứng minh model
sẽ trích xuất đúng hoặc tài khoản còn đủ hạn mức cho inference.

Ví dụ tùy chọn với OpenAI, dùng snapshot có Chat Completions:
[model GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini).
Không coi đây là kết luận model tốt nhất cho CV; cần so sánh chất lượng/chi phí
trên corpus của dự án. Tham số và JSON mode tham khảo
[Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

1. Tạo project/API key trong dashboard nhà cung cấp và cấu hình thanh toán/hạn
   mức theo tài khoản. Key cần quyền inference và đọc danh sách models cho readiness.
2. Điền key trực tiếp vào `.env` trên máy chạy service. Không gửi key trong chat
   hoặc frontend. OpenAI hướng dẫn giữ credentials ở server tại
   [Authentication](https://developers.openai.com/api/reference/overview#authentication).
3. Bổ sung các biến dưới đây, thay placeholder bằng thông tin của bạn:

```dotenv
# BE/worker trong Docker:
EXTRACTION_API_URL=http://extractor:8002
EXTRACTION_SERVICE_TOKEN=<random-token-at-least-32-characters>

# Ví dụ upstream OpenAI, không cần GPU local:
EXTRACTION_LLM_CHAT_URL=https://api.openai.com/v1/chat/completions
EXTRACTION_LLM_API_KEY=<project-api-key>
EXTRACTION_MODEL_NAME=gpt-4.1-mini-2025-04-14
EXTRACTION_LLM_TOKEN_PARAMETER=max_completion_tokens

EXTRACTION_LLM_TIMEOUT_SECONDS=90
EXTRACTION_REQUEST_TIMEOUT_SECONDS=240
EXTRACTION_MAX_SOURCE_CHARACTERS=100000
EXTRACTION_LLM_MAX_TOKENS=8192
EXTRACTION_SERVICE_CONCURRENCY=2
```

Khi cấu hình `EXTRACTION_LLM_CHAT_URL`, Settings yêu cầu
`EXTRACTION_REQUEST_TIMEOUT_SECONDS >= 2 * EXTRACTION_LLM_TIMEOUT_SECONDS + 30`:
đủ cho lần gọi đầu, một lần repair và thời gian đọc body/trả response.
Factory worker extraction yêu cầu `WORKER_JOB_TIMEOUT_SECONDS` lớn hơn request
timeout trước khi tạo HTTP client. Default `90/240/900` đáp ứng các điều kiện này.
Ví dụ LLM timeout 180 giây cần request timeout ít nhất 390 giây. Extractor HTTP
riêng không cấu hình LLM URL trong BE có thể dùng request timeout theo SLA của
service đó. Cấu hình timeout không ngăn hoàn toàn phí cloud sau disconnect.

Token service là secret riêng giữa worker và extractor; không dùng API key
cloud làm token service. Có thể sinh token trong PowerShell rồi tự dán vào
`.env` (không commit/in ra log):

```powershell
.\venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

Nếu dùng provider khác, đổi URL/key/model theo provider. Gateway chỉ hỗ trợ
giao thức trên, chưa có adapter riêng cho protocol Gemini/Claude native hoặc
mọi reasoning model. Gateway cũ chỉ nhận `max_tokens` thì đặt
`EXTRACTION_LLM_TOKEN_PARAMETER=max_tokens`. Không chuyển protocol bằng cách
chỉ đổi tên model. Không tự cắt CV vượt context limit hoặc output bị truncate.

Văn bản CV sẽ được gửi đến provider; người triển khai cần chọn tài khoản và
chính sách dữ liệu phù hợp với cách xử lý hồ sơ của công ty.

## Chạy Docker dev

Từ root repository, với `.env` Mongo/Redis/backend đã cấu hình như README:

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile extraction up -d --build
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile extraction ps
```

Profile `extraction` bật API, hạ tầng, extractor và worker-extraction. Muốn bật
parse worker cùng lúc thêm `--profile parse`; MinerU vẫn cần service riêng:

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile parse --profile extraction up -d --build
```

`extractor` dev bind loopback cổng 8002, production không publish cổng này.
Thay `.env` cần recreate containers; không cần xóa volume/database.

## API và chạy app local

Nếu BE/worker chạy trên host và Mongo/Redis bằng `docker-compose.local.yml`,
đặt `EXTRACTION_API_URL=http://127.0.0.1:8002` trong `.env`. Mở ba terminal từ root:

```powershell
# Terminal 1: BE
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2: service inference HTTP, gọi model cloud
.\venv\Scripts\python.exe -m uvicorn app.extraction_service:app --host 127.0.0.1 --port 8002 --no-access-log

# Terminal 3: worker extraction
$env:WORKER_ADAPTER_FACTORY='app.workers.extraction_adapter:create_adapter'
.\venv\Scripts\python.exe -m app.worker --queue resume-extract
```

Các endpoint BE yêu cầu access Bearer token thật:

| Thao tác | Endpoint | Quyền |
| --- | --- | --- |
| Tạo run | `POST /api/v1/resumes/{resume_id}/extraction-runs` | `resume_files:extract` |
| Theo dõi run | `GET /api/v1/resumes/{resume_id}/extraction-runs/{run_id}` | `resume_files:view` |
| Profile hiện tại | `GET /api/v1/resumes/{resume_id}/profiles/latest` | `resume_profiles:view` |
| Profile lịch sử | `GET /api/v1/resumes/{resume_id}/profiles/{profile_id}` | `resume_profiles:view` |

Tạo run gửi body `{"company_id":"..."}` và header `Idempotency-Key` 8–128 ký
tự. Company ID phải khớp resource. Quyền branch khác không mở quyền branch CV
đang gắn; kiểm tra company/membership/branch vẫn diễn ra ở service.

Run phải bắt đầu từ CV `status=parsed` có `raw_text`. API trả 202 với ID để poll
status. Khi `completed`, `result_id` là ID của profile và có thể đọc bằng endpoint
profile. Khi `failed`, sửa cấu hình/nguyên nhân rồi tạo run với key mới; retry
cùng key luôn trả run cũ, kể cả config model đã đổi.

Khi resume có run đang `queued`/`running`, request dùng key khác trả `409`,
kể cả input giống nhau. Key bị từ chối không được lưu hoặc ánh xạ sang run cũ.
Client giữ key gốc để retry/theo dõi run; chỉ gửi yêu cầu mới với key mới sau khi
run trước đã terminal nếu muốn chạy extraction lần nữa.

Claim dùng Mongo `find_one_and_update` trả snapshot ngay sau cập nhật trong cùng
một thao tác. Worker kiểm tra và renew lease bằng CAS trước khi gọi inference:
snapshot đến muộn sau takeover bị từ chối, không gọi AI hoặc đổi trạng thái của
worker mới. Delivery vẫn pending khi mất lease hoặc không kiểm tra được DB.
Renew xác nhận predicate lease khớp; việc Mongo không đổi dữ liệu khi claim và
renew cùng một mili giây không được coi là mất lease.
Readiness và inference dùng chung validation URL model; cấu hình sai trả `503`
đã lược bỏ chi tiết, không gây lỗi `500` hoặc thực hiện repair output vô ích.

Resume parse lại tăng `parse_revision` và bỏ pointer profile hiện tại. Completion
từ input revision cũ bị từ chối, kể cả raw_text giống nhau. Profile lịch sử vẫn
đọc được bằng ID, `is_current=false`. Tạo extraction lần nữa với key mới sau
run đã terminal tạo version tiếp theo; không ghi đè kết quả cũ.

DB bootstrap tạo quyền `resume_files:extract`, `resume_profiles:view` và gán
theo policy role hiện có; Candidate không được cấp quyền profile mới chỉ vì có
`resume_files:view`. Role custom cần được admin cấp quyền phù hợp. Không cấp inference run CRUD
chung để client tự sửa source/worker/lease hoặc giả mạo kết quả.

## Kiểm thử và nghiệm thu

Unit tests kiểm tra thiếu/mâu thuẫn, quote/hash/offsets Unicode, response malformed,
auth service, timeout/throttling, repair hữu hạn, context/concurrency và readiness.
Integration dùng replica set test riêng kiểm tra transaction rollback, version,
source thay đổi/xóa, cạnh tranh claim, lease/generation, retry và redelivery sau
commit nhưng ACK lỗi; HTTP authorization dùng JWT thật và quyền từ DB.
Regression sau rà soát kiểm tra key khác bị từ chối mà DB không đổi, retry key
gốc sau completion, claim response bị trì hoãn qua takeover, preflight từ chối
snapshot stale trước inference, readiness production và quan hệ timeout.
Một test đóng băng thời gian đúng mili giây BSON xác minh renew không đổi dữ
liệu vẫn thành công với owner/generation đúng và từ chối worker/generation sai.

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit -q --disable-warnings --maxfail=1
$env:TEST_MONGODB_URI='mongodb://127.0.0.1:27131/?replicaSet=phase3test&directConnection=true'
.\venv\Scripts\python.exe -m pytest -q --disable-warnings --maxfail=1
```

URI chỉ là ví dụ; cần tạo replica set test riêng trước. Test tự tạo database
ngẫu nhiên rồi dọn đúng database đó, không dùng database ứng dụng/production.

Nghiệm thu model thật sau cấu hình: dùng ít nhất CV Việt, Anh, PDF scan và DOCX,
đối chiếu dữ liệu/dẫn chứng với nhãn do HR kiểm tra. Thử dữ liệu thiếu, mâu thuẫn,
chỉ dẫn độc hại trong CV, retry và parse lại. Schema pass không chứng minh
hallucination bằng 0. Phase 4 screening và phase 5 HR chỉnh sửa có audit chưa
nằm trong phần 3 này.

Bằng chứng đợt triển khai và giới hạn của từng lượt test được ghi tại
[ISSUE_STATUS.md](reviews/ISSUE_STATUS.md#hoàn-thiện-phần-3--2026-10-12).
