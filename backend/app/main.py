from fastapi import FastAPI

from app.routers import (
    accounts,
    budgets,
    categories,
    goals,
    me,
    planned,
    reports,
    transactions,
    workspaces,
)

app = FastAPI(title="pulse-field-drill")

app.include_router(me.router, prefix="/api")
app.include_router(workspaces.router, prefix="/api")
app.include_router(accounts.router, prefix="/api")
app.include_router(categories.router, prefix="/api")
app.include_router(transactions.router, prefix="/api")
app.include_router(goals.router, prefix="/api")
app.include_router(budgets.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(planned.router, prefix="/api")
