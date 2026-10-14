# Trạng thái rà soát parsing — 2026-10-01

File này ghi nhận đợt hoàn thiện phần 2. Các báo cáo P0/P1 lịch sử không có trong
working tree hiện tại; tài liệu này không khôi phục hoặc thay thế kết luận cũ.

| Lỗi | Trạng thái code | Hướng xử lý |
| --- | --- | --- |
| PDF 9 trang text + 1 trang scan có thể bỏ sót trang scan | Đã sửa | Bất kỳ trang thiếu text đều yêu cầu OCR; tắt auto OCR thì báo lỗi rõ ràng |
| Native text quá dài bị cắt mà vẫn có thể báo thành công | Đã sửa | Từ chối vượt giới hạn ký tự/kích thước, không cắt ngầm |
| PDF hỏng bypass giới hạn số trang | Đã sửa | Không xác định được page count thì fail preflight |
| Native parser tiếp tục chạy sau timeout/cancel thread | Đã sửa | Tiến trình con có timeout, kill và reap khi hủy |
| DOCX fallback bỏ header/footer, sai thứ tự bảng | Đã sửa | Duyệt XML, lấy header/footer và bảng lồng; chặn fallback thiếu embedded content |
| Link ảnh có thể làm OCR rỗng vượt quality gate | Đã sửa | Đo visible text, bỏ URL/markup ảnh |
| HTTP protocol error và malformed files[] không phân loại đúng | Đã sửa | Transport retryable, response lỗi đi qua fallback, upload không buffer body |
| Xóa CV trong lúc parse vẫn có thể complete thành công | Đã sửa | Atomic predicate và kiểm tra số document được cập nhật trong transaction |
| Nghiệm thu MinerU thật, OCR tiếng Việt/Anh | Đạt smoke corpus 2026-10-02 | 5/5 CV mẫu qua API → Redis worker → MinerU 4.0.10 Standard → Mongo; corpus thực tế và restart/concurrency vẫn cần kiểm chứng |
| MinerU trả DOCX thiếu header/footer dù quality gate đạt | Đã sửa, E2E đạt | Bổ sung các dòng margin còn thiếu từ preflight; chống ghi trùng/giới hạn text; parser_version có `+docx-margins` |
| PDF mẫu sinh stream trực tiếp khiến PDFium render trắng | Đã sửa bộ mẫu | Dùng `page.replace_contents` để tạo stream indirect; PDFium đã kiểm tra có nội dung hiển thị |
| Log `worker_adapter_error` không cho biết loại lỗi | Đã sửa | Chỉ log queue, error code, exception type; test bảo đảm không log thông điệp chứa secret |

Chi tiết/bằng chứng: [PARSING_PHASE2_REMEDIATION.md](PARSING_PHASE2_REMEDIATION.md).

## Khởi động hạ tầng local — 2026-10-01

- Lỗi được báo: API startup gặp `node belongs to a set named 'None'` tại cổng
  27019. Khi kiểm tra, Docker Desktop không có daemon hoạt động; sau khởi động
  Desktop, Mongo/Redis local đều ở trạng thái Exited (255). Chạy lại Compose đã
  khôi phục `rs0` PRIMARY và xác thực Mongo/Redis thành công. Chưa xác định được
  trạng thái tiến trình Mongo tại thời điểm log lỗi ban đầu.
- Khoảng trống cấu hình đã sửa: healthcheck `ping` có thể chấp nhận tiến trình
  standalone tạm thời của Mongo image trong lần tạo users đầu tiên. Healthcheck
  mới kiểm tra `getCmdLineOpts` có `replication.replSetName` hoặc `replication.replSet`
  bằng `rs0` trước initializer (Mongo 7 chạy với `--replSet` trả về `replSet`).
  Initializer cũng kiểm tra tên set và chỉ thành công khi `rs0` là PRIMARY.
- Giữ URI yêu cầu replica set và các volume hiện có. Hướng dẫn khôi phục:
  [LOCAL_DEVELOPMENT.md](../LOCAL_DEVELOPMENT.md).
- Việc còn mở: chưa kiểm thử lại tình huống volume trắng từ đầu trong đợt này;
  kiểm tra trên volume local đã có dữ liệu không thay thế nghiệm thu lần khởi tạo.

### Xác minh lại — 2026-10-02

- Docker Desktop đã được khởi động và bản sửa healthcheck đã áp dụng vào
  `resume_mongo_local`. Mongo/Redis đều healthy trên volume local hiện có.
  `mongo-key` và `mongo-init-replica` đều kết thúc `Exited (0)`.
- MongoDB báo `rs0` PRIMARY; tài khoản ứng dụng xác thực được và kiểm tra
  transaction commit/rollback thành công. Chỉ collection chẩn đoán tạo riêng
  trong lần kiểm tra được xóa sau khi hoàn thành.
