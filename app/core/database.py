import motor.motor_asyncio
from beanie import init_beanie, Document
from app.core.config import settings

from app.models.job_requirement import JobRequirement
from app.models.user import User
from app.models.company import Company
from app.models.user_company import UserCompany
from app.models.actor_permission import ActorPermission
from app.models.permission import Permission
from app.models.actor import Actor
from app.models.user_actor import UserActor
from app.models.company_branch import CompanyBranch
from app.models.candidate_evaluation import CandidateEvaluation
from app.models.email_otp import EmailOTP
from app.models.resume_file import ResumeFile, ParsedResumeData
from app.models.screening_result import ScreeningResult
from app.models.ai_model import AIModel
from app.models.job_application import JobApplication

from pymongo.errors import DuplicateKeyError
import os
import datetime
import logging
from typing import Type, List

logger = logging.getLogger(__name__)
INIT_FILE_PATH = ".initdb"

DOCUMENT_MODELS = [
    User,
    Company,
    UserCompany,
    ActorPermission,
    Permission,
    Actor,
    UserActor,
    CompanyBranch,
    JobRequirement,
    CandidateEvaluation,
    EmailOTP,
    ResumeFile,
    ScreeningResult,
    AIModel,
    JobApplication,
]

MODEL_NAMES = {
    "User": User,
    "Company": Company,
    "UserCompany": UserCompany,
    "ActorPermission": ActorPermission,
    "Permission": Permission,
    "Actor": Actor,
    "UserActor": UserActor,
    "CompanyBranch": CompanyBranch,
    "JobRequirement": JobRequirement,
    "CandidateEvaluation": CandidateEvaluation,
    "EmailOTP": EmailOTP,
    "ResumeFile": ResumeFile,
    "ScreeningResult": ScreeningResult,
    "AIModel": AIModel,
    "JobApplication": JobApplication,
}

async def _ensure_default_permissions() -> None:
    default_actions = ("view", "create", "edit", "delete", "list")

    existing_perms_cursor = Permission.find_all()
    existing_perms_set = {perm.name for perm in await existing_perms_cursor.to_list()}

    perms_to_create = []

    for model_name, model_class in MODEL_NAMES.items():
        model_settings = getattr(model_class, "Settings", None)
        if model_settings and hasattr(model_settings, "name"):
            collection_name = model_settings.name
        else:
            collection_name = model_name.lower() + "s"
        
        for action in default_actions:
            perm_name = f"{collection_name}:{action}"
            
            if perm_name not in existing_perms_set:
                perms_to_create.append(
                    Permission(
                        name=perm_name, 
                        description=f"Permission to {action} {collection_name}",
                        is_active=True
                    )
                )
                existing_perms_set.add(perm_name)
    
    special_permissions = [
        ("resume_files:upload", "Permission to upload resume files"),
        ("resume_files:parse", "Permission to parse resume files"),
        ("resume_files:screen", "Permission to screen resumes"),
        ("screening_results:evaluate", "Permission to evaluate screening results"),
        ("ai_models:train", "Permission to train AI models"),
        ("ai_models:deploy", "Permission to deploy AI models"),
        ("jobs:match", "Permission to match jobs with resumes"),
        ("jobs:bulk_screen", "Permission to bulk screen resumes"),
    ]
    
    for perm_name, description in special_permissions:
        if perm_name not in existing_perms_set:
            perms_to_create.append(
                Permission(
                    name=perm_name,
                    description=description,
                    is_active=True
                )
            )
            existing_perms_set.add(perm_name)
    
    if perms_to_create:
        try:
            await Permission.insert_many(perms_to_create)
            logger.info(f"Created {len(perms_to_create)} default permissions.")
        except DuplicateKeyError:
            logger.info("Some default permissions already exist, skipping creation.")
    else:
        logger.info("No default permissions to create.")


