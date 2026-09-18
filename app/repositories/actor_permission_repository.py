from typing import List, Dict, Any, Optional
from bson import ObjectId
from app.models.actor import Actor
from app.models.permission import Permission
from app.models.actor_permission import ActorPermission
from app.utils.time import now_utc

class ActorPermissionRepository:
    @staticmethod
    async def get_actor(actor_id: str) -> Optional[Actor]:
        return await Actor.find_one({"_id": ObjectId(actor_id), "is_active": True})

    @staticmethod
    async def get_permissions(permission_ids: List[str]) -> List[Permission]:
        perm_oids = [ObjectId(pid) for pid in permission_ids]
        return await Permission.find(
            {"_id": {"$in": perm_oids}, "is_active": True}
        ).to_list()

    @staticmethod
    async def get_existing_link(actor_id: ObjectId, permission_id: ObjectId) -> Optional[ActorPermission]:
        return await ActorPermission.find_one({
            "actor_id": actor_id,
            "permission_id": permission_id
        })

    @staticmethod
    async def insert_links(links: List[ActorPermission]) -> None:
        if links:
            await ActorPermission.insert_many(links)

    @staticmethod
    async def delete_links(actor_id: ObjectId, permission_ids: List[str]) -> None:
        perm_oids = [ObjectId(pid) for pid in permission_ids]
        await ActorPermission.find({
            "actor_id": actor_id,
            "permission_id": {"$in": perm_oids}
        }).delete()

    @staticmethod
    async def get_actor_with_permissions(actor_id: str) -> Optional[Dict[str, Any]]:
        pipeline = [
            {
                "$match": {
                    "_id": ObjectId(actor_id),
                    "is_active": True
                }
            },
            {
                "$lookup": {
                    "from": "actor_permissions",
                    "localField": "_id",
                    "foreignField": "actor_id",
                    "as": "actor_permissions"
                }
            },
            {
                "$lookup": {
                    "from": "permissions",
                    "let": {
                        "permission_ids": "$actor_permissions.permission_id"
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$in": ["$_id", "$$permission_ids"]},
                                        {"$eq": ["$is_active", True]}
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "permissions"
                }
            },
            {
                "$project": {
                    "_id": 1,
                    "name": 1,
                    "description": 1,
                    "permissions": {
                        "$map": {
                            "input": "$permissions",
                            "as": "perm",
                            "in": {
                                "id": {"$toString": "$$perm._id"},
                                "name": "$$perm.name",
                                "description": "$$perm.description",
                                "is_active": "$$perm.is_active"
                            }
                        }
                    }
                }
            }
        ]

        try:
            collection = Actor.get_motor_collection()
        except AttributeError:
            collection = Actor.get_pymongo_collection()

        cursor = collection.aggregate(pipeline)
        result = await cursor.to_list(length=1)
        if result:
            return result[0]
        return None
