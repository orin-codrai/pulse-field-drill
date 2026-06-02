from pydantic import BaseModel, ConfigDict, Field


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: str


class WorkspaceSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: int


class WorkspaceCreate(BaseModel):
    """POST /api/workspaces (kind='shared' implicit; нет пути создать второй
    personal — он только через provisioning)."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
