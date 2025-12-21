import motor.motor_asyncio
from beanie import init_beanie
from app.repositories.config import settings

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

async def init_db():
    client = motor.motor_asyncio.AsyncIOMotorClient(settings.mongo_uri)
    await init_beanie(
        database= client.get_default_database(),
        document_models=[
            JobRequirement,
            User,
            Company,
            UserCompany,
            ActorPermission,
            Permission,
            Actor,
            UserActor,
            CompanyBranch,
            CandidateEvaluation,
            
        ],
    )