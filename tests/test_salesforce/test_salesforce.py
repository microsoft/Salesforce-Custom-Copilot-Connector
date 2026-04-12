from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from salesforce.api_client import (
    SalesforceErrorInfo,
    SalesforceObjectConfig,
    _extract_unsupported_fields,
    fetch_salesforce_records,
)


class StubResponse:
    def __init__(
        self,
        *,
        ok: bool,
        payload: object,
        status_code: int = 200,
        reason: str = "OK",
    ):
        self.ok = ok
        self._payload = payload
        self.status_code = status_code
        self.reason = reason
        self.text = json.dumps(payload)

    def json(self) -> object:
        return self._payload


def _extract_soql(url: str) -> str:
    return parse_qs(urlparse(url).query)["q"][0]


def test_fetch_salesforce_records_retries_without_invalid_field(monkeypatch, test_config):
    object_config = SalesforceObjectConfig(
        object_type="Account",
        fields=("Id", "Name", "AccountNumber"),
    )
    queries: list[str] = []
    responses = iter(
        [
            StubResponse(
                ok=False,
                status_code=400,
                reason="Bad Request",
                payload=[
                    {
                        "message": "No such column 'AccountNumber' on entity 'Account'.",
                        "errorCode": "INVALID_FIELD",
                    }
                ],
            ),
            StubResponse(
                ok=True,
                payload={
                    "records": [
                        {
                            "Id": "001000000000001AAA",
                            "Name": "Contoso",
                        }
                    ]
                },
            ),
        ]
    )

    def fake_get(url: str, headers: dict[str, str], timeout: int) -> StubResponse:
        assert headers["authorization"] == "Bearer token"
        assert timeout == 60
        queries.append(url)
        return next(responses)

    monkeypatch.setattr("salesforce.api_client.requests.get", fake_get)

    records = list(fetch_salesforce_records(test_config, "token", object_config))

    assert len(queries) == 2
    assert "AccountNumber" in _extract_soql(queries[0])
    assert "AccountNumber" not in _extract_soql(queries[1])
    assert records == [{"Id": "001000000000001AAA", "Name": "Contoso", "objectType": "Account"}]


def test_extract_unsupported_fields_for_relationship_path():
    error_info = SalesforceErrorInfo(
        error_code="INVALID_FIELD",
        message=(
            "Didn't understand relationship 'ConvertedAccount' in field path. "
            "If you are attempting to use a custom relationship, be sure to append the '__r' "
            "after the custom relationship name."
        ),
        raw_text="",
    )

    unsupported_fields = _extract_unsupported_fields(
        ("Id", "Name", "ConvertedAccount.Name", "ConvertedAccount.Owner.Name"),
        error_info,
    )

    assert unsupported_fields == ("ConvertedAccount.Name", "ConvertedAccount.Owner.Name")