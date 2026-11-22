from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints, model_validator


class Mode(StrEnum):
    FAST = "fast"
    QUALITY = "quality"


class Format(StrEnum):
    GLB = "glb"
    BLEND = "blend"


class Provider(StrEnum):
    SF3D = "sf3d"
    TRELLIS = "trellis2"
    DEMO = "demo"


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)]
        | None
    ) = None
    image: Path | None = None
    mode: Mode = Mode.FAST
    provider: Provider | None = None
    format: Format = Format.GLB
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 42
    allow_experimental: bool = False

    @model_validator(mode="after")
    def check_source(self) -> Self:
        if (self.prompt is None) == (self.image is None):
            raise ValueError("Provide exactly one of prompt or image.")
        if self.provider == Provider.DEMO and self.image is not None:
            raise ValueError("The demo provider accepts a primitive name as a prompt.")
        return self

    def selected_provider(self) -> Provider:
        return self.provider or (Provider.SF3D if self.mode == Mode.FAST else Provider.TRELLIS)


class StageMetrics(BaseModel):
    seconds: float
    peak_rss_bytes: PositiveInt | None = None
    peak_footprint_bytes: PositiveInt | None = None


class Asset(BaseModel):
    path: Path
    bytes: PositiveInt
    vertices: PositiveInt
    triangles: PositiveInt
    materials: int
    stages: dict[str, StageMetrics] = Field(default_factory=dict)


Status = Literal["queued", "running", "succeeded", "failed"]


class Job(BaseModel):
    id: str
    status: Status
    provider: Provider
    mode: Mode
    asset: Asset | None = None
    error: str | None = None
    log: Path | None = None
