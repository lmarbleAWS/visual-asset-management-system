# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
VAMS AI Chatbot Handler
Processes natural language queries and executes VAMS operations using AWS Bedrock
"""

import json
import os
import boto3
from typing import Dict, Any, List, Optional
from datetime import datetime
from backend.backend.common.validators import validate
from backend.backend.handlers.auth import request_to_claims
from backend.backend.handlers.authz import CasbinEnforcer
from backend.backend.common.dynamodb import to_update_expr

# Initialize AWS clients
bedrock_runtime = boto3.client('bedrock-runtime', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
dynamodb = boto3.resource('dynamodb')

# Environment variables
DATABASE_STORAGE_TABLE = os.environ["STORAGE_TABLE_NAME"]
ASSET_STORAGE_TABLE = os.environ["ASSET_STORAGE_TABLE_NAME"]

# Bedrock model configuration
BEDROCK_MODEL_ID = "anthropic.claude-sonnet-4-20250514-v1:0"  # Claude 4 Sonnet - can be configured


def lambda_handler(event, context):
    """
    Main Lambda handler for chat messages
    """
    try:
        # Parse request
        claims_and_roles = request_to_claims(event)
        
        # Authorization check
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return {
                    'statusCode': 401,
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    },
                    'body': json.dumps({'error': 'Unauthorized'})
                }
        
        body = json.loads(event.get('body', '{}'))
        
        # Extract parameters
        message = body.get('message', '')
        conversation_id = body.get('conversationId')
        page_context = body.get('context', {})
        
        # Validate input
        if not message:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Message is required'})
            }
        
        # Build system prompt with VAMS context
        system_prompt = build_system_prompt(claims_and_roles, page_context)
        
        # Call Bedrock with tool use
        bedrock_response = call_bedrock_with_tools(message, system_prompt, conversation_id)
        
        # Execute VAMS operations based on tool calls
        results = execute_vams_operations(
            bedrock_response.get('tool_calls', []),
            claims_and_roles,
            page_context
        )
        
        # Format final response
        final_response = format_chatbot_response(bedrock_response, results)
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(final_response)
        }
        
    except Exception as e:
        print(f"Error in chat handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'An error occurred processing your request',
                'message': str(e)
            })
        }


def build_system_prompt(claims_and_roles: Dict, page_context: Dict) -> str:
    """
    Build system prompt with VAMS context and available tools
    """
    user_email = claims_and_roles.get('email', 'Unknown')
    current_page = page_context.get('currentPage', 'unknown')
    current_database = page_context.get('databaseId')
    current_asset = page_context.get('assetId')
    
    prompt = f"""You are an AI assistant for the Visual Asset Management System (VAMS). 
You help users manage their 3D models, images, and other visual assets through natural language.

Current user: {user_email}
Current page: {current_page}
{f"Current database: {current_database}" if current_database else ""}
{f"Current asset: {current_asset}" if current_asset else ""}

You can help users:
- Search for assets across databases
- Get information about specific assets
- List files within assets
- View asset relationships
- List available databases and pipelines
- Execute workflows on assets

