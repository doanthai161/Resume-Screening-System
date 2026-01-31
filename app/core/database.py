import motor.motor_asyncio
from beanie import init_beanie
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

logger = logging.getLogger(__name__)
INIT_FILE_PATH = ".initdb"

# ==================== CẬP NHẬT DANH SÁCH MODELS ====================
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
    # Thêm các model mới
    ResumeFile,
    ScreeningResult,
    AIModel,
    JobApplication,  # Nếu có
]

# ==================== TẠO MODEL TỪ DICTIONARY ====================
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
    """Tạo các permissions mặc định cho tất cả models"""
    default_actions = ("view", "create", "edit", "delete", "list")

    existing_perms_cursor = Permission.find_all()
    existing_perms_set = {perm.name for perm in await existing_perms_cursor.to_list()}

    perms_to_create = []

    for model_name, model_class in MODEL_NAMES.items():
        # Lấy collection name từ Settings hoặc dùng model name
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
    
    # Thêm permissions đặc biệt cho Resume Screening
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
    """Tạo các actors mặc định và gán permissions"""
    # 1. Admin Actor
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
        # Lấy tất cả permissions
        all_permissions = await Permission.find_all().to_list()
        target_admin_perm_ids = {perm.id for perm in all_permissions}
            
        # Lấy permissions hiện tại của admin
        current_admin_links = await ActorPermission.find(
            ActorPermission.actor_id == admin_role.id
        ).to_list()
        current_admin_perm_ids = {link.permission_id for link in current_admin_links}

        # Tìm permissions thiếu
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

    # 2. Recruiter Actor
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
        # Define recruiter permissions pattern
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
        
        # Get permissions matching patterns
        recruiter_permissions = []
        all_permissions = await Permission.find_all().to_list()
        
        for perm in all_permissions:
            for pattern in recruiter_permission_patterns:
                import re
                if re.match(pattern, perm.name):
                    recruiter_permissions.append(perm)
                    break
        
        target_recruiter_perm_ids = {perm.id for perm in recruiter_permissions}
        
        # Get current recruiter permissions
        current_recruiter_links = await ActorPermission.find(
            ActorPermission.actor_id == recruiter_role.id
        ).to_list()
        current_recruiter_perm_ids = {link.permission_id for link in current_recruiter_links}

        # Find missing permissions
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

    # 3. Candidate Actor
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
        # Candidate permissions (limited)
        candidate_permission_patterns = [
            r"^users:view$",
            r"^users:edit$",  # Can edit own profile
            r"^job_requirements:view$",
            r"^job_requirements:list$",
            r"^resume_files:upload$",
            r"^resume_files:view$",
            r"^resume_files:edit$",
            r"^resume_files:delete$",
            r"^screening_results:view$",
            r"^candidate_evaluations:view$",
        ]
        
        # Get permissions matching patterns
        candidate_permissions = []
        all_permissions = await Permission.find_all().to_list()
        
        for perm in all_permissions:
            for pattern in candidate_permission_patterns:
                import re
                if re.match(pattern, perm.name):
                    candidate_permissions.append(perm)
                    break
        
        target_candidate_perm_ids = {perm.id for perm in candidate_permissions}
        
        # Get current candidate permissions
        current_candidate_links = await ActorPermission.find(
            ActorPermission.actor_id == candidate_role.id
        ).to_list()
        current_candidate_perm_ids = {link.permission_id for link in current_candidate_links}

        # Find missing permissions
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
    """Tạo các AI models mặc định nếu cần"""
    try:
        # Kiểm tra xem đã có AI models chưa
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
                    created_by=None,  # System
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
    """Tạo super user đầu tiên nếu chưa có"""
    if not settings.CREATE_FIRST_SUPERUSER:
        return
    
    # Kiểm tra xem đã có user nào chưa
    existing_users = await User.find_all().limit(1).to_list()
    if existing_users:
        logger.info("Users already exist, skipping first superuser creation.")
        return
    
    try:
        from app.core.security import get_password_hash
        
        # Tạo superuser
        superuser = User(
            email=settings.FIRST_SUPERUSER_EMAIL,
            full_name=settings.FIRST_SUPERUSER_FULL_NAME,
            hashed_password=get_password_hash(settings.FIRST_SUPERUSER_PASSWORD),
            is_active=True,
        )
        await superuser.insert()
        logger.info(f"Created first superuser: {settings.FIRST_SUPERUSER_EMAIL}")
        
        # Tìm admin actor
        admin_actor = await Actor.find_one(Actor.name == settings.ADMIN_ROLE_NAME)
        if admin_actor:
            # Gán admin role cho superuser
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
    client = motor.motor_asyncio.AsyncIOMotorClient(
        settings.MONGODB_URL,
        maxPoolSize=settings.MONGODB_MAX_POOL_SIZE,
        minPoolSize=settings.MONGODB_MIN_POOL_SIZE,
        serverSelectionTimeoutMS=settings.MONGODB_SERVER_SELECTION_TIMEOUT
    )
    
    # Lấy database
    database = client[settings.MONGODB_DB_NAME]
    
    # Khởi tạo Beanie
    await init_beanie(
        database=database,
        document_models=DOCUMENT_MODELS,
    )
    
    logger.info(f'Connection to MongoDB established: {settings.MONGODB_DB_NAME}')
    logger.info(f'Beanie initialized with {len(DOCUMENT_MODELS)} models')

    # Kiểm tra và seed data nếu cần
    if not os.path.exists(INIT_FILE_PATH):
        logger.info(f'File {INIT_FILE_PATH} not found. Starting default data initialization.')
        
        try:
            # Tạo các permissions mặc định
            await _ensure_default_permissions()
            
            # Tạo các actors mặc định và gán permissions
            await _ensure_default_actors()
            
            # Tạo AI models mặc định
            await _ensure_default_ai_models()
            
            # Tạo superuser đầu tiên
            await _create_first_superuser()
            
            # Tạo file init để đánh dấu đã seed data
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

async def close_db():
    """Đóng database connection"""
    # Motor client tự động quản lý connections
    logger.info("Database connections will be closed automatically.")

async def create_indexes():
    """Tạo indexes cho tất cả collections"""
    logger.info("Creating indexes for all collections...")
    
    for model in DOCUMENT_MODELS:
        try:
            # Beanie tự động tạo indexes từ class Settings
            await model.get_motor_collection().create_indexes(
                model.Settings.indexes if hasattr(model.Settings, 'indexes') else []
            )
            logger.debug(f"Created indexes for {model.__name__}")
        except Exception as e:
            logger.error(f"Error creating indexes for {model.__name__}: {e}")
    
    logger.info("Index creation completed.")

async def check_connection() -> bool:
    try:
        client = motor.motor_asyncio.AsyncIOMotorClient(
            settings.MONGODB_URL,
            serverSelectionTimeoutMS=5000
        )
        await client.admin.command('ping')
        return True
    except Exception as e:
        logger.error(f"Database connection check failed: {e}")
        return False
    finally:
        if 'client' in locals():
            client.close()