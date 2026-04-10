from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Iterator
from urllib.parse import urlencode
import logging
import re

import requests

from connector.identity_sync import SalesforceConstants
from connector.item_converter import METADATA_COLUMNS, load_converter_config
from connector.settings import AppConfig
from connector.utils import to_iso_z


logger = logging.getLogger("salesforce_connector")

INVALID_FIELD_NAME_PATTERNS = (
    re.compile(r"No such column '([^']+)' on entity", re.IGNORECASE),
    re.compile(r"No such column '([^']+)' on sobject", re.IGNORECASE),
)
INVALID_RELATIONSHIP_PATTERN = re.compile(
    r"Didn't understand relationship '([^']+)' in field path",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SalesforceObjectConfig:
    object_type: str
    fields: tuple[str, ...]
    filter_condition: str = ""


@dataclass(frozen=True)
class SalesforceErrorInfo:
    error_code: str | None
    message: str | None
    raw_text: str


def _dedupe_fields(fields: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for field in fields:
        if not field or field in seen:
            continue
        seen.add(field)
        ordered.append(field)
    return tuple(ordered)


def _build_object_configs() -> tuple[SalesforceObjectConfig, ...]:
    converter_config = load_converter_config()
    converter_objects = {
        object_config["objectName"]: object_config
        for object_config in converter_config["objectList"]
    }

    configs: list[SalesforceObjectConfig] = []

    for object_name in SalesforceConstants.ORDERED_OBJECT_NAMES:
        object_config = converter_objects.get(object_name)
        selected_fields = list((object_config or {}).get("selectedFields", {}).keys())
        fields = _dedupe_fields(["Id", *selected_fields, *METADATA_COLUMNS])
        configs.append(
            SalesforceObjectConfig(
                object_type=object_name,
                fields=fields,
                filter_condition=(object_config or {}).get("filterCondition", ""),
            )
        )

    # configs.append(
    #     SalesforceObjectConfig(
    #         object_type="Customer_Project__c",
    #         fields=_dedupe_fields(["Id", *LEGACY_QUERY_FIELDS["Customer_Project__c"]]),
    #     )
    #)

    return tuple(configs)


OBJECT_CONFIGS = _build_object_configs()


def get_salesforce_access_token(config: AppConfig) -> str:
    token_url = f"{config.connector.salesforce.instance_url}/services/oauth2/token"
    logger.info("Authenticating with Salesforce at %s", token_url)

    response = requests.post(
        token_url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": config.connector.salesforce.client_id,
            "client_secret": config.connector.salesforce.client_secret,
        },
        timeout=60,
    )

    if not response.ok:
        raise RuntimeError(
            f"Failed to authenticate with Salesforce: {response.status_code} {response.reason} - {response.text}"
        )

    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError("Salesforce authentication response did not contain an access token")

    logger.info("Successfully authenticated with Salesforce")
    return access_token


def get_all_items_from_api(config: AppConfig, since: datetime | None = None) -> Iterator[dict[str, Any]]:
    access_token = get_salesforce_access_token(config)

    for object_config in OBJECT_CONFIGS:
        for record in fetch_salesforce_records(config, access_token, object_config, since):
            clean_url = f"{config.connector.salesforce.instance_url}/{record['Id']}".replace("'", "").replace('"', "")
            record["url"] = clean_url
            yield record


def fetch_salesforce_records(
    config: AppConfig,
    access_token: str,
    object_config: SalesforceObjectConfig,
    since: datetime | None = None,
) -> Iterator[dict[str, Any]]:
    base_url = config.connector.salesforce.instance_url
    api_version = config.connector.salesforce.api_version
    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9,en-IN;q=0.8",
        "content-type": "application/json",
        "authorization": f"Bearer {access_token}",
    }

    active_config = object_config

    while True:
        soql = build_soql_query(active_config, since, query_limit=config.tuning.salesforce_query_limit)
        query_url = _build_query_url(base_url, api_version, soql)
        logger.info("Querying Salesforce %s: %s", active_config.object_type, soql)

        next_url: str | None = query_url
        fetched_count = 0
        retry_requested = False

        while next_url:
            response = requests.get(next_url, headers=headers, timeout=60)
            if not response.ok:
                error_info = _extract_salesforce_error_info(response)
                retry_config, removed_fields = _build_retry_object_config(active_config, error_info)
                if retry_config is not None and next_url == query_url and fetched_count == 0:
                    logger.warning(
                        "Retrying Salesforce %s without unsupported field(s): %s",
                        active_config.object_type,
                        ", ".join(removed_fields),
                    )
                    active_config = retry_config
                    retry_requested = True
                    break

                raise RuntimeError(_format_salesforce_fetch_error(active_config.object_type, response))

            data = response.json()
            for record in data.get("records", []):
                record["objectType"] = active_config.object_type
                fetched_count += 1
                yield record

            next_records_url = data.get("nextRecordsUrl")
            next_url = f"{base_url}{next_records_url}" if next_records_url else None

        if retry_requested:
            continue

        logger.info("Fetched %s %s records from Salesforce", fetched_count, active_config.object_type)
        return


