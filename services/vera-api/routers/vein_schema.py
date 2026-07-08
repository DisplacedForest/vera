"""Vein definition schema — the validation contract every vein definition passes,
whatever its origin (shipped file, builder draft, imported file, API body).

A definition carries identity/presentation (kind, label, icon, order, nominal_label,
blurb), the config surface (requires / providers / options, matching the catalog's
runtime semantics), and exactly one production shape:

  producer_jobs   scheduler job ids (shipped definitions whose producers are code)
  pipeline        ordered {block, params} steps run by the vein engine, plus a
                  cron `schedule`; params are structurally an object here and are
                  validated per-block by the engine

`validate_definition` normalizes a raw dict or raises ValueError; `json_schema`
exports the same contract as JSON Schema for clients."""

from typing import Any, Literal, Optional

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

BLOCKS = ("web_search", "http_fetch", "ha_state", "trip_band", "llm_judge", "llm_compose")


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["integration", "feature", "env"]
    id: Optional[str] = None
    integration: Optional[str] = None
    feature: Optional[str] = None
    names: Optional[list[str]] = None
    label: Optional[str] = None

    @model_validator(mode="after")
    def _fields_for_kind(self):
        if self.kind == "integration" and not self.id:
            raise ValueError("integration requirement needs `id`")
        if self.kind == "feature" and not (self.integration and self.feature):
            raise ValueError("feature requirement needs `integration` and `feature`")
        if self.kind == "env" and not self.names:
            raise ValueError("env requirement needs `names`")
        return self


class ProviderSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    hint: str = ""
    default: str = ""


class OptionField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    type: Literal["bool", "text", "number", "choice"]
    choices: Optional[list[str]] = None
    default: Optional[Any] = None
    env: Optional[str] = None
    hint: Optional[str] = None

    @model_validator(mode="after")
    def _choices_iff_choice(self):
        if self.type == "choice" and not self.choices:
            raise ValueError("choice field needs `choices`")
        if self.type != "choice" and self.choices:
            raise ValueError("`choices` belongs to choice fields")
        return self


class OptionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group: str
    fields: list[OptionField]


class PipelineStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)


class VeinDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)
    label: str = Field(min_length=1)
    icon: str = Field(min_length=1)
    order: int = 100
    nominal_label: str = "quiet"
    blurb: str = ""
    requires: list[Requirement] = Field(default_factory=list)
    providers: list[ProviderSlot] = Field(default_factory=list)
    options: list[OptionGroup] = Field(default_factory=list)
    journal: bool = False
    producer_jobs: Optional[list[str]] = None
    pipeline: Optional[list[PipelineStep]] = None
    schedule: Optional[str] = None

    @model_validator(mode="after")
    def _one_production_shape(self):
        if (self.producer_jobs is None) == (self.pipeline is None):
            raise ValueError("a definition carries exactly one of `producer_jobs` or `pipeline`")
        if self.pipeline is not None:
            if not self.pipeline:
                raise ValueError("`pipeline` needs at least one step")
            if not self.schedule or not croniter.is_valid(self.schedule):
                raise ValueError("a pipeline vein needs a valid cron `schedule`")
        elif self.schedule is not None:
            raise ValueError("`schedule` belongs to pipeline veins")
        return self


def validate_definition(raw: dict) -> dict:
    """Normalized definition dict, or ValueError with the validation detail."""
    try:
        return VeinDefinition.model_validate(raw).model_dump(exclude_none=True)
    except ValidationError as e:
        raise ValueError(str(e)) from e


def json_schema() -> dict:
    out = VeinDefinition.model_json_schema()
    out["x_builtin_blocks"] = list(BLOCKS)
    return out
