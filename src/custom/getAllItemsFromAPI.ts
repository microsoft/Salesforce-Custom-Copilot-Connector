import { Config } from "../models/Config";
import { Item } from "../models/Item";

/**
 * Authenticates with Salesforce using OAuth 2.0 Client Credentials flow.
 * @param config - The configuration object.
 * @returns A promise that resolves to the access token.
 */
async function getSalesforceAccessToken(config: Config): Promise<string> {
  const tokenUrl = `${config.connector.salesforce.instanceUrl}/services/oauth2/token`;
  
  const params = new URLSearchParams({
    grant_type: "client_credentials",
    client_id: config.connector.salesforce.clientId,
    client_secret: config.connector.salesforce.clientSecret,
  });

  config.context.log(`Authenticating with Salesforce at ${tokenUrl}`);

  const response = await fetch(tokenUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: params.toString(),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Failed to authenticate with Salesforce: ${response.statusText} - ${errorText}`);
  }

  const data = await response.json();
  config.context.log(`Successfully authenticated with Salesforce`);
  return data.access_token;
}

/**
 * Generic function to fetch records from Salesforce using SOQL.
 * @param config - The configuration object.
 * @param accessToken - The Salesforce access token.
 * @param objectType - The Salesforce object type (e.g., 'Account', 'Lead').
 * @param fields - Comma-separated list of fields to select.
 * @param since - Optional date to filter records updated after this date.
 * @returns A promise that resolves to an array of records with object type.
 */
async function fetchSalesforceRecords(
  config: Config,
  accessToken: string,
  objectType: string,
  fields: string,
  since?: Date
): Promise<any[]> {
  const baseUrl = config.connector.salesforce.instanceUrl;
  const apiVersion = config.connector.salesforce.apiVersion;
  
  // Build SOQL query with LIMIT 10 to avoid rate limits
  let soql = `SELECT ${fields} FROM ${objectType} LIMIT 10`;

  const queryUrl = `${baseUrl}/services/data/${apiVersion}/query?q=${encodeURIComponent(soql)}`;

  config.context.log(`Querying Salesforce ${objectType}: ${soql}`);

  const response = await fetch(queryUrl, {
    method: "GET",
    headers: {
      "accept": "application/json",
      "accept-language": "en-US,en;q=0.9,en-IN;q=0.8",
      "content-type": "application/json",
      "authorization": `Bearer ${accessToken}`,
    },
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Failed to fetch ${objectType} from Salesforce: ${response.statusText} - ${errorText}`);
  }

  const data = await response.json();
  config.context.log(`Fetched ${data.records?.length || 0} ${objectType} records from Salesforce`);
  
  // Add objectType to each record for identification
  const recordsWithType = (data.records || []).map((record: any) => ({
    ...record,
    objectType: objectType
  }));
  
  return recordsWithType;
}

// [Customization point]
// Fetch 6 Salesforce objects: 5 standard CRM objects + 1 custom object (Customer Project)
/**
 * Get all Salesforce CRM records, yielding data as soon as it's available.
 * @param config - The configuration object containing Salesforce credentials.
 * @param since - Optional date to filter records updated after this date.
 */
export async function* getAllItemsFromAPI(
  config: Config,
  since?: Date
): AsyncGenerator<Item> {
  // Authenticate with Salesforce
  const accessToken = await getSalesforceAccessToken(config);

  // Define the Salesforce objects to fetch with their fields (5 standard + 1 custom)
  const objectConfigs = [
    {
      type: 'Account',
      fields: 'Id, Name, Type, Industry, Phone, Website, BillingCity, BillingState, BillingCountry, AccountNumber, TickerSymbol, Site'
    },
    {
      type: 'Lead',
      fields: 'Id, FirstName, LastName, Company, Title, Email, Phone, MobilePhone, Fax, Status, LeadSource, City, State, Country, OwnerId, IsConverted, CreatedById'
    },
    {
      type: 'Contact',
      fields: 'Id, FirstName, LastName, Email, Phone, MobilePhone, HomePhone, OtherPhone, Title, Department, AccountId, MailingCity, MailingState, MailingCountry, AssistantName, AssistantPhone'
    },
    {
      type: 'Opportunity',
      fields: 'Id, Name, StageName, Amount, CloseDate, Probability, AccountId, Type, LeadSource, OwnerId, LastModifiedDate'
    },
    {
      type: 'Case',
      fields: 'Id, CaseNumber, Subject, Status, Priority, Origin, Reason, AccountId, ContactId, Description, OwnerId, CreatedDate, ClosedDate, IsClosed, LastModifiedById'
    },
    {
      type: 'Customer_Project__c',
      fields: 'Id, Name, Account__c, CreatedById, CreatedDate, LastModifiedById, LastModifiedDate, Project_description__c'
    }
  ];

  // Fetch and yield records from each object type
  for (const objectConfig of objectConfigs) {
    const records = await fetchSalesforceRecords(
      config,
      accessToken,
      objectConfig.type,
      objectConfig.fields,
      since
    );

    // Yield each record as an Item
    for (const record of records) {
      // Remove any quote characters from the URL
      const cleanUrl = `${config.connector.salesforce.instanceUrl}/${record.Id}`.replace(/['"]/g, '');
      
      yield {
        ...record,
        url: cleanUrl,
      } as Item;
    }
  }
}
