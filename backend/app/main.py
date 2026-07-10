import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.routers import chat, health

logging.basicConfig(level=logging.INFO)

settings = get_settings()

app = FastAPI(title="Chatbot Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(chat.router)


@app.get("/")
async def root():
    return {"message": "Welcome to the Chatbot Backend!"}
