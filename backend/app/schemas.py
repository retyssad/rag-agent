from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TimeWindow(StrictModel):
    days: list[int] = Field(min_length=1)
    start: int = Field(ge=0, le=23)
    end: int = Field(ge=1, le=24)

    def model_post_init(self, context):
        if self.start >= self.end or any(d < 0 or d > 6 for d in self.days):
            raise ValueError('时间段无效，days 为周一0至周日6，start 必须小于 end')


class RequirementPatch(StrictModel):
    goal: str | None = None
    budget_max: float | None = Field(None, ge=0, le=100000)
    district: str | None = None
    available_windows: list[TimeWindow] | None = None
    delivery_mode: Literal['线下', '线上'] | None = None
    preferences: list[str] | None = None
    self_reported_conditions: list[str] | None = None


class Requirements(RequirementPatch):
    preferences: list[str] = Field(default_factory=list)
    self_reported_conditions: list[str] = Field(default_factory=list)
    available_windows: list[TimeWindow] = Field(default_factory=list)
    version: int = 0
    unrestricted_fields: list[str] = Field(default_factory=list)


Intent = Literal['general_knowledge', 'knowledge_base_qa', 'coach_lookup', 'coach_recommendation',
                 'coach_comparison', 'requirement_update', 'personalized_advice', 'coach_preregistration']


class Route(StrictModel):
    intents: list[Intent] = Field(min_length=1)
    patch: RequirementPatch = Field(default_factory=RequirementPatch)
    coach_ids: list[str] = Field(default_factory=list)


class RegisterInput(StrictModel):
    username: str = Field(min_length=3, max_length=32, pattern=r'^\S+$')
    password: str = Field(min_length=8, max_length=128)


class LoginInput(StrictModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class MessageInput(StrictModel):
    content: str = Field(min_length=1, max_length=4000)
    patch: RequirementPatch | None = None
    coach_ids: list[str] = Field(default_factory=list, max_length=4)


class ReplyInput(StrictModel):
    answer: str = Field(min_length=1, max_length=2000)
    patch: RequirementPatch | None = None


class KnowledgeQuestion(StrictModel):
    question: str = Field(min_length=1, max_length=1000)


class SearchArgs(StrictModel):
    filters: RequirementPatch
    coach_ids: list[str] = Field(default_factory=list)


class ProfileArgs(StrictModel):
    coach_id: str


class SlotItem(StrictModel):
    """预登记的具体日期与整点窗口（香港时区）。"""
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    start: int = Field(ge=0, le=23)
    end: int = Field(ge=1, le=24)

    def model_post_init(self, context):
        if self.start >= self.end:
            raise ValueError('时间段无效，start 必须小于 end')


class PreregisterArgs(StrictModel):
    coach_id: str
    slots: list[SlotItem] = Field(min_length=1, max_length=12)
    # 备注只能是用户自己的一句话补充（≤200 字），不能是模型解释或内部标识。
    note: str = Field(default='', max_length=200)


class FavoriteInput(StrictModel):
    coach_id: str


class PreregisterInput(StrictModel):
    coach_id: str
    slots: list[SlotItem] = Field(min_length=1, max_length=12)
    note: str = Field(default='', max_length=200)
    session_id: str | None = None


class AvailabilityArgs(ProfileArgs):
    time_range: list[TimeWindow] = Field(default_factory=list)


class RetrievalArgs(StrictModel):
    query: str = Field(min_length=1, max_length=4000)
    coach_ids: list[str] | None = None
    filters: dict[str, str] = Field(default_factory=dict)


class RewriteArgs(StrictModel):
    query: str = Field(min_length=1, max_length=4000)


class EvidenceReason(StrictModel):
    text: str = Field(max_length=500)
    evidence_id: str
    quote: str = Field(min_length=2, max_length=500)


class Judgment(StrictModel):
    coach_id: str
    reasons: list[EvidenceReason] = Field(default_factory=list, max_length=5)
    pending: list[str] = Field(default_factory=list, max_length=5)


class EvaluateArgs(StrictModel):
    judgments: list[Judgment] = Field(min_length=1)


class ClarifyArgs(StrictModel):
    question: str = Field(min_length=1, max_length=1000)
    options: list[str] = Field(default_factory=list, max_length=6)
    reason: Literal['missing', 'empty', 'conflict'] = 'missing'


class DelegateArgs(StrictModel):
    task: str = Field(min_length=1, max_length=4000)


class FinishArgs(StrictModel):
    summary: str = Field(default='', max_length=3000)
