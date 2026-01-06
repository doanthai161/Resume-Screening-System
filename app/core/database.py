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
from pymongo.errors import DuplicateKeyError
import os
import datetime
import logging
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
]

async def _ensure_default_permissions() -> None:
    default_actions = ("view", "create", "edit", "delete")

    existing_perms_cursor = Permission.find_all()
    existing_perms_set = {perm.name for perm in await existing_perms_cursor.to_list()}

    perms_to_create = []

    for model in DOCUMENT_MODELS:
        model_settings = getattr(model, "Settings", None)
        model_name = getattr(model_settings, "name", model.__name__)

        for action in default_actions:
            perm_name = f"{model_name}:{action}"

            if perm_name not in existing_perms_set:
                perms_to_create.append(Permission(name=perm_name, mo_ta=f"permission {action} for {model_name}"))
                existing_perms_set.add(perm_name)
    
    if perms_to_create:
        try:
            await Permission.insert_many(perms_to_create)
            logger.info(f"created {len(perms_to_create)} default permissions.")
        except DuplicateKeyError:
            logger.info("some default permissions already exist, skipping creation.")
    else:
        logger.info("no default permissions to create.")


async def _ensure_default_actors() -> None:
    admin_role_name = settings.ADMIN_ROLE_NAME
    admin_role = await Actor.find_one(Actor.name == admin_role_name)

    if not admin_role:
        try:
            logger.info(f"Creating default actor: {admin_role_name}")
            admin_role = Actor(
                name=admin_role_name, 
                description="permission for system administrator.",
                is_default=True
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

    staff_role_name = settings.DEFAULT_ROLE_NAME
    staff_role = await Actor.find_one(Actor.name == staff_role_name)

    if not staff_role:
        try:
            logger.info(f"Creating default actor: '{staff_role_name}'.")
            staff_role = Actor(
                name=staff_role_name,
                description=f"permission for '{staff_role_name}'.",
                is_default=True
            )
            await staff_role.insert()
        except DuplicateKeyError:
            logger.info(f"Actor '{staff_role_name}' already exists, fetching...")
            staff_role = await Actor.find_one(Actor.name == staff_role_name)
    
    if staff_role:
        view_permissions = await Permission.find({"name": {"$regex": r":view$"}}).to_list()
        target_staff_perm_ids = {perm.id for perm in view_permissions}

        current_staff_links = await ActorPermission.find(
            ActorPermission.actor_id == staff_role.id
        ).to_list()
        current_staff_perm_ids = {link.permission_id for link in current_staff_links}

        missing_staff_perm_ids = target_staff_perm_ids - current_staff_perm_ids

        if missing_staff_perm_ids:
            links_to_create = [
                ActorPermission(actor_id=staff_role.id, permission_id=perm_id)
                for perm_id in missing_staff_perm_ids
            ]
            await ActorPermission.insert_many(links_to_create)
            logger.info(f"Assigned {len(links_to_create)} new view permissions to actor '{staff_role_name}'.")
        else:
            logger.info(f"Actor '{staff_role_name}' already has all view permissions.")

async def init_db():
    client = motor.motor_asyncio.AsyncIOMotorClient(settings.mongo_uri)
    await init_beanie(
        database=client.get_default_database(),
        document_models=DOCUMENT_MODELS,
    )
    logger.info('Connection to MongoDB established and Beanie initialized.')

    if not os.path.exists(INIT_FILE_PATH):
        logger.info(f'File {INIT_FILE_PATH} not found. Starting default data initialization.')
        await _ensure_default_permissions()
        await _ensure_default_actors()

        try:
            with open(INIT_FILE_PATH, 'w', encoding='utf-8') as f:
                f.write(f"Default data initialized on: {datetime.datetime.now(datetime.UTC)}")
            logger.info(f'created file {INIT_FILE_PATH}. Will not reinitialize default data on next startup.')
        except IOError as e:
            logger.error(f'cant created {INIT_FILE_PATH}. data may be reinitialized on next startup. error: {e}')

        logger.info('Default data initialization completed.')
    else:
        logger.info(f'File {INIT_FILE_PATH} found. Skipping default data initialization.')
    