- API local chạy thử ở cổng 18081 với `DEBUG=true`; `/live`, `/ready`, `/docs`
  đều trả 200. Tiến trình API thử đã dừng; Mongo/Redis được giữ chạy.
- Compose production, dev và local đều qua `config --quiet`;
  `git diff --check` không có lỗi whitespace. Không chạy toàn bộ unit suite
  vì thay đổi lần này chỉ gồm healthcheck, initializer và tài liệu hạ tầng.

## Contract trích xuất hồ sơ — 2026-10-07

| Khoảng trống / lỗi | Trạng thái | Cách xử lý / phần còn mở |
| --- | --- | --- |
| `ParsedResumeData` chưa có contract typed cho trích xuất và dẫn chứng | Đã chốt schema v1 riêng | `ResumeExtractionOutput` có thông tin cá nhân, summary, skills, experiences, education, certifications, languages; giữ parse contract cũ |
| Thiếu dữ liệu dễ bị thay bằng chuỗi rỗng hoặc suy diễn | Đã có validation trạng thái | Field `extracted` cần giá trị và dẫn chứng; thiếu cần `null` và lý do; mâu thuẫn cần nhiều phương án khác nhau có dẫn chứng |
| Quote/offset sai hoặc kết quả gắn vào bản CV đã parse lại | Đã có hàm xác minh nguồn | `validate_resume_extraction` kiểm tra SHA-256 của `raw_text` và mọi quote theo Unicode offsets; phải được gọi khi tích hợp extractor |
| Ngày không hợp lệ, tự thêm tháng/ngày, nhầm thiếu ngày kết thúc với đang làm | Đã có quy tắc/validation | Giữ độ chi tiết ngày, kiểm tra lịch và thứ tự; `is_current` có trạng thái/dẫn chứng riêng |
| Output nhận field nội bộ hoặc vượt giới hạn tài nguyên | Đã có giới hạn contract | `extra=forbid` ở mọi cấp; giới hạn chuỗi, section, dẫn chứng, alternatives và 2 MB UTF-8 toàn output |
| Test MinerU kỳ vọng `basic` nhưng bị tier `standard` trong môi trường local chi phối | Đã sửa test | Đặt rõ tier `basic` và auto OCR trong test tương ứng; không thay cấu hình runtime |
| Extractor, lưu phiên bản profile và HR chỉnh sửa có lịch sử | Chưa triển khai | Xây adapter theo contract, bổ sung orchestration/persistence và review API; nghiệm thu chất lượng thực tế riêng |

Contract và cách tích hợp:
[RESUME_EXTRACTION_CONTRACT.md](../RESUME_EXTRACTION_CONTRACT.md).
JSON Schema và ví dụ đầu ra giả lập được lưu trong `docs/contracts/`; regression
test kiểm tra artifact đúng model và ví dụ khớp văn bản nguồn.

Bằng chứng đợt này:

- `tests/unit/test_resume_extraction.py`: **60 passed**.
- Toàn bộ `tests/unit`: **288 passed**, gồm kiểm tra tương thích `ParseOutput`
  cũ. Phải chạy ngoài sandbox vì test subprocess parser bị Windows chặn named
  pipe trong sandbox. Pytest vẫn phát sinh warnings; chưa xử lý trong phạm vi này.
- Compile `app`/`tests` và `git diff --check` thành công.
- Không đổi endpoint, document Mongo hoặc pipeline MinerU; không cần migration.
  Chưa chạy integration DB hoặc E2E extractor vì bước này mới chốt contract.
  Quote đúng nguồn chưa chứng minh giá trị diễn giải đúng: cần corpus có nhãn
  và HR review khi triển khai extractor.

## Hoàn thiện phần 3 — 2026-10-12

