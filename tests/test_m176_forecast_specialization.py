from __future__ import annotations
from hashlib import sha256
import unittest
from dusty.forecast_campaign import ScoredPITForecast
from dusty.forecast_specialization import *

def fp(x):return sha256(x.encode()).hexdigest()
def score(case,p,err,base,hit=True,cov=True):return ScoredPITForecast(fp(case),p,fp(case+p),err,base,hit,cov)
class M176ProviderRegimeSpecializationTests(unittest.TestCase):
    def setUp(self):self.b=ForecastContextBucket('EURUSD','M15','london','trend',4);self.p=SpecializationPolicy(3,.1,.6)
    def test_specialist_is_bucket_specific_and_has_no_vote_authority(self):
        rows=[ContextualForecastScore(score(str(i),'chronos2',.5,1.0,True),self.b) for i in range(3)]
        out=specialize_provider('chronos2',self.b,rows,policy=self.p);self.assertEqual(out.status,SpecializationStatus.SPECIALIST);self.assertGreater(out.skill,.1);self.assertFalse(out.voting_authority);self.assertFalse(out.broker_write_authority)
    def test_same_provider_can_be_weak_in_different_regime(self):
        weak=ForecastContextBucket('EURUSD','M15','london','range',4)
        rows=[ContextualForecastScore(score(str(i),'chronos2',1.2,1.0,False),weak) for i in range(3)]
        self.assertEqual(specialize_provider('chronos2',weak,rows,policy=self.p).status,SpecializationStatus.WEAK)
    def test_sparse_bucket_is_insufficient_not_specialist(self):
        rows=[ContextualForecastScore(score('1','kronos-small',.2,1.0),self.b)]
        self.assertEqual(specialize_provider('kronos-small',self.b,rows,policy=self.p).status,SpecializationStatus.INSUFFICIENT)
    def test_other_bucket_rows_do_not_leak_into_target_bucket(self):
        other=ForecastContextBucket('GBPUSD','M15','london','trend',4)
        rows=[ContextualForecastScore(score(str(i),'timesfm-2.5',.1,1),other) for i in range(10)]
        self.assertEqual(specialize_provider('timesfm-2.5',self.b,rows,policy=self.p).case_count,0)
    def test_duplicate_case_identity_fails_closed(self):
        s=score('same','chronos2',.2,1);rows=[ContextualForecastScore(s,self.b)]*3
        with self.assertRaises(ValueError):specialize_provider('chronos2',self.b,rows,policy=SpecializationPolicy(1,0,0))
if __name__=='__main__':unittest.main()
