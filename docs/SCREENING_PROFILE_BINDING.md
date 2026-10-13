# Gắn hồ sơ trích xuất với screening

Ngày cập nhật: 2026-10-13. Đã triển khai BE nối profile vào screening và kiểm tra
dẫn chứng. Bằng chứng kiểm thử của đợt này được ghi trong ISSUE_STATUS.md;
regression không thay thế nghiệm thu chất lượng AI trên CV thật.

Thay đổi này nối đầu ra phần 3 vào contract screening của BE. Adapter HTTP phía
BE đã có; AI service inference riêng vẫn cần triển khai. Hướng dẫn và contract:
[SCREENING_HTTP_ADAPTER.md](SCREENING_HTTP_ADAPTER.md). Chưa nghiệm thu model
đánh giá CV thật.

## Workflow và nguồn dữ liệu

```text
CV → parsing → extraction → profile hiện tại
  → tạo screening run → đóng băng source/profile/scorecard
  → worker đánh giá theo snapshot
  → BE kiểm tra dẫn chứng và nguồn hiện tại
  → tính score/coverage → transaction ghi result/run/application
```

Run mới chỉ bắt đầu khi CV đã `parsed`, có `raw_text` và profile trích xuất hiện
tại thuộc đúng CV/company/parse revision. Profile có thông tin thiếu hoặc mâu
thuẫn vẫn là đầu vào hợp lệ; worker phải giữ trạng thái chưa đủ dữ liệu thay vì
tự bổ sung thông tin hoặc chấm mặc định bằng 0.

`ScreeningRun.source_snapshot` là dữ liệu có kiểu rõ ràng, do BE tạo. Snapshot
giữ văn bản gốc cùng SHA-256, parse revision, ID/version của profile và kết quả
trích xuất, kèm metadata truy nguồn parsing/extraction. Scorecard và cấu hình
model tiếp tục được đóng băng theo cơ chế screening hiện có.

Worker dùng snapshot của run cho toàn bộ một lần xử lý, kể cả retry. Worker không
đọc profile mới nhất để âm thầm thay đầu vào của run đang chạy. Client không được
tự gửi raw text, snapshot, worker ID, lease hoặc kết quả chấm điểm vào API tạo run.

`ScreeningResult` giữ bản sao snapshot đã dùng để có thể đối chiếu kết quả về sau.
Snapshot đầy đủ là dữ liệu nội bộ; response API chỉ thêm metadata truy nguồn,
không trả toàn bộ `raw_text` hoặc nội dung profile qua DTO screening. Dẫn chứng
trong `criteria_evaluations` vẫn là một phần của kết quả được phân quyền.

Response tạo/poll run và đọc result bổ sung `resume_profile_id`, `profile_version`,
`source_parse_revision`, `source_text_sha256`, `source_is_current`. Cờ current
kiểm tra cả nguồn CV và identity application tại lúc đọc; nguồn đổi thì false,
legacy thiếu snapshot thì null. Kết quả lịch sử giữ nguyên dữ liệu đã chấm.

## Quyền và phạm vi dữ liệu

| Thao tác | Quyền / phạm vi |
| --- | --- |
| Tạo screening run | `screening_runs:create` và `resume_profiles:view`; company đang hoạt động, application branch và resume branch |
| Đọc trạng thái run | `screening_runs:view`; company, application branch và resume branch |
| Đọc kết quả | `screening_runs:view`; company, application branch và resume branch |

Kiểm tra diễn ra ở service và router theo policy hiện có. Membership của một
branch không cấp quyền đọc hoặc ghi branch khác trong cùng company. Company ID
từ request phải khớp tài nguyên; ID/version profile do BE chọn và kiểm tra.

Candidate chỉ có quyền xem CV không được dùng đường screening để đọc toàn bộ
profile. Role custom cần được cấp đủ quyền phù hợp với nghiệp vụ.

## Idempotency và run đang hoạt động

Retry phải giữ `Idempotency-Key` gốc. Sau kiểm tra quyền, nếu company/key đã có
run cho cùng application và model thì API trả run đó, kể cả profile, parse
revision hoặc scorecard hiện tại đã thay đổi. Đây là đọc lại command đã được
nhận; không cập nhật snapshot và không tạo lần inference mới.
Kiểm tra quyền vẫn áp dụng với CV gốc nếu application đã chuyển sang CV khác;
key cũ không mở quyền đọc resource thuộc branch mà caller không có quyền.

Key gốc không được dùng cho application hoặc model khác. Key khác trong lúc
application có run chưa terminal trả `409`, không trả run có key khác và không
tạo command mới. Sau terminal, muốn đánh giá hồ sơ/scorecard mới thì client tạo
run với key mới một cách rõ ràng.

Run cũ bị lỗi do nguồn đã đổi vẫn được đọc lại bằng key gốc. Muốn chạy với nguồn
mới cần tạo run mới; không retry run cũ bằng cách sửa snapshot.

## Dẫn chứng chấm điểm

BE kiểm tra mọi dẫn chứng với `raw_text` đã đóng băng, bao gồm dẫn chứng của tiêu
chí đã chấm, thiếu dữ liệu hoặc mâu thuẫn. Chuỗi `text` phải khớp nguyên văn;
không fuzzy matching, không tự sửa OCR hoặc chuẩn hóa lại nguồn.

- Có `start` thì phải có `end` và ngược lại. Vị trí bắt đầu được tính từ 0, vị
  trí kết thúc không được tính vào đoạn trích.
