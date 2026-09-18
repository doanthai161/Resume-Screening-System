from typing import List, Optional
from bson import ObjectId
from app.models.actor import Actor
from app.schemas.actor import ActorCreate, ActorUpdate

class ActorRepository:
    @staticmethod
    async def get_by_name(name: str) -> Optional[Actor]:
        return await Actor.find_one({'name': name, 'is_active': True})

    @staticmethod
    async def get_by_id(actor_id: str) -> Optional[Actor]:
        return await Actor.find_one({"_id": ObjectId(actor_id), "is_active": True})

    @staticmethod
    async def get_all() -> List[Actor]:
        return await Actor.find({"is_active": True}).to_list()

    @staticmethod
    async def create(data: ActorCreate) -> Actor:
        actor = Actor(
            name=data.name,
            is_active=True,
            description=data.description,
        )
        await actor.insert()
        return actor

    @staticmethod
    async def update(actor: Actor, data: ActorUpdate) -> Actor:
        if data.name is not None:
            actor.name = data.name
        if data.description is not None:
            actor.description = data.description
        await actor.save()
        return actor

    @staticmethod
    async def soft_delete(actor: Actor) -> None:
        actor.is_active = False
        await actor.save()
