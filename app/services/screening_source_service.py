"""Shared write fence against concurrent parsing, extraction or resume deletion."""
from beanie import PydanticObjectId

from app.core.errors import CustomError, ErrorCodes
from app.core.transactions import current_session
from app.models.resume_file import ResumeFile
from app.schemas.screening_source import ScreeningSourceSnapshot


def screening_source_predicate(resume_id, company_id, snapshot: ScreeningSourceSnapshot) -> dict:
    predicate = {
        "_id": PydanticObjectId(resume_id), "company_id": PydanticObjectId(company_id),
        "is_deleted": False, "status": "parsed", "checksum": snapshot.resume_checksum,
        "parsed_data.raw_text": snapshot.source_text,
        "latest_extraction_profile_id": snapshot.resume_profile_id,
        "latest_parse_run_id": snapshot.source_parse_run_id,
    }
    if snapshot.source_parse_revision:
        predicate["parse_revision"] = snapshot.source_parse_revision
    else:
        predicate["$or"] = [{"parse_revision": 0}, {"parse_revision": None}]
    return predicate


async def fence_screening_source(resume_id, company_id, snapshot: ScreeningSourceSnapshot) -> None:
    if current_session() is None:
        raise RuntimeError("Screening source fencing requires a transaction")
    result = await ResumeFile.get_motor_collection().update_one(
        screening_source_predicate(resume_id, company_id, snapshot),
        {"$inc": {"screening_fence_version": 1}}, session=current_session(),
    )
    if result.matched_count != 1:
        raise CustomError(ErrorCodes.VALIDATION, "Screening source is no longer current", 422)
