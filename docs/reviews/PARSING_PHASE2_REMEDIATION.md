# Hoàn thiện parsing CV — 2026-10-01

## Phạm vi

Hoàn thiện đọc PDF/DOCX, chọn OCR, fallback, xử lý lỗi và lưu raw_text trước bước
trích xuất hồ sơ. Không thêm trích xuất skills/experience/education.

Danh sách lỗi và bản sửa nằm trong [ISSUE_STATUS.md](ISSUE_STATUS.md).
Hướng dẫn chạy và nghiệm thu: [MINERU_ADAPTER.md](../MINERU_ADAPTER.md).

## Kiểm tra và giới hạn bằng chứng

- Unit regression dùng PDF/DOCX tạo thật, tiến trình native thật và HTTP giả lập.
- Mongo integration chạy replica set riêng `parsep2` trên loopback cổng 27261,
  database tên ngẫu nhiên `p1_test_*`; không dùng database ứng dụng.
- Test mới nối upload service → parse pipeline → HTTP MinerU giả lập → Mongo thật,
  kiểm tra hai worker nhận trùng và chỉ một kết quả/lịch sử được lưu.
- Test transaction kiểm tra CV bị xóa/mất trong lúc parse không bị ghi lại và run
  không được complete. Test cũ được cập nhật để tôn trọng `next_retry_at`.
- Chưa có kiểm chứng MinerU thật hoặc restart process worker với Redis thật.
  Môi trường lúc rà soát không có `MINERU_API_URL`, chưa cài MinerU, Docker daemon tắt.
- `scripts/verify_parse_e2e.py` kiểm tra toàn bộ luồng trên stack test thật; native
  fallback không được tính là pass. Bộ mẫu có PDF scan tiếng Việt/Anh và DOCX Việt.

Kết quả: chạy toàn bộ `python -m pytest -q --disable-warnings --maxfail=1`
với `TEST_MONGODB_URI` của instance trên: **259 passed, không có test skipped**.
Trong đó có **31 integration test** trên Mongo thật; MinerU/Redis trong các test
này vẫn được giả lập. Đây là kiểm chứng race/recovery theo state, không phải
smoke test restart container. Bộ 5 CV mẫu đã sinh thành công; CLI E2E đã kiểm tra
`--help`, chưa chạy E2E khi chưa có stack MinerU.

Sau thay đổi cuối về giới hạn JSON Unicode: chạy lại nhóm document regression
**13 passed**. `compileall app tests scripts`, `git diff --check` và Compose config
production/dev với `.env.example` đều đạt. Không có bằng chứng build/runtime Docker
trong lần này. Instance mongod test do tác vụ tạo đã được dừng sau verification.

## Tương thích

Không đổi URL hoặc response contract. PDF hỏng/thiếu trang và DOCX fallback không
đầy đủ trước đây có thể được chấp nhận; nay trả lỗi hoặc dùng OCR/MinerU. Tham số
`PARSE_NATIVE_TEXT_PAGE_RATIO` được giữ để đọc cấu hình cũ nhưng không cho bỏ qua
trang thiếu text. Biến mới `PARSE_PREFLIGHT_TIMEOUT_SECONDS` mặc định 30 giây.
Native preflight chạy bằng Python executable của worker trong tiến trình riêng.
Quality/confidence hiện là heuristic, không phải xác suất OCR chính xác.

## Kiểm thử thực tế — 2026-10-02

Đã cài service MinerU 4.0.10 riêng trong `.tmp/mineru-venv`, tải/verify ONNX và
`MinerU2.5-Pro-2605-1.2B-GGUF`. Service Standard bind loopback cổng 8001;
API local ở 8000, Mongo `rs0` tại 27019, Redis tại 6381. Worker dùng cùng uploads
và cấu hình của API; cài đặt MinerU không thay dependency trong venv BE.
Hướng dẫn lặp lại: [service MinerU](../../services/mineru/README.md).

Lượt nghiệm thu cuối `phase2-local-20261002-r3-standard` dùng bộ mẫu mới
`.tmp/phase2-local-20261002-v2/manifest.json`, qua HTTP login/upload/create run,
Redis worker thật, MinerU thật và transaction Mongo thật:

| Mẫu | OCR | Kết quả | Cụm từ khớp |
| --- | --- | --- | --- |
| PDF digital | Không | MinerU completed | 3/3 |
| PDF scan tiếng Anh | Có | MinerU completed | 4/4 |
| PDF scan tiếng Việt | Có | MinerU completed | 6/6 |
| PDF mixed | Có | MinerU completed | 4/4 |
| DOCX Việt, có header/footer | Không | MinerU completed + docx-margins | 6/6 |

**5/5 đạt**, mỗi run hoàn thành ngay attempt 1; script đối chiếu `raw_text`,
trạng thái CV/run và timestamp kết quả đã lưu. Report local không chứa text CV
hay secret: `test-artifacts/parse-e2e-phase2-local-20261002-r3-standard.json`.
Các CV/candidate/company tạo cho đợt này là dữ liệu tổng hợp; giữ lại trong dev
để người dùng xem thử. Không chạy integration suite vào database dev.

Các lượt trước là bằng chứng phát hiện lỗi, không tính là đạt: lượt đầu 1/5 do
sandbox Windows chặn named pipe của preflight và DOCX mất footer; Basic sau
khởi chạy ngoài sandbox đạt 3/5 nhưng thiếu hai cụm tiếng Việt và dùng native
fallback cho PDF mẫu render trắng. PDF mẫu dùng content stream direct dù pypdf
vẫn đọc được text; sửa bằng `replace_contents` và kiểm tra PDFium render có nội
dung. Không hạ yêu cầu khớp dấu hoặc tính native fallback là MinerU pass.

Bản sửa DOCX lấy riêng header/footer trong preflight, bổ sung dòng provider bỏ
sót và gắn `+docx-margins` trong parser_version. Test bao phủ việc giữ text thực,
không thêm trùng nếu provider đã trả Markdown, và từ chối tổng text quá giới hạn.
Worker log thêm exception type để chẩn đoán `worker_adapter_error`, vẫn không
log nội dung exception; regression kiểm tra thông điệp chứa secret không xuất hiện.

Unit suite sau các thay đổi: **228 passed**. Đây là unit/mock regression,
phân biệt với 5 case E2E có service thật. Corpus nhỏ chưa chứng minh độ chính xác
OCR trên CV nhiều cột/ảnh xoay/chất lượng thấp; restart/concurrency ở cấp process
worker vẫn chưa được nghiệm thu trong đợt này.

`compileall app tests scripts`, PowerShell syntax của ba script MinerU,
Compose config production/dev/local và `git diff --check` đều đạt. Kiểm tra
cuối: `/live`, `/ready`, `/docs` của API và `/v1/health` của MinerU đều trả 200;
worker đang ready. Các service local được giữ chạy sau nghiệm thu.
