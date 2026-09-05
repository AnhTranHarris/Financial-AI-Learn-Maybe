from __future__ import annotations
from datetime import datetime, timezone
from hashlib import sha256
import unittest

from dusty.broker_calibration import BrokerEconomicsCalibration, CalibrationStatus
from dusty.cost_torture import CostTortureAssessment
from dusty.forward_decay import HistoricalForwardDecay, DecayStatus
from dusty.parameter_stability import NeighborhoodAssessment, NeighborhoodStatus
from dusty.regime_torture import RegimeTortureAssessment, RegimeTortureStatus
from dusty.robustness_gate import RobustnessCertificationPolicy, RobustnessGateStatus, certify_robustness
from dusty.strategy_dependency import StrategyDependencyMatrix, DependencyStatus
from dusty.tail_risk import TailRiskReport, TailRiskStatus
from dusty.walk_forward_lab import WalkForwardSummary


def fp(x:str)->str: return sha256(x.encode()).hexdigest()

class M174RobustnessCertificationTests(unittest.TestCase):
    def fixtures(self, *, calibrated=True, measured_decay=True, wf=.9, cost_pass=True):
        status=CalibrationStatus.CALIBRATED if calibrated else CalibrationStatus.UNCALIBRATED
        metrics=(1,2,3,.1,.2,.3,3,4,.5) if calibrated else (None,)*9
        cal=BrokerEconomicsCalibration(status,fp('broker'),'EURUSD',40 if calibrated else 0,4 if calibrated else 0,(fp('obs'),) if calibrated else (),*metrics,'ok' if calibrated else 'missing')
        wfrow=WalkForwardSummary(fp('plan'),4,3,wf,.02,-.01,.08,100)
        neigh=NeighborhoodAssessment(fp('p'),NeighborhoodStatus.STABLE,4,4,1.0,1.0,.9,.8,.2,'stable')
        reg=RegimeTortureAssessment(RegimeTortureStatus.PASSED,datetime.now(timezone.utc),4,4,1.0,-.01,.08,(fp('r1'),fp('r2'),fp('r3'),fp('r4')),'pass')
        cost=CostTortureAssessment(cal.fingerprint,4,1.0 if cost_pass else .5,cost_pass,-.02,.09)
        decay=HistoricalForwardDecay(DecayStatus.MEASURED if measured_decay else DecayStatus.MISSING_FORWARD,fp('s'),fp('hist'),fp('fwd') if measured_decay else None,2.0,1.5 if measured_decay else None,.75 if measured_decay else None,.25 if measured_decay else None,40 if measured_decay else 0,'measured' if measured_decay else 'missing')
        tail=TailRiskReport(TailRiskStatus.MEASURED,100,.95,.12,.05,.08,-.1,4,.15,'measured')
        dep=StrategyDependencyMatrix(DependencyStatus.DIVERSIFIED,(fp('s1'),fp('s2')),100,(),.2,.3,'diversified')
        return cal,wfrow,neigh,reg,cost,decay,tail,dep

    def policy(self): return RobustnessCertificationPolicy(.75,.50,.20,.10)

    def test_all_required_evidence_can_only_create_serious_research_challenger(self):
        cal,wf,n,r,c,d,t,dep=self.fixtures()
        out=certify_robustness(calibration=cal,walk_forward=wf,neighborhood=n,regime=r,cost=c,decay=d,tail=t,dependency=dep,policy=self.policy())
        self.assertEqual(out.status,RobustnessGateStatus.SERIOUS_CHALLENGER)
        self.assertEqual(out.blockers,())
        self.assertFalse(out.broker_write_authority); self.assertFalse(out.promotion_authority); self.assertFalse(out.entry_veto_authority); self.assertFalse(out.risk_override_authority)

    def test_missing_real_broker_or_forward_evidence_is_pending_not_pass(self):
        cal,wf,n,r,c,d,t,dep=self.fixtures(calibrated=False,measured_decay=False)
        # cost evidence cannot truthfully be calibrated when broker evidence is missing; use a non-passing placeholder bound to the same profile identity.
        c=CostTortureAssessment(cal.fingerprint,0,0.0,False,0.0,0.0)
        out=certify_robustness(calibration=cal,walk_forward=wf,neighborhood=n,regime=r,cost=c,decay=d,tail=t,dependency=dep,policy=self.policy())
        self.assertEqual(out.status,RobustnessGateStatus.PENDING)
        self.assertIn('broker_calibration',out.blockers); self.assertIn('historical_forward_decay',out.blockers)

    def test_measured_failure_rejects_instead_of_becoming_pending(self):
        cal,wf,n,r,c,d,t,dep=self.fixtures(wf=.5,cost_pass=False)
        out=certify_robustness(calibration=cal,walk_forward=wf,neighborhood=n,regime=r,cost=c,decay=d,tail=t,dependency=dep,policy=self.policy())
        self.assertEqual(out.status,RobustnessGateStatus.REJECTED)
        self.assertIn('walk_forward',out.blockers); self.assertIn('cost_torture',out.blockers)

    def test_policy_thresholds_are_explicit_not_hidden_constants(self):
        cal,wf,n,r,c,d,t,dep=self.fixtures()
        strict=RobustnessCertificationPolicy(.95,.90,.05,.02)
        out=certify_robustness(calibration=cal,walk_forward=wf,neighborhood=n,regime=r,cost=c,decay=d,tail=t,dependency=dep,policy=strict)
        self.assertEqual(out.status,RobustnessGateStatus.REJECTED)

if __name__=='__main__': unittest.main()
