from __future__ import annotations
"""M176 provider regime-specialization evidence."""
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json,math
from statistics import mean
from typing import Iterable
from .forecast_campaign import EXPECTED_PROVIDERS,ScoredPITForecast

def _canon(v):return json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False,default=str)
def _digest(v):return sha256(_canon(v).encode()).hexdigest()
class SpecializationStatus(StrEnum): INSUFFICIENT="insufficient"; SPECIALIST="specialist"; WEAK="weak"
@dataclass(frozen=True,slots=True)
class ForecastContextBucket:
    symbol:str; timeframe:str; session:str; regime:str; horizon_steps:int
    def __post_init__(self):
        for n in ("symbol","timeframe","session","regime"):
            x=str(getattr(self,n)).strip().upper() if n in ("symbol","timeframe") else str(getattr(self,n)).strip().lower()
            if not x: raise ValueError(f"{n} required")
            object.__setattr__(self,n,x)
        if not 1<=int(self.horizon_steps)<=64: raise ValueError("horizon out of range")
    @property
    def fingerprint(self):return _digest((self.symbol,self.timeframe,self.session,self.regime,self.horizon_steps))
@dataclass(frozen=True,slots=True)
class ContextualForecastScore:
    score:ScoredPITForecast; bucket:ForecastContextBucket
@dataclass(frozen=True,slots=True)
class SpecializationPolicy:
    minimum_cases:int; minimum_skill:float; minimum_direction_accuracy:float
    def __post_init__(self):
        if int(self.minimum_cases)<1:raise ValueError("minimum_cases must be positive")
        for n in ("minimum_skill","minimum_direction_accuracy"):
            x=float(getattr(self,n));
            if not math.isfinite(x) or (n=="minimum_direction_accuracy" and not 0<=x<=1):raise ValueError(f"invalid {n}")
@dataclass(frozen=True,slots=True)
class ProviderSpecialization:
    provider_id:str; bucket:ForecastContextBucket; status:SpecializationStatus; case_count:int; mae:float|None; baseline_mae:float|None; skill:float|None; direction_accuracy:float|None; interval_80_coverage:float|None
    @property
    def fingerprint(self):return _digest((self.provider_id,self.bucket.fingerprint,self.status.value,self.case_count,self.mae,self.baseline_mae,self.skill,self.direction_accuracy,self.interval_80_coverage))
    @property
    def broker_write_authority(self):return False
    @property
    def voting_authority(self):return False

def specialize_provider(provider_id:str,bucket:ForecastContextBucket,rows:Iterable[ContextualForecastScore],*,policy:SpecializationPolicy)->ProviderSpecialization:
    p=provider_id.strip().lower()
    if p not in EXPECTED_PROVIDERS:raise ValueError("unexpected provider")
    selected=tuple(r.score for r in rows if r.score.provider_id==p and r.bucket==bucket)
    if len({r.case_fingerprint for r in selected})!=len(selected):raise ValueError("duplicate contextual forecast cases")
    if len(selected)<policy.minimum_cases:return ProviderSpecialization(p,bucket,SpecializationStatus.INSUFFICIENT,len(selected),None,None,None,None,None)
    mae=mean(r.absolute_error for r in selected);base=mean(r.baseline_absolute_error for r in selected);skill=None if base==0 else 1-mae/base;direction=mean(r.direction_hit for r in selected);coverage=mean(r.interval_80_hit for r in selected)
    strong=skill is not None and skill>=policy.minimum_skill and direction>=policy.minimum_direction_accuracy
    return ProviderSpecialization(p,bucket,SpecializationStatus.SPECIALIST if strong else SpecializationStatus.WEAK,len(selected),mae,base,skill,direction,coverage)
