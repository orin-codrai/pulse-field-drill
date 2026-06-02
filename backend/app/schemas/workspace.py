from pydantic import BaseModel, ConfigDict


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: str


class WorkspaceSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: int