async def _ensure_default_actors() -> None:
    admin_role_name = settings.ADMIN_ROLE_NAME
    admin_role = await Actor.find_one(Actor.name == admin_role_name)

    if not admin_role:
        try:
            logger.info(f"Creating default actor: {admin_role_name}")
            admin_role = Actor(
                name=admin_role_name, 
                description="Full system administrator with all permissions",
                is_default=True,
                is_system=True
            )
            await admin_role.insert()
        except DuplicateKeyError:
            logger.info(f"Actor '{admin_role_name}' already exists, fetching...")
            admin_role = await Actor.find_one(Actor.name == admin_role_name)

    if admin_role:
        all_permissions = await Permission.find_all().to_list()
        target_admin_perm_ids = {perm.id for perm in all_permissions}
            
        current_admin_links = await ActorPermission.find(
            ActorPermission.actor_id == admin_role.id
        ).to_list()
        current_admin_perm_ids = {link.permission_id for link in current_admin_links}

        missing_admin_perm_ids = target_admin_perm_ids - current_admin_perm_ids
            
        if missing_admin_perm_ids:
            links_to_create = [
                ActorPermission(actor_id=admin_role.id, permission_id=perm_id)
                for perm_id in missing_admin_perm_ids
            ]
            await ActorPermission.insert_many(links_to_create)
            logger.info(f"Assigned {len(links_to_create)} new permissions to actor '{admin_role_name}'.")
        else:
            logger.info(f"Actor '{admin_role_name}' already has all permissions.")

    recruiter_role_name = settings.RECRUITER_ROLE_NAME
    recruiter_role = await Actor.find_one(Actor.name == recruiter_role_name)

    if not recruiter_role:
        try:
            logger.info(f"Creating default actor: '{recruiter_role_name}'")
            recruiter_role = Actor(
                name=recruiter_role_name,
                description="Recruiter with permissions to manage jobs and screen resumes",
                is_default=True
            )
            await recruiter_role.insert()
        except DuplicateKeyError:
            logger.info(f"Actor '{recruiter_role_name}' already exists, fetching...")
            recruiter_role = await Actor.find_one(Actor.name == recruiter_role_name)
    
    if recruiter_role:
        recruiter_permission_patterns = [
            r"^users:view$",
            r"^users:list$",
            r"^companies:view$",
            r"^companies:list$",
            r"^company_branches:view$",
            r"^company_branches:list$",
            r"^job_requirements:.*$",  # All job permissions
            r"^resume_files:.*$",      # All resume permissions
            r"^screening_results:.*$", # All screening permissions
            r"^candidate_evaluations:.*$", # All candidate evaluation permissions
            r"^jobs:.*$",              # All job-related permissions
        ]
        
        recruiter_permissions = []
        all_permissions = await Permission.find_all().to_list()
        
        for perm in all_permissions:
            for pattern in recruiter_permission_patterns:
                import re
                if re.match(pattern, perm.name):
                    recruiter_permissions.append(perm)
                    break
        
        target_recruiter_perm_ids = {perm.id for perm in recruiter_permissions}
        
        current_recruiter_links = await ActorPermission.find(
            ActorPermission.actor_id == recruiter_role.id
        ).to_list()
        current_recruiter_perm_ids = {link.permission_id for link in current_recruiter_links}

        missing_recruiter_perm_ids = target_recruiter_perm_ids - current_recruiter_perm_ids

        if missing_recruiter_perm_ids:
            links_to_create = [
                ActorPermission(actor_id=recruiter_role.id, permission_id=perm_id)
                for perm_id in missing_recruiter_perm_ids
            ]
            await ActorPermission.insert_many(links_to_create)
            logger.info(f"Assigned {len(links_to_create)} new permissions to actor '{recruiter_role_name}'.")
        else:
            logger.info(f"Actor '{recruiter_role_name}' already has all recruiter permissions.")

    candidate_role_name = settings.CANDIDATE_ROLE_NAME
    candidate_role = await Actor.find_one(Actor.name == candidate_role_name)

    if not candidate_role:
        try:
            logger.info(f"Creating default actor: '{candidate_role_name}'")
            candidate_role = Actor(
                name=candidate_role_name,
                description="Candidate with permissions to view and apply for jobs",
                is_default=True
            )
            await candidate_role.insert()
        except DuplicateKeyError:
            logger.info(f"Actor '{candidate_role_name}' already exists, fetching...")
            candidate_role = await Actor.find_one(Actor.name == candidate_role_name)
    
    if candidate_role:
        candidate_permission_patterns = [
            r"^users:view$",
            r"^users:edit$",
            r"^job_requirements:view$",
            r"^job_requirements:list$",
            r"^resume_files:upload$",
            r"^resume_files:view$",
            r"^resume_files:edit$",
            r"^resume_files:delete$",
            r"^screening_results:view$",
            r"^candidate_evaluations:view$",
        ]
        
        candidate_permissions = []
        all_permissions = await Permission.find_all().to_list()
        
        for perm in all_permissions:
            for pattern in candidate_permission_patterns:
                import re
                if re.match(pattern, perm.name):
                    candidate_permissions.append(perm)
                    break
        
        target_candidate_perm_ids = {perm.id for perm in candidate_permissions}
        
        current_candidate_links = await ActorPermission.find(
            ActorPermission.actor_id == candidate_role.id
        ).to_list()
        current_candidate_perm_ids = {link.permission_id for link in current_candidate_links}

        missing_candidate_perm_ids = target_candidate_perm_ids - current_candidate_perm_ids

        if missing_candidate_perm_ids:
            links_to_create = [
                ActorPermission(actor_id=candidate_role.id, permission_id=perm_id)
                for perm_id in missing_candidate_perm_ids
            ]
            await ActorPermission.insert_many(links_to_create)
            logger.info(f"Assigned {len(links_to_create)} new permissions to actor '{candidate_role_name}'.")
        else:
            logger.info(f"Actor '{candidate_role_name}' already has all candidate permissions.")

