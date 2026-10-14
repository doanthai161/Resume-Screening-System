# Backend MVP: tích hợp HR và quản trị công ty

Cập nhật **2026-10-14**. Năm ưu tiên BE đã có code: contract tenant dùng được
cho frontend, tra cứu/tải CV, chọn model screening, HR sửa hồ sơ có phiên bản,
và HR ghi đè đánh giá kèm lịch sử. Worker/adapter screening đã có; **AI service
thực hiện inference `/v1/screen` vẫn cần triển khai riêng**. Đọc toàn bộ endpoint
tại [PROJECT_FEATURES_AND_API.md](PROJECT_FEATURES_AND_API.md); kết quả kiểm thử
mới nhất và phần chưa nghiệm thu tại [ISSUE_STATUS.md](reviews/ISSUE_STATUS.md).

## 1. Chín API mới

Prefix mặc định `/api/v1`. JSON dùng `ApiResponse {success,data,message}`;
download trả file. Business API nhận access Bearer token. Hai POST HR yêu cầu
`Idempotency-Key` 8–128 ký tự; retry cùng lệnh phải giữ key và payload gốc.

| Method / URL | Input chính | Output / status | Quyền và phạm vi |
| --- | --- | --- | --- |
| `GET /api/v1/resumes` | Query `company_id`, optional `candidate_id`, `page=1`, `size=20` (1–100) | 200 `ResumeListResponse {items,total,page,size}` | `resume_files:view`; HR chỉ CV ở branch được phép, owner/superuser có scope company; Candidate được đọc CV của hồ sơ liên kết chính mình |
| `GET /api/v1/resumes/{resume_id}` | ID CV | 200 `ResumeResponse` | `resume_files:view`; company/user/branch active, CV chưa xóa; Candidate có ngoại lệ đọc CV của mình |
| `GET /api/v1/resumes/{resume_id}/download` | ID CV | 200 attachment; không có envelope JSON | Cùng quyền detail; kiểm đường dẫn, loại file thường, kích thước và SHA-256 trước stream |
| `GET /api/v1/ai-models` | Query `page=1`, `size=20` (1–100), optional `model_type=scoring/skill_matcher` | 200 `{items,total,page,size}` | `screening_runs:create`; chỉ model active hỗ trợ screening; trả `id,name,provider,model_id,version,description`, không config/credentials |
| `POST /api/v1/resumes/{resume_id}/profile-revisions` | JSON `base_profile_id`, `expected_version>=1`, `reason`, optional `profile`, `hr_annotations`; header key | 201 `ProfileResponse` | Cần **cả** `resume_profiles:edit` và `resume_profiles:view`, company/branch của CV; nguồn phải còn hiện hành |
| `GET /api/v1/resumes/{resume_id}/profiles` | Query `page=1`, `size=20` (1–100) | 200 `{items,total,page,size}`; version mới trước | `resume_profiles:view`, company/branch của CV; mỗi item có `is_current` và nguồn AI/HR |
| `POST /api/v1/recruitment/screenings/{run_id}/overrides` | JSON `company_id`, `expected_version>=0`, `overall_score`, `decision`, `reason`, optional `comments`; header key | 201 `ScreeningOverrideResponse` | Cần **cả** `screening_results:override` và `screening_runs:view`; branch application và CV; chỉ result mới nhất với nguồn còn hiện hành |
| `GET /api/v1/recruitment/screenings/{run_id}/overrides` | Query `company_id`, `page=1`, `size=20` (1–100) | 200 `{items,total,page,size}` | `screening_runs:view`; cùng scope application/CV; đọc được lịch sử của result được phép |
| `GET /api/v1/recruitment/screenings/{run_id}/assessment` | Query `company_id` | 200 `{ai,effective,latest_override,override_revision,source_is_current,is_latest_result,review_required}` | `screening_runs:view`; tách kết quả AI khỏi đánh giá HR; metadata cho biết assessment đang xem có còn hiện hành |

