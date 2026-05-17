from fastapi import FastAPI

from app.routers import me

app = FastAPI(title="pulse-field-drill")

app.include_router(me.router, prefix="/api")
