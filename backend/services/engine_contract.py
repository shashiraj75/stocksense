"""
Shared response contract for the Selection Engine's scoring engines.

SEAR-001 found every scoring engine (PredictionEngine's fundamental/technical/
sentiment scores, quality_factors.py's sub-scores, multibagger_scorecard.py)
returning its own ad hoc dict shape — no two engines agree on field names for
the same concept (e.g. "score" vs "scorecard.score", "reasons" vs "checks").
This module defines one shape every engine *should* converge on.

Sprint #002 scope: build the contract only. Per the sprint brief, no existing
engine is migrated to return this type yet — `PredictionEngine`,
`quality_factors.py`, and `multibagger_scorecard.py` keep returning their
current dict shapes unchanged in this sprint. Migration is tracked as a
Sprint 003+ follow-up (roadmap item 1.6, the typed `info` contract, is the
natural pairing — the input and output contracts should likely be designed
together rather than have this one migrated first in isolation).

Usage (future, not wired in yet):

    from services.engine_contract import EngineResponse, Grade

    def some_engine(...) -> EngineResponse:
        return EngineResponse(
            score=72.5,
            grade=Grade.BUY,
            confidence=64.0,
            strengths=["Strong ROE (24%)", "Low debt"],
            weaknesses=["High valuation (P/E 48)"],
            risks=["Promoter pledge at 6%"],
            explanation="Strong fundamentals offset by rich valuation.",
            metadata={"engine": "fundamental_score", "market": "IN"},
        )
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Grade(str, Enum):
    """Canonical verdict labels. Individual engines today use different
    vocabularies (BUY/SELL/HOLD/REJECTED in PredictionEngine vs.
    strong_buy/watchlist/avoid/elite_strong_buy in Multibagger) — this enum
    is the target vocabulary for engines migrated onto EngineResponse, not
    a renaming of those engines' existing output today."""

    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    WATCH = "watch"
    SELL = "sell"
    AVOID = "avoid"
    REJECTED = "rejected"


@dataclass
class EngineResponse:
    """Standard return shape for a Selection Engine scoring function.

    Fields mirror the sprint brief exactly: score, grade, confidence,
    strengths, weaknesses, risks, explanation, metadata.
    """

    score: float
    grade: Grade
    confidence: float
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    explanation: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not 0 <= self.score <= 100:
            raise ValueError(f"score must be in [0, 100], got {self.score}")
        if not 0 <= self.confidence <= 100:
            raise ValueError(f"confidence must be in [0, 100], got {self.confidence}")
        if isinstance(self.grade, str) and not isinstance(self.grade, Grade):
            self.grade = Grade(self.grade)

    def to_dict(self) -> dict[str, Any]:
        """Serialization helper for API responses / cache storage."""
        return {
            "score": self.score,
            "grade": self.grade.value,
            "confidence": self.confidence,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "risks": self.risks,
            "explanation": self.explanation,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EngineResponse":
        return cls(
            score=data["score"],
            grade=Grade(data["grade"]) if isinstance(data.get("grade"), str) else data["grade"],
            confidence=data["confidence"],
            strengths=data.get("strengths", []),
            weaknesses=data.get("weaknesses", []),
            risks=data.get("risks", []),
            explanation=data.get("explanation", ""),
            metadata=data.get("metadata", {}),
        )