async def _ensure_default_ai_models() -> None:
    try:
        existing_models = await AIModel.find_all().to_list()
        
        if not existing_models:
            logger.info("Creating default AI models...")
            
            default_models = [
                AIModel(
                    name="resume-parser-default",
                    model_type="resume_parser",
                    provider="custom",
                    model_id="resume-parser-v1",
                    version="1.0.0",
                    description="Default resume parser using rule-based extraction",
                    is_active=True,
                    config={
                        "parser_type": "rule_based",
                        "supported_formats": ["pdf", "docx", "doc"],
                        "extraction_fields": ["personal_info", "skills", "experience", "education"]
                    },
                    created_by=None,
                ),
                AIModel(
                    name="skill-matcher-default",
                    model_type="skill_matcher",
                    provider="custom",
                    model_id="skill-matcher-v1",
                    version="1.0.0",
                    description="Default skill matching algorithm using keyword matching",
                    is_active=True,
                    config={
                        "matching_algorithm": "keyword_similarity",
                        "similarity_threshold": 0.7,
                        "use_synonyms": True
                    },
                    created_by=None,
                ),
                AIModel(
                    name="scoring-model-default",
                    model_type="scoring",
                    provider="custom",
                    model_id="scoring-model-v1",
                    version="1.0.0",
                    description="Default scoring model with weighted criteria",
                    is_active=True,
                    config={
                        "weights": {
                            "skills": 0.4,
                            "experience": 0.3,
                            "education": 0.2,
                            "other": 0.1
                        },
                        "normalization": "min_max"
                    },
                    created_by=None,
                )
            ]
            
            await AIModel.insert_many(default_models)
            logger.info(f"Created {len(default_models)} default AI models.")
    except Exception as e:
        logger.error(f"Error creating default AI models: {e}")