CV response có IDs company/candidate/branch, tên gốc, size/MIME/checksum, status,
thời gian xử lý, `parse_revision`, `latest_parse_run_id` và
`latest_extraction_profile_id`; không trả `file_path` hoặc toàn bộ `raw_text`.
CV thiếu branch chỉ owner/superuser hoặc Candidate sở hữu được đọc. Download
local chặn traversal, symlink/reparse point, file đổi identity/size/checksum;
trả `Cache-Control: private, no-store`, `nosniff` và đóng handle khi disconnect.
Storage khác local hoặc file không còn hợp lệ trả 404; không có static public
uploads hoặc API delete/restore CV trong đợt này.

## 2. Contract tenant và các thay đổi tương thích

- `CompanyResponse` thêm `id`, `is_active`, `updated_at`. Company PUT cho phép
  chỉ gửi field cần đổi; `name`, `company_short_name`, `company_code`, `email`,
  `website` không nhận null/blank. `description`, `tax_code`, `logo_url` có thể
  xóa bằng null. Field nội bộ/field ngoài allowlist nhận 422; không đổi owner
  qua PUT. Page/size trả về khớp giá trị đã chuẩn hóa.
- Company list/get đọc quyền từ DB, chỉ company active mà caller thực sự sở hữu,
  là superuser hoặc có membership còn hiệu lực qua branch active. Global
  `Administrator` hay permission tên `admin` không tự mở tenant access.
- Branch list của member chỉ trả branch được phép; owner/superuser thấy branch
  active trong company. Detail luôn kiểm đúng branch. Cache business metadata
  phục hồi model/BSON đúng kiểu; cache không cấp quyền và payload cache hỏng
  được bỏ để đọc lại DB. Tạo branch yêu cầu body `company_id` khớp path.
- Assignment active trùng trả 409; đổi role assignment inactive trả 400. Assign
  lại assignment inactive bắt đầu hiệu lực từ hiện tại và bỏ hạn cũ khi không
  truyền lịch mới. Policy chống tự nâng quyền, sửa actual owner, quản lý role
  ngang/cao hơn và cấp direct permissions vẫn áp dụng.
- User–actor assign/get thêm `id`, `user_actor_id`, `actor.id`; DELETE dùng ID
  **mapping** này. GET hiện vẫn chỉ trả một mapping, không phải list mọi actor.

Caller có token/quyền global vẫn cần scope resource hiện tại. Permission lấy
từ user/actor/permission active; membership cần `is_active=true`, start date đã
đến và end date chưa hết. Quyền branch A không mở quyền branch B.

## 3. HR sửa hồ sơ và xác nhận thông tin ngoài CV

Ví dụ dưới đây là dữ liệu synthetic để hướng dẫn tích hợp, không phải kết quả
model hoặc hồ sơ ứng viên thật. IDs cần thay bằng dữ liệu API trả về.

```http
POST /api/v1/resumes/111111111111111111111111/profile-revisions
Idempotency-Key: hr-profile-synthetic-001
```

```json
{
  "base_profile_id": "222222222222222222222222",
  "expected_version": 1,
  "reason": "HR đã xác minh thông tin kỹ năng trong buổi phỏng vấn",
  "hr_annotations": [
    {
      "section": "skills",
      "label": "Kỹ năng đã xác minh",
      "value": "Kubernetes",
      "reason": "Ứng viên cung cấp ví dụ triển khai trong phỏng vấn"
    }
  ]
}
```

`profile`, nếu gửi, là **toàn bộ `ResumeExtractionOutput`** theo
[contract extraction](RESUME_EXTRACTION_CONTRACT.md), không phải PATCH từng
field. Hash nguồn và mọi quote/Unicode offset vẫn phải khớp nguyên văn CV.
Thông tin HR biết qua phỏng vấn được ghi riêng trong `hr_annotations`, không
tạo quote CV giả. Mỗi annotation gồm `section`, `label`, `value`, `reason`;
section nhận `personal_info/summary/skills/experiences/education/certifications/languages`.
Không trùng label trong cùng section, không nhận `id`, `confirmed_by`,
`confirmed_at` từ client. BE tạo fact ID, reviewer và thời gian UTC.

Ít nhất một trong `profile` hoặc `hr_annotations` phải có giá trị. Bỏ
`hr_annotations`/gửi null giữ facts của phiên bản cha; gửi danh sách thay thế
toàn bộ facts, `[]` xóa facts. Facts được giữ lại vẫn giữ người/thời điểm đã
xác nhận trước đó. `edited_by` ghi người tạo revision hiện tại.

