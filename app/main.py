from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.core.database import init_db
import uvicorn

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def hello():
    return {"msg": "Resume Screening API is running!"}

if __name__ == "__main__":
    uvicorn.run("main:app", reload=True)