async def _create_first_superuser() -> None:
    if not settings.CREATE_FIRST_SUPERUSER:
        return
    
    existing_users = await User.find_all().limit(1).to_list()
    if existing_users:
        logger.info("Users already exist, skipping first superuser creation.")
        return
    
    try:
        from app.core.security import get_password_hash
        
        superuser = User(
            email=settings.FIRST_SUPERUSER_EMAIL,
            full_name=settings.FIRST_SUPERUSER_FULL_NAME,
            hashed_password=get_password_hash(settings.FIRST_SUPERUSER_PASSWORD),
            is_active=True,
        )
        await superuser.insert()
        logger.info(f"Created first superuser: {settings.FIRST_SUPERUSER_EMAIL}")
        
        admin_actor = await Actor.find_one(Actor.name == settings.ADMIN_ROLE_NAME)
        if admin_actor:
            user_actor = UserActor(
                user_id=superuser.id,
                actor_id=admin_actor.id,
                created_by=superuser.id,
            )
            await user_actor.insert()
            logger.info(f"Assigned admin role to {settings.FIRST_SUPERUSER_EMAIL}")
            
    except DuplicateKeyError:
        logger.info(f"Superuser {settings.FIRST_SUPERUSER_EMAIL} already exists.")
    except Exception as e:
        logger.error(f"Error creating first superuser: {e}")

async def init_db():
    try:
        logger.info(f"Connecting to MongoDB...")
        
        client = motor.motor_asyncio.AsyncIOMotorClient(
            settings.MONGODB_URL,
            maxPoolSize=settings.MONGODB_MAX_POOL_SIZE,
            minPoolSize=settings.MONGODB_MIN_POOL_SIZE,
            serverSelectionTimeoutMS=settings.MONGODB_SERVER_SELECTION_TIMEOUT
        )
        
        await client.admin.command('ping')
        logger.info("✓ MongoDB connection successful")
        
        database = client[settings.MONGODB_DB_NAME]
        
        try:
            await init_beanie(
                database=database,
                document_models=DOCUMENT_MODELS,
            )
            logger.info(f'Beanie initialized with {len(DOCUMENT_MODELS)} models in database: {settings.MONGODB_DB_NAME}')
        except Exception as beanie_error:
            logger.warning(f"Beanie initialization failed: {beanie_error}")
            logger.warning("Continuing without Beanie - some ODM features may not work")
            for model in DOCUMENT_MODELS:
                try:
                    model._database = database
                    logger.debug(f"Assigned database to {model.__name__}")
                except:
                    pass
        
        if not os.path.exists(INIT_FILE_PATH):
            logger.info(f'File {INIT_FILE_PATH} not found. Starting default data initialization.')
            
            try:
                await _ensure_default_permissions()
                await _ensure_default_actors()
                await _ensure_default_ai_models()
                await _create_first_superuser()
                await create_indexes()
                
                with open(INIT_FILE_PATH, 'w', encoding='utf-8') as f:
                    f.write(f"Default data initialized on: {datetime.datetime.now(datetime.UTC)}")
                    
                logger.info(f'Created file {INIT_FILE_PATH}. Will not reinitialize default data on next startup.')
                logger.info('Default data initialization completed.')
                
            except IOError as e:
                logger.error(f'Cannot create {INIT_FILE_PATH}. Data may be reinitialized on next startup. Error: {e}')
            except Exception as e:
                logger.error(f'Error during data initialization: {e}')
                raise
        else:
            logger.info(f'File {INIT_FILE_PATH} found. Skipping default data initialization.')
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Failed to initialize database: {e}")
        return False

async def close_db():
    logger.info("Database connections will be closed automatically.")