| Finding / phần thiếu | Trạng thái hiện tại | Cách xử lý |
| --- | --- | --- |
| P2: thiếu dữ liệu không giữ được quote đọc một phần | Đã sửa | `insufficient_evidence` giữ `value=null`, reason và evidence được xác minh với nguồn |
| P2: mâu thuẫn thứ tự ngày làm hỏng toàn profile | Đã sửa | Giữ hai mốc cùng dẫn chứng, tính cờ `conflicting_dates` / `conflicting_current_role` cho HR |
| P2: record không có thông tin nhận diện vẫn như record đầy đủ | Đã sửa | `missing_identity` theo loại record; cờ được BE tính lại, model không thể bỏ qua |
| Chưa có extractor thực thi | Đã có code HTTP service riêng | Prompt Việt/Anh, gọi model cloud theo Chat Completions JSON mode, repair tối đa một lần; tính hash/offsets bằng code |
| Chưa có run/result/queue cho extraction | Đã triển khai | Queue `resume-extract`, lease/generation, heartbeat, backoff/recovery, run snapshot và profile immutable có version |
| Kết quả từ parsing cũ/xóa CV có thể được gắn nhầm | Đã chặn | Predicate source text + parse revision, transaction ghi pointer/profile/run; parse lại vô hiệu pointer profile hiện tại |
| API extraction/profile chưa có tenant/RBAC | Đã triển khai và kiểm thử DB thật | Tạo run cần `resume_files:extract`; trạng thái run cần `resume_files:view`; profile cần quyền riêng `resume_profiles:view`; company/branch/membership được kiểm tra ở service |
| Quyền CV của Candidate có thể vô tình mở quyền xem profile chi tiết | Đã chặn | Profile dùng quyền riêng; bootstrap không cấp quyền này cho Candidate, regression kiểm tra chỉ có CV view bị từ chối |
| Lỗi provider/timeout/truncate/quote giả | Đã xử lý ở contract và adapter | Giới hạn body/context/depth/concurrency/timeout, từ chối quote sai/không rõ vị trí, không log response chứa CV/key |
| Nghiệm thu chất lượng model cloud trên CV thật | Còn mở | Cần cấu hình tài khoản/API key/model và corpus có nhãn; HTTP test double không thay thế nghiệm thu AI |

Chi tiết cấu hình cloud, API, command và giới hạn:
[EXTRACTION_PHASE3.md](../EXTRACTION_PHASE3.md).

Tương thích: thêm `resume_extraction_runs`, `resume_profiles` và metadata revision
trên resume. Document cũ đọc mặc định revision 0. API parsing giữ contract cũ;
JSON Schema draft extraction đã cập nhật thêm `review_issues` và giữ dữ liệu
thiếu/mâu thuẫn. Không xóa dữ liệu để tạo index. Cần restart API/worker sau cập
nhật để mọi completion parsing tăng revision theo code mới.

Bằng chứng đợt triển khai 2026-10-12 (lịch sử, trước lần sửa lỗi bên dưới):

- Suite tổng trên replica set test riêng: **365 passed**. Sau khi bổ sung quyền
  profile riêng, chạy lại nhóm bảo mật/extraction: **181 unit passed** và
  **19 integration extraction passed**, gồm allowed/denied với JWT thật.
  Các lượt chạy này có test trùng nhau, không cộng số lượng thành tổng mới.
- Integration xác minh rollback, cạnh tranh claim, generation/lease cũ, source
  đổi/xóa, profile version, backoff và redelivery khi ACK lỗi sau commit.
  Mongo là DB thật; inference HTTP dùng ASGI/MockTransport và Redis ACK là mock.
  Chưa tuyên bố E2E production hoặc chất lượng model thật đã đạt.
- Build image thành công; smoke container chạy UID/GID `10001:10001`, read-only:
  `/live=200`, `/ready=503` khi chưa cấu hình model/key, không báo ready giả.
- Compile `app`/`tests`, Compose production/dev profile extraction và diff check
  đều thành công. Pytest/stack còn deprecation warnings; chưa cleanup trong đợt này.
- Hai container test do tác vụ tạo đã được xác minh label và dừng; không dừng
  service người dùng hoặc xóa volume/database hiện có.

Các con số ở phần 2026-10-07 là lịch sử. Việc còn mở để nghiệm thu nghiệp vụ:
cấu hình model/key cloud và chạy corpus CV Việt/Anh/scan/DOCX có nhãn HR; test
double không kiểm chứng khả năng hiểu CV, chống hallucination hay chất lượng OCR.

## Sửa lỗi sau rà soát extraction — 2026-10-12

| Finding | Trạng thái | Cách sửa / regression |
| --- | --- | --- |
| P2: key khác nhận run active nhưng không được lưu; retry sau terminal tạo thêm run | Đã sửa | Key gốc vẫn trả run cũ; key khác khi active trả 409. HTTP/JWT regression kiểm tra queued/running và DB không đổi; key mới chỉ tạo run khi gọi rõ ràng sau terminal |
| P2: claim UPDATE rồi GET có thể trả worker/generation sau takeover | Đã sửa | Mongo `find_one_and_update` với `ReturnDocument.AFTER`, không đọc lại run. Test giữ ghi Mongo thật rồi trì hoãn response, expiry/recovery và worker mới claim; snapshot cũ vẫn giữ generation ban đầu |
| P2: snapshot claim đến muộn vẫn có thể bắt đầu gọi AI trước heartbeat | Đã chặn | Worker extraction renew lease bằng CAS trước inference; mất lease/lỗi DB không gọi adapter, không complete/fail/ACK. Regression Mongo xác nhận run thuộc worker mới không đổi |
| Renew ngay sau claim trong cùng mili giây BSON báo mất lease dù vẫn khớp predicate | Đã sửa trong lần rà bản sửa | Kiểm tra `matched_count` thay vì `modified_count`; frozen-clock regression trên Mongo giữ DB không đổi và chỉ cho owner/generation hợp lệ renew |
| P3: URL model bị policy production từ chối khiến readiness trả 500 | Đã sửa | Readiness và inference dùng chung validation, lỗi cấu hình trả 503 đã lược bỏ chi tiết; không outbound hoặc repair khi URL sai |
| Timeout request ngắn hơn hai lần gọi model; worker timeout ngắn hơn request | Đã chặn cấu hình | Khi có LLM URL: request >= 2 × LLM + 30 giây. Factory extraction yêu cầu worker timeout > request; validation diễn ra trước tạo HTTP client |
| Validation lỗi cấu hình có thể đưa input chứa secret vào traceback | Đã giảm rủi ro | Settings bật `hide_input_in_errors`; regression dùng secret giả xác nhận chuỗi lỗi timeout không chứa API key, service token, JWT secret hoặc Mongo URI |