When the user asks about "this asset" or "this database", use the context provided above.
Be concise and helpful. If you need to perform operations, use the available tools.
Always respect user permissions - you can only access resources the user has access to.
"""
    return prompt


def call_bedrock_with_tools(message: str, system_prompt: str, conversation_id: Optional[str] = None) -> Dict:
    """
    Call AWS Bedrock with function calling for VAMS operations
    """
    # Define tools for Claude
    tools = [
        {
            "name": "search_assets",
            "description": "Search for assets using keywords, filters, and metadata queries",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query keywords"
                    },
                    "database_id": {
                        "type": "string",
                        "description": "Filter by specific database ID"
                    },
                    "asset_type": {
                        "type": "string",
                        "description": "Filter by asset type (e.g., '3d-model', 'image')"
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by tags"
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "get_asset_details",
            "description": "Get detailed information about a specific asset",
            "input_schema": {
                "type": "object",
                "properties": {
                    "database_id": {
                        "type": "string",
                        "description": "Database ID containing the asset"
                    },
                    "asset_id": {
                        "type": "string",
                        "description": "Asset ID to retrieve details for"
                    }
                },
                "required": ["database_id", "asset_id"]
            }
        },
        {
            "name": "list_databases",
            "description": "List all available databases",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "list_database_assets",
            "description": "List all assets in a specific database",
            "input_schema": {
                "type": "object",
                "properties": {
                    "database_id": {
                        "type": "string",
                        "description": "Database ID to list assets from"
                    }
                },
                "required": ["database_id"]
            }
        },
        {
            "name": "list_asset_files",
            "description": "List files within a specific asset",
            "input_schema": {
                "type": "object",
                "properties": {
                    "database_id": {
                        "type": "string",
                        "description": "Database ID"
                    },
                    "asset_id": {
                        "type": "string",
                        "description": "Asset ID"
                    }
                },
                "required": ["database_id", "asset_id"]
            }
        },
        {
            "name": "get_asset_relationships",
            "description": "Get relationships (parents, children, related assets) for an asset",
            "input_schema": {
                "type": "object",
                "properties": {
                    "database_id": {
                        "type": "string",
                        "description": "Database ID"
                    },
                    "asset_id": {
                        "type": "string",
                        "description": "Asset ID"
                    }
                },
                "required": ["database_id", "asset_id"]
            }
        }
    ]
    
    # Build messages for Claude
    messages = [
        {
            "role": "user",
            "content": message
        }
    ]
    
    # Call Bedrock
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2000,
        "system": system_prompt,
        "messages": messages,
        "tools": tools
    }
    
    response = bedrock_runtime.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(request_body)
    )
    
    response_body = json.loads(response['body'].read())
    
    # Parse tool calls from response
    tool_calls = []
    assistant_message = ""
    
    for content_block in response_body.get('content', []):
        if content_block.get('type') == 'text':
            assistant_message = content_block.get('text', '')
        elif content_block.get('type') == 'tool_use':
            tool_calls.append({
                'id': content_block.get('id'),
                'name': content_block.get('name'),
                'input': content_block.get('input', {})
            })
    
    return {
        'message': assistant_message,
        'tool_calls': tool_calls,
        'stop_reason': response_body.get('stop_reason')
    }


def execute_vams_operations(tool_calls: List[Dict], claims_and_roles: Dict, page_context: Dict) -> List[Dict]:
    """
    Execute VAMS API operations based on tool calls from Bedrock
    """
    results = []
    
    for tool_call in tool_calls:
        tool_name = tool_call['name']
        tool_input = tool_call['input']
        
        try:
            if tool_name == 'search_assets':
                result = search_assets_operation(tool_input, claims_and_roles)
            elif tool_name == 'get_asset_details':
                result = get_asset_details_operation(tool_input, claims_and_roles)
            elif tool_name == 'list_databases':
                result = list_databases_operation(claims_and_roles)
            elif tool_name == 'list_database_assets':
                result = list_database_assets_operation(tool_input, claims_and_roles)
            elif tool_name == 'list_asset_files':
                result = list_asset_files_operation(tool_input, claims_and_roles)
            elif tool_name == 'get_asset_relationships':
                result = get_asset_relationships_operation(tool_input, claims_and_roles)
            else:
                result = {'error': f'Unknown tool: {tool_name}'}
            
            results.append({
                'tool_id': tool_call['id'],
                'tool_name': tool_name,
                'success': 'error' not in result,
                'data': result
            })
            
        except Exception as e:
            results.append({
                'tool_id': tool_call['id'],
                'tool_name': tool_name,
                'success': False,
                'error': str(e)
            })
    
    return results


def search_assets_operation(params: Dict, claims_and_roles: Dict) -> Dict:
    """
    Search for assets using OpenSearch/simple query
    Note: This is a simplified implementation - real implementation would use VAMS search API
    """
    # TODO: Integrate with actual VAMS search endpoint
    # For now, return placeholder
    return {
        'message': 'Search functionality will be integrated with VAMS search API',
        'query': params.get('query'),
        'results': []
    }


def get_asset_details_operation(params: Dict, claims_and_roles: Dict) -> Dict:
    """
    Get detailed information about an asset
    """
    database_id = params['database_id']
    asset_id = params['asset_id']
    
    # Check authorization
    # TODO: Add proper authorization check using CasbinEnforcer
    
    # Get asset from DynamoDB
    table = dynamodb.Table(ASSET_STORAGE_TABLE)
    response = table.get_item(
        Key={
            'databaseId': database_id,
            'assetId': asset_id
        }
    )
    
    if 'Item' not in response:
        return {'error': 'Asset not found'}
    
    return {'asset': response['Item']}


def list_databases_operation(claims_and_roles: Dict) -> Dict:
    """
    List all available databases
    """
    table = dynamodb.Table(DATABASE_STORAGE_TABLE)
    response = table.scan()
    
    # TODO: Filter by user permissions
    databases = response.get('Items', [])
    
    return {
        'databases': databases,
        'count': len(databases)
    }


def list_database_assets_operation(params: Dict, claims_and_roles: Dict) -> Dict:
    """
    List assets in a database
    """
    database_id = params['database_id']
    
    table = dynamodb.Table(ASSET_STORAGE_TABLE)
    response = table.query(
        KeyConditionExpression='databaseId = :db_id',
        ExpressionAttributeValues={
            ':db_id': database_id
        }
    )
    
    assets = response.get('Items', [])
    
    return {
        'assets': assets,
        'count': len(assets),
        'database_id': database_id
    }


def list_asset_files_operation(params: Dict, claims_and_roles: Dict) -> Dict:
    """
    List files in an asset
    Note: This would integrate with the actual file listing API
    """
    return {
        'message': 'File listing will be integrated with VAMS file API',
        'database_id': params['database_id'],
        'asset_id': params['asset_id']
    }


def get_asset_relationships_operation(params: Dict, claims_and_roles: Dict) -> Dict:
    """
    Get asset relationships
    Note: This would integrate with the asset links API
    """
    return {
        'message': 'Relationships will be integrated with VAMS asset links API',
        'database_id': params['database_id'],
        'asset_id': params['asset_id']
    }


def format_chatbot_response(bedrock_response: Dict, operation_results: List[Dict]) -> Dict:
    """
    Format the final response to send back to the frontend
    """
    response = {
        'message': bedrock_response['message'],
        'timestamp': datetime.utcnow().isoformat(),
        'operations': operation_results
    }
    
    # Add summary of operations
    if operation_results:
        successful = sum(1 for r in operation_results if r.get('success'))
        response['operations_summary'] = {
            'total': len(operation_results),
            'successful': successful,
            'failed': len(operation_results) - successful
        }
    
    return response
