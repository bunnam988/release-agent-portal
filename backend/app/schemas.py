from pydantic import BaseModel


class StartSessionRequest(BaseModel):
    workflow_id: str
    args: dict[str, str | bool] = {}
    # UI-provided identity, for audit logging only (the underlying
    # Jira/GitHub/Gerrit actions still run as the shared opencode service
    # account). Real deployments should replace this with an SSO-derived
    # identity instead of trusting a client-supplied field.
    user: str = "unknown"


class StartSessionResponse(BaseModel):
    session_id: str


class ReplyRequest(BaseModel):
    text: str


class PermissionDecisionRequest(BaseModel):
    allow: bool


class QuestionAnswerRequest(BaseModel):
    # One list of selected option labels per question, in question order.
    answers: list[list[str]]


class LoginRequest(BaseModel):
    password: str