Tương thích: không đổi endpoint, DTO hoặc collection/index. Request với key khác
trong lúc active trước đây trả 202, nay trả 409; client cần giữ key gốc. Default
timeout 90/240/900 không đổi. Cấu hình tùy chỉnh thiếu budget sẽ bị từ chối khi
khởi tạo Settings/worker extraction; worker parse không bị ràng buộc timeout mới.

Bằng chứng bản cuối lần sửa này:

- `python -m pytest -q --disable-warnings --maxfail=1`: **394 passed**, không
  skipped. Chạy với `TEST_MONGODB_URI` trỏ replica set test `phase3test` tại
  loopback cổng 27131; fixture tạo/dọn database UUID riêng, không dùng database
  ứng dụng. Bộ regression mới gồm HTTP/JWT key khác, snapshot claim qua takeover,
  CAS trước inference và renew không đổi dữ liệu trong cùng mili giây BSON.
- Unit regression kiểm tra URL production/readiness/inference, timeout budget,
  factory không tạo client khi cấu hình sai, lease lost/lỗi DB trước dispatch,
  và lỗi validation Settings không hiển thị input chứa secret giả.
- Compile `app`/`tests` và `git diff --check` đạt. Suite chạy ngoài sandbox sau
  khi lượt đầu bị Windows từ chối thư mục tạm của pytest. Các lượt chạy trước
  là kiểm tra trung gian, không cộng số lượng thành tổng mới. Warnings dependency
  và deprecation vẫn còn, chưa xử lý trong phạm vi sửa extraction.
- Mongo test do tác vụ khởi động được dừng sau kiểm thử; không xóa volume hoặc
  thay đổi service ứng dụng. Không build/smoke Docker image mới trong lần sửa này.

Model HTTP và Redis trong regression vẫn là test double; nghiệm thu cloud/Redis
thật và chất lượng CV vẫn mở. Timeout/disconnect vẫn có thể phát sinh phí ở
provider; các bản sửa này không cam kết inference cloud chạy đúng một lần.

## Nối profile extraction vào screening — 2026-10-12

Đã triển khai và kiểm thử BE nối profile với screening. Bằng chứng **394 passed**
ở phần trước là lịch sử; kết quả bản cuối của đợt này là **484 passed** bên dưới.

| Finding / khoảng trống | Trạng thái đợt này | Cách xử lý |
| --- | --- | --- |
| Screening chưa nhận profile trích xuất có phiên bản | Đã triển khai | Run mới cần CV parsed và profile hiện tại; `source_snapshot` có kiểu rõ ràng, giữ profile/source/hash/revision và metadata truy nguồn |
| Worker có thể lấy dữ liệu live khác lúc tạo run | Đã chặn | Snapshot cố định trong run, sao chép vào result nội bộ; API chỉ trả metadata nguồn và `source_is_current`, không toàn raw text/profile |
| Dẫn chứng có text/page nhưng chưa đối chiếu với CV | Đã chặn | Khớp nguyên văn, paired offsets Unicode; quote không có offsets phải duy nhất; run mới yêu cầu `page=null` |
| Tạo/poll screening chỉ kiểm tra company, có thể vượt branch; service start chưa kiểm tra permission | Đã sửa | Start cần create + profile view; start/get run/get result kiểm tra application branch và resume branch ở service |
| Key retry bị ảnh hưởng bởi profile/scorecard thay đổi; key khác nhận run active không đúng command | Đã sửa | Key gốc trả run đã nhận nếu cùng application/model; key khác khi active trả 409. Replay sau rebind vẫn kiểm tra quyền CV gốc |
| Parse/profile thay đổi khi đang screening nhưng result vẫn gắn vào application | Đã chặn | Preflight/completion kiểm tra source/profile hiện tại; stale trả 422 terminal và không ghi result/pointer/completion event |
| Snapshot transaction chỉ đọc ResumeFile chưa chặn write-skew với parse/extraction | Đã sửa | CAS cùng ResumeFile và `$inc screening_fence_version` trong start/preflight/completion; write thật, không thay phiên bản nội dung |
| Application revision lấy trước transition nội bộ, fallback pointer vượt CAS | Đã sửa | Tính đúng revision sau own start transition; giữ stage của HR, pointer update ràng buộc identity/revision; CAS thất bại rollback |
| Application không còn khớp CV/job/company vẫn gọi inference | Đã chặn | Preflight kiểm tra application trước dispatch; sai identity hoặc đã xóa trả 422 terminal, không gọi adapter |
| Legacy run thiếu source snapshot vẫn có thể complete với bằng chứng không xác minh | Đã chặn | Giữ historical read; preflight/completion thiếu snapshot trả 422, không dựng lại snapshot từ dữ liệu live |
| Screening completion 422 vẫn retry inference cùng input sai | Đã sửa | Xử lý 422 permanent cho screening, giữ phục hồi riêng cho lỗi DB/commit chưa rõ kết quả |
| Adapter AI screening và nghiệm thu CV thật | Còn mở | Nối worker với model theo frozen input/scorecard; regression contract không thay thế đánh giá chất lượng model |

