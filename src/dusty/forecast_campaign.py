from __future__ import annotations

"""M175 large point-in-time forecast campaign evidence and scoring."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
from statistics import mean
from typing import Iterable

from .provider_forecast_adapter import ForecastEvidence

EXPECTED_PROVIDERS=("chronos2","kronos-small","timesfm-2.5")

def _canon(v:object)->str: return json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False,default=str)
def _digest(v:object)->str: return sha256(_canon(v).encode()).hexdigest()
def _sha(v:str,label:str)->str:
    x=str(v).strip().lower()
    if len(x)!=64 or any(c not in "0123456789abcdef" for c in x): raise ValueError(f"{label} requires sha256")
    return x
def _aware(v:datetime,label:str)->datetime:
    if v.tzinfo is None or v.utcoffset() is None: raise ValueError(f"{label} must be timezone-aware")
    return v.astimezone(timezone.utc)
def _finite(v:float,label:str)->float:
    x=float(v)
    if not math.isfinite(x): raise ValueError(f"{label} must be finite")
    return x

@dataclass(frozen=True,slots=True)
class PITForecastCase:
    case_fingerprint:str; symbol:str; timeframe:str; as_of:datetime; target_at:datetime; horizon_steps:int; origin_value:float; context_sha256:str
    def __post_init__(self):
        object.__setattr__(self,"case_fingerprint",_sha(self.case_fingerprint,"forecast case")); object.__setattr__(self,"context_sha256",_sha(self.context_sha256,"forecast context"))
        object.__setattr__(self,"as_of",_aware(self.as_of,"as_of")); object.__setattr__(self,"target_at",_aware(self.target_at,"target_at"))
        if self.target_at<=self.as_of: raise ValueError("forecast outcome must be later than decision time")
        if not 1<=int(self.horizon_steps)<=64: raise ValueError("horizon out of range")
        object.__setattr__(self,"origin_value",_finite(self.origin_value,"origin_value"))
        if self.origin_value<=0: raise ValueError("origin_value must be positive")
        object.__setattr__(self,"symbol",self.symbol.strip().upper()); object.__setattr__(self,"timeframe",self.timeframe.strip().upper())
        if not self.symbol or not self.timeframe: raise ValueError("market identity required")

@dataclass(frozen=True,slots=True)
class PITForecastAttempt:
    case_fingerprint:str; provider_id:str; evidence:ForecastEvidence|None=None; error:str=""
    def __post_init__(self):
        object.__setattr__(self,"case_fingerprint",_sha(self.case_fingerprint,"attempt case")); p=self.provider_id.strip().lower(); object.__setattr__(self,"provider_id",p)
        if p not in EXPECTED_PROVIDERS: raise ValueError("unexpected forecast provider")
        if (self.evidence is None)==(not self.error): raise ValueError("attempt requires exactly evidence or error")
        if self.evidence is not None and self.evidence.provider_id!=p: raise ValueError("provider evidence identity drift")
    @property
    def fingerprint(self)->str: return _digest({"case":self.case_fingerprint,"provider":self.provider_id,"evidence":None if self.evidence is None else self.evidence.fingerprint,"error":self.error})

@dataclass(frozen=True,slots=True)
class PITForecastOutcome:
    case_fingerprint:str; observed_at:datetime; target_value:float
    def __post_init__(self):
        object.__setattr__(self,"case_fingerprint",_sha(self.case_fingerprint,"outcome case")); object.__setattr__(self,"observed_at",_aware(self.observed_at,"observed_at")); object.__setattr__(self,"target_value",_finite(self.target_value,"target_value"))
        if self.target_value<=0: raise ValueError("target_value must be positive")

@dataclass(frozen=True,slots=True)
class ScoredPITForecast:
    case_fingerprint:str; provider_id:str; evidence_fingerprint:str; absolute_error:float; baseline_absolute_error:float; direction_hit:bool; interval_80_hit:bool
    @property
    def skill_contribution(self)->float: return self.baseline_absolute_error-self.absolute_error
    @property
    def fingerprint(self)->str: return _digest(self.__dict__ if hasattr(self,"__dict__") else (self.case_fingerprint,self.provider_id,self.evidence_fingerprint,self.absolute_error,self.baseline_absolute_error,self.direction_hit,self.interval_80_hit))

class ForecastCampaignStatus(StrEnum): INSUFFICIENT="insufficient"; SCORED="scored"
@dataclass(frozen=True,slots=True)
class ForecastCampaignSummary:
    status:ForecastCampaignStatus; scored_count:int; unavailable_count:int; provider_counts:tuple[tuple[str,int],...]; mae:float|None; baseline_mae:float|None; skill:float|None; direction_accuracy:float|None; interval_80_coverage:float|None
    @property
    def broker_write_authority(self)->bool:return False
    @property
    def promotion_authority(self)->bool:return False

def score_attempt(case:PITForecastCase,attempt:PITForecastAttempt,outcome:PITForecastOutcome)->ScoredPITForecast|None:
    if attempt.case_fingerprint!=case.case_fingerprint or outcome.case_fingerprint!=case.case_fingerprint: raise ValueError("forecast case identity mismatch")
    if outcome.observed_at<case.target_at: raise ValueError("outcome observed before target became knowable")
    if attempt.evidence is None: return None
    e=attempt.evidence
    if e.symbol.upper()!=case.symbol or e.timeframe.upper()!=case.timeframe or e.as_of!=case.as_of or e.horizon_steps!=case.horizon_steps or e.context_sha256!=case.context_sha256: raise ValueError("forecast PIT evidence drift")
    actual=outcome.target_value; pred=e.p50; baseline=case.origin_value
    pd=0 if pred==baseline else (1 if pred>baseline else -1); ad=0 if actual==baseline else (1 if actual>baseline else -1)
    return ScoredPITForecast(case.case_fingerprint,e.provider_id,e.fingerprint,abs(pred-actual),abs(baseline-actual),pd==ad,e.p10<=actual<=e.p90)

def summarize_campaign(scored:Iterable[ScoredPITForecast],*,unavailable_count:int,minimum_scored:int)->ForecastCampaignSummary:
    rows=tuple(scored)
    if minimum_scored<1 or unavailable_count<0: raise ValueError("campaign bounds invalid")
    counts=tuple(sorted((p,sum(r.provider_id==p for r in rows)) for p in EXPECTED_PROVIDERS))
    if len(rows)<minimum_scored: return ForecastCampaignSummary(ForecastCampaignStatus.INSUFFICIENT,len(rows),unavailable_count,counts,None,None,None,None,None)
    mae=mean(r.absolute_error for r in rows); base=mean(r.baseline_absolute_error for r in rows); skill=None if base==0 else 1-mae/base
    return ForecastCampaignSummary(ForecastCampaignStatus.SCORED,len(rows),unavailable_count,counts,mae,base,skill,mean(r.direction_hit for r in rows),mean(r.interval_80_hit for r in rows))
