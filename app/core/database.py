import hashlib
import logging
from typing import Iterable, Sequence

import motor.motor_asyncio
from beanie import Document, init_beanie
from bson import ObjectId
from pymongo import UpdateOne
from pymongo.errors import DuplicateKeyError, OperationFailure

from app.core.config import settings
from app.models.actor import Actor
from app.models.actor_permission import ActorPermission
from app.models.ai_model import AIModel
from app.models.application_review import ApplicationReview
from app.models.application_stage_event import ApplicationStageEvent
from app.models.audit_log import AuditLog
from app.models.candidate import Candidate
from app.models.candidate_evaluation import CandidateEvaluation
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.database_migration import DatabaseMigration
from app.models.email_otp import EmailOTP
from app.models.job_application import JobApplication
from app.models.job_requirement import JobRequirement
from app.models.job_scorecard import JobScorecard
from app.models.permission import Permission
from app.models.resume_file import ResumeFile
from app.models.screening_result import ScreeningResult
from app.models.screening_run import ResumeParseRun, ScreeningRun
from app.models.user import User
from app.models.user_actor import UserActor
from app.models.user_company import UserCompany
from app.utils.time import now_utc

logger = logging.getLogger(__name__)
_mongo_client = None


DOCUMENT_MODELS: list[type[Document]] = [
    User,
    Company,
    UserCompany,
    ActorPermission,
    Permission,
    Actor,
    UserActor,
    CompanyBranch,
    JobRequirement,
    CandidateEvaluation,  # Legacy collection kept readable during migration.
    EmailOTP,
    ResumeFile,
    ScreeningResult,
    AIModel,
    JobApplication,
    AuditLog,
    Candidate,
    ApplicationStageEvent,
    JobScorecard,
    ScreeningRun,
    ResumeParseRun,
    ApplicationReview,
    DatabaseMigration,
]

# Internal metadata does not receive API permissions.
PERMISSION_MODELS: Sequence[type[Document]] = tuple(
    model for model in DOCUMENT_MODELS if model is not DatabaseMigration
)


def _collection_name(model: type[Document]) -> str:
    settings_class = getattr(model, "Settings", None)
    return getattr(settings_class, "name", f"{model.__name__.lower()}s")


async def _ensure_default_permissions() -> None:
    default_actions = ("view", "create", "edit", "delete", "list")
    existing = {permission.name for permission in await Permission.find_all().to_list()}
    desired = {
        f"{_collection_name(model)}:{action}"
        for model in PERMISSION_MODELS
        for action in default_actions
    }
    desired.update(
        {
            "resume_files:upload",
            "resume_files:parse",
            "resume_files:screen",
            "screening_results:evaluate",
            "ai_models:train",
            "ai_models:deploy",
            "jobs:match",
            "jobs:bulk_screen",
        }
    )

    for name in sorted(desired - existing):
        try:
            action = name.rsplit(":", 1)[-1]
            await Permission(
                name=name,
                description=f"Permission to {action} {name.rsplit(':', 1)[0]}",
                is_active=True,
            ).insert()
        except DuplicateKeyError:
            # Another worker completed the same idempotent bootstrap write.
            continue


async def _get_or_create_actor(
    name: str,
    description: str,
    *,
    is_system: bool = False,
) -> Actor:
    actor = await Actor.find_one(Actor.name == name)
    if actor:
        return actor
    try:
        return await Actor(
            name=name,
            description=description,
            is_active=True,
            is_default=True,
            is_system=is_system,
        ).insert()
    except DuplicateKeyError:
        actor = await Actor.find_one(Actor.name == name)
        if actor is None:
            raise
        return actor


async def _sync_actor_permissions(actor: Actor, permission_names: Iterable[str]) -> None:
    permissions = await Permission.find(
        {"name": {"$in": list(permission_names)}, "is_active": True}
    ).to_list()
    current_links = await ActorPermission.find(ActorPermission.actor_id == actor.id).to_list()
    current_ids = {link.permission_id for link in current_links}
    for permission in permissions:
        if permission.id in current_ids:
            continue
        try:
            await ActorPermission(actor_id=actor.id, permission_id=permission.id).insert()
        except DuplicateKeyError:
            continue


async def _ensure_default_actors() -> None:
    all_permissions = await Permission.find(Permission.is_active == True).to_list()
    admin = await _get_or_create_actor(
        settings.ADMIN_ROLE_NAME,
        "Full system administrator",
        is_system=True,
    )
    await _sync_actor_permissions(admin, (permission.name for permission in all_permissions))

    recruiter = await _get_or_create_actor(
        settings.RECRUITER_ROLE_NAME,
        "Recruiter with tenant-scoped hiring workflow access",
    )
    recruiter_prefixes = (
        "companies:",
        "company_branches:",
        "job_requirements:",
        "candidates:",
        "resume_files:",
        "job_applications:",
        "application_stage_events:",
        "job_scorecards:",
        "screening_runs:",
        "screening_results:",
        "resume_parse_runs:",
        "application_reviews:",
        "jobs:",
    )
    await _sync_actor_permissions(
        recruiter,
        (
            permission.name
            for permission in all_permissions
            if permission.name.startswith(recruiter_prefixes)
        ),
    )

    candidate = await _get_or_create_actor(
        settings.CANDIDATE_ROLE_NAME,
        "Candidate with self-service application access",
    )
    candidate_permissions = {
        "users:view",
        "users:edit",
        "job_requirements:view",
        "job_requirements:list",
        "resume_files:upload",
        "resume_files:view",
        "job_applications:create",
        "job_applications:view",
        "job_applications:list",
        "screening_results:view",
    }
    await _sync_actor_permissions(candidate, candidate_permissions)