Workflow, tương thích và hướng kiểm tra:
[SCREENING_PROFILE_BINDING.md](../SCREENING_PROFILE_BINDING.md).

Thay đổi tương thích cần lưu ý: run mới phải có profile hiện tại và quyền
`resume_profiles:view`; historical run/result vẫn đọc được nhưng queued/running
legacy thiếu snapshot không complete. Dẫn chứng có số trang tự suy ra hoặc
không khớp nguồn bị từ chối. Không đổi URL hoặc xóa dữ liệu/index để áp dụng
snapshot/counter mới. API và worker cần được restart cùng contract.

Bằng chứng bản cuối:

- `python -m pytest -q --disable-warnings --maxfail=1`: **484 passed**, không
  skipped, với `TEST_MONGODB_URI` của replica set test riêng `phase3test` tại
  loopback cổng 27131. Chạy ngoài sandbox để Windows cho phép thư mục tạm và
  subprocess parser. Fixture tạo/dọn đúng database UUID test, không dùng DB ứng dụng.
- **48 integration mới** kiểm tra JWT thật/tenant/branch, profile thiếu hoặc sai,
  snapshot không đổi, idempotency/replay CV gốc, metadata lịch sử, preflight,
  source/evidence sai, rollback, source CAS và application rebind concurrent.
  Sáu fixture completion cũ đã bổ sung ResumeFile/Profile/source snapshot thực.
- **40 unit mới** kiểm tra Unicode/whitespace, quote lặp, offsets, hash/profile
  bị thay đổi, page chưa xác minh, dữ liệu mutable và lỗi đã lược bỏ nội dung.
  Worker tests bổ sung invalid-input/lost-lease trước inference; 422 không retry.
- Compile `app`/`tests` và `git diff --check` đạt. Warnings dependency/deprecation
  còn tồn tại. Lượt 479 passed là kiểm tra trung gian trước guard bổ sung;
  không cộng các lượt chạy với nhau.
- Mongo test do tác vụ khởi động được dừng sau kiểm thử; không xóa volume hoặc
  thay đổi service ứng dụng. Không build/smoke Docker image mới trong đợt này.

Mongo là DB thật; queue/Redis và model inference trong regression vẫn là test
double. Adapter AI screening, chất lượng model/CV thật và E2E cloud/Redis vẫn mở.

## Adapter BE screening qua HTTP — 2026-10-13

Đã triển khai contract và adapter HTTP ở BE; bản cuối đạt **593 passed**, không
skipped, với MongoDB replica set test riêng. Con số **484 passed** ở phần trước
là lịch sử của screening profile binding, không phải kết quả adapter HTTP.