async def create_indexes():
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.core.config import settings
    
    logger.info("Creating/verifying database indexes...")
    
    client = None
    try:
        client = AsyncIOMotorClient(settings.MONGODB_URI)
        db = client[settings.MONGODB_DB_NAME]
        
        await db.users.create_index([("email", 1)], unique=True, name="idx_users_email")
        await db.users.create_index([("full_name", 1)], name="idx_users_full_name")
        await db.users.create_index([("phone_number", 1)], unique=True, sparse=True, name="idx_users_phone")
        
        await db.companies.create_index([("user_id", 1)], name="idx_companies_user_id")
        await db.companies.create_index([("company_code", 1)], unique=True, name="idx_companies_company_code")
        await db.companies.create_index([("email", 1)], name="idx_companies_email")
        await db.companies.create_index([("is_active", 1)], name="idx_companies_active")
        await db.companies.create_index([("name", 1)], name="idx_companies_name")
        
        await db.permissions.create_index([("name", 1)], unique=True, name="idx_permissions_name")
        await db.permissions.create_index([("is_active", 1)], name="idx_permissions_active")
        
        await db.actors.create_index([("name", 1)], name="idx_actors_name")
        await db.actors.create_index([("created_at", -1)], name="idx_actors_created_at_desc")
        await db.actors.create_index([("is_active", 1)], name="idx_actors_active")
        
        await db.email_otps.create_index([("expires_at", 1)], expireAfterSeconds=0, name="ttl_index")
        await db.email_otps.create_index([("email", 1), ("otp_type", 1)], name="email_otp_type_idx")
        await db.email_otps.create_index([("email", 1), ("otp_type", 1), ("is_used", 1), ("expires_at", 1)], 
                                         name="active_otp_idx")
        await db.email_otps.create_index([("is_used", 1)], name="idx_otp_is_used")
        
        logger.info("All indexes created successfully")
        
    except Exception as e:
        logger.error(f"Error creating indexes: {e}")
        raise
    finally:
        if client:
            client.close()

async def check_connection() -> bool:
    try:
        client = motor.motor_asyncio.AsyncIOMotorClient(
            settings.MONGODB_URL,
            serverSelectionTimeoutMS=5000
        )
        await client.admin.command('ping')
        logger.info("MongoDB connection successful")
        return True
    except Exception as e:
        logger.error(f"MongoDB connection check failed: {e}")
        return False
    finally:
        if 'client' in locals():
            client.close()

async def get_database_info() -> dict:
    try:
        client = motor.motor_asyncio.AsyncIOMotorClient(settings.MONGODB_URL)
        db = client[settings.MONGODB_DB_NAME]
        
        db_stats = await db.command("dbstats")
        
        collections = await db.list_collection_names()
        index_info = {}
        for collection_name in collections:
            try:
                collection = db[collection_name]
                indexes = await collection.index_information()
                index_info[collection_name] = {
                    "count": len(indexes),
                    "indexes": list(indexes.keys())
                }
            except Exception as e:
                logger.warning(f"Cannot get indexes for {collection_name}: {e}")
        
        client.close()
        
        return {
            "database_name": settings.MONGODB_DB_NAME,
            "collections": collections,
            "collection_count": len(collections),
            "database_size": db_stats.get("dataSize", 0),
            "index_size": db_stats.get("indexSize", 0),
            "total_size": db_stats.get("totalSize", 0),
            "index_info": index_info,
            "status": "connected"
        }
    except Exception as e:
        logger.error(f"Error getting database info: {e}")
        return {
            "database_name": settings.MONGODB_DB_NAME,
            "status": "error",
            "error": str(e)
        }

async def cleanup_expired_data():
    try:
        from datetime import datetime, timezone
        
        expired_otp_count = await EmailOTP.find({
            "expires_at": {"$lt": datetime.now(timezone.utc)}
        }).delete()
        
        if expired_otp_count > 0:
            logger.info(f"Cleaned up {expired_otp_count} expired OTPs")
        
        return expired_otp_count
    except Exception as e:
        logger.error(f"Error cleaning up expired data: {e}")
        return 0