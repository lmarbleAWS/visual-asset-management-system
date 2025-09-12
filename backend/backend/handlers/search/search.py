# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
from handlers.auth import request_to_claims
import boto3
import os
from customLogging.logger import safeLogger
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEvent
from urllib.parse import urlparse
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth, RequestError
from common.validators import validate
from common import get_ssm_parameter_value
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer

logger = safeLogger(service="Search")
claims_and_roles = {}

try:
    asset_table = os.environ['ASSET_STORAGE_TABLE_NAME']
    database_table = os.environ['DATABASE_STORAGE_TABLE_NAME']

except Exception as e:
    logger.exception("Failed loading environment variables")
    raise

dbResource = boto3.resource('dynamodb')
dbClient = boto3.client('dynamodb')
deserializer = TypeDeserializer()

#
# Single doc Example
#
# document = {
#   'title': 'Moneyball',
#   'director': 'Bennett Miller',
#   'year': '2011'
# }
#
# response = client.index(
#     index = 'python-test-index',
#     body = document,
#     id = '1',
#     refresh = True
# )
#
# Bulk indexing example
#
# movies = """
#   { "index" : { "_index" : "my-dsl-index", "_id" : "2" } }
#   { "title" : "Interstellar",
#       "director" : "Christopher Nolan", "year" : "2014"}
#   { "create" : { "_index" : "my-dsl-index", "_id" : "3" } }
#   { "title" : "Star Trek Beyond", "director" : "Justin Lin", "year" : "2015"}
#   { "update" : {"_id" : "3", "_index" : "my-dsl-index" } }
#   { "doc" : {"year" : "2016"} }'
# """
# strip whitespace from each line
# movies = "\n".join([line.strip() for line in movies.split('\n')])
#
# client.bulk(movies)


class ValidationError(Exception):
    def __init__(self, code: int, resp: object) -> None:
        self.code = code
        self.resp = resp


