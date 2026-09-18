from typing import Optional
from bson import ObjectId
from app.models.user import User
from app.models.actor import Actor
from app.models.user_actor import UserActor

class UserActorRepository:
    @staticmethod
    async def get_user(user_id: str) -> Optional[User]:
        return await User.find_one({"_id": ObjectId(user_id), "is_active": True})

    @staticmethod
    async def get_actor(actor_id: str) -> Optional[Actor]:
        return await Actor.find_one({"_id": ObjectId(actor_id), "is_active": True})

    @staticmethod
    async def get_user_actor_by_user(user_id: str) -> Optional[UserActor]:
        return await UserActor.find_one({"user_id": ObjectId(user_id)})

    @staticmethod
    async def get_user_actor_by_id(user_actor_id: str) -> Optional[UserActor]:
        return await UserActor.find_one({"_id": ObjectId(user_actor_id)})

    @staticmethod
    async def create_user_actor(user_id: str, actor_id: str, created_by: str) -> UserActor:
        user_actor = UserActor(
            user_id=ObjectId(user_id),
            actor_id=ObjectId(actor_id),
            created_by=ObjectId(created_by)
        )
        await user_actor.insert()
        return user_actor

    @staticmethod
    async def save_user_actor(user_actor: UserActor) -> None:
        await user_actor.save()

    @staticmethod
    async def delete_user_actor(user_actor: UserActor) -> None:
        await user_actor.delete()
