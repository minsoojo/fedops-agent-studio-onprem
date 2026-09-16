from typing import Literal

from pydantic import BaseModel, Field, model_validator, field_validator


class SaveFileRequest(BaseModel):
    localProjectId: str
    filePath: str
    content: str


class CreateFileRequest(BaseModel):
    localProjectId: str
    filePath: str
    content: str = ""


class FileActionRequest(BaseModel):
    localProjectId: str
    filePath: str


class CreateWorkspaceRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    sourceTaskId: str | None = None

    @model_validator(mode="after")
    def require_creation_source(self) -> "CreateWorkspaceRequest":
        if not str(self.sourceTaskId or "").strip() and not str(self.name or "").strip():
            raise ValueError("Select a FedOps Web Draft or enter a local project name.")
        return self


class DeleteWorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ImportLegacyWorkspacesRequest(BaseModel):
    projectNames: list[str] = Field(min_length=1)


class WorkspaceActionRequest(BaseModel):
    action: Literal[
        "validate",
        "local-train",
        "release-readiness",
        "participation-readiness",
        "run-file",
    ]
    environmentId: str | None = None
    filePath: str | None = None
    dataPath: str | None = None


class TaskDataSampleRequest(BaseModel):
    index: int = Field(default=0, ge=0)
    dataPath: str = Field(default="", max_length=512)


class ValidationUploadRequest(BaseModel):
    consent: bool = Field(strict=True)
    relativePath: str = Field(default="", max_length=512)

    @field_validator('consent')
    @classmethod
    def require_consent(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError('Explicit server storage consent is required.')
        return value


class LinkTaskRequest(BaseModel):
    taskId: str = Field(min_length=1)