async def _ensure_default_ai_models() -> None:
    defaults = (
        {
            "name": "resume-parser-default",
            "model_type": "resume_parser",
            "provider": "custom",
            "model_id": "resume-parser-v1",
            "version": "1.0.0",
            "description": "Default rule-based resume parser",
            "config": {
                "parser_type": "rule_based",
                "supported_formats": ["pdf", "docx"],
            },
        },
        {
            "name": "skill-matcher-default",
            "model_type": "skill_matcher",
            "provider": "custom",
            "model_id": "skill-matcher-v1",
            "version": "1.0.0",
            "description": "Default keyword skill matcher",
            "config": {"similarity_threshold": 0.7, "use_synonyms": True},
        },
        {
            "name": "scoring-model-default",
            "model_type": "scoring",
            "provider": "custom",
            "model_id": "scoring-model-v1",
            "version": "1.0.0",
            "description": "Default weighted scoring model",
            "config": {
                "weights": {
                    "skills": 0.4,
                    "experience": 0.3,
                    "education": 0.2,
                    "other": 0.1,
                }
            },
        },
    )
    for data in defaults:
        exists = await AIModel.find_one(
            {
                "provider": data["provider"],
                "model_id": data["model_id"],
                "version": data["version"],
            }
        )
        if exists:
            continue
        try:
            await AIModel(**data, is_active=True, created_by=None).insert()
        except DuplicateKeyError:
            continue


async def _create_first_superuser() -> None:
    if not settings.CREATE_FIRST_SUPERUSER:
        return
    if await User.find_all().limit(1).to_list():
        return

    from app.core.security import get_password_hash

    superuser = User(
        email=settings.FIRST_SUPERUSER_EMAIL.lower(),
        full_name=settings.FIRST_SUPERUSER_FULL_NAME,
        hashed_password=get_password_hash(settings.FIRST_SUPERUSER_PASSWORD),
        is_active=True,
        is_verified=True,
        is_superuser=True,
    )
    try:
        await superuser.insert()
    except DuplicateKeyError:
        superuser = await User.find_one(User.email == settings.FIRST_SUPERUSER_EMAIL.lower())
        if superuser is None:
            raise

    admin = await Actor.find_one(Actor.name == settings.ADMIN_ROLE_NAME)
    if admin and not await UserActor.find_one(
        {"user_id": superuser.id, "actor_id": admin.id}
    ):
        try:
            await UserActor(
                user_id=superuser.id,
                actor_id=admin.id,
                created_by=superuser.id,
            ).insert()
        except DuplicateKeyError:
            pass


async def _record_schema_baseline() -> None:
    version = 1
    name = "recruitment_pipeline_schema"
    checksum = hashlib.sha256(name.encode("utf-8")).hexdigest()
    existing = await DatabaseMigration.find_one(DatabaseMigration.version == version)
    if existing:
        if existing.checksum != checksum:
            raise RuntimeError(f"Database migration {version} checksum mismatch")
        return
    try:
        await DatabaseMigration(version=version, name=name, checksum=checksum).insert()
    except DuplicateKeyError:
        pass


async def _bootstrap_database() -> None:
    await _ensure_default_permissions()
    await _ensure_default_actors()
    await _ensure_default_ai_models()
    await _create_first_superuser()
    await _record_schema_baseline()


