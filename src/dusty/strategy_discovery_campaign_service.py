from __future__ import annotations

"""M196.6 production integration for batched strategy discovery.

The M196.5 discovery service remains available as a compatibility boundary. This
service is the PC-launcher path for M196.6: raw catalog hypotheses are filtered
for unsupported true cross-symbol dependency semantics, globally deduplicated by
the campaign planner, assigned one bounded single- or multi-timeframe profile,
and drained in fixed six-item workload windows. Ollama calls remain sequential.

No method in this module has broker-write, live-write, promotion, risk-override,
Guardian-override, or running-snapshot hot-swap authority.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

from .source_intake import StrategyProposal, deduplicate_proposals, proposals_from_vibe
from .strategy_discovery import (
    CROSS_SYMBOL_QUERIES,
    DiscoveryMode,
    DiscoveryStatus,
    DiscoveryTrigger,
    REPORT_SCHEMA,
    StrategyDiscoveryConfig,
    StrategyDiscoveryResult,
    StrategyDiscoveryService,
    StrategyDiscoveryState,
    _atomic_json,
    _bounded_error,
    _evidence_payload,
    _looks_cross_symbol,
    _utc,
    most_recent_sunday_slot_utc,
    write_discovery_state,
)
from .strategy_reconstruction_campaign import (
    CampaignExecutionResult,
    PlanStatus,
    RECONSTRUCTION_BATCH_SIZE,
    ReconstructionCampaign,
    execute_reconstruction_campaign,
    plan_reconstruction_campaign,
)


class BatchedStrategyDiscoveryService(StrategyDiscoveryService):
    """Production discovery lane with fixed six-item campaign windows."""

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    def __init__(self, config: StrategyDiscoveryConfig | None = None, **kwargs: object) -> None:
        super().__init__(config, **kwargs)
        if self.config.max_reconstructions != RECONSTRUCTION_BATCH_SIZE:
            raise ValueError(
                "M196.6 strategy discovery reconstruction window must be exactly 6; "
                "catalog_limit controls total ideas considered"
            )

    def discover(
        self,
        mode: DiscoveryMode = DiscoveryMode.BOTH,
        *,
        trigger: DiscoveryTrigger = DiscoveryTrigger.MANUAL,
        now: datetime | None = None,
    ) -> StrategyDiscoveryResult:
        if not isinstance(mode, DiscoveryMode) or not isinstance(trigger, DiscoveryTrigger):
            raise ValueError("strategy discovery mode/trigger invalid")
        attempted = _utc(now or datetime.now(timezone.utc))
        prior_state = self.state()
        write_discovery_state(
            self.config.state_path,
            StrategyDiscoveryState(
                attempted,
                prior_state.last_completed_utc,
                prior_state.last_status,
                prior_state.last_report_path,
            ),
        )

        errors: list[str] = []
        catalog_evidence: list[dict[str, object]] = []
        cross_evidence: list[dict[str, object]] = []
        raw_proposals: tuple[StrategyProposal, ...] = ()
        deferred_cross: tuple[StrategyProposal, ...] = ()
        campaign: ReconstructionCampaign | None = None
        execution: CampaignExecutionResult | None = None
        added = 0
        any_source_available = False

        contractor = self._contractor_factory(self.config.vibe_root.resolve(), self.config.work_root.resolve())

        if mode in {DiscoveryMode.NEW_STRATEGIES, DiscoveryMode.BOTH, DiscoveryMode.CROSS_SYMBOL}:
            catalog = contractor.invoke("list_strategies", {"limit": self.config.catalog_limit, "offset": 0})
            payload = _evidence_payload(catalog)
            if payload is None:
                errors.append("vibe_catalog_unavailable:" + _bounded_error(catalog.error))
            else:
                any_source_available = True
                catalog_evidence.append(payload)
                try:
                    raw_proposals = proposals_from_vibe(catalog.evidence)  # type: ignore[arg-type]
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(f"vibe_catalog_parse_failed:{type(exc).__name__}:{_bounded_error(exc)}")

        if raw_proposals:
            raw_cross = tuple(row for row in raw_proposals if _looks_cross_symbol(row))
            deferred_cross = deduplicate_proposals(raw_cross)
            campaign_input = tuple(row for row in raw_proposals if not _looks_cross_symbol(row))
            campaign = plan_reconstruction_campaign(
                campaign_input,
                allowed_symbols=self.config.allowed_symbols,
                estate_path=self.config.estate_path,
            )
            for plan in campaign.plans:
                if plan.status is PlanStatus.DEFERRED:
                    errors.append(f"campaign_deferred:{plan.proposal.proposal_id}:{plan.reason}")

        ready_plans = () if campaign is None else tuple(
            plan for plan in campaign.plans if plan.status is PlanStatus.READY
        )

        if mode in {DiscoveryMode.NEW_STRATEGIES, DiscoveryMode.BOTH} and campaign is not None and ready_plans:
            try:
                model_digest = self._digest_resolver(self.config.model_tag)
                execution = execute_reconstruction_campaign(
                    campaign,
                    builder=self._builder,
                    model_tag=self.config.model_tag,
                    model_digest=model_digest,
                    allowed_features=self.config.allowed_features,
                    allowed_sessions=self.config.allowed_sessions,
                    estate_path=self.config.estate_path,
                    created_at=attempted,
                )
                added = execution.added_to_estate
                for row in execution.rows:
                    if row.status.value != "added":
                        errors.append(f"estate_population_{row.status.value}:{row.proposal_id}:{row.reason}")
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"strategy_reconstruction_failed:{type(exc).__name__}:{_bounded_error(exc)}")

        if mode in {DiscoveryMode.CROSS_SYMBOL, DiscoveryMode.BOTH}:
            for query in CROSS_SYMBOL_QUERIES:
                result = contractor.invoke("web_search", {"query": query, "max_results": 5})
                payload = _evidence_payload(result)
                if payload is None:
                    errors.append("cross_symbol_search_unavailable:" + _bounded_error(result.error))
                    continue
                any_source_available = True
                payload["query"] = query
                cross_evidence.append(payload)

        status = self._result_status(any_source_available, errors)
        stamp = attempted.strftime("%Y%m%dT%H%M%SZ")
        report_path = self.config.report_directory / f"strategy-discovery-{stamp}.json"
        _atomic_json(
            report_path,
            {
                "schema": REPORT_SCHEMA,
                "created_at_utc": attempted.isoformat(),
                "mode": mode.value,
                "trigger": trigger.value,
                "status": status.value,
                "schedule": {
                    "default": "Sunday 08:00 U.S. Central",
                    "most_recent_slot_utc": most_recent_sunday_slot_utc(attempted).isoformat(),
                },
                "catalog": {
                    "evidence": catalog_evidence,
                    "proposals_seen": len(raw_proposals),
                    "new_single_symbol_candidates": [plan.proposal.proposal_id for plan in ready_plans],
                    "deferred_cross_symbol_candidates": [row.proposal_id for row in deferred_cross],
                },
                "reconstruction_campaign": self._campaign_payload(campaign),
                "estate_population": self._execution_payload(execution),
                "cross_symbol_research": {
                    "policy": (
                        "Web-search results and true pairs/intermarket catalog ideas remain untrusted research leads. "
                        "M196.6 may carry bounded symbol options for an ordinary strategy family, but it does not "
                        "invent cross-symbol dependency execution semantics that StrategySpecV2 cannot yet express."
                    ),
                    "evidence": cross_evidence,
                },
                "errors": errors,
                "authority": {
                    "broker_write": False,
                    "live_write": False,
                    "promotion": False,
                    "risk_override": False,
                    "guardian_override": False,
                    "hot_swap_running_snapshot": False,
                },
            },
        )

        completed_time = attempted if status is not DiscoveryStatus.UNAVAILABLE else prior_state.last_completed_utc
        write_discovery_state(
            self.config.state_path,
            StrategyDiscoveryState(attempted, completed_time, status, str(report_path.resolve())),
        )
        return StrategyDiscoveryResult(
            status=status,
            mode=mode,
            trigger=trigger,
            attempted_at_utc=attempted,
            proposals_seen=len(raw_proposals),
            new_single_symbol_candidates=len(ready_plans),
            deferred_cross_symbol_candidates=len(deferred_cross),
            added_to_estate=added,
            cross_symbol_web_leads=len(cross_evidence),
            report_path=report_path.resolve(),
            errors=tuple(errors),
        )

    @staticmethod
    def _campaign_payload(campaign: ReconstructionCampaign | None) -> dict[str, object] | None:
        if campaign is None:
            return None
        ready = tuple(plan for plan in campaign.plans if plan.status is PlanStatus.READY)
        return {
            "protocol": "dusty-m1966-discovery-campaign-v1",
            "proposals_seen": campaign.proposals_seen,
            "families_after_dedupe": campaign.proposals_after_dedupe,
            "duplicates_removed": campaign.duplicates_removed,
            "ready_reconstructions": len(ready),
            "already_represented": sum(plan.status is PlanStatus.ALREADY_REPRESENTED for plan in campaign.plans),
            "deferred": sum(plan.status is PlanStatus.DEFERRED for plan in campaign.plans),
            "batch_size_governor": RECONSTRUCTION_BATCH_SIZE,
            "batch_sizes": [len(batch) for batch in campaign.batches],
            "profiles": [
                {
                    "proposal_id": plan.proposal.proposal_id,
                    "primary_timeframe": plan.profile.primary,
                    "context_timeframes": list(plan.profile.context),
                    "timeframe_mode": plan.profile.mode.value,
                    "assignment_basis": plan.profile.basis.value,
                    "target_symbols": list(plan.target_symbols),
                    "deferred_symbols": list(plan.deferred_symbols),
                }
                for plan in ready
                if plan.profile is not None
            ],
            "authority": {
                "parallel_ollama_calls": False,
                "broker_write": False,
                "promotion": False,
            },
        }

    @staticmethod
    def _execution_payload(execution: CampaignExecutionResult | None) -> dict[str, object]:
        if execution is None:
            return {"rows": [], "estate_update": None, "batches_completed": 0, "model_calls_scheduled": 0}
        update = execution.final_estate_update
        return {
            "rows": [
                {
                    "proposal_id": row.proposal_id,
                    "status": row.status.value,
                    "reconstruction_fingerprint": row.reconstruction_fingerprint,
                    "reason": row.reason,
                }
                for row in execution.rows
            ],
            "estate_update": None if update is None else {
                "path": str(update.path),
                "sha256": update.sha256,
                "added": update.added,
                "total": update.total,
            },
            "estate_checkpoints": len(execution.estate_updates),
            "batches_completed": execution.batches_completed,
            "model_calls_scheduled": execution.model_calls_scheduled,
        }
