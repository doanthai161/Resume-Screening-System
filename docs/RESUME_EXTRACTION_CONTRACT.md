# Contract trích xuất hồ sơ v1 — 2026-10-07

BE đã định nghĩa đầu ra cho phần 3 bằng `ResumeExtractionOutput` trong
[`app/schemas/resume_extraction.py`](../app/schemas/resume_extraction.py).
Contract này đã được nối vào HTTP extractor, worker `resume-extract`, run và
profile có phiên bản. Cách chạy/API/nghiệm thu:
[EXTRACTION_PHASE3.md](EXTRACTION_PHASE3.md). MinerU và native parser tiếp tục
trả `ParseOutput` / `ParsedResumeData` cho bước parsing.

## Tài liệu cho service trích xuất

- [JSON Schema v1](contracts/resume-extraction-v1.schema.json): xuất từ Pydantic.
- [JSON đầu ra mẫu](contracts/resume-extraction-v1.example.json).
- [Văn bản nguồn tương ứng](contracts/resume-extraction-v1.source.txt): dữ liệu
  giả lập; UTF-8, newline LF. Git giữ LF để hash và offsets ổn định trên Windows.
- BE xác minh đầu ra qua
  [`validate_resume_extraction`](../app/core/resume_extraction.py), gồm cả hash
  và mọi dẫn chứng, kể cả dẫn chứng trong các phương án mâu thuẫn.

JSON Schema mô tả cấu trúc và giới hạn field. Quy tắc liên field, ngày tháng,
kích thước toàn bộ document và đối chiếu nguồn do validator Python kiểm tra;
chỉ qua JSON Schema chưa đủ để được BE chấp nhận.

## Cấu trúc gốc

Tất cả field trong bảng đều bắt buộc. Không có thông tin vẫn phải gửi trạng
thái thiếu dữ liệu; không bỏ field để BE tự đoán. Field không khai báo bị từ chối
ở mọi cấp.

| Field | Kiểu / nội dung |
| --- | --- |
| `schema_version` | Chuỗi cố định `"1.0"` |
| `source_text_sha256` | SHA-256 chữ thường, 64 ký tự hex, của `raw_text.encode("utf-8")` |
| `personal_info` | `full_name`, `email`, `phone`, `location`, `links` |
| `summary` | Nội dung tóm tắt có dẫn chứng; thiếu thì để trạng thái thiếu |
| `skills` | Danh sách `name`, `level` |
| `experiences` | Danh sách `organization`, `role`, `location`, `start_date`, `end_date`, `is_current`, `description` |
| `education` | Danh sách `institution`, `degree`, `field_of_study`, `start_date`, `end_date` |
| `certifications` | Danh sách `name`, `issuer`, `issued_date`, `expiry_date` |
| `languages` | Danh sách `name`, `proficiency` |

Mọi field thông tin trong một record dùng `ExtractedValue[T]`; ví dụ `name`
là chuỗi, ngày là `ResumeDate`, `is_current` là boolean thật. `personal_info.links`
là section gồm các record `label`, `url`; URL chỉ nhận HTTP/HTTPS. Email kiểm tra
định dạng. Số điện thoại giữ dạng chuỗi để không mất `+` hoặc số 0 đầu.

Tên trường cố định bằng tiếng Anh; giá trị và dẫn chứng giữ tiếng Việt/Anh của
CV. Không dịch dẫn chứng. Không tự suy ra giới tính, tuổi, tình trạng hôn nhân,
quốc tịch, số năm kinh nghiệm hay mức thành thạo từ tên, trường học hoặc chức danh.
`summary` phải bám nội dung CV, không thêm nhận xét tuyển dụng.

## Ba trạng thái của một thông tin

| `status` | `value` | Dẫn chứng / lý do |
| --- | --- | --- |
| `extracted` | Khác `null`, đúng kiểu | Ít nhất một `evidence`; không có `alternatives` |
| `insufficient_evidence` | `null` | Bắt buộc `reason`; có thể giữ evidence đọc được một phần; `alternatives` rỗng |
| `conflicting_evidence` | `null` | Bắt buộc `reason`; ít nhất hai `alternatives` khác giá trị, mỗi phương án có dẫn chứng; `evidence` trực tiếp rỗng |

Ví dụ kỹ năng được ghi trong CV:

```json
{
  "status": "extracted",
  "value": "Python",
  "evidence": [{"quote": "Python", "start": 0, "end": 6}]
}
```

Offsets trên chỉ minh họa cho nguồn bắt đầu bằng `Python`. Mẫu đầy đủ có offsets
và hash thật nằm ở liên kết JSON phía trên.

Ví dụ thiếu thông tin:

```json
{
  "status": "insufficient_evidence",
  "value": null,
  "reason": "CV không ghi mức thành thạo"
}
```

