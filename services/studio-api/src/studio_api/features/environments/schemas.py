from typing import Literal

from pydantic import BaseModel, Field


EnvironmentOwnerType = Literal["federated-task", "agent-build", "agent-serve"]


class CreateEnvironmentRequest(BaseModel):
    ownerType: EnvironmentOwnerType
    ownerId: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=48)
    pythonVersion: str
    select: bool = True


class EnvironmentOwnerRequest(BaseModel):
    ownerType: EnvironmentOwnerType
    ownerId: str = Field(min_length=1)


class UpdateRequirementsRequest(BaseModel):
    ownerType: EnvironmentOwnerType
    ownerId: str = Field(min_length=1)
    content: str = Field(max_length=128 * 1024)
