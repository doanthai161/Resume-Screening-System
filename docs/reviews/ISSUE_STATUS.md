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