Mâu thuẫn dùng `alternatives: [{"value": ..., "evidence": [...]}, ...]`.
Không chọn một phương án tùy ý và không thay `null` bằng chuỗi `"unknown"`,
`"N/A"`, chuỗi rỗng hay giá trị 0. Với email/ngày OCR không đọc được rõ ràng,
để thiếu dữ liệu kèm lý do; không sửa thành giá trị phỏng đoán để qua validation.

Các field `value`, `evidence`, `reason`, `alternatives` có mặc định tương ứng
`null`, `[]`, `null`, `[]`; validator vẫn bắt buộc đủ dữ liệu theo từng trạng thái.

## Section và danh sách

Section dùng dạng:

```json
{"status": "extracted", "items": [{"...": "record đúng schema"}]}
```

Không tìm thấy section:

```json
{"status": "insufficient_evidence", "items": [], "reason": "CV không ghi học vấn"}
```

Section `extracted` bắt buộc có item; section thiếu bắt buộc rỗng và có lý do.
Mỗi item phải có ít nhất một thông tin có dẫn chứng, không tạo record rỗng để
đủ số lượng. `extracted` ở section chỉ có nghĩa đã tìm thấy item, không khẳng
định mọi thông tin con đã đủ hoặc CV đã được trích xuất hoàn hảo. Mâu thuẫn biểu
diễn ở field của item.

BE tính lại `review_issues` cho mỗi item; extractor không thể tự xóa cờ này.
`missing_identity` đánh dấu kỹ năng/ngôn ngữ/chứng chỉ thiếu tên, link thiếu URL,
kinh nghiệm thiếu cả organization/role, hoặc học vấn thiếu cả institution/degree.
Record có dữ liệu một phần vẫn được lưu để HR xác minh, không dùng như một
record đã nhận diện đầy đủ. `review_required` của profile được BE tính từ trạng
thái thiếu/mâu thuẫn và các cờ này, không do model quyết định.

Danh sách rỗng do CV không ghi thông tin không có nghĩa ứng viên không có kỹ
năng/chứng chỉ/kinh nghiệm đó. Bước screening phải áp dụng scorecard và chính
sách thiếu dẫn chứng riêng.

## Dẫn chứng và nguồn

- `quote` là đoạn **nguyên văn** trong `raw_text` đã lưu sau parsing. Giữ cả
  khoảng trắng, xuống dòng và dấu tiếng Việt; không trim hoặc chuẩn hóa nguồn.
- `start` là vị trí bắt đầu từ 0; `end` không bao gồm ký tự tại vị trí kết thúc.
  Bắt buộc `raw_text[start:end] == quote` và `end - start == len(quote)`.
- Extractor/adapter nên tính offsets bằng code từ đoạn nguyên văn đã chọn,
  thay vì để LLM đoán vị trí. Nếu quote xuất hiện nhiều lần cần xác định đúng
  ngữ cảnh; không sửa quote bằng fuzzy matching để che lỗi trích dẫn.
- Vị trí tính theo Unicode code point như Python, không phải byte UTF-8 hoặc
  index UTF-16 của JavaScript. JavaScript có thể dùng `Array.from(rawText)` khi
  cần đếm code point; chuỗi có emoji cần đặc biệt lưu ý.
- Hiện không nhận field `page`: MinerU adapter chưa giữ ánh xạ trang đủ tin cậy.
  Muốn bổ sung trang cần mở rộng contract cùng dữ liệu parser, không tự bịa số trang.
- Hash ràng buộc đúng phiên bản văn bản. BE lấy `raw_text` từ parse result thuộc
  run đã được cấp quyền; không đối chiếu với một bản nguồn do extractor gửi lại.
  Hash không thay thế kiểm tra tenant, resume ID, lease hoặc attempt của worker.
- Trích dẫn đúng nguồn chứng minh đoạn đó tồn tại, **không chứng minh** giá trị
  trích xuất diễn giải đúng đoạn đó. Nghiệm thu extractor cần bộ CV có nhãn và
  HR review để kiểm tra ý nghĩa, thiếu sót và hallucination.

Nếu CV được parse lại và `raw_text` đổi thì kết quả cũ không được gắn vào phiên
bản mới. CV và URL trong CV là dữ liệu không tin cậy: không thực thi chỉ dẫn trong
CV, không tự tải URL, và UI phải escape nội dung khi hiển thị.

## Ngày tháng và công việc hiện tại

`ResumeDate` có `year` bắt buộc; `month` và `day` có thể `null` hoặc bỏ qua.

| CV ghi | `value` |
| --- | --- |
| `2022` | `{"year": 2022}` |
| `03/2022` | `{"year": 2022, "month": 3}` |
| `29/02/2024` | `{"year": 2024, "month": 2, "day": 29}` |