| Finding / khoảng trống | Trạng thái đợt này | Cách xử lý |
| --- | --- | --- |
| Worker screening chưa có adapter gọi AI service riêng | Đã triển khai BE | Một POST `/v1/screen` cho mỗi attempt; service scorer được triển khai riêng, extraction `/v1/extract` không thay thế scorer |
| HTTP response có thể đúng schema nhưng thuộc run/profile/scorecard khác | Đã chặn | Contract v1 echo identity và binding hash/profile version/scorecard/model/prompt; mismatch permanent trước completion |
| Run cũ có source nhưng chưa có model/prompt metadata mới | Đã fail-closed | HTTP input phải có frozen model/prompt; worker nhận run đánh dấu failed terminal trước dispatch, không dựng metadata từ model live/config dict; historical read giữ nguyên |
| Retry/redelivery có thể dùng key HTTP khác hoặc đầu vào live | Đã xử lý | `Idempotency-Key` ổn định bằng run ID, giữ source/profile/scorecard/model metadata đã đóng băng; không retry nội bộ adapter |
| Lỗi HTTP tạm thời có thể dùng hết attempts liên tiếp, không có backoff bền vững | Đã chặn | Lưu `next_retry_at` Mongo theo 5/10/20/40 giây, tối đa 60; claim chỉ nhận run đến hạn, maintenance publish sau deadline, không publish ngay khi fail |
| Deadline retry đọc từ Mongo có thể không kèm múi giờ khi trả API | Đã chuẩn hóa | `next_retry_at` được serialize UTC có `Z`; test đối chiếu BSON UTC qua `ensure_utc`, không so sánh trực tiếp naive/aware |
| Toàn `AIModel.config` có thể đưa secret hoặc cấu hình không được phép sang wire | Đã chặn | Request allowlist metadata, không gửi config dict, JWT/cookie hoặc API key upstream; AI service quản lý provider credentials |
| HTTP body/JSON hoặc thời gian xử lý không giới hạn | Đã giới hạn | Giới hạn request/response/depth, strict JSON và timeout; worker timeout phải lớn hơn request, validation trước tạo client |
| Redirect/URL production có thể chuyển token và CV ra ngoài địa chỉ cấu hình | Đã chặn | Không theo redirect, không URL credentials/query/fragment; HTTPS cho external, HTTP chỉ host nội bộ được phép |
| Lỗi tạm thời và permanent chưa được phân loại cho worker | Đã xử lý | Transport/timeout/408/429/5xx retryable có deadline/backoff; malformed/binding/auth và các response còn lại permanent; không log nội dung nhạy cảm |
| Endpoint scorer và nghiệm thu model thật | Còn mở | Cần AI service `/v1/screen`, credentials provider, idempotency server và corpus có nhãn HR; BE adapter không thực thi server inference |

Hướng dẫn cấu hình, contract, retry và giới hạn:
[SCREENING_HTTP_ADAPTER.md](../SCREENING_HTTP_ADAPTER.md).

Không thay URL business API hoặc cơ chế snapshot/evidence/scoring của BE.
Worker chỉ hoạt động khi URL/token/factory hợp lệ và AI service scorer thực
tế được cung cấp. Token service là secret riêng; không đưa API key cloud vào
body từ BE. Timeout/disconnect vẫn có thể phát sinh phí hoặc inference lặp nếu
AI service chưa thực thi idempotency phía server.

Tương thích retry: run API thêm `next_retry_at` optional, serialize UTC khi có
giá trị; document cũ thiếu field được đọc như null, không cần backfill.
Claim/complete/terminal xóa deadline;
default 3 attempts chỉ dùng 5/10 giây chờ sau hai lỗi đầu. Maintenance polling
có thể khiến dispatch muộn hơn deadline (mặc định poll 60 giây), không cam kết
chạy đúng giây. Retry/restart giữ nguyên run/key/body; client poll run cũ thay
vì tạo key mới để bỏ qua backoff.

Legacy thiếu model/prompt: worker hợp lệ nhận run sẽ fail permanent trước HTTP;
run đang giữ lease cần chờ worker/recovery kết thúc. Client không tự sửa run
hoặc snapshot; chỉ tạo command mới với key mới sau terminal nếu cần.

Bằng chứng bản cuối:

- `python -m pytest -q --disable-warnings --maxfail=1`: **593 passed**, không
  skipped, với `TEST_MONGODB_URI` của replica set test riêng `phase3test` tại
  loopback cổng 27131. Fixture tạo/dọn đúng database UUID test; không dùng DB
  ứng dụng. Chạy ngoài sandbox để thư mục tạm/subprocess Windows hoạt động.
- **95 unit cases mới** kiểm tra request/response binding, field allowlist,
  quote/criteria, MIME/compression/body/depth limits, HTTP status/timeout,
  cancellation/client close, endpoint/token/budget và settings không lộ input.
- **14 integration cases mới** kiểm tra worker HTTP dùng frozen model/scorecard,
  identity/evidence sai không ghi result/pointer, lỗi permanent, retry giữ cùng
  run/key/body, commit rồi ACK thất bại không inference lại, deadline không bị
  bypass, Redis publish lỗi rồi recovery, attempt budget và lease stale.
- Lượt riêng **62 integration screening passed** và **472 unit passed** là
  kiểm tra trung gian, không cộng vào tổng. Lần full suite đầu dừng ở **1 failed,
  63 passed** do test so sánh datetime naive/aware; đã sửa bằng `ensure_utc` và
  xác minh API deadline serialize UTC. Kết quả cuối **593 passed** là sau sửa.