def get_database(database_id):
    tableDb = dbResource.Table(database_table)

    db_response = tableDb.get_item(
        Key={
            'databaseId': database_id
        }
    )

    database = db_response.get("Item", {})
    allowed = False

    if database:
        # Add Casbin Enforcer to check if the current user has permissions to retrieve the asset for a database:
        database.update({
            "object__type": "asset"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(database, "GET"):
                allowed = True

    return database_id if allowed else None


def get_databases(show_deleted=False):
    paginatorDb = dbClient.get_paginator('scan')
    operator = "NOT_CONTAINS"
    if show_deleted:
        operator = "CONTAINS"
    db_filter = {
        "databaseId": {
            "AttributeValueList": [{"S": "#deleted"}],
            "ComparisonOperator": f"{operator}"
        }
    }
    page_iteratorDb = paginatorDb.paginate(
        TableName=database_table,
        ScanFilter=db_filter,
        PaginationConfig={
            'MaxItems': 1000,
            'PageSize': 1000,
            'StartingToken': None
        }
    ).build_full_result()

    pageIteratorItems = []
    pageIteratorItems.extend(page_iteratorDb['Items'])

    while 'NextToken' in page_iteratorDb:
        nextToken = page_iteratorDb['NextToken']
        page_iteratorDb = paginatorDb.paginate(
            TableName=database_table,
            ScanFilter=db_filter,
            PaginationConfig={
                'MaxItems': 1000,
                'PageSize': 1000,
                'StartingToken': nextToken
            }
        ).build_full_result()

        pageIteratorItems.extend(page_iteratorDb['Items'])

    result = {}
    items = []
    for item in pageIteratorItems:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}

        # Add Casbin Enforcer to check if the current user has permissions to retrieve the asset for a database:
        deserialized_document.update({
            "object__type": "asset"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(deserialized_document, "GET"):
                items.append(deserialized_document)

    result['Items'] = items

    if 'NextToken' in page_iteratorDb:
        result['NextToken'] = page_iteratorDb['NextToken']
    return result      

def get_unique_mapping_fields(mapping):
    ignorePropertiesFields = ["_rectype"] #exclude these exact fields from search
    ignorePropertiesFieldPrefixes = ["num_", "date_", "geo_", "bool_"] #exclude these field prefixes from search

    arr = []
    if "mappings" in mapping and "properties" in mapping["mappings"]:
        for k, v in mapping["mappings"].get("properties", {}).items():
            #if key field not in the ignorePropertiesFields array and key field does not start with any of the prefixes in ignorePropertiesFieldPrefixes, add it to the output field array
            if k not in ignorePropertiesFields and not any(k.startswith(prefix) for prefix in ignorePropertiesFieldPrefixes):
                arr.append(str(k))

    return arr

def token_to_criteria(token):

    if token.get("propertyKey") is None or token.get("propertyKey") == "all":
        return {
            "multi_match": {
                "query": token.get("value"),
                "type": "best_fields",
                "lenient": True,
            }
        }

    else:
        return {
            "match": {
                token.get("propertyKey"): token.get("value")
            }
        }


def sanitize_sort_fields(sort_config):
    """
    Sanitizes sort configuration to handle fields that may not have proper mappings.
    Replaces problematic sort fields with safe alternatives.
    """
    if not sort_config or not isinstance(sort_config, list):
        return ["_score"]
    
    sanitized_sort = []
    
    for sort_item in sort_config:
        if isinstance(sort_item, dict):
            # Check if this is a sort on list_tags field
            if "list_tags" in sort_item:
                # Replace list_tags sort with a safe alternative
                # Use list_tags.keyword if available, otherwise fall back to _score
                try:
                    sanitized_sort.append({"list_tags.keyword": sort_item["list_tags"]})
                except:
                    # If there's any issue, fall back to score-based sorting
                    logger.warning("Unable to sort by list_tags, falling back to _score")
                    sanitized_sort.append("_score")
            else:
                # Keep other sort configurations as-is
                sanitized_sort.append(sort_item)
        else:
            # Keep string-based sorts (like "_score") as-is
            sanitized_sort.append(sort_item)
    
    # Ensure we always have at least one sort field
    if not sanitized_sort:
        sanitized_sort = ["_score"]
    
    return sanitized_sort


def property_token_filter_to_opensearch_query(token_filter, uniqueMappingFieldsForGeneralQuery = [], start=0, size=2000):
    """
    Converts a property token filter to an OpenSearch query.
    """
    must_operators = ["=", ":", None]
    must_not_operators = ["!=", "!:"]

    must_criteria = []
    must_not_criteria = []
    filter_criteria = []
    should_criteria = []

    #Add token field if not already added
    if 'tokens' not in token_filter:
        token_filter['tokens'] = []

    #Add filter token exclusions
    #token_filter["tokens"].append({"operation":"AND","operator":"!=","propertyKey":"str_databaseid","value":"#deleted"})
    #token_filter["tokens"].append({"operation":"AND","operator":"!=","propertyKey":"str_key","value":".previewFile."})

    #Add properly formatted tokens
    if len(token_filter.get("tokens", [])) > 0:
        if token_filter.get("operation", "AND").upper() == "AND":
            must_criteria = [
                token_to_criteria(tok) for tok in token_filter['tokens']
                if tok['operator'] in must_operators]

            must_not_criteria = [
                token_to_criteria(tok) for tok in token_filter['tokens']
                if tok['operator'] in must_not_operators]

        elif token_filter.get("operation").upper() == "OR":
            must_not_criteria = [
                token_to_criteria(tok) for tok in token_filter['tokens']
                if tok['operator'] in must_not_operators]
            should_criteria = [
                token_to_criteria(tok) for tok in token_filter['tokens']
                if tok['operator'] in must_operators]


    #If we have a general search query from the textbar, add that.
    if token_filter.get("query"):
        logger.info("Text field search provided... adding filter")
        for field in uniqueMappingFieldsForGeneralQuery:
            should_criteria.append({
                "wildcard": {
                    field: {
                        "value": "*" + token_filter.get("query") + "*",
                        "case_insensitive": True
                    }
                }
            })

    #Conduct exclusions
    must_not_criteria.append({
                "regexp": {
                    "str_databaseid": ".*#deleted.*"
                }
            })

    must_not_criteria.append({
            "wildcard": {
                            "str_key": {
                                "value": "*.previewFile.*",
                                "case_insensitive": True
                            }
                        }
    })


    #Conduct database access checks to reduce record count for processing
    #Parse filters and look if there is a record with "str_databaseid" in it. 
    #If there is, parse out the database name, then remove the database query string record from the original token_filter filters 
    #Test if we have access to the database, if not remove it. If so, leave it and don't add back all allowed databases
    #Example filter: "[{query_string: {query: "(_rectype:("asset"))"}}, {query_string: {query: "(str_databaseid:("test"))"}}]"
    addAllAllowedDBs = True
    if token_filter.get("filters"):
        for filter in token_filter.get("filters"):
            if filter.get("query_string", {}).get("query"):
                if "str_databaseid" in filter.get("query_string", {}).get("query"):
                    #parse out the database name from the filter
                    specificDatabaseNameProvided = filter.get("query_string", {}).get("query").split(":")[1].split("(\"")[1].split("\")")[0]
                    allowedDb = get_database(specificDatabaseNameProvided)

                    if allowedDb is None:
                        #remove the database query string record from the original token_filter filters
                        token_filter["filters"].remove(filter)
                    else:
                        #We are keeping the record because it's allowed and not adding all the allowed back
                        addAllAllowedDBs = False

    #Add now all allowed DBs if no other specified
    if addAllAllowedDBs:
        allowedDatabases = get_databases()
        databasebaseQueryString = ""

        if len(allowedDatabases.get("Items", [])) > 0:
            for allowedDatabase in allowedDatabases.get("Items", []):
                databasebaseQueryString += "\""+allowedDatabase.get("databaseId") + "\" OR "
            #Remove the last " OR "
            databasebaseQueryString = databasebaseQueryString[:-4]
        else:
            databasebaseQueryString = "\"NOACCESSDATABASE\""

        filter_criteria.append({
            "query_string": {
                "query": "(" + "str_databaseid:(" + databasebaseQueryString + "))"
            }
        })


    #Add the filters criteria
    filter_criteria.extend(token_filter.get("filters", []))

    # Sanitize sort configuration to handle mapping issues
    sanitized_sort = sanitize_sort_fields(token_filter.get("sort", ["_score"]))

    query = {
        "from": start,
        "size": size,
        "sort": sanitized_sort,
        "query": {
            "bool": {
                "must": must_criteria,
                "must_not": must_not_criteria,
                "filter": filter_criteria,
                "should": should_criteria,
            }
        },
        "highlight": {
            "pre_tags": [
                "@opensearch-dashboards-highlighted-field@"
            ],
            "post_tags": [
                "@/opensearch-dashboards-highlighted-field@"
            ],
            "fields": {
                "str_*": {},
                "list_*": {}
            },
            "fragment_size": 2147483647
        },
        "aggs": {
            "str_assettype": {
                "filter": {
                    "bool": {
                        "must_not": [
                            {
                                "regexp": {
                                    "str_databaseid": ".*#deleted.*"
                                }
                            },
                            {
                                "wildcard": {
                                    "str_key": {
                                        "value": "*.previewFile.*",
                                        "case_insensitive": True
                                    }
                                }
                            }
                        ]
                    }
                },
                "aggs": {
                    "filtered_assettype": {
                        "terms": {
                            "field": "str_assettype.raw",
                            "size": 1000
                        }
                    }
                }
            },
            "str_fileext": {
                "filter": {
                    "bool": {
                        "must_not": [
                            {
                                "regexp": {
                                    "str_databaseid": ".*#deleted.*"
                                }
                            },
                            {
                                "wildcard": {
                                    "str_key": {
                                        "value": "*.previewFile.*",
                                        "case_insensitive": True
                                    }
                                }
                            }
                        ]
                    }
                },
                "aggs": {
                    "filtered_fileext": {
                        "terms": {
                            "field": "str_fileext.raw",
                            "size": 1000
                        }
                    }
                }
            },
            "str_databaseid": {
                "filter": {
                    "bool": {
                        "must_not": [
                            {
                                "regexp": {
                                    "str_databaseid": ".*#deleted.*"
                                }
                            },
                            {
                                "wildcard": {
                                    "str_key": {
                                        "value": "*.previewFile.*",
                                        "case_insensitive": True
                                    }
                                }
                            }
                        ]
                    }
                },
                "aggs": {
                    "filtered_databaseid": {
                        "terms": {
                            "field": "str_databaseid.raw",
                            "size": 1000
                        }
                    }
                }
            },
            "list_tags": {
                "filter": {
                    "bool": {
                        "must_not": [
                            {
                                "regexp": {
                                    "str_databaseid": ".*#deleted.*"
                                }
                            },
                            {
                                "wildcard": {
                                    "str_key": {
                                        "value": "*.previewFile.*",
                                        "case_insensitive": True
                                    }
                                }
                            }
                        ]
                    }
                },
                "aggs": {
                    "filtered_tags": {
                        "terms": {
                            "field": "list_tags.keyword",
                            "size": 1000
                        }
                    }
                }
            }
        }
    }

    #filters results that are 0 score (no relevancy) when doing a general search
    if token_filter.get("query"):
        query["min_score"] = "0.01"


    return query

class SearchAOS():
    def __init__(self, host, auth, indexName):
        self.client = OpenSearch(
            hosts=[{'host': urlparse(host).hostname, 'port': 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=20
        )
        self.indexName = indexName

    @staticmethod
    def from_env(env=os.environ):
        logger.info(env.get("AOS_ENDPOINT_PARAM"))
        logger.info(env.get("AOS_INDEX_NAME_PARAM"))
        logger.info(env.get("AWS_REGION"))
        region = env.get('AWS_REGION')
        service = env.get('AOS_TYPE')  # aoss (serverless) or es (provisioned)
        aos_disabled = env.get('AOS_DISABLED')


        if aos_disabled == "true":
            return
        else:
            credentials = boto3.Session().get_credentials()
            auth = AWSV4SignerAuth(credentials, region, service)
            host = get_ssm_parameter_value('AOS_ENDPOINT_PARAM', region, env)
            indexName = get_ssm_parameter_value(
                'AOS_INDEX_NAME_PARAM', region, env)

            logger.info("AOS endpoint:" + host)
            logger.info("Index endpoint:" + indexName)

            return SearchAOS(
                host=host,
                auth=auth,
                indexName=indexName
            )

    def search(self, query):
        logger.info("aos query")
        logger.info(query)
        try:
            return self.client.search(
                body=query,
                index=self.indexName,
            )
        except Exception as e:
            # Handle specific OpenSearch mapping errors
            if "No mapping found" in str(e) and "in order to sort on" in str(e):
                logger.warning(f"Sort field mapping error: {str(e)}")
                # Remove problematic sort and retry with default sort
                if "sort" in query:
                    logger.info("Retrying search with default sort configuration")
                    query_copy = query.copy()
                    query_copy["sort"] = ["_score"]
                    return self.client.search(
                        body=query_copy,
                        index=self.indexName,
                    )
            # Re-raise the exception if it's not a mapping error we can handle
            raise e

    def mapping(self):
        return self.client.indices.get_mapping(
            self.indexName).get(self.indexName)



def lambda_handler(
    event: APIGatewayProxyEvent,
    context: LambdaContext,
    search_fn=SearchAOS.from_env,
):
    global claims_and_roles
    aos_disabled = os.environ.get('AOS_DISABLED')

    logger.info("Received event: " + json.dumps(event, indent=2))
    logger.info(event)

    try:
        #Initial Validation
        if "body" not in event and \
                event['requestContext']['http']['method'] == "POST":
            raise ValidationError(400, {"error": "Missing request body for POST"})


        #ABAC Checks
        claims_and_roles = request_to_claims(event)

        operation_allowed_on_asset = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if  casbin_enforcer.enforceAPI(event):
                operation_allowed_on_asset = True

        if operation_allowed_on_asset:

            #If AOS not disabled (i.e. OpenSearch is deployed), go the AOS route. Otherwise error
            if aos_disabled == "false":

                search_ao = search_fn()
                #Get's return a mapping for the search index (no actual asset data returned so no ABAC check)
                if event['requestContext']['http']['method'] == "GET":
                    return {
                        "statusCode": 200,
                        "body": json.dumps(search_ao.mapping()),
                    }

                #Load body for POST after taking care of GET
                try:
                    body = json.loads(event['body'])
                except json.JSONDecodeError as e:
                    logger.exception(f"Invalid JSON in request body: {e}")
                    return {
                        'statusCode': 400,
                        'body': json.dumps({"message": "Invalid JSON in request body"})
                    }

                #POST Parameters
                logger.info("Validating POST parameters")
                (valid, message) = validate({
                    'from': {
                        'value': str(body.get("from", 0)),
                        'validator': 'NUMBER'
                    },
                    'size': {
                        'value': str(body.get("size", 0)),
                        'validator': 'NUMBER'
                    },
                })
                if not valid:
                    logger.error(message)
                    response = STANDARD_JSON_RESPONSE
                    response['body'] = json.dumps({"message": message})
                    response['statusCode'] = 400
                    return response

                #Get unique mapping fields for general query
                uniqueMappingFieldsForGeneralQuery = []
                if body.get("query"):
                    uniqueMappingFieldsForGeneralQuery = get_unique_mapping_fields(search_ao.mapping())

                #get query
                query = property_token_filter_to_opensearch_query(body, uniqueMappingFieldsForGeneralQuery)

                result = search_ao.search(query)
                filtered_hits = []
                for hit in result["hits"]["hits"]:

                    #Exclude if deleted (this is a catch-all and should already be filtered through the input query)
                    if hit["_source"]["str_databaseid"].endswith("#deleted"):
                        continue

                    #Casbin ABAC check
                    hit_document = {
                        "databaseId": hit["_source"].get("str_databaseid", ""),
                        "assetName": hit["_source"].get("str_assetname", ""),
                        "tags": hit["_source"].get("list_tags", ""),
                        "assetType": hit["_source"].get("str_assettype", ""),
                        "object__type": "asset" #for the purposes of checking ABAC, this should always be type "asset" until ABAC is implemented with asset files object types
                    }

                    if len(claims_and_roles["tokens"]) > 0:
                        casbin_enforcer = CasbinEnforcer(claims_and_roles)
                        if casbin_enforcer.enforce(hit_document, "GET"):
                            filtered_hits.append(hit)

                #If a body.from and body.size is specified for paginiation, reduce down the filtered_hits to that range
                #Otherwise return full list
                if (body.get("from") or body.get("size")) and len(filtered_hits) > 0:
                    fromNum = int(body.get("from", -1))
                    sizeNum = int(body.get("size", -1))

                    if fromNum > 0 and sizeNum > 0:
                        filtered_hits_page = filtered_hits[fromNum:fromNum+sizeNum]
                    elif fromNum > 0:
                        filtered_hits_page = filtered_hits[fromNum:]
                    else: # sizeNum > 0:
                        filtered_hits_page = filtered_hits[:sizeNum]
                        
                    result["hits"]["hits"] = filtered_hits_page
                else:
                    result["hits"]["hits"] = filtered_hits

                result["hits"]["total"]["value"] = len(filtered_hits)

                # Fix aggregation structure to match expected format
                # The aggregations are now nested under filter aggregations, so we need to extract them
                if "aggregations" in result:
                    fixed_aggregations = {}
                    
                    # Extract nested aggregations and restore original structure
                    if "str_assettype" in result["aggregations"] and "filtered_assettype" in result["aggregations"]["str_assettype"]:
                        fixed_aggregations["str_assettype"] = result["aggregations"]["str_assettype"]["filtered_assettype"]
                    
                    if "str_fileext" in result["aggregations"] and "filtered_fileext" in result["aggregations"]["str_fileext"]:
                        fixed_aggregations["str_fileext"] = result["aggregations"]["str_fileext"]["filtered_fileext"]
                    
                    if "str_databaseid" in result["aggregations"] and "filtered_databaseid" in result["aggregations"]["str_databaseid"]:
                        fixed_aggregations["str_databaseid"] = result["aggregations"]["str_databaseid"]["filtered_databaseid"]
                    
                    if "list_tags" in result["aggregations"] and "filtered_tags" in result["aggregations"]["list_tags"]:
                        fixed_aggregations["list_tags"] = result["aggregations"]["list_tags"]["filtered_tags"]
                    
                    result["aggregations"] = fixed_aggregations

                return {
                    'statusCode': 200,
                    'body': json.dumps(result)
                }
            else:
                return {
                    'statusCode': 404,
                    'body': json.dumps({"message": 'Search is not available when OpenSearch feature is not enabled. '})
                }

        else:
            return {
                'statusCode': 403,
                'body': json.dumps({"message": 'Not Authorized'})
            }
    except ValidationError as ex:
        return {
            'statusCode': ex.code,
            'body': json.dumps(ex.resp)
        }
    except RequestError as e:
        # Handle OpenSearch RequestError specifically
        logger.exception(f"OpenSearch RequestError: {str(e)}")
        if "No mapping found" in str(e) and "in order to sort on" in str(e):
            return {
                'statusCode': 400,
                'body': json.dumps({"message": "Invalid sort field in search query. Please check field mappings."})
            }
        else:
            return {
                'statusCode': 400,
                'body': json.dumps({"message": f"OpenSearch query error."})
            }
    except boto3.exceptions.Boto3Error as e:
        logger.exception(f"AWS Service Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({"message": "AWS Service Error"})
        }
    except json.JSONDecodeError as e:
        logger.exception(f"JSON Decode Error: {str(e)}")
        return {
            'statusCode': 400,
            'body': json.dumps({"message": "Invalid JSON format"})
        }
    except KeyError as e:
        logger.exception(f"Key Error: {str(e)}")
        return {
            'statusCode': 400,
            'body': json.dumps({"message": "Missing required field"})
        }
    except ValueError as e:
        logger.exception(f"Value Error: {str(e)}")
        return {
            'statusCode': 400,
            'body': json.dumps({"message": "Invalid value"})
        }
    except Exception as e:
        logger.exception(f"Unexpected error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({"message": "Internal Server Error"})
        }
