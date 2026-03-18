// [Customization point]
// If you need additional properties in the item object, you can add them here
/**
 * Represents a Salesforce CRM item (Account, Lead, Contact, Opportunity, or Case).
 * This is an internal representation of the item before translated
 * into a Graph API item for further ingestion to the Graph API.
 */
export interface Item {
  Id: string;
  objectType: string;
  url: string;
  Name?: string;
  
  // Allow any additional fields from Salesforce objects
  [key: string]: any;
}