- Offsets đếm ký tự Unicode theo slicing Python, không phải byte UTF-8 hoặc
  chỉ số UTF-16 của JavaScript.
- Khi không có offsets, đoạn trích phải xác định được duy nhất một vị trí trong
  nguồn. Đoạn xuất hiện nhiều lần cần trích rộng hơn hoặc gửi cặp offsets đúng.
- Run mới yêu cầu `page=null`: parser hiện chưa có bản đồ trang đủ tin cậy để BE
  xác minh số trang. Không suy ra số trang từ Markdown hoặc bố cục CV.

Ví dụ với nguồn đúng bằng `Python backend project`:

```json
{
  "status": "evaluated",
  "score": 80,
  "reason": "CV có dự án backend sử dụng Python.",
  "evidence": [
    {
      "page": null,
      "text": "Python backend project",
      "start": 0,
      "end": 22
    }
  ]
}
```

Quote đúng chỉ chứng minh đoạn trích tồn tại. Việc suy luận một kỹ năng, kinh
nghiệm hoặc mức điểm từ đoạn đó vẫn cần nghiệm thu model và HR review. Tổng
điểm, coverage và quyết định cuối cùng tiếp tục do BE tính theo scorecard.

## Nguồn thay đổi và tính nhất quán

Trước inference và completion, BE yêu cầu CV vẫn tồn tại, vẫn `parsed`, cùng parse revision,
hash văn bản và profile hiện tại như snapshot. Parse lại với text giống hệt
hoặc tạo profile mới từ cùng text vẫn làm run đang chạy bị stale.

Khi nguồn/profile đã thay đổi, completion trả `422` và worker đánh dấu lỗi
terminal. Transaction không ghi result, pointer application hoặc sự kiện
screening completed. Không tự gắn đánh giá từ hồ sơ cũ vào hồ sơ hiện tại.

Chỉ đọc ResumeFile trong Mongo snapshot transaction không đủ bảo vệ quy tắc
này: parsing/extraction có thể ghi ResumeFile trong khi screening chỉ ghi run
và application. Start/preflight/completion screening vì vậy dùng CAS trên cùng ResumeFile
và tăng `screening_fence_version` bằng `$inc` trong transaction. Đây là write
thực sự, tạo xung đột với parse/extraction concurrent; không dùng một `$set`
không đổi dữ liệu làm lock và không tăng `parse_revision`/file `version` để né
race.

Counter fencing không thay phiên bản nội dung CV. Document cũ mặc định counter
bằng 0, không cần backfill hoặc xóa dữ liệu/index. Lỗi transaction do concurrent
change không được coi là completion thành công; delivery/lease giữ cơ chế
phục hồi hiện có.

Khi cập nhật application, BE kiểm tra identity/revision và giữ thay đổi stage
của HR; không dùng fallback gắn pointer vô điều kiện để vượt qua CAS thất bại.
Revision do chính thao tác start chuyển `applied` sang `screening` được tính
đúng trong snapshot, không coi là HR đã thay đổi đầu vào.
Preflight cũng kiểm tra application còn khớp company/CV/job trước khi gọi AI;
input không còn hợp lệ bị fail terminal mà không dispatch inference.

## Dữ liệu và caller cũ

- Historical run/result thiếu `source_snapshot` vẫn đọc được. Metadata nguồn
  có thể không có; không khẳng định dẫn chứng của kết quả lịch sử đã được xác
  minh bằng contract mới.
- Queued/running run cũ thiếu snapshot không được complete. BE trả `422`, worker
  kết thúc run với lỗi permanent. Không lấy CV/profile hiện tại để dựng lại
  snapshot cho command cũ. HR cần tạo run mới sau khi extraction hoàn tất.
- Run mới yêu cầu profile hiện tại và quyền `resume_profiles:view`; request
  trước đây dùng được ngay sau upload/parsing có thể bị từ chối.
- Caller giữ key gốc khi retry; muốn đánh giá phiên bản mới dùng key mới sau khi
  run trước terminal. Key khác khi đang active trả `409`.
- Adapter screening phải trả dẫn chứng nguyên văn với `page=null` và offsets
  đúng nếu dùng. Dẫn chứng không có trong nguồn, quote mơ hồ hoặc số trang tự
  suy ra khiến completion bị từ chối.
- `422` của completion screening là lỗi permanent, không gọi inference thêm
  chỉ để retry cùng một source/snapshot không hợp lệ.

Không thay URL API hiện có. Deploy cần cập nhật/restart API và các worker cùng
contract; không giữ worker cũ đang trả dẫn chứng không tương thích.

## Xác minh và phần còn mở

Regression cần kiểm tra nguồn đổi/xóa, cùng text nhưng khác revision/profile,
quote sai/mơ hồ, paired offsets Unicode, page chưa xác minh, idempotency sau
nguồn đổi, quyền thiếu/khác company/branch, legacy read/completion và rollback
khi CAS hoặc lease thất bại. Race parse/extraction với screening cần Mongo
replica set thật, không thay bằng mongomock để kết luận atomicity.

Kết quả lần chạy hiện tại được cập nhật tại
[ISSUE_STATUS.md](reviews/ISSUE_STATUS.md). Con số **394 passed** thuộc lần sửa
extraction trước đó, không phải bằng chứng của thay đổi screening này.

Phần còn mở: AI service screening riêng, nghiệm thu trên CV thật và nhãn HR, kiểm thử
cloud/Redis thực tế, giao diện và HR chỉnh sửa profile/override có lịch sử. Việc
đóng băng nguồn và xác minh dẫn chứng không tự hoàn thành các phần này.
