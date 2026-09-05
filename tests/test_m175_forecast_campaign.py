from __future__ import annotations
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import unittest
from dusty.forecast_campaign import *
from dusty.provider_forecast_adapter import ForecastEvidence,PROTOCOL

def fp(x):return sha256(x.encode()).hexdigest()
def evidence(case,provider='chronos2',p50=101.0):
    return ForecastEvidence(PROTOCOL,provider,'model','rev','runtime','license',case.symbol,case.timeframe,case.as_of,case.as_of,case.horizon_steps,case.origin_value,98.0,p50,104.0,case.context_sha256,fp('req'+provider),fp('resp'+provider))
class M175LargePITForecastCampaignTests(unittest.TestCase):
    def case(self):
        a=datetime(2025,1,1,tzinfo=timezone.utc);return PITForecastCase(fp('case'),'EURUSD','M15',a,a+timedelta(hours=1),4,100.0,fp('ctx'))
    def test_scores_only_outcomes_knowable_after_target(self):
        c=self.case();a=PITForecastAttempt(c.case_fingerprint,'chronos2',evidence(c));o=PITForecastOutcome(c.case_fingerprint,c.target_at,102.0)
        s=score_attempt(c,a,o);self.assertEqual(s.absolute_error,1.0);self.assertEqual(s.baseline_absolute_error,2.0);self.assertTrue(s.direction_hit);self.assertTrue(s.interval_80_hit)
        with self.assertRaises(ValueError):score_attempt(c,a,PITForecastOutcome(c.case_fingerprint,c.target_at-timedelta(seconds=1),102.0))
    def test_unavailable_attempt_is_retained_but_not_scored(self):
        c=self.case();a=PITForecastAttempt(c.case_fingerprint,'kronos-small',None,'timeout');o=PITForecastOutcome(c.case_fingerprint,c.target_at,102.0)
        self.assertIsNone(score_attempt(c,a,o));self.assertTrue(a.fingerprint)
    def test_pit_identity_drift_is_rejected(self):
        c=self.case();e=evidence(c);bad=PITForecastCase(c.case_fingerprint,'GBPUSD','M15',c.as_of,c.target_at,4,100,fp('ctx'))
        with self.assertRaises(ValueError):score_attempt(bad,PITForecastAttempt(c.case_fingerprint,'chronos2',e),PITForecastOutcome(c.case_fingerprint,c.target_at,102))
    def test_large_campaign_requires_explicit_minimum_and_reports_no_change_skill(self):
        c=self.case();rows=[]
        for i,p in enumerate(EXPECTED_PROVIDERS):
            e=evidence(c,p,101+i*.2);rows.append(score_attempt(c,PITForecastAttempt(c.case_fingerprint,p,e),PITForecastOutcome(c.case_fingerprint,c.target_at,102)))
        small=summarize_campaign(rows,unavailable_count=2,minimum_scored=10);self.assertEqual(small.status,ForecastCampaignStatus.INSUFFICIENT);self.assertIsNone(small.skill)
        ok=summarize_campaign(rows,unavailable_count=2,minimum_scored=3);self.assertEqual(ok.status,ForecastCampaignStatus.SCORED);self.assertGreater(ok.skill,0);self.assertFalse(ok.broker_write_authority);self.assertFalse(ok.promotion_authority)
    def test_case_requires_future_target_and_sha_context(self):
        a=datetime.now(timezone.utc)
        with self.assertRaises(ValueError):PITForecastCase(fp('x'),'EURUSD','M15',a,a,4,1,fp('c'))
if __name__=='__main__':unittest.main()
