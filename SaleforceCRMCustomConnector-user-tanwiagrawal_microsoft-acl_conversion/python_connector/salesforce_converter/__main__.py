"""
CLI entry point for salesforce_converter.
Usage: python -m salesforce_converter
   or: python salesforce_converter/__main__.py
"""

import json
import sys
from pathlib import Path

# Allow running as `python __main__.py` in addition to `python -m salesforce_converter`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from salesforce_converter.converter import SalesforceConverter


def main():
    converter = SalesforceConverter("https://ap15.salesforce.com")

    # Sample Salesforce API response
    sf_response = {
        "totalSize": 2,
        "done": True,
        "records": [
            {
                "attributes": {"type": "Account"},
                "Id": "0012v00002RkkJnAAJ",
                "Name": "GenePoint",
                "Description": "Genomics company",
                "AccountNumber": "CC978213",
                "BillingAddress": {
                    "street": "345 Shoreline Park",
                    "city": "Mountain View",
                    "state": "CA",
                    "postalCode": "94043",
                    "country": "US",
                },
                "Phone": "(650) 867-3450",
                "IsDeleted": False,
                "OwnerId": "005X",
                "Owner": {"Name": "Rohit"},
                "CreatedById": "005C",
                "CreatedBy": {"Name": "John"},
                "CreatedDate": "2019-06-14T17:35:22Z",
                "LastModifiedById": "005M",
                "LastModifiedBy": {"Name": "Rohit"},
                "LastModifiedDate": "2025-03-10T09:22:15Z",
                "Contacts": {
                    "totalSize": 1,
                    "done": True,
                    "records": [
                        {
                            "attributes": {"type": "Contact"},
                            "Id": "003AAAAAAAAAAAAAAQ",
                            "Name": "Edna Frank",
                            "Email": "edna@genepoint.com",
                            "Account": {"Id": "0012v00002RkkJnAAJ", "Name": "GenePoint"},
                            "IsDeleted": False,
                            "OwnerId": "005X",
                            "Owner": {"Name": "Rohit"},
                            "CreatedById": "005C",
                            "CreatedBy": {"Name": "John"},
                            "CreatedDate": "2019-06-14T17:35:22Z",
                            "LastModifiedById": "005M",
                            "LastModifiedBy": {"Name": "Rohit"},
                            "LastModifiedDate": "2025-03-10T09:22:15Z",
                        }
                    ],
                },
            },
            {
                "attributes": {"type": "Account"},
                "Id": "001DDDDDDDDDDDDDDD",
                "Name": "Globex",
                "Description": "Evil corp",
                "AccountNumber": "GX001",
                "Phone": "(555) 123-4567",
                "IsDeleted": False,
                "OwnerId": "005X",
                "Owner": {"Name": "Alice"},
                "CreatedById": "005C",
                "CreatedBy": {"Name": "Bob"},
                "CreatedDate": "2024-01-01T00:00:00Z",
                "LastModifiedById": "005M",
                "LastModifiedBy": {"Name": "Alice"},
                "LastModifiedDate": "2025-01-01T00:00:00Z",
            },
        ],
    }

    items = converter.convert(sf_response)

    print(f"Total items: {len(items)}")
    print(json.dumps(items, indent=2, default=str))


if __name__ == "__main__":
    main()
