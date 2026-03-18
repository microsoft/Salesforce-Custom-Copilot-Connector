import { Item } from "../models/Item";
import { ExternalConnectors } from "@microsoft/microsoft-graph-types";
import { getAclFromITem } from "./getAclFromItem";

// [Customization point]
// If there is additional logic to transform the item, you can add it here
// This function is used to transform Salesforce CRM items into a format that can be ingested by the Graph API.
// The item is transformed into an ExternalItem object that can be ingested by the Graph API.
// See the Graph API documentation to understand the structure of the ExternalItem object and how to convert the item into it.
// https://learn.microsoft.com/en-us/graph/api/resources/connectors-api-overview?view=graph-rest-1.0
// https://learn.microsoft.com/en-us/graph/api/externalconnectors-externalconnection-put-items?view=graph-rest-1.0

/**
 * Get a title for the item based on its type
 */
function getItemTitle(item: Item): string {
  switch (item.objectType) {
    case 'Account':
      return item.Name || item.Id;
    case 'Lead':
      return `${item.FirstName || ''} ${item.LastName || ''}`.trim() || item.Id;
    case 'Contact':
      return `${item.FirstName || ''} ${item.LastName || ''}`.trim() || item.Id;
    case 'Opportunity':
      return item.Name || item.Id;
    case 'Case':
      return item.Subject || `Case ${item.CaseNumber}` || item.Id;
    case 'Customer_Project__c':
      return item.Name || `Customer Project ${item.Id}`;
    default:
      return item.Name || item.Id;
  }
}

/**
 * Get content text for the item
 */
function getItemContent(item: Item): string {
  switch (item.objectType) {
    case 'Account':
      return `${item.Name || ''} - ${item.Type || ''} - ${item.Industry || ''} - ${item.BillingCity || ''}`.trim();
    case 'Lead':
      return `${item.FirstName || ''} ${item.LastName || ''} - ${item.Company || ''} - ${item.Title || ''} - ${item.Email || ''}`.trim();
    case 'Contact':
      return `${item.FirstName || ''} ${item.LastName || ''} - ${item.Title || ''} - ${item.Email || ''} - ${item.Department || ''}`.trim();
    case 'Opportunity':
      return `${item.Name || ''} - ${item.StageName || ''} - ${item.Amount || ''} - ${item.CloseDate || ''}`.trim();
    case 'Case':
      return `${item.Subject || ''} - ${item.Status || ''} - ${item.Priority || ''} - ${item.Description || ''}`.trim();
    case 'Customer_Project__c':
      return `Customer Project: ${item.Name || ''} - Created: ${item.CreatedDate || ''}`.trim();
    default:
      return JSON.stringify(item);
  }
}

/**
 * Transforms a Salesforce CRM item to a Graph API ExternalItem.
 * @param item - The Salesforce CRM item to transform.
 * @returns The transformed ExternalItem for Graph API ingestion.
 */
export function getExternalItemFromItem(item: Item): ExternalConnectors.ExternalItem {
  // Build properties object with all non-null fields from the item
  const properties: any = {
    "title@odata.type": "String",
    title: getItemTitle(item),
    "url@odata.type": "String",
    url: item.url,
    objectType: item.objectType,
  };

  // Mapping of Salesforce custom field names (with underscores) to valid Graph schema property names
  const fieldNameMap: Record<string, string> = {
    'Account__c': 'AccountC',
    'Project_description__c': 'ProjectDescriptionC',
    'Title': 'JobTitle',
  };

  // Add all other fields from the item (excluding special fields)
  for (const [key, value] of Object.entries(item)) {
    if (key !== 'Id' && key !== 'objectType' && key !== 'url' && key !== 'attributes' && value !== null && value !== undefined) {
      const mappedKey = fieldNameMap[key] ?? key;
      properties[mappedKey] = value;
    }
  }

  return {
    id: item.Id,
    properties,
    content: {
      value: getItemContent(item),
      type: "text",
    },
    acl: getAclFromITem(item),
  } as ExternalConnectors.ExternalItem;
}
