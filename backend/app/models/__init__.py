"""Импорт всех моделей сюда — иначе Base.metadata будет пуст для autogenerate."""

from app.models.account import Account
from app.models.audit_log import AuditLog
from app.models.budget import Budget
from app.models.category import Category
from app.models.envelope import Envelope
from app.models.envelope_entry import EnvelopeEntry
from app.models.planned_operation import PlannedOperation
from app.models.receipt import Receipt
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_invite import WorkspaceInvite
from app.models.workspace_member import WorkspaceMember

__all__ = [
    "Account",
    "AuditLog",
    "Budget",
    "Category",
    "Envelope",
    "EnvelopeEntry",
    "PlannedOperation",
    "Receipt",
    "Transaction",
    "User",
    "Workspace",
    "WorkspaceInvite",
    "WorkspaceMember",
]