def _build_query_url(base_url: str, api_version: str, soql: str) -> str:
    return f"{base_url}/services/data/{api_version}/query?{urlencode({'q': soql})}"


def _format_salesforce_fetch_error(object_type: str, response: requests.Response) -> str:
    return (
        "Failed to fetch "
        f"{object_type} from Salesforce: {response.status_code} {response.reason} - {response.text}"
    )


def _extract_salesforce_error_info(response: requests.Response) -> SalesforceErrorInfo:
    try:
        payload = response.json()
    except ValueError:
        return SalesforceErrorInfo(error_code=None, message=None, raw_text=response.text)

    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict):
                error_code = entry.get("errorCode")
                message = entry.get("message")
                if error_code or message:
                    return SalesforceErrorInfo(error_code=error_code, message=message, raw_text=response.text)
    elif isinstance(payload, dict):
        return SalesforceErrorInfo(
            error_code=payload.get("errorCode"),
            message=payload.get("message"),
            raw_text=response.text,
        )

    return SalesforceErrorInfo(error_code=None, message=None, raw_text=response.text)


def _build_retry_object_config(
    object_config: SalesforceObjectConfig,
    error_info: SalesforceErrorInfo,
) -> tuple[SalesforceObjectConfig | None, tuple[str, ...]]:
    unsupported_fields = _extract_unsupported_fields(object_config.fields, error_info)
    if not unsupported_fields:
        return None, ()

    remaining_fields = tuple(field for field in object_config.fields if field not in set(unsupported_fields))
    if not remaining_fields or len(remaining_fields) == len(object_config.fields):
        return None, ()

    return replace(object_config, fields=remaining_fields), unsupported_fields


def _extract_unsupported_fields(
    fields: tuple[str, ...],
    error_info: SalesforceErrorInfo,
) -> tuple[str, ...]:
    if error_info.error_code != "INVALID_FIELD" or not error_info.message:
        return ()

    for pattern in INVALID_FIELD_NAME_PATTERNS:
        match = pattern.search(error_info.message)
        if match:
            return _match_exact_or_prefixed_fields(fields, match.group(1))

    relationship_match = INVALID_RELATIONSHIP_PATTERN.search(error_info.message)
    if relationship_match:
        relationship_name = relationship_match.group(1).lower()
        return tuple(
            field
            for field in fields
            if field.split(".", 1)[0].lower() == relationship_name
        )

    return ()


def _match_exact_or_prefixed_fields(fields: tuple[str, ...], candidate: str) -> tuple[str, ...]:
    candidate_lower = candidate.lower()
    exact_matches = tuple(field for field in fields if field.lower() == candidate_lower)
    if exact_matches:
        return exact_matches

    prefix = f"{candidate_lower}."
    return tuple(field for field in fields if field.lower().startswith(prefix))


def build_soql_query(object_config: SalesforceObjectConfig, since: datetime | None, query_limit: int = 10) -> str:
    soql = f"SELECT {', '.join(object_config.fields)} FROM {object_config.object_type}"
    where_clauses: list[str] = []
    if object_config.filter_condition:
        where_clauses.append(object_config.filter_condition)
    if since:
        where_clauses.append(f"LastModifiedDate >= {to_iso_z(since)}")
    if where_clauses:
        soql += f" WHERE {' AND '.join(where_clauses)}"
    soql += f" LIMIT {query_limit}"
    return soql
