"""Импорт всех моделей сюда — иначе Base.metadata будет пуст для autogenerate."""

from app.models.account import Account
from app.models.budget import Budget
from app.models.category import Category
from app.models.goal import Goal
from app.models.receipt import Receipt
from app.models.transaction import Transaction
from app.models.user import User

__all__ = [
    "Account",
    "Budget",
    "Category",
    "Goal",
    "Receipt",
    "Transaction",
    "User",
]
