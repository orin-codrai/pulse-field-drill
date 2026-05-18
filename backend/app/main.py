from fastapi import FastAPI

from app.routers import accounts, categories, me, transactions

app = FastAPI(title="pulse-field-drill")

app.include_router(me.router, prefix="/api")
app.include_router(accounts.router, prefix="/api")
app.include_router(categories.router, prefix="/api")
app.include_router(transactions.router, prefix="/api")