BE append revision `origin=hr`, `parent_profile_id`, `edited_by`, `edit_reason`,
version mới và key/input hash; bản AI gốc không bị sửa. HR revision giữ
`extraction_run_id` của nguồn cha để truy nguồn, nên nhiều revision HR có thể
chia sẻ run này. Startup backfill profile legacy thiếu `origin` thành `ai`,
bỏ index cũ `extraction_run_id_1` và thay bằng unique index chỉ `origin=ai`;
key HR và `(resume_file_id,version)` vẫn có ràng buộc unique.

CV legacy thiếu `parse_revision` được đọc với giá trị mặc định 0. Extraction
start và complete dùng cùng source predicate: revision 0 khớp field 0/null/
thiếu; revision khác 0 phải khớp chính xác. Không cần ghi đè revision legacy
chỉ để tạo run; tenant, text nguồn, trạng thái và pointer vẫn được fencing.

Pointer CV, revision counter, shared `screening_fence_version` và bản HR được
ghi trong cùng transaction. Base/version/source stale, parse/extraction đang
active hoặc writer cạnh tranh trả 409; pointer không được thay nếu insert
revision thất bại. Key cùng command/reviewer trả bản đã ghi, kể cả khi nó đã
trở thành lịch sử (`is_current=false`); key khác payload/CV/reviewer trả 409.
Replay vẫn kiểm quyền hiện tại. Không dùng key mới để xử lý timeout chưa rõ
commit; reload latest/history khi conflict rồi quyết định một lệnh mới.

Screening đang active không khóa HR sửa hồ sơ. Worker dùng source cũ sẽ bị
fencing từ chối 422 trước inference/completion phù hợp; không gắn result hay
stage event của nguồn stale. Muốn đánh giá hồ sơ mới, đợi run cũ terminal rồi
tạo screening command mới. Run mới giữ profile/provenance/facts HR trong
snapshot bất biến; xem [SCREENING_PROFILE_BINDING.md](SCREENING_PROFILE_BINDING.md).

Evidence HR trong output screening có dạng:

```json
{
  "origin": "hr",
  "hr_fact_id": "333333333333333333333333",
  "text": "Kubernetes"
}
```

`hr_fact_id` phải thuộc snapshot của run; `text` phải bằng **đúng value đã lưu**.
Evidence HR không được có page hoặc offset CV. Evidence mặc định `origin=cv`
vẫn cần quote thật, span đúng/duy nhất và không được trỏ HR fact. Worker/AI
service không thể tạo thêm xác nhận HR hoặc dùng value khác của cùng fact.

## 4. HR ghi đè đánh giá

```http
POST /api/v1/recruitment/screenings/444444444444444444444444/overrides
Idempotency-Key: hr-override-synthetic-001
```

```json
{
  "company_id": "555555555555555555555555",
  "expected_version": 0,
  "overall_score": 88,
  "decision": "pass",
  "reason": "HR đã xác minh tiêu chí còn thiếu qua phỏng vấn",
  "comments": "Ví dụ synthetic cho hợp đồng API"
}
```

`expected_version=0` cho override đầu tiên; các lần sau dùng `override_revision`
đọc từ assessment. `overall_score` phải gửi, có thể null cho `hold`; `pass/fail`
cần score hữu hạn 0–100. `reason` không rỗng; reviewer/time do BE ghi.

Override được lưu riêng, append-only. Chỉ counter/pointer override của
ScreeningResult thay đổi; score, decision, criteria/evidence AI gốc giữ nguyên.
New override yêu cầu result là pointer mới nhất của application và CV/profile
nguồn vẫn hiện hành. Nguồn thay đổi, result cũ, version stale hoặc writer cạnh
tranh trả 409. Transaction dùng CAS của result, shared CV fence và application
revision; lỗi insert rollback toàn bộ pointer/counter. Key cùng payload/result/
reviewer/base version replay bản cũ; replay vẫn kiểm quyền nhưng không tạo
override mới trên nguồn stale.

