from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator
from urllib.parse import urlencode
import logging

import requests

from connector.item_converter import METADATA_COLUMNS, load_converter_config
from connector.settings import AppConfig
from connector.utils import to_iso_z


logger = logging.getLogger("salesforce_connector")
QUERY_LIMIT = 10

LEGACY_QUERY_FIELDS: dict[str, tuple[str, ...]] = {
    "Account": (
        "Name",
        "Type",
        "Industry",
        "Phone",
        "Website",
        "BillingCity",
        "BillingState",
        "BillingCountry",
        "AccountNumber",
        "TickerSymbol",
        "Site",
    ),
    "Lead": (
        "FirstName",
        "LastName",
        "Company",
        "Title",
        "Email",
        "Phone",
        "MobilePhone",
        "Fax",
        "Status",
        "LeadSource",
        "City",
        "State",
        "Country",
        "OwnerId",
        "IsConverted",
        "CreatedById",
    ),
    "Contact": (
        "FirstName",
        "LastName",
        "Email",
        "Phone",
        "MobilePhone",
        "HomePhone",
        "OtherPhone",
        "Title",
        "Department",
        "AccountId",
        "MailingCity",
        "MailingState",
        "MailingCountry",
        "AssistantName",
        "AssistantPhone",
    ),
    "Opportunity": (
        "Name",
        "StageName",
        "Amount",
        "CloseDate",
        "Probability",
        "AccountId",
        "Type",
        "LeadSource",
        "OwnerId",
        "LastModifiedDate",
    ),
    "Case": (
        "CaseNumber",
        "Subject",
        "Status",
        "Priority",
        "Origin",
        "Reason",
        "AccountId",
        "ContactId",
        "Description",
        "OwnerId",
        "CreatedDate",
        "ClosedDate",
        "IsClosed",
        "LastModifiedById",
    ),
    "Customer_Project__c": (
        "Name",
        "Account__c",
        "CreatedById",
        "CreatedDate",
        "LastModifiedById",
        "LastModifiedDate",
        "Project_description__c",
    ),
}


@dataclass(frozen=True)
class SalesforceObjectConfig:
    object_type: str
    fields: tuple[str, ...]
    filter_condition: str = ""


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
    ordered_object_names = ["Account", "Lead", "Contact", "Opportunity", "Case"]

    for object_name in ordered_object_names:
        object_config = converter_objects.get(object_name)
        selected_fields = list((object_config or {}).get("selectedFields", {}).keys())
        legacy_fields = list(LEGACY_QUERY_FIELDS.get(object_name, ()))
        fields = _dedupe_fields(["Id", *selected_fields, *METADATA_COLUMNS, *legacy_fields])
        configs.append(
            SalesforceObjectConfig(
                object_type=object_name,
                fields=fields,
                filter_condition=(object_config or {}).get("filterCondition", ""),
            )
        )

    configs.append(
        SalesforceObjectConfig(
            object_type="Customer_Project__c",
            fields=_dedupe_fields(["Id", *LEGACY_QUERY_FIELDS["Customer_Project__c"]]),
        )
    )

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
    soql = build_soql_query(object_config, since)

    query_url = f"{base_url}/services/data/{api_version}/query?{urlencode({'q': soql})}"
    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9,en-IN;q=0.8",
        "content-type": "application/json",
        "authorization": f"Bearer {access_token}",
    }

    logger.info("Querying Salesforce %s: %s", object_config.object_type, soql)

    next_url: str | None = query_url
    fetched_count = 0

    while next_url:
        response = requests.get(next_url, headers=headers, timeout=60)
        if not response.ok:
            raise RuntimeError(
                "Failed to fetch "
                f"{object_config.object_type} from Salesforce: {response.status_code} {response.reason} - {response.text}"
            )

        data = response.json()
        for record in data.get("records", []):
            record["objectType"] = object_config.object_type
            fetched_count += 1
            yield record

        next_records_url = data.get("nextRecordsUrl")
        next_url = f"{base_url}{next_records_url}" if next_records_url else None

    logger.info("Fetched %s %s records from Salesforce", fetched_count, object_config.object_type)


def build_soql_query(object_config: SalesforceObjectConfig, since: datetime | None) -> str:
    soql = f"SELECT {', '.join(object_config.fields)} FROM {object_config.object_type}"
    where_clauses: list[str] = []
    if object_config.filter_condition:
        where_clauses.append(object_config.filter_condition)
    if since:
        where_clauses.append(f"LastModifiedDate >= {to_iso_z(since)}")
    if where_clauses:
        soql += f" WHERE {' AND '.join(where_clauses)}"
    soql += f" LIMIT {QUERY_LIMIT}"
    return soql