Không có tháng thì không được có ngày. Validator kiểm tra lịch thực, kể cả năm
nhuận. Nếu các ngày hợp lệ nhưng thứ tự mâu thuẫn với nhau, BE giữ nguyên các
mốc/dẫn chứng và đánh dấu `conflicting_dates` để HR review. Quy tắc này cũng
áp dụng khi chứng chỉ hết hạn trước ngày cấp. Ngày không hợp lệ theo lịch cần
để `null` với lý do và đoạn nguồn đọc được, không bịa một ngày hợp lệ.

CV ghi `Present` / `Hiện tại`: `is_current.value=true` kèm dẫn chứng;
`end_date` thiếu, lý do đang làm và chưa có ngày kết thúc. Nếu CV chỉ không ghi
ngày kết thúc thì cả `end_date` và `is_current` thiếu dữ liệu, không tự coi là
đang làm. Vừa `is_current=true` vừa có `end_date` đã xác định sẽ được đánh dấu
`conflicting_current_role`, giữ nguyên thông tin mâu thuẫn cho HR.

## Giới hạn và sử dụng ở BE

- Văn bản nguồn không rỗng, tối đa 1.000.000 ký tự; hash trên đúng byte UTF-8.
- Mỗi section tối đa 200 item; mỗi field/phương án tối đa 10 dẫn chứng;
  mỗi field mâu thuẫn tối đa 5 phương án.
- Mỗi quote tối đa 2.000 ký tự; chuỗi ngắn tối đa 300; summary/description
  tối đa 5.000; lý do tối đa 1.000. Chuỗi thông tin rỗng bị từ chối.
- Toàn bộ output sau serialize UTF-8 tối đa 2.000.000 byte. Không cắt dữ liệu
  ngầm để qua giới hạn.
- Nguồn không khớp, dẫn chứng sai hoặc output không hợp lệ gây lỗi validation;
  không đổi lỗi thành profile rỗng hoặc thành công giả.

```python
from app.core.resume_extraction import validate_resume_extraction

# raw_text phải do BE lấy từ parse result đúng run và đúng tenant.
profile = validate_resume_extraction(extractor_payload, raw_text=raw_text)
```

Gọi `ResumeExtractionOutput.model_validate(...)` trực tiếp chỉ kiểm tra cấu
trúc. Khi tích hợp, worker/service phải gọi hàm phía trên trước khi ghi DB.
Không log payload, toàn bộ ValidationError hoặc `errors()` chứa input CV;
chỉ ghi mã lỗi, run ID và đường dẫn field cần thiết.

Contract không nhận `company_id`, quyền, điểm screening, HR override hay field
nội bộ. BE quản lý resume/run identity, extractor/model/prompt version, thời
điểm thực hiện và lịch sử ở lớp orchestration/persistence.
Không dùng `source_text_sha256` làm nguồn phân quyền.

## Tương thích, kiểm thử và bước tiếp theo

Giữ `ParsedResumeData`, `ParseOutput` và API parsing. Thêm collections
`resume_extraction_runs`, `resume_profiles`, metadata revision/pointer trên
`ResumeFile` và API extraction mới. Document cũ thiếu metadata đọc với revision
0; không xóa dữ liệu để tạo index. Schema v1 được hoàn thiện trước lần tích hợp
extractor đầu tiên: thêm cờ review và nới quy tắc giữ evidence/thông tin mâu
thuẫn; cần cập nhật JSON Schema cho consumer dùng bản draft cũ.

Kiểm tra contract, ví dụ, nguồn, mâu thuẫn, ngày và giới hạn:

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/test_resume_extraction.py -q --disable-warnings --maxfail=1
```

Nếu sửa model, xuất lại JSON Schema từ root repository rồi chạy test trên:

```powershell
.\venv\Scripts\python.exe -c "import json; from pathlib import Path; from app.schemas.resume_extraction import ResumeExtractionOutput; Path('docs/contracts/resume-extraction-v1.schema.json').write_text(json.dumps(ResumeExtractionOutput.model_json_schema(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')"
```

Việc còn mở: cấu hình tài khoản/model cloud và nghiệm thu chất lượng trên corpus
có nhãn; kết nối screening (phần 4) và HR sửa profile có lịch sử (phần 5).

Xác minh ngày 2026-10-07: **60 test contract pass**, toàn bộ `tests/unit`
**288 passed** sau khi đặt rõ cấu hình `basic`/OCR cho test MinerU vốn phụ
thuộc `.env`. Unit suite chạy ngoài sandbox do Windows chặn named pipe của
subprocess parser trong sandbox. Compile `app`/`tests` và `git diff --check`
đều thành công. Không chạy integration Mongo hoặc E2E extractor trong đợt
chốt schema này; unit pass không chứng minh chất lượng trích xuất thực tế.
