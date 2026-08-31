from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from urllib.error import URLError

from dusty.data_acquisition import (
    AcquisitionMode,
    BLSAdapter,
    CFTCPublicAdapter,
    ECBAdapter,
    GitHubKnownRepositoryAdapter,
    HTTPSClient,
    RetryPolicy,
    SECSubmissionsAdapter,
    macro_observation_evidence,
    source_capability,
)


class RoutingFetcher:
    def __init__(self, routes: dict[str, bytes], *, fail_first: bool = False) -> None:
        self.routes = routes
        self.fail_first = fail_first
        self.calls = 0

    def __call__(self, request, timeout: float) -> bytes:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise URLError("temporary")
        return self.routes[request.full_url]


class DataAcquisitionTests(unittest.TestCase):
    def test_capability_manifest_is_honest(self) -> None:
        self.assertTrue(source_capability("bls").automatic)
        self.assertTrue(source_capability("fed_rss").automatic)
        self.assertTrue(source_capability("bls_calendar").automatic)
        self.assertEqual(source_capability("tradingview").mode, AcquisitionMode.UNSUPPORTED_AUTOMATIC)
        self.assertFalse(source_capability("forex_factory").automatic)
        self.assertFalse(source_capability("eia").automatic)
        self.assertFalse(source_capability("unknown-source").automatic)

    def test_https_client_retries_boundedly(self) -> None:
        fetcher = RoutingFetcher({"https://example.com/x": b"{}"}, fail_first=True)
        sleeps = []
        client = HTTPSClient(fetch_bytes=fetcher, sleeper=sleeps.append, policy=RetryPolicy(attempts=2, base_delay_seconds=0.1))
        self.assertEqual(client.get_json("https://example.com/x"), {})
        self.assertEqual(fetcher.calls, 2)
        self.assertEqual(sleeps, [0.1])
        with self.assertRaises(ValueError):
            client.get_text("http://example.com/x")

    def test_bls_series_normalization_uses_retrieval_as_known_time_and_bridges_to_evidence(self) -> None:
        url = "https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0"
        payload = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"data": [{"year": "2026", "period": "M07", "value": "323.0"}]}]}}
        client = HTTPSClient(fetch_bytes=RoutingFetcher({url: json.dumps(payload).encode()}), sleeper=lambda _: None)
        known = datetime(2026, 8, 31, tzinfo=timezone.utc)
        rows = BLSAdapter(client).fetch_series("cusr0000sa0", retrieved_at=known)
        self.assertEqual(rows[0].series_id, "CUSR0000SA0")
        self.assertEqual(rows[0].period, "2026-M07")
        self.assertEqual(rows[0].value, 323.0)
        self.assertEqual(rows[0].known_at, known)
        evidence = macro_observation_evidence(rows[0])
        self.assertEqual(evidence.observed_at, known)
        self.assertEqual(evidence.category, "macro")
        self.assertEqual(evidence.value["value"], 323.0)

    def test_ecb_csv_normalization(self) -> None:
        url = "https://data-api.ecb.europa.eu/service/data/EXR/M.USD.EUR.SP00.A?format=csvdata&startPeriod=2026-07&endPeriod=2026-07"
        csv_bytes = b"TIME_PERIOD,OBS_VALUE,UNIT\n2026-07,1.17,USD\n"
        client = HTTPSClient(fetch_bytes=RoutingFetcher({url: csv_bytes}), sleeper=lambda _: None)
        rows = ECBAdapter(client).fetch_series("EXR", "M.USD.EUR.SP00.A", start_period="2026-07", end_period="2026-07", retrieved_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].value, 1.17)

    def test_sec_adapter_refuses_non_contact_user_agent(self) -> None:
        client = HTTPSClient(fetch_bytes=RoutingFetcher({}), sleeper=lambda _: None)
        with self.assertRaises(ValueError):
            SECSubmissionsAdapter(client)

    def test_sec_recent_filings_use_acceptance_time_when_available(self) -> None:
        url = "https://data.sec.gov/submissions/CIK0000320193.json"
        payload = {"filings": {"recent": {"accessionNumber": ["0001"], "form": ["10-Q"], "filingDate": ["2026-08-01"], "primaryDocument": ["a.htm"], "acceptanceDateTime": ["2026-08-01T16:03:00Z"]}}}
        client = HTTPSClient(fetch_bytes=RoutingFetcher({url: json.dumps(payload).encode()}), sleeper=lambda _: None, user_agent="DustyDragonTest research@example.com")
        events = SECSubmissionsAdapter(client, clock=lambda: 1.0, sleeper=lambda _: None).fetch_recent("320193")
        self.assertEqual(events[0].cik, "0000320193")
        self.assertEqual(events[0].known_at.hour, 16)

    def test_cftc_adapter_keeps_dataset_semantics_external(self) -> None:
        url = "https://publicreporting.cftc.gov/resource/abcd-1234.json?%24limit=2"
        client = HTTPSClient(fetch_bytes=RoutingFetcher({url: b'[{"market":"EUR"}]'}), sleeper=lambda _: None)
        rows = CFTCPublicAdapter(client).fetch_rows("abcd-1234", limit=2)
        self.assertEqual(rows[0]["market"], "EUR")

    def test_known_github_file_can_be_acquired_without_executing_it(self) -> None:
        url = "https://api.github.com/repos/octo/repo/contents/strategy.py?ref=main"
        payload = {"type": "file", "encoding": "base64", "content": "cHJpbnQoJ25vJyk=", "html_url": "https://github.com/octo/repo/blob/main/strategy.py"}
        client = HTTPSClient(fetch_bytes=RoutingFetcher({url: json.dumps(payload).encode()}), sleeper=lambda _: None)
        item = GitHubKnownRepositoryAdapter(client).fetch_text("octo/repo", "strategy.py")
        self.assertEqual(item.text, "print('no')")


if __name__ == "__main__":
    unittest.main()
