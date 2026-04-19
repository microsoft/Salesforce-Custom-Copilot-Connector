"""
Item models and conversion package.

This package converts raw Salesforce SOQL query results into Microsoft Graph
``externalItem`` payloads ready for the PUT API.

Modules
-------
item_models
    Data classes representing Graph external items: :class:`SearchableItem`,
    :class:`DeletedItem`, :class:`AccessControlEntry`, and :class:`Content`.

item_converter
    The conversion engine.  :class:`SalesforceObjectHandler` maps a single
    Salesforce object type's fields to Graph schema properties.
    :class:`SalesforceConverter` is the high-level facade that accepts a SOQL
    result set and returns a list of serialised ``externalItem`` dicts.
"""