Endpoint `/result` cũ tiếp tục trả kết quả AI. Frontend đọc `/assessment` khi
cần đánh giá áp dụng: `ai` là kết quả AI; `effective.source` bằng `ai` hoặc
`hr_override`; `latest_override` và history giữ lý do/người/thời gian/AI base.
Effective assessment áp dụng cho **result đang xem**, kể cả lịch sử; UI cần
đọc `source_is_current`, `is_latest_result`, `review_required` để tránh dùng
đánh giá cũ như kết quả hiện hành. Override không tự chuyển stage và không
tự viết lại profile.

Đọc run/result/override history vẫn cần quyền application branch và scope CV
nguồn hiện tại. CV soft-deleted còn cho đọc kết quả lịch sử nếu policy
company/branch vẫn hợp lệ; không mở lại download CV. CV không branch hoặc
document CV đã mất chỉ actual company owner/superuser được đọc lịch sử, cùng
permission và company/application scope cần thiết. Membership một branch không
đủ để xem retained audit có nguồn không xác định branch.

`ApplicationReview` vẫn là nhận xét riêng theo reviewer/vòng review. POST trùng
application/reviewer/round trả 409. GET reviews giữ response list cũ, nay có
`page=1`, `size=20` (tối đa 100); client cần phân trang để đọc thêm review.

## 5. Scope tuyển dụng và luồng frontend

Application create của HR kiểm cả job branch và CV branch. Get/stage/review
kiểm application/job nhất quán và quyền CV nguồn; replay stage kiểm quyền trước
trả event cũ. HR application list join **job và CV** trong DB trước count/
pagination, kiểm job branch/branch snapshot application nhất quán và CV đúng
tenant/branch/chưa xóa. Application legacy thiếu branch snapshot được xác định
scope bằng job. Trang/total không chứa resource ngoài scope; HR list không dùng
cache cũ làm nguồn quyền. Scorecard create/get kiểm job branch trước business cache.
Candidate tự phục vụ vẫn có nhánh application của chính mình với job published
hợp lệ và permission tương ứng. Candidate record thuộc company, không tự trở
thành resource branch; search job legacy vẫn có scope riêng trong inventory.

Frontend có thể làm luồng:

1. Đăng nhập; lấy company/branch được phép, candidate/job/scorecard và model
   screening active.
2. Upload CV, lưu `resume_id`; list/detail/download để HR tra hồ sơ. Tạo
   application rồi tạo parse run, poll đến terminal.
3. Parsing hoàn tất thì tạo extraction run, poll và đọc latest profile. HR sửa
   thông tin CV có dẫn chứng hoặc xác nhận riêng thông tin ngoài CV; đọc history.
4. Tạo screening với current profile, active scorecard và model ID; poll run rồi
   đọc result/assessment. Lỗi screening chỉ công bố `error_code` và thông báo
   chung, không đưa nội dung lỗi provider/secret vào UI.
5. HR lưu review hoặc override có lý do; chuyển stage bằng lệnh stage riêng.
   Khi source/version conflict, reload profile/assessment và giữ key gốc khi
   đang retry cùng command.

## 6. Phần còn mở và kiểm thử

AI scorer `/v1/screen`, prompt/provider credentials/idempotency server và nghiệm
thu chất lượng với CV Việt/Anh/PDF scan còn phải làm ở service AI. Extractor
`/v1/extract` hiện có không tự trở thành scorer. Xem
[SCREENING_HTTP_ADAPTER.md](SCREENING_HTTP_ADAPTER.md) và
[EXTRACTION_PHASE3.md](EXTRACTION_PHASE3.md) cho contract/cấu hình hiện tại.

Frontend, delete/restore CV, list/cancel run, CRUD model/credentials, branch
update/delete, timeline/audit UI, dashboard/export/ranking và object storage là
các phần riêng; không coi có document/helper như đã có endpoint.

Regression đợt này có test JWT allowed/denied và DB không đổi, cache cold/warm,
profile/override replay, source stale, concurrent CAS/rollback, migration legacy
và workflow synthetic. Chỉ kết quả suite dùng Mongo replica set thật mới là
bằng chứng transaction; HTTP model và cache/queue test double không nghiệm thu
cloud/MinerU hay Redis E2E. Xem kết quả cuối tại
[ISSUE_STATUS.md](reviews/ISSUE_STATUS.md); không dùng số test lịch sử làm kết
quả mới của đợt MVP này.