- Compile `app`/`tests`, `git diff --check`, hai JSON Schema khớp model và hai
  ví dụ synthetic qua echo/source/evidence/scoring đạt. Compose production và
  dev với `--profile screening config --quiet` đạt; không in resolved secrets.
- Mongo test đã dừng sau kiểm thử; không xóa volume hoặc thay đổi service/DB
  ứng dụng. Không build/smoke Docker image mới. Warnings dependency/deprecation
  còn tồn tại, chưa xử lý trong phạm vi adapter HTTP.

Mongo là DB thật; HTTP AI và Redis/queue trong regression vẫn là test double.
Endpoint scorer, model/cloud, idempotency server, corpus có nhãn HR và E2E
Redis/cloud vẫn mở. Không dùng **484 passed** lịch sử hoặc cộng lượt trung gian
để thay thế nghiệm thu model/CV thật.

## Năm ưu tiên BE MVP — 2026-10-14

Phạm vi: hoàn thiện API BE, phân quyền và nghiệp vụ HR trước; AI service
screening inference được để lại theo yêu cầu. Hướng dẫn tích hợp và chín API
mới: [BE_MVP.md](../BE_MVP.md). Inventory hiện có **103 endpoint explicit**,
gồm 93 business, 7 root vận hành và 3 extractor; không tính docs tự sinh.

| Finding / khoảng trống | Bản sửa |
| --- | --- |
| Company response thiếu ID/state; update buộc gửi lại toàn bộ hoặc nhận field nội bộ | Thêm ID/state/time; optional allowlist, field nghiệp vụ bắt buộc không nhận null, field nội bộ nhận 422 |
| Company/branch reads có thể dùng quyền global hoặc membership hết hạn; warm cache trả dữ liệu sai kiểu/scope | Recheck DB user/company/branch/membership, không bypass bằng Administrator; deserialize cache về đúng model, cache hỏng refetch |
| Assignment trùng trả lỗi DB; reassign dùng thời hạn cũ; actor mapping khó unassign | Duplicate 409, inactive role update 400, reset lịch mặc định khi reassign; response có mapping IDs |
| Audit membership không lưu vì event enum không khớp; caller/method/endpoint/IP bị bỏ | Bổ sung bốn user_company events, nhận CurrentUser, map field đúng model; regression bốn thao tác JWT kiểm AuditLog Mongo thật |
| Thiếu API tra cứu/tải CV và chọn model screening | Thêm CV list/detail/download có scope/pagination; model active allowlist, không config/credentials |
| CV không gắn branch hoặc CV khác branch được mở qua application/screening | Chỉ actual owner/superuser dùng CV branchless trong HR workflow; Candidate giữ ngoại lệ đọc CV của mình; kiểm cả job và CV ở application, result/history/assessment |
| Application list/count dùng scope snapshot/cache đã lỗi thời | Join job và CV trong Mongo trước count/pagination, kiểm branch nhất quán; HR list không sử dụng cache làm nguồn quyền |
| Review duplicate và list không giới hạn; stage replay bỏ qua quyền đã mất | Duplicate review 409, list có page/size, quyền application/CV; stage kiểm quyền trước replay |
| Download có thể lộ đường dẫn/tải file thay đổi hoặc rò handle khi disconnect | Attachment private/no-store, không public static; xác minh root/company/path/reparse/regular file/identity/size/SHA-256, đóng handle theo lifecycle; open/hash pool riêng tối đa 4, cancellation giữ quota đến native completion, busy 429 |
| Parse key alias theo checksum của CV khác; key khác nhận active run ngoài command | Hash mới gồm resume ID; legacy replay đối chiếu CV/parser/options trước live model lookup nên parser đã inactive vẫn đọc được command gốc; khác key khi active trả 409 |
| HR chưa sửa profile có phiên bản; thông tin ngoài CV có thể thành quote giả | Append-only profile revision, reason/reviewer/version/key; CV correction vẫn exact evidence, HR facts riêng có server ID/time |
| HR source/evidence chưa có provenance hoặc xác minh fact | Snapshot lưu origin/reviewer/reason/time/facts; HR evidence cần đúng ID/value và không page/offsets; CV evidence vẫn exact quote |
| HR revision chạy đồng thời parse/extraction/screening; stale pointer/partial insert | Shared ResumeFile fence + CAS + transaction, active parse/extract chặn edit; stale screening fail; rollback cả revision/pointer |
| Unique index extraction run cũ không cho HR revision cùng nguồn | Backfill origin ai, thay duy nhất index cũ đã biết bằng unique partial AI; unique version/key giữ nguyên, không xóa hồ sơ |
| Legacy CV thiếu parse_revision không khớp fence mới | Revision 0 nhận missing/null theo policy nguồn; test start/complete trên raw legacy document |
| HR override có thể ghi đè AI hoặc mất lịch sử; concurrent/stale base được chấp nhận | Collection append-only riêng, reason/reviewer/version/idempotency; CAS result/application + CV fence, source/latest-result checks, original AI bất biến |
| Đánh giá AI và HR không tách rõ trên API | Giữ `/result` là AI; `/assessment` trả ai/effective/history và current/latest/review flags; không tự chuyển stage hoặc carry override sang result mới |
| Timestamp HR khác giữa response đầu và Mongo replay | Chuẩn hóa BSON milliseconds trước ghi và UTC khi serialize profile/facts/snapshot/override |
| Run status có thể trả nguyên thông điệp lỗi provider | Chỉ error_code và thông báo chung; không raw lỗi/CV/secret |

