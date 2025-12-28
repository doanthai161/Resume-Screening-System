from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.core.database import init_db
import uvicorn
from app.dependencies.versions import api_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(lifespan=lifespan)
app.include_router(api_router, prefix="/api")

@app.get("/")
async def hello():
    return {"msg": "Resume Screening API is running!"}

if __name__ == "__main__":
    uvicorn.run("main:app", reload=True)