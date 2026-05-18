from fastapi import FastAPI

from app.routers import accounts, me

app = FastAPI(title="pulse-field-drill")

app.include_router(me.router, prefix="/api")
app.include_router(accounts.router, prefix="/api")
