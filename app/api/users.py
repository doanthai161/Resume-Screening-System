import os
import re
from fastapi import APIRouter, HTTPException
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from bson import ObjectId
from typing import Optional
from pydantic import BaseModel, EmailStr
from app.dependencies.error_code import ErrorCode
from app.models.user import User
from app.repositories.config import settings
from app.utils.password import verify_password
from app.schemas.user import (
    UserResponse,
    UserCreate,
    UserListRespponse,
    UserUpdate,
)
# from app.core.