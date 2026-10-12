# HTTP extractor

Process riêng trong cùng repository: `app.extraction_service:app`.
Chạy CPU nhẹ, gọi model cloud; không cần tải weights hoặc GPU cho bước extraction.
Worker gọi `POST /v1/extract` có service Bearer token, sau đó BE xác minh và lưu
profile. `GET /live` kiểm tra process, `GET /ready` kiểm tra model upstream.

Cấu hình cloud, Docker/local, API và giới hạn:
[EXTRACTION_PHASE3.md](../../docs/EXTRACTION_PHASE3.md).
Contract: [RESUME_EXTRACTION_CONTRACT.md](../../docs/RESUME_EXTRACTION_CONTRACT.md).

Trong Docker dùng `--profile extraction`; chưa cấu hình model/key/token thì
readiness sẽ fail, không trả kết quả inference giả.