Tương thích: thêm permissions `resume_profiles:edit`, `screening_results:override`;
role custom phải được cấp đúng quyền. Scope HR hiện chặt hơn theo branch của cả
job và CV, CV branchless không mở cho member thường. Reviews GET giữ list
envelope nhưng mặc định phân trang 20, tối đa 100. Source/evidence thêm field
optional/default, contract screening giữ `1.0`; caller AI strict allowlist cũ
cần cập nhật schema. API/worker cần restart cùng contract, chưa tự triển khai.

Bằng chứng bản cuối của đợt này:

- `python -m pytest -q --disable-warnings --maxfail=5`: **808 passed**, không
  skipped, trong 550 giây. `TEST_MONGODB_URI` trỏ replica set test `phase3test`
  tại loopback 27131; `TEST_REDIS_URL` trỏ Redis test riêng tại loopback 27132.
  Fixture tạo/dọn database và namespace UUID riêng, không dùng DB ứng dụng.
- Suite gồm **564 unit**, **241 integration** và **3 E2E/router cases**.
  Integration dùng Mongo replica set thật; riêng ba workflow dưới đây dùng
  Redis thật. Cache/HTTP model giả lập ở các nhóm khác vẫn có nhãn rõ ràng.
- **215 cases mới**: 92 unit và 123 integration cho tenant/contract/cache,
  CV/download/cancellation/model catalog/parse replay, profile HR/provenance,
  override/history/CAS/rollback, audit và workflow. Không cộng các lượt chạy
  riêng vào tổng **808**.
- Compile `app`/`tests`, `git diff --check`, JSON Schema/ví dụ screening khớp
  model/binding/evidence đạt. Compose production/dev với `--profile workers
  config --quiet` đạt; không in resolved secrets. Inventory AST có 103/103
  endpoint khớp tài liệu, đường dẫn local đã kiểm tra.
- Lượt riêng tenant **19 passed**, workflow **3 passed**, replay override UTC
  **1 passed** và rollback stage **1 passed** là kiểm tra trung gian/subset.
  Các lượt full trước dừng ở timezone/replay hoặc fixture cache JSON/HTTP 201/
  CV thiếu; bản cuối đã sửa và chạy lại. Test không bị sửa để bỏ kiểm tra quyền
  hoặc rollback; fixture nay cung cấp CV thật trong DB đúng scope.
- Warnings Beanie/Pydantic/dependency/deprecation còn tồn tại. Không build/
  smoke Docker image mới, không nghiệm thu model/provider/MinerU thật trong
  đợt này. Hai container test được dừng sau kiểm tra, không xóa volume.

**593 passed** ở phần trước là lịch sử, không phải kết quả lần chạy MVP hiện tại.

Lượt riêng `tests/integration/test_mvp_be_workflow.py`: **3 passed** trên cả
MongoDB replica set và Redis thật. Kiểm tra HTTP/JWT/storage → parse/extract →
profile HR → screening → override/assessment; Redis consumer mới reclaim
pending sau lỗi ACK không xử lý lại; và recovery publish khi XADD lỗi sau
commit Mongo. Adapter parsing/extraction/screening là test double synthetic,
chưa kiểm thử process kill/restart container hoặc endpoint model thật.

Việc còn mở: server AI `/v1/screen`, provider/cloud idempotency, nghiệm thu
CV Việt/Anh/PDF scan và chất lượng model, giao diện. Workflow synthetic dùng
parser/extractor/scorer có kiểm soát; không coi kết quả đó là model thật.
Audit decorator chung vẫn dùng cơ chế lưu best-effort async cũ; bản sửa enum/
mapping không tạo audit outbox hoặc bảo đảm log chung tồn tại khi process bị
kill. Lịch sử profile/override HR được lưu transaction riêng, không dựa vào
audit decorator này.
Không deploy, commit/push hoặc thay database/volume ứng dụng trong đợt này.