async def _run_pre_init_index_migrations(database) -> None:
    """Remove only indexes explicitly replaced by this schema revision."""
    replacements = {
        "resume_files": {"uq_resume_company_checksum"},
    }
    for collection_name, obsolete_names in replacements.items():
        existing = await database[collection_name].index_information()
        for index_name in obsolete_names.intersection(existing):
            try:
                await database[collection_name].drop_index(index_name)
                logger.info("Dropped obsolete index %s.%s", collection_name, index_name)
            except OperationFailure as exc:
                if exc.code != 27:  # IndexNotFound from a concurrent startup worker.
                    raise

    user_indexes = await database["users"].index_information()
    old_phone_index = user_indexes.get("phone_number_1")
    if old_phone_index and not old_phone_index.get("unique", False):
        try:
            await database["users"].drop_index("phone_number_1")
            logger.info("Dropped non-unique users.phone_number_1 before unique replacement")
        except OperationFailure as exc:
            if exc.code != 27:
                raise

    migration_name = "normalize_user_identity_v2"
    migration_checksum = hashlib.sha256(migration_name.encode("utf-8")).hexdigest()
    migration_id = ObjectId(migration_checksum[:24])
    migrations = database["database_migrations"]
    existing_migration = await migrations.find_one(
        {"$or": [{"_id": migration_id}, {"version": 2}]}
    )
    if existing_migration:
        if existing_migration.get("checksum") != migration_checksum:
            raise RuntimeError("Database migration 2 checksum mismatch")
        return

    users = await database["users"].find(
        {}, {"email": 1, "phone_number": 1}
    ).to_list(length=None)
    seen_emails: set[str] = set()
    seen_phones: set[str] = set()
    updates = []
    for user in users:
        email = str(user.get("email") or "").strip().lower()
        raw_phone = str(user.get("phone_number") or "").strip()
        phone = None
        if raw_phone:
            prefix = "+" if raw_phone.startswith("+") else ""
            digits = "".join(character for character in raw_phone if character.isdigit())
            phone = f"{prefix}{digits}" if digits else None
        if email:
            if email in seen_emails:
                raise RuntimeError("Cannot normalize users: duplicate case-insensitive emails exist")
            seen_emails.add(email)
        if phone:
            if phone in seen_phones:
                raise RuntimeError("Cannot create unique phone index: duplicate normalized phones exist")
            seen_phones.add(phone)
        updates.append(
            UpdateOne(
                {"_id": user["_id"]},
                {"$set": {"email": email, "phone_number": phone}},
            )
        )
    if updates:
        await database["users"].bulk_write(updates, ordered=False)
    try:
        await migrations.insert_one(
            {
                "_id": migration_id,
                "version": 2,
                "name": migration_name,
                "checksum": migration_checksum,
                "applied_at": now_utc(),
            }
        )
    except DuplicateKeyError:
        pass


async def init_db() -> bool:
    global _mongo_client
    try:
        _mongo_client = motor.motor_asyncio.AsyncIOMotorClient(
            settings.MONGODB_URL,
            maxPoolSize=settings.MONGODB_MAX_POOL_SIZE,
            minPoolSize=settings.MONGODB_MIN_POOL_SIZE,
            serverSelectionTimeoutMS=settings.MONGODB_SERVER_SELECTION_TIMEOUT,
        )
        await _mongo_client.admin.command("ping")
        database = _mongo_client[settings.MONGODB_DB_NAME]
        await _run_pre_init_index_migrations(database)
        # Index creation is part of initialization. Unique-index failures are
        # fatal so the API never runs without its data-integrity guarantees.
        await init_beanie(database=database, document_models=DOCUMENT_MODELS)
        await _bootstrap_database()
        logger.info(
            "Database initialized with %s document models in %s",
            len(DOCUMENT_MODELS),
            settings.MONGODB_DB_NAME,
        )
        return True
    except Exception:
        logger.exception("Database initialization failed")
        if _mongo_client:
            _mongo_client.close()
            _mongo_client = None
        raise


async def close_db() -> None:
    global _mongo_client
    if _mongo_client:
        _mongo_client.close()
        _mongo_client = None


async def check_connection() -> bool:
    client = None
    try:
        if _mongo_client is not None:
            await _mongo_client.admin.command("ping")
            return True
        client = motor.motor_asyncio.AsyncIOMotorClient(
            settings.MONGODB_URL,
            serverSelectionTimeoutMS=settings.MONGODB_SERVER_SELECTION_TIMEOUT,
        )
        await client.admin.command("ping")
        return True
    except Exception:
        logger.exception("MongoDB connection check failed")
        return False
    finally:
        if client:
            client.close()


async def get_database_info() -> dict:
    client = None
    try:
        client = motor.motor_asyncio.AsyncIOMotorClient(settings.MONGODB_URL)
        database = client[settings.MONGODB_DB_NAME]
        stats = await database.command("dbstats")
        collections = await database.list_collection_names()
        index_info = {}
        for collection_name in collections:
            indexes = await database[collection_name].index_information()
            index_info[collection_name] = {
                "count": len(indexes),
                "indexes": list(indexes.keys()),
            }
        return {
            "database_name": settings.MONGODB_DB_NAME,
            "collections": collections,
            "collection_count": len(collections),
            "database_size": stats.get("dataSize", 0),
            "index_size": stats.get("indexSize", 0),
            "total_size": stats.get("totalSize", 0),
            "index_info": index_info,
            "status": "connected",
        }
    except Exception as exc:
        logger.exception("Could not read database information")
        return {"database_name": settings.MONGODB_DB_NAME, "status": "error", "error": str(exc)}
    finally:
        if client:
            client.close()


async def cleanup_expired_data() -> int:
    # Email OTPs are normally removed by the MongoDB TTL index. This remains a
    # maintenance fallback for environments where TTL cleanup is delayed.
    from app.utils.time import now_utc

    try:
        result = await EmailOTP.find({"expires_at": {"$lt": now_utc()}}).delete()
        return int(getattr(result, "deleted_count", result or 0))
    except Exception:
        logger.exception("Expired-data cleanup failed")
        return 0
