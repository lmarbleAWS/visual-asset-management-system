# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import re
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from common.dynamodb import validate_pagination_info
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.assetsV3 import (
    AssetFileItemModel, ListAssetFilesRequestModel, ListAssetFilesResponseModel,
    FileInfoRequestModel, FileInfoResponseModel, MoveFileRequestModel,
    CopyFileRequestModel, ArchiveFileRequestModel, UnarchiveFileRequestModel, DeleteFileRequestModel,
    FileOperationResponseModel, RevertFileVersionRequestModel, RevertFileVersionResponseModel,
    SetPrimaryFileRequestModel, SetPrimaryFileResponseModel, CreateFolderRequestModel, CreateFolderResponseModel,
    DeleteAssetPreviewResponseModel, DeleteAuxiliaryPreviewAssetFilesRequestModel, DeleteAuxiliaryPreviewAssetFilesResponseModel
)

# Configure AWS clients with retry configuration
region = os.environ.get('AWS_REGION', 'us-east-1')

# Standardized retry configuration for all AWS clients
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

s3_client = boto3.client('s3', config=retry_config)
s3_resource = boto3.resource('s3', config=retry_config)
dynamodb = boto3.resource('dynamodb', config=retry_config)
lambda_client = boto3.client('lambda', config=retry_config)
logger = safeLogger(service_name="AssetFiles")

# Load environment variables
try:
    s3_asset_buckets_table = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    asset_database_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    asset_version_files_table_name = os.environ["ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME"] 
    asset_aux_bucket_name = os.environ["S3_ASSET_AUXILIARY_BUCKET"]
    send_email_function_name = os.environ["SEND_EMAIL_FUNCTION_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
buckets_table = dynamodb.Table(s3_asset_buckets_table)
asset_table = dynamodb.Table(asset_database_table_name)
asset_version_files_table = dynamodb.Table(asset_version_files_table_name)

# Define allowed extensions
allowed_previewFile_extensions = ['.png', '.jpg', '.jpeg', '.svg', '.gif']

#######################
# Utility Functions
#######################

def send_subscription_email(database_id, asset_id):
    """Send email notifications to subscribers when an asset is updated"""
    try:
        payload = {
            'databaseId': database_id,
            'assetId': asset_id,
        }
        lambda_client.invoke(
            FunctionName=send_email_function_name,
            InvocationType='Event',
            Payload=json.dumps(payload)
        )
    except Exception as e:
        logger.exception(f"Error invoking send_email Lambda function: {e}")

def get_asset_with_permissions(databaseId: str, assetId: str, operation: str, claims_and_roles: Dict) -> Dict:
    """Get asset and verify permissions for the specified operation
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        operation: The operation to check permissions for (GET, POST, PUT, DELETE)
        claims_and_roles: The claims and roles from the request
        
    Returns:
        The asset if found and user has permissions, otherwise raises an exception
        
    Raises:
        VAMSGeneralErrorResponse: If asset not found or user doesn't have permissions
    """
    try:
        # Get the asset from DynamoDB
        response = asset_table.get_item(Key={'databaseId': databaseId, 'assetId': assetId})
        asset = response.get('Item', {})
        
        if not asset:
            raise VAMSGeneralErrorResponse("Asset not found in database. Note: Files cannot be moved cross-database.")
        
        # Check permissions
        asset["object__type"] = "asset"
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(asset, operation):
                raise VAMSGeneralErrorResponse("Not authorized to perform this operation on the asset")
        
        return asset
    except Exception as e:
        if isinstance(e, VAMSGeneralErrorResponse):
            raise e
        logger.exception(f"Error getting asset with permissions: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving asset.")

def get_default_bucket_details(bucketId):
    """Get default S3 bucket details from database default bucket DynamoDB"""
    try:

        bucket_response = buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(bucketId),
            Limit=1
        )
        # Use the first item from the query results
        bucket = bucket_response.get("Items", [{}])[0] if bucket_response.get("Items") else {}
        bucket_id = bucket.get('bucketId')
        bucket_name = bucket.get('bucketName')
        base_assets_prefix = bucket.get('baseAssetsPrefix')

        #Check to make sure we have what we need
        if not bucket_name or not base_assets_prefix:
            raise VAMSGeneralErrorResponse(f"Error getting database default bucket details.")
        
        #Make sure we end in a slash for the path
        if not base_assets_prefix.endswith('/'):
            base_assets_prefix += '/'

        # Remove leading slash from file path if present
        if base_assets_prefix.startswith('/'):
            base_assets_prefix = base_assets_prefix[1:]

        return {
            'bucketId': bucket_id,
            'bucketName': bucket_name,
            'baseAssetsPrefix': base_assets_prefix
        }
    except Exception as e:
        logger.exception(f"Error getting bucket details: {e}")
        raise VAMSGeneralErrorResponse(f"Error getting bucket details.")

def get_asset_s3_location(asset: Dict) -> Tuple[str, str]:
    """Extract bucket from asset + s3 asset table, and key from asset location
    
    Args:
        asset: The asset dictionary
        
    Returns:
        Tuple of (bucket, key)
        
    Raises:
        VAMSGeneralErrorResponse: If asset location is missing
    """
    asset_location = asset.get('assetLocation', {})
    bucket_id = asset.get('bucketId')
    
    bucketDetails = get_default_bucket_details(bucket_id)
    
    if not asset_location:
        raise VAMSGeneralErrorResponse("Asset location not found")
    
    bucket = bucketDetails.get("bucketName")
    key = asset_location.get('Key')
    
    if not key:
        raise VAMSGeneralErrorResponse("Asset key not found in asset location")
    
    return bucket, key

def resolve_asset_file_path(asset_base_key: str, file_path: str) -> str:
    """
    Intelligently resolve the full S3 key, avoiding duplication if file_path already contains the asset base key.
    
    Args:
        asset_base_key: The base key from assetLocation (e.g., "assetId/" or "custom/path/")
        file_path: The file path from the request (may or may not include the base key)
        
    Returns:
        The properly resolved S3 key without duplication
    """
    # Normalize the asset base key to ensure it ends with '/'
    if asset_base_key and not asset_base_key.endswith('/'):
        asset_base_key = asset_base_key + '/'
    
    # Remove leading slash from file path if present
    if file_path.startswith('/'):
        file_path = file_path[1:]
    
    # Check if file_path already starts with the asset_base_key
    if file_path.startswith(asset_base_key):
        # File path already contains the base key, use as-is
        logger.info(f"File path '{file_path}' already contains base key '{asset_base_key}', using as-is")
        return file_path
    else:
        # File path doesn't contain base key, combine them
        resolved_path = asset_base_key + file_path
        logger.info(f"Combined base key '{asset_base_key}' with file path '{file_path}' to get '{resolved_path}'")
        return resolved_path

def is_file_archived(bucket: str, key: str, version_id: str = None) -> bool:
    """Determine if file is archived based on S3 delete markers
    
    Args:
        bucket: The S3 bucket name
        key: The S3 object key
        version_id: Optional specific version ID to check
        
    Returns:
        True if file is archived (has delete marker), False otherwise
    """
    try:
        if version_id:
            # Check if specific version is a delete marker
            response = s3_client.list_object_versions(
                Bucket=bucket,
                Prefix=key,
                MaxKeys=1000
            )
            
            # Check if the specified version is a delete marker
            for marker in response.get('DeleteMarkers', []):
                if marker['Key'] == key and marker['VersionId'] == version_id:
                    return True
            return False
        else:
            # Check if current version is deleted (has delete marker as latest)
            try:
                s3_client.head_object(Bucket=bucket, Key=key)
                return False  # Object exists, not archived
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchKey':
                    # Object doesn't exist, check if it has delete markers
                    response = s3_client.list_object_versions(
                        Bucket=bucket,
                        Prefix=key,
                        MaxKeys=1
                    )
                    return len(response.get('DeleteMarkers', [])) > 0
                else:
                    raise
    except Exception as e:
        logger.warning(f"Error checking archive status for {key}: {e}")
        return False

def filter_archived_files(file_list: List[Dict], include_archived: bool = False) -> List[Dict]:
    """Filter out archived files unless explicitly requested
    
    Args:
        file_list: List of file dictionaries
        include_archived: Whether to include archived files
        
    Returns:
        Filtered list of files
    """
    if include_archived:
        return file_list
    
    return [f for f in file_list if not f.get('isArchived', False)]

def check_destination_file_exists(bucket: str, key: str, path_display: str) -> bool:
    """Check if a destination file exists in S3, with improved error handling for new folders
    
    Args:
        bucket: The S3 bucket
        key: The S3 object key
        path_display: The path to display in error messages
        
    Returns:
        True if file exists, False if it doesn't exist
        
    Raises:
        VAMSGeneralErrorResponse: For errors other than 'file not found'
    """
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code')
        # NoSuchKey or 404 means the file doesn't exist, which is what we want
        if error_code == 'NoSuchKey' or error_code == '404':
            return False
        # For any other error, log details and raise a user-friendly message
        logger.exception(f"Error checking destination file {key} in bucket {bucket}: {e}")
        raise VAMSGeneralErrorResponse(f"Error accessing destination path. Please verify the folder exists.")

def copy_s3_object(source_bucket: str, source_key: str, dest_bucket: str, dest_key: str, source_asset_id: str = None, source_database_id: str = None, dest_asset_id: str = None, dest_database_id: str = None) -> bool:
    """Copy an S3 object from one location to another
    
    Args:
        source_bucket: Source bucket
        source_key: Source key
        dest_bucket: Destination bucket
        dest_key: Destination key
        source_asset_id: Source asset ID (optional)
        source_database_id: Source database ID (optional)
        dest_asset_id: Destination asset ID (optional)
        dest_database_id: Destination database ID (optional)
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Check if we need to update metadata (when copying to a different asset)
        if (source_asset_id and dest_asset_id and source_asset_id != dest_asset_id) or \
           (source_database_id and dest_database_id and source_database_id != dest_database_id):
            
            # Get existing metadata from source object
            source_object = s3_client.head_object(Bucket=source_bucket, Key=source_key)
            metadata = source_object.get('Metadata', {})
            
            # Update assetid and databaseid fields while preserving other metadata
            if dest_asset_id:
                metadata['assetid'] = dest_asset_id
            if dest_database_id:
                metadata['databaseid'] = dest_database_id
            
            # Copy with updated metadata
            s3_resource.meta.client.copy(
                CopySource={'Bucket': source_bucket, 'Key': source_key},
                Bucket=dest_bucket,
                Key=dest_key,
                MetadataDirective='REPLACE',
                Metadata=metadata
            )
        else:
            # Standard copy with preserved metadata
            s3_resource.meta.client.copy(
                CopySource={'Bucket': source_bucket, 'Key': source_key},
                Bucket=dest_bucket,
                Key=dest_key
            )
        return True
    except Exception as e:
        logger.exception(f"Error copying S3 object from {source_key} to {dest_key}: {e}")
        return False

def delete_assetAuxiliary_files(prefix):
    """Delete auxiliary files for an asset
    
    Args:
        assetLocation: The asset location object with Key (dict or AssetLocationModel)
    """

    if not prefix:
        return

    # Add the folder delimiter to the end of the key if not already
    if not prefix.endswith('/'):
        prefix = prefix + '/'

    logger.info(f"Deleting Temporary Auxiliary Assets Files Under Folder Prefix: {asset_aux_bucket_name}:{prefix}")

    try:
        # Get all assets in assetAuxiliary bucket (unversioned, temporary files for the auxiliary assets) for deletion
        # Use assetLocation key as root folder key for assetAuxiliaryFiles
        assetAuxiliaryBucketFilesDeleted = []
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=asset_aux_bucket_name, Prefix=prefix):
            if 'Contents' in page:
                for item in page['Contents']:
                    assetAuxiliaryBucketFilesDeleted.append(item['Key'])
                    logger.info(f"Deleting auxiliary asset file: {item['Key']}")
                    s3_client.delete_object(Bucket=asset_aux_bucket_name, Key=item['Key'])

    except Exception as e:
        logger.exception(f"Error deleting auxiliary files (they may not exist in the first place): {e}")

    return

def move_auxiliary_files(source_key: str, dest_key: str) -> None:
    """Move auxiliary files from source prefix to destination prefix.
    Silently logs errors if operations fail.
    
    Args:
        source_key: Source key prefix
        dest_key: Destination key prefix
    """
    try:
        # List all objects with the source prefix in auxiliary bucket
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=asset_aux_bucket_name, Prefix=source_key):
            if 'Contents' not in page:
                # No auxiliary files found, nothing to move
                return
                
            for obj in page.get('Contents', []):
                source_aux_key = obj['Key']
                
                # Calculate the destination key by replacing the source prefix with destination prefix
                dest_aux_key = source_aux_key.replace(source_key, dest_key, 1)
                
                try:
                    # Copy to new location
                    s3_resource.meta.client.copy(
                        CopySource={'Bucket': asset_aux_bucket_name, 'Key': source_aux_key},
                        Bucket=asset_aux_bucket_name,
                        Key=dest_aux_key
                    )
                    
                    # Delete from old location
                    s3_client.delete_object(
                        Bucket=asset_aux_bucket_name,
                        Key=source_aux_key
                    )
                    
                    logger.info(f"Successfully moved auxiliary file from {source_aux_key} to {dest_aux_key}")
                except Exception as e:
                    # Log error but continue with other files
                    logger.warning(f"Error moving auxiliary file {source_aux_key} to {dest_aux_key}: {e}")
                    
    except Exception as e:
        # Log error but don't raise exception to caller
        logger.warning(f"Error processing auxiliary files for move operation: {e}")

def copy_auxiliary_files(source_key: str, dest_key: str) -> None:
    """Copy auxiliary files from source prefix to destination prefix.
    Silently logs errors if operations fail.
    
    Args:
        source_key: Source key prefix
        dest_key: Destination key prefix
    """
    try:
        # List all objects with the source prefix in auxiliary bucket
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=asset_aux_bucket_name, Prefix=source_key):
            if 'Contents' not in page:
                # No auxiliary files found, nothing to copy
                return
                
            for obj in page.get('Contents', []):
                source_aux_key = obj['Key']
                
                # Calculate the destination key by replacing the source prefix with destination prefix
                dest_aux_key = source_aux_key.replace(source_key, dest_key, 1)
                
                try:
                    # Copy to new location
                    s3_resource.meta.client.copy(
                        CopySource={'Bucket': asset_aux_bucket_name, 'Key': source_aux_key},
                        Bucket=asset_aux_bucket_name,
                        Key=dest_aux_key
                    )
                    
                    logger.info(f"Successfully copied auxiliary file from {source_aux_key} to {dest_aux_key}")
                except Exception as e:
                    # Log error but continue with other files
                    logger.warning(f"Error copying auxiliary file {source_aux_key} to {dest_aux_key}: {e}")
                    
    except Exception as e:
        # Log error but don't raise exception to caller
        logger.warning(f"Error processing auxiliary files for copy operation: {e}")

def delete_s3_object(bucket: str, key: str) -> bool:
    """Permanently delete an S3 object (current version only)
    
    Args:
        bucket: The S3 bucket
        key: The S3 object key
        
    Returns:
        True if successful, False otherwise
    """
    try:
        s3_client.delete_object(
            Bucket=bucket,
            Key=key
        )
        return True
    except Exception as e:
        logger.exception(f"Error deleting S3 object {key}: {e}")
        return False

def delete_s3_object_all_versions(bucket: str, key: str) -> bool:
    """Permanently delete an S3 object and all its versions
    
    Args:
        bucket: The S3 bucket
        key: The S3 object key
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # List all versions of the object
        versions_response = s3_client.list_object_versions(
            Bucket=bucket,
            Prefix=key,
            MaxKeys=1000  # Limit to 1000 versions
        )
        
        # Delete all versions
        for version in versions_response.get('Versions', []):
            if version['Key'] == key:
                s3_client.delete_object(
                    Bucket=bucket,
                    Key=key,
                    VersionId=version['VersionId']
                )
                logger.info(f"Deleted version {version['VersionId']} of {key}")
        
        # Delete all delete markers
        for marker in versions_response.get('DeleteMarkers', []):
            if marker['Key'] == key:
                s3_client.delete_object(
                    Bucket=bucket,
                    Key=key,
                    VersionId=marker['VersionId']
                )
                logger.info(f"Deleted delete marker {marker['VersionId']} of {key}")
        
        return True
    except Exception as e:
        logger.exception(f"Error deleting all versions of S3 object {key}: {e}")
        return False

def delete_s3_prefix(bucket: str, prefix: str) -> List[str]:
    """Permanently delete all objects under a prefix (current versions only)
    
    Args:
        bucket: The S3 bucket
        prefix: The S3 key prefix
        
    Returns:
        List of deleted file keys
    """
    deleted_files = []
    
    try:
        # List all objects with the prefix
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                
                # Skip if it's a folder marker (key ends with '/')
                if key.endswith('/'):
                    continue
                
                # Delete the object
                if delete_s3_object(bucket, key):
                    deleted_files.append(key)
    
    except Exception as e:
        logger.exception(f"Error deleting files under prefix {prefix}: {e}")
    
    return deleted_files

def delete_s3_prefix_all_versions(bucket: str, prefix: str) -> List[str]:
    """Permanently delete all objects and their versions under a prefix
    
    Args:
        bucket: The S3 bucket
        prefix: The S3 key prefix
        
    Returns:
        List of deleted file keys
    """
    deleted_files = []
    
    try:
        # Get all object versions under the prefix
        paginator = s3_client.get_paginator('list_object_versions')
        
        # Track keys we've already processed to avoid duplicates
        processed_keys = set()
        
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            # Process all versions
            for version in page.get('Versions', []):
                key = version['Key']
                
                # Process folder markers separately
                if key.endswith('/'):
                    if key not in processed_keys:
                        # Delete all versions of this folder marker
                        if delete_s3_object_all_versions(bucket, key):
                            deleted_files.append(key)
                            processed_keys.add(key)
                    continue
                
                # Skip if already processed
                if key in processed_keys:
                    continue
                
                # Delete all versions of this object
                if delete_s3_object_all_versions(bucket, key):
                    deleted_files.append(key)
                    processed_keys.add(key)
            
            # Check for any keys in delete markers that weren't in versions
            for marker in page.get('DeleteMarkers', []):
                key = marker['Key']
                
                # Process folder markers separately
                if key.endswith('/'):
                    if key not in processed_keys:
                        # Delete all versions of this folder marker
                        if delete_s3_object_all_versions(bucket, key):
                            deleted_files.append(key)
                            processed_keys.add(key)
                    continue
                
                # Skip if already processed
                if key in processed_keys:
                    continue
                
                # Delete all versions of this object
                if delete_s3_object_all_versions(bucket, key):
                    deleted_files.append(key)
                    processed_keys.add(key)
        
        # Check if the prefix folder itself exists and delete it if it does
        # Ensure the prefix ends with a slash for folder check
        folder_prefix = prefix if prefix.endswith('/') else prefix + '/'
        try:
            # Check if the folder marker exists
            s3_client.head_object(Bucket=bucket, Key=folder_prefix)
            # Delete the folder marker if it exists
            if delete_s3_object_all_versions(bucket, folder_prefix):
                deleted_files.append(folder_prefix)
        except ClientError as e:
            # If the folder doesn't exist, that's fine
            if e.response['Error']['Code'] != '404' and e.response['Error']['Code'] != 'NoSuchKey':
                logger.warning(f"Error checking folder marker {folder_prefix}: {e}")
    
    except Exception as e:
        logger.exception(f"Error deleting all versions under prefix {prefix}: {e}")
    
    return deleted_files

def archive_s3_prefix(bucket: str, prefix: str, databaseId: str, assetId: str) -> List[str]:
    """Archive all objects under a prefix
    
    Args:
        bucket: The S3 bucket
        prefix: The S3 key prefix
        databaseId: The database ID
        assetId: The asset ID
        
    Returns:
        List of archived file keys
    """
    archived_files = []
    
    try:
        # List all objects with the prefix
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                
                # Process folder markers separately
                if key.endswith('/'):
                    # Archive the folder marker
                    if delete_s3_object(bucket, key):
                        archived_files.append(key)
                    continue
                
                # Archive the object
                if delete_s3_object(bucket, key):
                    archived_files.append(key)
        
        # Check if the prefix folder itself exists and archive it if it does
        # Ensure the prefix ends with a slash for folder check
        folder_prefix = prefix if prefix.endswith('/') else prefix + '/'
        try:
            # Check if the folder marker exists
            s3_client.head_object(Bucket=bucket, Key=folder_prefix)
            # Archive the folder marker if it exists
            if delete_s3_object(bucket, folder_prefix):
                archived_files.append(folder_prefix)
        except ClientError as e:
            # If the folder doesn't exist, that's fine
            if e.response['Error']['Code'] != '404' and e.response['Error']['Code'] != 'NoSuchKey':
                logger.warning(f"Error checking folder marker {folder_prefix}: {e}")
    
    except Exception as e:
        logger.exception(f"Error archiving files under prefix {prefix}: {e}")
    
    return archived_files

def validate_cross_asset_permissions(source_asset: Dict, dest_asset: Dict, claims_and_roles: Dict) -> bool:
    """Validate permissions for operations involving multiple assets
    
    Args:
        source_asset: Source asset dictionary
        dest_asset: Destination asset dictionary
        claims_and_roles: The claims and roles from the request
        
    Returns:
        True if user has permissions on both assets, False otherwise
        
    Raises:
        VAMSGeneralErrorResponse: If assets are in different databases
    """
    # Ensure both assets are in the same database
    if source_asset['databaseId'] != dest_asset['databaseId']:
        raise VAMSGeneralErrorResponse("Cross-database operations are not allowed")
    
    # Check permissions on both assets
    source_asset["object__type"] = "asset"
    dest_asset["object__type"] = "asset"
    
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        
        # Need GET permission on source and POST permission on destination
        source_allowed = casbin_enforcer.enforce(source_asset, "GET")
        dest_allowed = casbin_enforcer.enforce(dest_asset, "POST")
        
        if not source_allowed:
            raise VAMSGeneralErrorResponse("Not authorized to read from source asset")
        
        if not dest_allowed:
            raise VAMSGeneralErrorResponse("Not authorized to write to destination asset")
        
        return True
    
    return False

def move_s3_object(source_bucket: str, source_key: str, dest_bucket: str, dest_key: str) -> bool:
    """Move an S3 object from one location to another
    
    Args:
        source_bucket: Source bucket
        source_key: Source key
        dest_bucket: Destination bucket
        dest_key: Destination key
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Copy the object to the new location using managed transfer for large files
        s3_resource.meta.client.copy(
            CopySource={'Bucket': source_bucket, 'Key': source_key},
            Bucket=dest_bucket,
            Key=dest_key
        )
        
        # Delete the original object
        s3_client.delete_object(
            Bucket=source_bucket,
            Key=source_key
        )
        
        return True
    except Exception as e:
        logger.exception(f"Error moving S3 object from {source_key} to {dest_key}: {e}")
        return False

# Function removed as asset versioning is now a standalone capability

def get_s3_object_metadata(bucket: str, key: str, include_versions: bool = False) -> Dict:
    """Get detailed metadata for an S3 object
    
    Args:
        bucket: The S3 bucket
        key: The S3 object key
        include_versions: Whether to include version history
        
    Returns:
        Dictionary containing object metadata and versions if requested
        
    Raises:
        VAMSGeneralErrorResponse: If object not found or error retrieving metadata
    """
    try:
        # Get object metadata
        response = s3_client.head_object(Bucket=bucket, Key=key)
        
        # Extract basic metadata
        result = {
            'fileName': os.path.basename(key),
            'key': key,
            'relativePath': '/' + key.split('/', 1)[1] if '/' in key else key,
            'isFolder': key.endswith('/'),
            'size': response.get('ContentLength'),
            'contentType': response.get('ContentType'),
            'lastModified': response['LastModified'].isoformat(),
            'etag': response.get('ETag', '').strip('"'),
            'storageClass': response.get('StorageClass', 'STANDARD'),
            'isArchived': is_file_archived(bucket, key)
        }
        
        # Add primaryType from S3 metadata (only for non-folder objects)
        if not result['isFolder']:
            metadata = response.get('Metadata', {})
            primary_type = metadata.get('vams-primarytype', '')
            result['primaryType'] = primary_type if primary_type else None
        else:
            result['primaryType'] = None
        
        # Include version history if requested
        if include_versions:
            try:
                versions_response = s3_client.list_object_versions(
                    Bucket=bucket,
                    Prefix=key,
                    MaxKeys=100  # Limit to 100 versions
                )
                
                versions = []
                for version in versions_response.get('Versions', []):
                    if version['Key'] == key:
                        # Enhanced version information
                        version_info = {
                            'versionId': version['VersionId'],
                            'lastModified': version['LastModified'].isoformat(),
                            'size': version['Size'],
                            'isLatest': version['IsLatest'],
                            'storageClass': version.get('StorageClass', 'STANDARD'),
                            'etag': version.get('ETag', '').strip('"'),
                            'isArchived': is_file_archived(bucket, key, version['VersionId'])
                        }
                        versions.append(version_info)
                
                # Sort versions by date (newest first)
                versions.sort(key=lambda x: x['lastModified'], reverse=True)
                result['versions'] = versions
            except Exception as e:
                logger.warning(f"Error retrieving version history for {key}: {e}")
                # Continue without version history
        
        return result
    
    except ClientError as e:
        logger.exception(f"Error getting S3 object metadata: {e}")
        if e.response['Error']['Code'] == 'NoSuchKey' or e.response['Error']['Code'] == '404':
            # Check if the file is archived (has delete markers)
            try:
                versions_response = s3_client.list_object_versions(
                    Bucket=bucket,
                    Prefix=key,
                    MaxKeys=100
                )
                
                # Check if the file has any delete markers or versions
                has_delete_markers = any(marker['Key'] == key for marker in versions_response.get('DeleteMarkers', []))
                has_versions = any(version['Key'] == key for version in versions_response.get('Versions', []))
                
                if has_delete_markers or has_versions:
                    # File exists but is archived, construct metadata
                    result = {
                        'fileName': os.path.basename(key),
                        'key': key,
                        'relativePath': '/' + key.split('/', 1)[1] if '/' in key else key,
                        'isFolder': key.endswith('/'),
                        'isArchived': True,
                        'storageClass': 'STANDARD'
                    }
                    
                    # Find the latest version to get additional metadata
                    latest_version = None
                    for version in versions_response.get('Versions', []):
                        if version['Key'] == key:
                            if latest_version is None or version['LastModified'] > latest_version['LastModified']:
                                latest_version = version
                    
                    # Add size and other metadata if available
                    if latest_version:
                        result['size'] = latest_version.get('Size')
                        result['lastModified'] = latest_version['LastModified'].isoformat()
                        result['etag'] = latest_version.get('ETag', '').strip('"')
                    
                    # Include version history if requested
                    if include_versions:
                        versions = []
                        
                        # Add regular versions
                        for version in versions_response.get('Versions', []):
                            if version['Key'] == key:
                                version_info = {
                                    'versionId': version['VersionId'],
                                    'lastModified': version['LastModified'].isoformat(),
                                    'size': version['Size'],
                                    'isLatest': version['IsLatest'],
                                    'storageClass': version.get('StorageClass', 'STANDARD'),
                                    'etag': version.get('ETag', '').strip('"'),
                                    'isArchived': False
                                }
                                versions.append(version_info)
                        
                        # Add delete markers
                        for marker in versions_response.get('DeleteMarkers', []):
                            if marker['Key'] == key:
                                version_info = {
                                    'versionId': marker['VersionId'],
                                    'lastModified': marker['LastModified'].isoformat(),
                                    'isLatest': marker['IsLatest'],
                                    'storageClass': 'STANDARD',
                                    'isArchived': True,
                                    'size': 0  # Add default size for delete markers
                                }
                                versions.append(version_info)
                        
                        # Sort versions by date (newest first)
                        versions.sort(key=lambda x: x['lastModified'], reverse=True)
                        result['versions'] = versions
                    
                    return result
                else:
                    # File truly doesn't exist
                    raise VAMSGeneralErrorResponse(f"File not found.")
            except Exception as inner_e:
                if isinstance(inner_e, VAMSGeneralErrorResponse):
                    raise inner_e
                logger.exception(f"Error checking archive status: {inner_e}")
                raise VAMSGeneralErrorResponse(f"Error retrieving file metadata.")
        raise VAMSGeneralErrorResponse(f"Error retrieving file metadata.")

def list_s3_objects_with_archive_status(bucket: str, prefix: str, query_params: Dict, include_archived: bool = False) -> Dict:
    """List S3 objects with pagination and archive status
    
    Args:
        bucket: The S3 bucket
        prefix: The S3 key prefix
        query_params: Dictionary containing pagination parameters
        include_archived: Whether to include archived files
        
    Returns:
        Dictionary containing the list of files and pagination token if applicable
    """
    logger.info(f"Listing files from bucket: {bucket}, prefix: {prefix}")
    
    # Configure pagination
    pagination_config = {
        'MaxItems': int(query_params.get('maxItems', 1000)),
        'PageSize': int(query_params.get('pageSize', 1000))
    }
    
    # Add starting token if provided
    if query_params.get('startingToken'):
        pagination_config['StartingToken'] = query_params['startingToken']
    
    # If prefix filter is provided, append it to the base prefix
    if query_params.get('prefix'):
        if not prefix.endswith('/'):
            prefix = prefix + '/'
        prefix = prefix + query_params['prefix'].lstrip('/')
    
    # List objects with pagination
    result = {
        "items": []
    }
    
    try:
        # First, get current objects (non-archived)
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(
            Bucket=bucket,
            Prefix=prefix,
            PaginationConfig=pagination_config
        ):
            for obj in page.get('Contents', []):
                # Extract filename from key
                file_name = os.path.basename(obj['Key'])
                
                # Determine if it's a folder (key ends with '/' or fileName is empty)
                is_folder = obj['Key'].endswith('/') or not file_name
                
                # Get relative path by removing the prefix
                relative_path = obj['Key']
                if relative_path.startswith(prefix):
                    relative_path = relative_path[len(prefix):]
                    # Ensure relative path starts with /
                    if not relative_path.startswith('/'):
                        relative_path = '/' + relative_path
                
                # Create the item with all required fields
                item = {
                    'fileName': file_name,
                    'key': obj['Key'],
                    'relativePath': relative_path,
                    'isFolder': is_folder,
                    'dateCreatedCurrentVersion': obj['LastModified'].isoformat(),
                    'storageClass': obj.get('StorageClass', 'STANDARD')
                }
                
                # Add size for non-folders
                if not is_folder:
                    item['size'] = obj['Size']
                
                # Get version ID and check archive status
                try:
                    version_info = s3_client.head_object(
                        Bucket=bucket,
                        Key=obj['Key']
                    )
                    item['versionId'] = version_info.get('VersionId', 'null')
                    
                    # Check if file is archived
                    item['isArchived'] = is_file_archived(bucket, obj['Key'])
                    
                    # Add primaryType from S3 metadata (only for non-folder objects)
                    if not is_folder:
                        metadata = version_info.get('Metadata', {})
                        primary_type = metadata.get('vams-primarytype', '')
                        item['primaryType'] = primary_type if primary_type else None
                    else:
                        item['primaryType'] = None
                    
                except Exception as e:
                    logger.warning(f"Error getting version info for {obj['Key']}: {e}")
                    item['versionId'] = 'null'
                    item['isArchived'] = False
                    item['primaryType'] = None
                
                # Only add non-archived files unless include_archived is True
                if not item['isArchived'] or include_archived:
                    result["items"].append(item)
            
            # Add next token if available
            if 'NextToken' in page:
                result['nextToken'] = page['NextToken']
        
        # If include_archived is True, also get objects with delete markers
        if include_archived:
            logger.info("Including archived files with delete markers")
            # Use list_object_versions to get delete markers
            try:
                # Create a new paginator for versions
                version_paginator = s3_client.get_paginator('list_object_versions')
                
                # Track keys we've already processed to avoid duplicates
                existing_keys = {item['key'] for item in result['items']}
                
                for page in version_paginator.paginate(
                    Bucket=bucket,
                    Prefix=prefix,
                    PaginationConfig=pagination_config
                ):
                    # Process delete markers
                    for marker in page.get('DeleteMarkers', []):
                        key = marker['Key']
                        
                        # Skip if we already have this key in our results
                        if key in existing_keys:
                            continue
                        
                        # Skip folders
                        if key.endswith('/'):
                            continue
                        
                        # Extract filename and relative path
                        file_name = os.path.basename(key)
                        relative_path = key
                        if relative_path.startswith(prefix):
                            relative_path = relative_path[len(prefix):]
                            if not relative_path.startswith('/'):
                                relative_path = '/' + relative_path
                        
                        # Find the latest version before the delete marker
                        latest_version = None
                        for version in page.get('Versions', []):
                            if version['Key'] == key:
                                latest_version = version
                                break
                        
                        # Create item for the archived file
                        item = {
                            'fileName': file_name,
                            'key': key,
                            'relativePath': relative_path,
                            'isFolder': False,
                            'dateCreatedCurrentVersion': marker['LastModified'].isoformat(),
                            'storageClass': 'STANDARD',
                            'versionId': marker['VersionId'],
                            'isArchived': True
                        }
                        
                        # Add size if we found a version
                        if latest_version:
                            item['size'] = latest_version.get('Size', 0)
                        
                        # Add to results
                        result["items"].append(item)
                        existing_keys.add(key)
                    
                    # We don't update the nextToken here as we're just supplementing the main listing
            
            except Exception as e:
                logger.warning(f"Error listing delete markers: {e}")
                # Continue with what we have, don't fail the whole request
    
    except ClientError as e:
        logger.exception(f"Error listing S3 objects: {e}")
        if e.response['Error']['Code'] == 'NoSuchKey':
            # If the prefix doesn't exist, return empty list
            return result
        raise VAMSGeneralErrorResponse(f"Error listing files.")
    
    logger.info(f"Found {len(result['items'])} files in the path")
    return result

def is_preview_file(file_path: str) -> bool:
    """Determine if a file is a preview file based on its path
    
    Args:
        file_path: The file path to check
        
    Returns:
        True if the file is a preview file, False otherwise
    """
    # Check if the file path contains the preview file pattern
    return '.previewFile.' in file_path

def get_base_file_for_preview(preview_file_path: str) -> str:
    """Get the base file path for a preview file
    
    Args:
        preview_file_path: The preview file path
        
    Returns:
        The base file path
    """
    # Remove the .previewFile.X suffix
    return preview_file_path.split('.previewFile.')[0]

def is_allowed_preview_extension(file_path: str) -> bool:
    """Check if a preview file has an allowed extension
    
    Args:
        file_path: The file path to check
        
    Returns:
        True if the file has an allowed extension, False otherwise
    """
    
    # Extract the extension after .previewFile.
    if '.previewFile.' in file_path:
        extension = '.' + file_path.split('.previewFile.')[1]
        return extension.lower() in allowed_previewFile_extensions
    
    return False

def find_preview_files_for_base(bucket: str, base_key: str) -> List[str]:
    """Find preview files for a base file
    
    Args:
        bucket: The S3 bucket
        base_key: The base file key
        
    Returns:
        List of preview file keys
    """
    preview_files = []
    
    try:
        # Get the directory and filename parts
        directory = os.path.dirname(base_key)
        filename = os.path.basename(base_key)
        
        # Create the prefix for listing objects
        prefix = f"{directory}/" if directory else ""
        
        # Create the pattern to match preview files for this base file
        pattern = f"{filename}.previewFile."
        
        logger.info(f"Searching for preview files in bucket {bucket} with prefix {prefix}")
        logger.info(f"Looking for pattern: {pattern}")
        
        # List objects in the directory
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                base_filename = os.path.basename(key)
                
                # Check if this is a preview file for our base file
                if base_filename.startswith(pattern):
                    preview_files.append(key)
        
        # Sort the preview files alphabetically
        preview_files.sort()
        
    except Exception as e:
        logger.warning(f"Error finding preview files for {base_key}: {e}")
    
    return preview_files

def find_preview_files_for_base_including_archived(bucket: str, base_key: str) -> List[Dict]:
    """Find preview files for a base file, including archived ones
    
    Args:
        bucket: The S3 bucket
        base_key: The base file key
        
    Returns:
        List of dictionaries with preview file information including key and archive status
    """
    preview_files = []
    
    try:
        # Get the directory and filename parts
        directory = os.path.dirname(base_key)
        filename = os.path.basename(base_key)
        
        # Create the prefix for listing objects
        prefix = f"{directory}/" if directory else ""
        
        # Create the pattern to match preview files for this base file
        pattern = f"{filename}.previewFile."
        
        logger.info(f"Searching for preview files (including archived) in bucket {bucket} with prefix {prefix}")
        logger.info(f"Looking for pattern: {pattern}")
        
        # Use list_object_versions to find both current and archived files
        versions_response = s3_client.list_object_versions(
            Bucket=bucket,
            Prefix=prefix,
            MaxKeys=1000
        )
        
        # Track keys we've already processed to avoid duplicates
        processed_keys = set()
        
        # Process all versions to find preview files
        for version in versions_response.get('Versions', []):
            key = version['Key']
            base_filename = os.path.basename(key)
            
            # Skip if already processed or not a preview file for our base file
            if key in processed_keys or not base_filename.startswith(pattern):
                continue
            
            # Add to processed keys to avoid duplicates
            processed_keys.add(key)
            
            # Check if this version is the latest
            is_latest = version.get('IsLatest', False)
            
            # Check if this file is archived
            is_archived = False
            for marker in versions_response.get('DeleteMarkers', []):
                if marker['Key'] == key and marker.get('IsLatest', False):
                    is_archived = True
                    break
            
            # Add to preview files list with metadata
            preview_files.append({
                'key': key,
                'isArchived': is_archived,
                'isLatest': is_latest,
                'versionId': version.get('VersionId')
            })
        
        # Also check delete markers for files that might only have delete markers
        for marker in versions_response.get('DeleteMarkers', []):
            key = marker['Key']
            base_filename = os.path.basename(key)
            
            # Skip if already processed or not a preview file for our base file
            if key in processed_keys or not base_filename.startswith(pattern):
                continue
            
            # Add to processed keys to avoid duplicates
            processed_keys.add(key)
            
            # Add to preview files list with metadata
            preview_files.append({
                'key': key,
                'isArchived': True,
                'isLatest': marker.get('IsLatest', False),
                'versionId': marker.get('VersionId')
            })
        
        # Sort the preview files alphabetically by key
        preview_files.sort(key=lambda x: x['key'])
        
        logger.info(f"Found {len(preview_files)} preview files (including archived) for {base_key}")
        
    except Exception as e:
        logger.warning(f"Error finding preview files (including archived) for {base_key}: {e}")
    
    return preview_files

def get_top_preview_file(preview_files: List[str], filter_extensions: bool = True) -> Optional[str]:
    """Get the top preview file from a list of preview files
    
    Args:
        preview_files: List of preview file keys
        filter_extensions: Whether to filter by allowed extensions
        
    Returns:
        The top preview file key or None if no valid preview files
    """
    if not preview_files:
        return None
    
    if filter_extensions:
        # Filter by allowed extensions
        allowed_files = [f for f in preview_files if is_allowed_preview_extension(f)]
        if allowed_files:
            return allowed_files[0]  # Return the first allowed file
        return None
    else:
        # Return the first file without filtering
        return preview_files[0]

def get_asset_file_versions(assetId: str, assetVersionId: str, relativeFileKey: Optional[str]) -> Optional[Dict]:
    """Get file versions for a specific asset version
    
    Args:
        assetId: The asset ID
        assetVersionId: The asset version ID
        
    Returns:
        Dictionary with file versions or None if not found
    """
    try:
        # Create partition key in the format {assetId}:{assetversionId}
        partition_key = f"{assetId}:{assetVersionId}"
        
        # Query all records with the same partition key
        if relativeFileKey:
            response = asset_version_files_table.query(
                KeyConditionExpression=Key('assetId:assetVersionId').eq(partition_key) & Key('fileKey').eq(relativeFileKey)
            )
        else:
            response = asset_version_files_table.query(
                KeyConditionExpression=Key('assetId:assetVersionId').eq(partition_key)
            )
        
        items = response.get('Items', [])
        
        # If no items found, return None
        if not items:
            return None
        
        # Reconstruct the file versions structure
        files = []
        for item in items:
            file_info = {
                'relativeKey': item.get('fileKey'),
                'versionId': item.get('versionId'),
                'size': item.get('size'),
                'lastModified': item.get('lastModified'),
                'etag': item.get('etag')
            }
            files.append(file_info)
        
        # Return in the original format for backward compatibility
        return {
            'assetId': assetId,
            'assetVersionId': assetVersionId,
            'files': files,
            'createdAt': items[0].get('createdAt') if items else datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.exception(f"Error getting asset file versions: {e}")
        return None

#######################
# API Handler Functions
#######################

def delete_file(databaseId: str, assetId: str, file_path: str, is_prefix: bool, confirm_permanent_delete: bool, claims_and_roles: Dict) -> FileOperationResponseModel:
    """Permanently delete a file or files under a prefix
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        file_path: The file path or prefix
        is_prefix: Whether to delete all files under the prefix
        confirm_permanent_delete: Safety confirmation for permanent deletion
        claims_and_roles: The claims and roles from the request
        
    Returns:
        FileOperationResponseModel with the result of the operation
    """
    # Require confirmation for permanent deletion
    if not confirm_permanent_delete:
        raise VAMSGeneralErrorResponse("Permanent deletion requires confirmation. Set confirmPermanentDelete to true.")
    
    # Get asset and verify permissions (need POST permission to modify)
    asset = get_asset_with_permissions(databaseId, assetId, "POST", claims_and_roles)
    
    # Check if trying to delete the top-level folder
    if file_path == "/" or file_path == "":
        raise VAMSGeneralErrorResponse("Cannot delete the top-level asset folder")
    
    # Get asset location
    bucket, base_key = get_asset_s3_location(asset)
    
    # Use smart path resolution to avoid duplication
    full_key = resolve_asset_file_path(base_key, file_path)
    
    # Check if path exists (including archived files)
    try:
        if not is_prefix:
            # For single file, check if it exists using list_object_versions
            # This will find both regular files and archived files (with delete markers)
            versions_response = s3_client.list_object_versions(
                Bucket=bucket,
                Prefix=full_key,
                MaxKeys=100
            )
            
            # Check if the file exists (has any versions or delete markers)
            has_versions = any(version['Key'] == full_key for version in versions_response.get('Versions', []))
            has_delete_markers = any(marker['Key'] == full_key for marker in versions_response.get('DeleteMarkers', []))
            
            if not (has_versions or has_delete_markers):
                raise VAMSGeneralErrorResponse(f"File not found.")
        else:
            # For prefix, check if at least one object exists
            response = s3_client.list_objects_v2(
                Bucket=bucket,
                Prefix=full_key,
                MaxKeys=1
            )
            if 'Contents' not in response or len(response['Contents']) == 0:
                # If no current objects, check for archived objects
                versions_response = s3_client.list_object_versions(
                    Bucket=bucket,
                    Prefix=full_key,
                    MaxKeys=1
                )
                
                has_versions = any(version['Key'].startswith(full_key) for version in versions_response.get('Versions', []))
                has_delete_markers = any(marker['Key'].startswith(full_key) for marker in versions_response.get('DeleteMarkers', []))
                
                if not (has_versions or has_delete_markers):
                    raise VAMSGeneralErrorResponse(f"No files found under prefix.")
    except ClientError as e:
        logger.exception(f"Error checking file existence: {e}")
        raise VAMSGeneralErrorResponse(f"Error checking file.")
    
    # Delete file(s)
    affected_files = []
    
    if is_prefix:
        # Delete all files under prefix including all versions
        deleted_keys = delete_s3_prefix_all_versions(bucket, full_key)

        # Delete aux files under prefix if they exist
        delete_assetAuxiliary_files(full_key)
        
        # Convert full keys to relative paths
        for key in deleted_keys:
            if key.startswith(base_key):
                relative_path = key[len(base_key):]
                affected_files.append(relative_path)
    else:
        # Check if this is a preview file - don't allow direct operations on preview files
        if is_preview_file(file_path):
            raise VAMSGeneralErrorResponse(f"Cannot directly delete preview files. Delete the base file instead.")
            
        # For base files, also delete any associated preview files
        preview_files = find_preview_files_for_base(bucket, full_key)
        for preview_file in preview_files:
            if delete_s3_object_all_versions(bucket, preview_file):
                # Get relative path for reporting
                if preview_file.startswith(base_key):
                    rel_preview_path = '/' + preview_file[len(base_key):]
                else:
                    rel_preview_path = '/' + preview_file
                
                affected_files.append(rel_preview_path)
                logger.info(f"Deleted preview file: {rel_preview_path}")
        
        # Delete the main file including all versions
        success = delete_s3_object_all_versions(bucket, full_key)

        # Delete aux files if they exist
        delete_assetAuxiliary_files(full_key)
        
        if not success:
            raise VAMSGeneralErrorResponse(f"Failed to delete file.")
        
        affected_files.append(file_path)

    # Send email for asset file change
    send_subscription_email(databaseId, assetId)
    
    # Return response
    return FileOperationResponseModel(
        success=True,
        message=f"Successfully deleted {len(affected_files)} file(s) and all versions" + 
                (f" under prefix {file_path}" if is_prefix else f": {file_path}"),
        affectedFiles=affected_files
    )

def archive_file(databaseId: str, assetId: str, file_path: str, is_prefix: bool, claims_and_roles: Dict) -> FileOperationResponseModel:
    """Archive a file or files under a prefix (soft delete)
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        file_path: The file path or prefix
        is_prefix: Whether to archive all files under the prefix
        claims_and_roles: The claims and roles from the request
        
    Returns:
        FileOperationResponseModel with the result of the operation
    """
    # Get asset and verify permissions (need POST permission to modify)
    asset = get_asset_with_permissions(databaseId, assetId, "POST", claims_and_roles)
    
    # Check if trying to archive the top-level folder
    if file_path == "/" or file_path == "":
        raise VAMSGeneralErrorResponse("Cannot archive the top-level asset folder")
    
    # Get asset location
    bucket, base_key = get_asset_s3_location(asset)
    
    # Use smart path resolution to avoid duplication
    full_key = resolve_asset_file_path(base_key, file_path)
    
    # Check if path exists and if it's already archived
    try:
        if not is_prefix:
            try:
                # For single file, check if it exists
                s3_client.head_object(Bucket=bucket, Key=full_key)
                
                # If we get here, the file exists and is not archived
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchKey':
                    # File doesn't exist, check if it's archived
                    if is_file_archived(bucket, full_key):
                        raise VAMSGeneralErrorResponse(f"File is already archived.")
                    else:
                        raise VAMSGeneralErrorResponse(f"File not found.")
                raise e
        else:
            # For prefix, check if at least one object exists
            response = s3_client.list_objects_v2(
                Bucket=bucket,
                Prefix=full_key,
                MaxKeys=1
            )
            if 'Contents' not in response or len(response['Contents']) == 0:
                raise VAMSGeneralErrorResponse(f"No files found under prefix.")
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            raise VAMSGeneralErrorResponse(f"File not found.")
        raise VAMSGeneralErrorResponse(f"Error checking file.")
    
    # Archive file(s)
    affected_files = []
    
    if is_prefix:
        # Archive all files under prefix
        archived_keys = archive_s3_prefix(bucket, full_key, databaseId, assetId)
        
        # Convert full keys to relative paths
        for key in archived_keys:
            if key.startswith(base_key):
                relative_path = key[len(base_key):]
                affected_files.append(relative_path)
    else:
        # Check if this is a preview file - allow direct archive operations on preview files
        if not is_preview_file(file_path):
            # For base files, also archive any associated preview files
            preview_files = find_preview_files_for_base(bucket, full_key)
            for preview_file in preview_files:
                if delete_s3_object(bucket, preview_file):
                    # Get relative path for reporting
                    if preview_file.startswith(base_key):
                        rel_preview_path = '/' + preview_file[len(base_key):]
                    else:
                        rel_preview_path = '/' + preview_file
                    
                    affected_files.append(rel_preview_path)
                    logger.info(f"Archived preview file: {rel_preview_path}")
        
        # Archive the main file
        success = delete_s3_object(bucket, full_key)
        
        if not success:
            raise VAMSGeneralErrorResponse(f"Failed to archive file.")
        
        affected_files.append(file_path)

    # Send email for asset file change
    send_subscription_email(databaseId, assetId)
    
    # Return response
    return FileOperationResponseModel(
        success=True,
        message=f"Successfully archived {len(affected_files)} file(s)" + 
                (f" under prefix {file_path}" if is_prefix else f": {file_path}"),
        affectedFiles=affected_files
    )

def unarchive_file(databaseId: str, assetId: str, file_path: str, claims_and_roles: Dict) -> FileOperationResponseModel:
    """Unarchive a file by creating a new version prior to the delete marker
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        file_path: The file path to unarchive
        claims_and_roles: The claims and roles from the request
        
    Returns:
        FileOperationResponseModel with the result of the operation
    """
    # Get asset and verify permissions (need POST permission to modify)
    asset = get_asset_with_permissions(databaseId, assetId, "POST", claims_and_roles)
    
    # Get asset location
    bucket, base_key = get_asset_s3_location(asset)
    
    # Use smart path resolution to avoid duplication
    full_key = resolve_asset_file_path(base_key, file_path)
    
    # Check if file exists and is archived using list_object_versions instead of head_object
    # since head_object will fail with 404 for archived files
    try:
        versions_response = s3_client.list_object_versions(
            Bucket=bucket,
            Prefix=full_key,
            MaxKeys=100
        )
        
        # Check if the file exists (has any versions or delete markers)
        has_versions = any(version['Key'] == full_key for version in versions_response.get('Versions', []))
        has_delete_markers = any(marker['Key'] == full_key for marker in versions_response.get('DeleteMarkers', []))
        
        if not (has_versions or has_delete_markers):
            raise VAMSGeneralErrorResponse(f"File not found.")
        
        # Check if the file is archived (latest version is a delete marker)
        is_archived = False
        for marker in versions_response.get('DeleteMarkers', []):
            if marker['Key'] == full_key and marker.get('IsLatest', False):
                is_archived = True
                break
        
        if not is_archived:
            raise VAMSGeneralErrorResponse(f"File is not archived.")
    except ClientError as e:
        logger.exception(f"Error checking file archive status: {e}")
        raise VAMSGeneralErrorResponse(f"Error checking file.")
    
    # Get version history to find the delete marker and the version before it
    try:
        versions_response = s3_client.list_object_versions(
            Bucket=bucket,
            Prefix=full_key,
            MaxKeys=100  # Limit to 100 versions
        )
        
        # Find the delete marker
        delete_marker = None
        for marker in versions_response.get('DeleteMarkers', []):
            if marker['Key'] == full_key and marker.get('IsLatest', False):
                delete_marker = marker
                break
        
        if not delete_marker:
            raise VAMSGeneralErrorResponse(f"Could not find delete marker for file.")
        
        # Find the latest version before the delete marker
        latest_version = None
        for version in versions_response.get('Versions', []):
            if version['Key'] == full_key:
                # Found a version, check if it's the latest one before the delete marker
                if not latest_version or version['LastModified'] > latest_version['LastModified']:
                    latest_version = version
        
        if not latest_version:
            raise VAMSGeneralErrorResponse(f"Could not find a previous version for file.")
        
        # Copy the latest version to create a new current version (effectively unarchiving)
        copy_response = s3_client.copy_object(
            CopySource={
                'Bucket': bucket,
                'Key': full_key,
                'VersionId': latest_version['VersionId']
            },
            Bucket=bucket,
            Key=full_key,
            MetadataDirective='COPY'  # Preserve metadata from source version
        )
        
        new_version_id = copy_response.get('VersionId', 'null')
        affected_files = [file_path]
        
        # If this is a base file, also unarchive any associated preview files
        if not is_preview_file(file_path):
            # Find preview files for this base file, including archived ones
            logger.info(f"Looking for preview files (including archived) for base file: {full_key}")
            preview_files_info = find_preview_files_for_base_including_archived(bucket, full_key)
            
            logger.info(f"Found {len(preview_files_info)} preview files (including archived)")
            
            for preview_file_info in preview_files_info:
                preview_file = preview_file_info['key']
                is_archived = preview_file_info['isArchived']
                
                try:
                    # Skip if the preview file is not archived
                    if not is_archived:
                        logger.info(f"Preview file {preview_file} is not archived - skipping")
                        continue
                    
                    # Get versions to find the latest version before the delete marker
                    preview_versions_response = s3_client.list_object_versions(
                        Bucket=bucket,
                        Prefix=preview_file,
                        MaxKeys=100
                    )
                    
                    # Find all versions for this preview file
                    preview_versions = []
                    for version in preview_versions_response.get('Versions', []):
                        if version['Key'] == preview_file:
                            preview_versions.append(version)
                    
                    # If no versions, skip this preview file
                    if not preview_versions:
                        logger.info(f"Preview file {preview_file} has no versions - skipping")
                        continue
                    
                    # Find the latest version
                    preview_latest_version = None
                    for version in preview_versions:
                        if not preview_latest_version or version['LastModified'] > preview_latest_version['LastModified']:
                            preview_latest_version = version
                    
                    if preview_latest_version:
                        # Copy the latest version to create a new current version (effectively unarchiving)
                        s3_client.copy_object(
                            CopySource={
                                'Bucket': bucket,
                                'Key': preview_file,
                                'VersionId': preview_latest_version['VersionId']
                            },
                            Bucket=bucket,
                            Key=preview_file,
                            MetadataDirective='COPY'  # Preserve metadata from source version
                        )
                        logger.info(f"Successfully unarchived preview file {preview_file} from version {preview_latest_version['VersionId']}")
                            
                        # Get relative path for reporting
                        if preview_file.startswith(base_key):
                            rel_preview_path = '/' + preview_file[len(base_key):]
                        else:
                            rel_preview_path = '/' + preview_file
                        
                        affected_files.append(rel_preview_path)
                        logger.info(f"Unarchived preview file: {rel_preview_path}")
                except Exception as e:
                    # Log error but continue with other preview files
                    logger.warning(f"Error unarchiving preview file {preview_file}: {e}")

        # Send email for asset file change
        send_subscription_email(databaseId, assetId)
        
        # Return response
        return FileOperationResponseModel(
            success=True,
            message=f"Successfully unarchived file: {file_path}" + 
                    (f" and {len(affected_files) - 1} associated preview files" if len(affected_files) > 1 else ""),
            affectedFiles=affected_files
        )
        
    except ClientError as e:
        logger.exception(f"Error unarchiving file: {e}")
        raise VAMSGeneralErrorResponse(f"Error unarchiving file.")

def copy_file(databaseId: str, assetId: str, source_path: str, dest_path: str, dest_asset_id: Optional[str], claims_and_roles: Dict) -> FileOperationResponseModel:
    """Copy a file within an asset or between assets in the same database
    
    Args:
        databaseId: The database ID
        assetId: The source asset ID
        source_path: The source file path
        dest_path: The destination file path
        dest_asset_id: Optional destination asset ID (if different from source)
        claims_and_roles: The claims and roles from the request
        
    Returns:
        FileOperationResponseModel with the result of the operation
    """
    # Get source asset and verify permissions
    source_asset = get_asset_with_permissions(databaseId, assetId, "GET", claims_and_roles)
    
    # Check if this is a preview file - don't allow direct operations on preview files
    if is_preview_file(source_path):
        raise VAMSGeneralErrorResponse(f"Cannot directly copy preview files. Copy the base file instead.")
    
    # Determine if this is a cross-asset operation
    is_cross_asset = dest_asset_id is not None and dest_asset_id != assetId
    
    # Get destination asset if cross-asset operation
    if is_cross_asset:
        dest_asset = get_asset_with_permissions(databaseId, dest_asset_id, "POST", claims_and_roles)
        
        # Validate cross-asset permissions
        validate_cross_asset_permissions(source_asset, dest_asset, claims_and_roles)
    else:
        # Same asset, need POST permission
        dest_asset = get_asset_with_permissions(databaseId, assetId, "POST", claims_and_roles)
    
    # Get asset locations
    source_bucket, source_base_key = get_asset_s3_location(source_asset)
    dest_bucket, dest_base_key = get_asset_s3_location(dest_asset)
    
    # Use smart path resolution to avoid duplication for both source and destination
    source_key = resolve_asset_file_path(source_base_key, source_path)
    dest_key = resolve_asset_file_path(dest_base_key, dest_path)
    
    # Check if source exists
    try:
        s3_client.head_object(Bucket=source_bucket, Key=source_key)
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            raise VAMSGeneralErrorResponse(f"Source file not found.")
        raise VAMSGeneralErrorResponse(f"Error checking source file.")
    
    # Check if destination already exists using the helper function
    if check_destination_file_exists(dest_bucket, dest_key, dest_path):
        raise VAMSGeneralErrorResponse(f"Destination file already exists.")
    
    # Copy the file
    success = copy_s3_object(
        source_bucket, 
        source_key, 
        dest_bucket, 
        dest_key,
        source_asset_id=assetId,
        source_database_id=databaseId,
        dest_asset_id=dest_asset_id if is_cross_asset else assetId,
        dest_database_id=databaseId
    )
    
    if not success:
        raise VAMSGeneralErrorResponse(f"Failed to copy file.")
    
    # Find and copy any preview files associated with this file
    logger.info(f"Looking for preview files for base file: {source_key}")
    preview_files = find_preview_files_for_base(source_bucket, source_key)
    logger.info(f"Found {len(preview_files)} preview files: {preview_files}")
    copied_preview_files = []
    
    for preview_file in preview_files:
        # Calculate the destination preview file path properly
        source_dir = os.path.dirname(source_key)
        dest_dir = os.path.dirname(dest_key)
        preview_filename = os.path.basename(preview_file)
        # Replace the base filename in the preview filename
        source_filename = os.path.basename(source_key)
        dest_filename = os.path.basename(dest_key)
        new_preview_filename = preview_filename.replace(source_filename, dest_filename, 1)
        preview_dest = os.path.join(dest_dir, new_preview_filename).replace('\\', '/')
        
        logger.info(f"Copying preview file from {preview_file} to {preview_dest}")
        
        # Copy the preview file
        copy_success = copy_s3_object(
            source_bucket, 
            preview_file, 
            dest_bucket, 
            preview_dest,
            source_asset_id=assetId,
            source_database_id=databaseId,
            dest_asset_id=dest_asset_id if is_cross_asset else assetId,
            dest_database_id=databaseId
        )
        
        if copy_success:
            # Get relative paths for reporting
            if preview_dest.startswith(dest_base_key):
                rel_preview_dest = '/' + preview_dest[len(dest_base_key):]
            else:
                rel_preview_dest = '/' + preview_dest
                
            copied_preview_files.append(rel_preview_dest)
            logger.info(f"Successfully copied preview file to {rel_preview_dest}")
        else:
            logger.error(f"Failed to copy preview file from {preview_file} to {preview_dest}")
    
    # Copy auxiliary files if they exist
    copy_auxiliary_files(source_key, dest_key)

    # Send email for asset file change
    send_subscription_email(databaseId, dest_asset_id if is_cross_asset else assetId)
    
    # Return response
    affected_files = [dest_path] + copied_preview_files
    return FileOperationResponseModel(
        success=True,
        message=f"Successfully copied file from {source_path} to {dest_path}" + 
                (f" in asset {dest_asset_id}" if is_cross_asset else "") +
                (f" and {len(copied_preview_files)} associated preview files" if copied_preview_files else ""),
        affectedFiles=affected_files
    )

def move_file(databaseId: str, assetId: str, source_path: str, dest_path: str, claims_and_roles: Dict) -> FileOperationResponseModel:
    """Move a file within an asset
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        source_path: The source file path
        dest_path: The destination file path
        claims_and_roles: The claims and roles from the request
        
    Returns:
        FileOperationResponseModel with the result of the operation
    """
    # Get asset and verify permissions (need POST permission to modify)
    asset = get_asset_with_permissions(databaseId, assetId, "POST", claims_and_roles)
    
    # Check if this is a preview file - don't allow direct operations on preview files
    if is_preview_file(source_path):
        raise VAMSGeneralErrorResponse(f"Cannot directly move preview files. Move the base file instead.")
    
    # Get asset location
    bucket, base_key = get_asset_s3_location(asset)
    
    # Use smart path resolution to avoid duplication
    source_key = resolve_asset_file_path(base_key, source_path)
    dest_key = resolve_asset_file_path(base_key, dest_path)
    
    # Check if source exists
    try:
        s3_client.head_object(Bucket=bucket, Key=source_key)
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            raise VAMSGeneralErrorResponse(f"Source file not found.")
        raise VAMSGeneralErrorResponse(f"Error checking source file.")
    
    # Check if destination already exists using the helper function
    if check_destination_file_exists(bucket, dest_key, dest_path):
        raise VAMSGeneralErrorResponse(f"Destination file already exists")
    
    # Move the file
    success = move_s3_object(bucket, source_key, bucket, dest_key)
    
    if not success:
        raise VAMSGeneralErrorResponse(f"Failed to move file ")
    
    # Find and move any preview files associated with this file
    logger.info(f"Looking for preview files for base file: {source_key}")
    preview_files = find_preview_files_for_base(bucket, source_key)
    logger.info(f"Found {len(preview_files)} preview files: {preview_files}")
    moved_preview_files = []
    
    for preview_file in preview_files:
        # Calculate the destination preview file path properly
        source_dir = os.path.dirname(source_key)
        dest_dir = os.path.dirname(dest_key)
        preview_filename = os.path.basename(preview_file)
        # Replace the base filename in the preview filename
        source_filename = os.path.basename(source_key)
        dest_filename = os.path.basename(dest_key)
        new_preview_filename = preview_filename.replace(source_filename, dest_filename, 1)
        preview_dest = os.path.join(dest_dir, new_preview_filename).replace('\\', '/')
        
        logger.info(f"Moving preview file from {preview_file} to {preview_dest}")
        
        # Move the preview file
        move_success = move_s3_object(bucket, preview_file, bucket, preview_dest)
        
        if move_success:
            # Get relative paths for reporting
            if preview_file.startswith(base_key):
                rel_preview_source = '/' + preview_file[len(base_key):]
            else:
                rel_preview_source = '/' + preview_file
                
            if preview_dest.startswith(base_key):
                rel_preview_dest = '/' + preview_dest[len(base_key):]
            else:
                rel_preview_dest = '/' + preview_dest
                
            moved_preview_files.append(rel_preview_source)
            moved_preview_files.append(rel_preview_dest)
            logger.info(f"Successfully moved preview file from {rel_preview_source} to {rel_preview_dest}")
        else:
            logger.error(f"Failed to move preview file from {preview_file} to {preview_dest}")
    
    # Move auxiliary files if they exist
    move_auxiliary_files(source_key, dest_key)

    # Send email for asset file change
    send_subscription_email(databaseId, assetId)
    
    # Return response
    affected_files = [source_path, dest_path] + moved_preview_files
    return FileOperationResponseModel(
        success=True,
        message=f"Successfully moved file from {source_path} to {dest_path}" + 
                (f" and {len(moved_preview_files) // 2} associated preview files" if moved_preview_files else ""),
        affectedFiles=affected_files
    )

def revert_file_version(databaseId: str, assetId: str, file_path: str, version_id: str, claims_and_roles: Dict) -> RevertFileVersionResponseModel:
    """Revert a file to a previous version by copying it as the new current version
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        file_path: The file path to revert
        version_id: The version ID to revert to
        claims_and_roles: The claims and roles from the request
        
    Returns:
        RevertFileVersionResponseModel with the result of the operation
    """
    # Get asset and verify permissions (need POST permission to modify)
    asset = get_asset_with_permissions(databaseId, assetId, "POST", claims_and_roles)
    
    # Get asset location
    bucket, base_key = get_asset_s3_location(asset)
    
    # Use smart path resolution to avoid duplication
    full_key = resolve_asset_file_path(base_key, file_path)
    
    # Check if file exists
    try:
        # Get object metadata with version history
        metadata = get_s3_object_metadata(bucket, full_key, include_versions=True)
        
        # Check if the specified version exists
        if not metadata.get('versions'):
            raise VAMSGeneralErrorResponse(f"No version history found for file.")
        
        version_found = False
        for version in metadata.get('versions', []):
            if version['versionId'] == version_id:
                version_found = True
                # Check if version is already the latest
                if version['isLatest']:
                    raise VAMSGeneralErrorResponse(f"Version is already the current version")
                # Check if version is archived
                if version['isArchived']:
                    raise VAMSGeneralErrorResponse(f"Cannot revert to archived version.")
                break
        
        if not version_found:
            raise VAMSGeneralErrorResponse("Version not found for file")
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            raise VAMSGeneralErrorResponse(f"File not found.")
        raise VAMSGeneralErrorResponse(f"Error checking file.")
    
    # Get the current version ID for reference
    current_version_id = next((v['versionId'] for v in metadata.get('versions', []) if v['isLatest']), None)
    
    # Copy the specified version to create a new current version
    try:
        copy_response = s3_client.copy_object(
            CopySource={
                'Bucket': bucket,
                'Key': full_key,
                'VersionId': version_id
            },
            Bucket=bucket,
            Key=full_key,
            MetadataDirective='COPY'  # Preserve metadata from source version
        )
        
        new_version_id = copy_response.get('VersionId', 'null')
        
    except Exception as e:
        logger.exception(f"Error reverting file version: {e}")
        raise VAMSGeneralErrorResponse(f"Failed to revert file version.")

    #Delete aux files for asset as they don't match anymore with the version. 
    delete_assetAuxiliary_files(full_key)

    #send email for asset file change
    send_subscription_email(databaseId, assetId)
    
    # Return response
    affected_files = [file_path]
    return RevertFileVersionResponseModel(
        success=True,
        message=f"Successfully reverted file {file_path} to version {version_id}",
        filePath=file_path,
        revertedFromVersionId=version_id,
        newVersionId=new_version_id
    )

def get_file_info(databaseId: str, assetId: str, file_path: str, include_versions: bool, claims_and_roles: Dict) -> FileInfoResponseModel:
    """Get detailed information about a specific file
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        file_path: The relative file path
        include_versions: Whether to include version history
        claims_and_roles: The claims and roles from the request
        
    Returns:
        FileInfoResponseModel with detailed file information
    """
    # Get asset and verify permissions
    asset = get_asset_with_permissions(databaseId, assetId, "GET", claims_and_roles)
    
    # Get asset location
    bucket, base_key = get_asset_s3_location(asset)
    
    # Use smart path resolution to avoid duplication
    full_key = resolve_asset_file_path(base_key, file_path)
    
    # Get object metadata
    metadata = get_s3_object_metadata(bucket, full_key, include_versions)
    
    # Check for Asset Version Mismatch
    # Get current asset version ID if available and versions are requested and not a folder
    if include_versions and 'versions' in metadata and metadata.get("isFolder", False) and asset.get('currentVersionId'):
        current_version_id = asset.get('currentVersionId', '0')
        
        if current_version_id:
            # Get the relative path without leading slash for comparison
            relative_path = file_path.lstrip('/')
            
            # Get file version for the current asset version
            asset_file_versions = get_asset_file_versions(assetId, current_version_id, relative_path)
            
            # Find the matching version record
            matching_version = None
            if asset_file_versions and asset_file_versions.get('files'):
                # Should only be one record since we filtered by relativeFileKey
                if len(asset_file_versions.get('files')) > 0:
                    matching_version = asset_file_versions.get('files')[0]
            
            # Check each version and set mismatch flag for the current version
            for version in metadata['versions']:
                if version['isLatest']:
                    # If file is archived, it's automatically a mismatch
                    if version['isArchived']:
                        version['currentAssetVersionFileVersionMismatch'] = True
                    # Otherwise check if it matches the version in asset file versions
                    elif matching_version and matching_version.get('versionId') == version['versionId']:
                        version['currentAssetVersionFileVersionMismatch'] = False
                    else:
                        version['currentAssetVersionFileVersionMismatch'] = True
    
    # Add preview file information if this is not a preview file itself
    if not is_preview_file(file_path) and not metadata.get('isFolder', False):
        # Find preview files for this base file
        preview_files = find_preview_files_for_base(bucket, full_key)
        
        # Get the top preview file with allowed extension
        top_preview_file = get_top_preview_file(preview_files)
        
        if top_preview_file:
            # Add relative path to the preview file
            if top_preview_file.startswith(base_key):
                relative_preview_path = '/' + top_preview_file[len(base_key):]
            else:
                relative_preview_path = '/' + top_preview_file
            
            metadata['previewFile'] = relative_preview_path
        else:
            metadata['previewFile'] = ""
    else:
        # For preview files or folders, set empty preview file
        metadata['previewFile'] = ""
    
    # Return response model
    return FileInfoResponseModel(**metadata)

def create_folder(databaseId: str, assetId: str, request_model: CreateFolderRequestModel, claims_and_roles):
    """Create a folder in S3 for the specified asset"""
    # Verify asset exists
    asset = get_asset_with_permissions(databaseId, assetId, "POST", claims_and_roles)
    
    # Get bucket details from asset's bucketId
    bucketDetails = get_default_bucket_details(asset['bucketId'])
    asset_bucket = bucketDetails['bucketName']
    baseAssetsPrefix = bucketDetails['baseAssetsPrefix']
    
    # Get the asset's base location
    asset_base_key = asset.get('assetLocation', {}).get('Key', f"{baseAssetsPrefix}{assetId}/")

    # Normalize the path by combining asset base key with the relative folder path
    normalized_key_path = resolve_asset_file_path(asset_base_key, request_model.relativeKey)
    
    # Create the folder in S3 (in S3, folders are represented by zero-byte objects with a trailing slash)
    try:
        s3_client.put_object(
            Bucket=asset_bucket,
            Key=normalized_key_path,
            Body=''
        )
        
        logger.info(f"Created folder {normalized_key_path} in bucket {asset_bucket}")
        
        return CreateFolderResponseModel(
            message=f"Folder created successfully",
            relativeKey=request_model.relativeKey
        )
    except Exception as e:
        logger.exception(f"Error creating folder: {e}")
        raise VAMSGeneralErrorResponse(f"Error creating folder.")

def set_primary_file(databaseId: str, assetId: str, file_path: str, primary_type: str, primary_type_other: Optional[str], claims_and_roles: Dict) -> SetPrimaryFileResponseModel:
    """Set or remove primary type metadata for a file in S3
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        file_path: The file path to set primary type for
        primary_type: The primary type value (empty string to remove, or one of the allowed values)
        primary_type_other: The custom primary type when primary_type is 'other'
        claims_and_roles: The claims and roles from the request
        
    Returns:
        SetPrimaryFileResponseModel with the result of the operation
    """
    # Get asset and verify permissions (need POST permission to modify)
    asset = get_asset_with_permissions(databaseId, assetId, "POST", claims_and_roles)
    
    # Validate that filePath doesn't end with '/' (folders not allowed)
    if file_path.endswith('/'):
        raise VAMSGeneralErrorResponse("Cannot set primary type on folders. File path must not end with '/'")
    
    # Get asset location
    bucket, base_key = get_asset_s3_location(asset)
    
    # Use smart path resolution to avoid duplication
    full_key = resolve_asset_file_path(base_key, file_path)
    
    # Check if file exists and is not archived
    try:
        current_object = s3_client.head_object(Bucket=bucket, Key=full_key)
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            # Check if file is archived
            if is_file_archived(bucket, full_key):
                raise VAMSGeneralErrorResponse(f"Cannot set primary type on archived file")
            else:
                raise VAMSGeneralErrorResponse(f"File not found")
        raise VAMSGeneralErrorResponse(f"Error checking file")
    
    # Check if file is archived
    if is_file_archived(bucket, full_key):
        raise VAMSGeneralErrorResponse(f"Cannot set primary type on archived file.")
    
    try:
        # Get current metadata
        current_metadata = current_object.get('Metadata', {})
        
        # Determine the metadata value to set
        if primary_type == '':
            # Remove the metadata attribute by excluding it from the new metadata
            new_metadata = {k: v for k, v in current_metadata.items() if k != 'vams-primarytype'}
            operation_message = f"Removed primary type metadata from file: {file_path}"
            final_primary_type = None
        else:
            # Set or update the metadata attribute
            new_metadata = current_metadata.copy()
            if primary_type == 'other':
                new_metadata['vams-primarytype'] = primary_type_other
                final_primary_type = primary_type_other
            else:
                new_metadata['vams-primarytype'] = primary_type
                final_primary_type = primary_type
            operation_message = f"Set primary type '{final_primary_type}' for file: {file_path}"
        
        # Copy the object with updated metadata (this creates a new version with the updated metadata)
        s3_client.copy_object(
            CopySource={'Bucket': bucket, 'Key': full_key},
            Bucket=bucket,
            Key=full_key,
            MetadataDirective='REPLACE',
            Metadata=new_metadata,
            ContentType=current_object.get('ContentType', 'binary/octet-stream')
        )
        
        # Send email notification for asset file change
        send_subscription_email(databaseId, assetId)
        
        # Return response
        return SetPrimaryFileResponseModel(
            success=True,
            message=operation_message,
            filePath=file_path,
            primaryType=final_primary_type
        )
        
    except Exception as e:
        logger.exception(f"Error setting primary type metadata: {e}")
        raise VAMSGeneralErrorResponse(f"Failed to set primary type metadata.")

def list_asset_files(databaseId: str, assetId: str, query_params: Dict, claims_and_roles: Dict) -> ListAssetFilesResponseModel:
    """List files for an asset
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        query_params: Dictionary containing query parameters
        claims_and_roles: The claims and roles from the request
        
    Returns:
        ListAssetFilesResponseModel with the list of files
    """
    # Get asset and verify permissions
    asset = get_asset_with_permissions(databaseId, assetId, "GET", claims_and_roles)
    
    # Get asset location
    bucket, key = get_asset_s3_location(asset)
    
    # Parse query parameters
    request_model = ListAssetFilesRequestModel(
        maxItems=query_params.get('maxItems', 1000),
        pageSize=query_params.get('pageSize', 1000),
        startingToken=query_params.get('startingToken'),
        prefix=query_params.get('prefix'),
        includeArchived=query_params.get('includeArchived', False)
    )
    
    # List files with archive status
    result = list_s3_objects_with_archive_status(
        bucket, 
        key, 
        request_model.dict(),
        request_model.includeArchived
    )
    
    # Convert to response model
    file_items = []
    preview_files = []
    base_files = {}
    
    # First pass: separate preview files and base files
    for item in result.get('items', []):
        if is_preview_file(item['key']):
            # This is a preview file
            preview_files.append(item)
        else:
            # This is a base file
            file_items.append(AssetFileItemModel(**item))
            # Store in lookup dictionary for preview file matching
            base_files[item['key']] = len(file_items) - 1
    
    # Process preview files
    orphaned_preview_files = []
    
    for preview_item in preview_files:
        # Get the base file key for this preview file
        base_key = get_base_file_for_preview(preview_item['key'])
        
        # Check if the base file exists
        if base_key in base_files:
            # Base file exists, add this preview file to it if it has an allowed extension
            if is_allowed_preview_extension(preview_item['key']):
                base_file_index = base_files[base_key]
                
                # Only add if the base file doesn't already have a preview file
                if not hasattr(file_items[base_file_index], 'previewFile') or not file_items[base_file_index].previewFile:
                    # Add relative path to the preview file
                    if preview_item['key'].startswith(key):
                        relative_preview_path = '/' + preview_item['key'][len(key):]
                    else:
                        relative_preview_path = '/' + preview_item['key']
                    
                    # Add preview file to base file
                    file_items[base_file_index].previewFile = relative_preview_path
        else:
            # Base file doesn't exist, this is an orphaned preview file
            orphaned_preview_files.append(preview_item['key'])
    
    # Log orphaned preview files
    if orphaned_preview_files:
        logger.warning(f"Found {len(orphaned_preview_files)} orphaned preview files: {orphaned_preview_files}")
    
    # Initialize previewFile field for files that don't have one
    for file_item in file_items:
        if not hasattr(file_item, 'previewFile'):
            file_item.previewFile = ""
    
    # Check for Asset Version Mismatch
    # Get current asset version ID if available
    current_version_id = None
    if asset.get('currentVersionId'):
        current_version_id = asset.get('currentVersionId', '0')
    
    # If we have a current version, check file versions against asset version files
    if current_version_id:
        # Get all file versions for the current asset version
        asset_file_versions = get_asset_file_versions(assetId, current_version_id, None)
        
        # Create a lookup dictionary for faster matching
        file_version_lookup = {}
        if asset_file_versions and asset_file_versions.get('files'):
            for file_version in asset_file_versions.get('files'):
                relative_key = file_version.get('relativeKey')
                if relative_key:
                    file_version_lookup[relative_key] = file_version
        
        # Check each file against the asset version files
        for file_item in file_items:
            # Separate Folder assets are not included ever in asset versions
            if file_item.isFolder:
                continue
                
            # If file is archived, it's automatically a mismatch
            if file_item.isArchived:
                file_item.currentAssetVersionFileVersionMismatch = True
                continue
            
            # Get the relative path without leading slash for comparison
            relative_path = file_item.relativePath.lstrip('/')
            
            # Find matching record in asset file versions
            matching_version = file_version_lookup.get(relative_path)
            
            # Set mismatch flag
            if matching_version and matching_version.get('versionId') == file_item.versionId:
                file_item.currentAssetVersionFileVersionMismatch = False
            else:
                file_item.currentAssetVersionFileVersionMismatch = True
    
    return ListAssetFilesResponseModel(
        items=file_items,
        nextToken=result.get('nextToken')
    )

def handle_delete_file(event, context) -> APIGatewayProxyResponseV2:
    """Handle DELETE /deleteFile requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})

    
        # Parse request model
        request_model = parse(body, model=DeleteFileRequestModel)
        
        # Process request
        response = delete_file(
            path_params['databaseId'],
            path_params['assetId'],
            request_model.filePath,
            request_model.isPrefix,
            request_model.confirmPermanentDelete,
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def handle_unarchive_file(event, context) -> APIGatewayProxyResponseV2:
    """Handle POST /unarchiveFile requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Parse request model
        request_model = parse(body, model=UnarchiveFileRequestModel)
        
        # Process request
        response = unarchive_file(
            path_params['databaseId'],
            path_params['assetId'],
            request_model.filePath,
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def handle_archive_file(event, context) -> APIGatewayProxyResponseV2:
    """Handle DELETE /archiveFile requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Parse request model
        request_model = parse(body, model=ArchiveFileRequestModel)
        
        # Process request
        response = archive_file(
            path_params['databaseId'],
            path_params['assetId'],
            request_model.filePath,
            request_model.isPrefix,
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def handle_copy_file(event, context) -> APIGatewayProxyResponseV2:
    """Handle POST /copyFile requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Parse request model
        request_model = parse(body, model=CopyFileRequestModel)
        
        # Process request
        response = copy_file(
            path_params['databaseId'],
            path_params['assetId'],
            request_model.sourcePath,
            request_model.destinationPath,
            request_model.destinationAssetId,
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def handle_move_file(event, context) -> APIGatewayProxyResponseV2:
    """Handle POST /moveFile requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Parse request model
        request_model = parse(body, model=MoveFileRequestModel)
        
        # Process request
        response = move_file(
            path_params['databaseId'],
            path_params['assetId'],
            request_model.sourcePath,
            request_model.destinationPath,
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def handle_file_info(event, context) -> APIGatewayProxyResponseV2:
    """Handle GET /fileInfo requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Get query parameters
        query_params = event.get('queryStringParameters', {}) or {}
        
        # Parse request body if present
        if event.get('body'):

            # Parse request body with enhanced error handling
            body = event.get('body')

            # Parse JSON body safely
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except json.JSONDecodeError as e:
                    logger.exception(f"Invalid JSON in request body: {e}")
                    return validation_error(body={'message': "Invalid JSON in request body"})
            elif isinstance(body, dict):
                body = body
            else:
                logger.error("Request body is not a string")
                return validation_error(body={'message': "Request body cannot be parsed"})
                
            # Parse request model
            request_model = parse(body, model=FileInfoRequestModel)
        else:
            # If no body, require filePath in query parameters
            if 'filePath' not in query_params:
                return validation_error(body={'message': "filePath is required in query parameters or request body"})
            
            # Create request model from query parameters
            request_model = FileInfoRequestModel(
                filePath=query_params['filePath'],
                includeVersions=query_params.get('includeVersions', 'false').lower() == 'true'
            )
        
        # Process request
        response = get_file_info(
            path_params['databaseId'],
            path_params['assetId'],
            request_model.filePath,
            request_model.includeVersions,
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def handle_revert_file_version(event, context) -> APIGatewayProxyResponseV2:
    """Handle POST /revertVersion requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        if 'versionId' not in path_params:
            return validation_error(body={'message': "No version ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
            'versionId': {
                'value': path_params['versionId'],
                'validator': 'STRING_256'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Parse request model
        request_model = parse(body, model=RevertFileVersionRequestModel)
        
        # Process request
        response = revert_file_version(
            path_params['databaseId'],
            path_params['assetId'],
            request_model.filePath,
            path_params['versionId'],
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def handle_create_folder(event, context) -> APIGatewayProxyResponseV2:
    """Handle POST /createFolder requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Parse request model
        request_model = parse(body, model=CreateFolderRequestModel)
        
        # Process request
        response = create_folder(
            path_params['databaseId'],
            path_params['assetId'],
            request_model,
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def delete_auxiliary_preview_asset_files(databaseId: str, assetId: str, file_path: str, claims_and_roles: Dict) -> DeleteAuxiliaryPreviewAssetFilesResponseModel:
    """Delete auxiliary preview asset files for a specific file path
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        file_path: The file path to delete auxiliary files for (treated as a prefix)
        claims_and_roles: The claims and roles from the request
        
    Returns:
        DeleteAuxiliaryPreviewAssetFilesResponseModel with the result of the operation
    """
    # Get asset and verify permissions (need POST permission to modify)
    asset = get_asset_with_permissions(databaseId, assetId, "POST", claims_and_roles)
    
    # Get asset location
    bucket, base_key = get_asset_s3_location(asset)
    
    # Use smart path resolution to avoid duplication
    full_key = resolve_asset_file_path(base_key, file_path)
    
    # Check if auxiliary files exist under the prefix
    file_count = 0
    deleted_files = []
    
    try:
        # List objects in the auxiliary bucket with the prefix
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=asset_aux_bucket_name, Prefix=full_key):
            if 'Contents' in page:
                file_count += len(page['Contents'])
                for item in page['Contents']:
                    deleted_files.append(item['Key'])
        
        if file_count == 0:
            raise VAMSGeneralErrorResponse(f"No auxiliary files found under prefix")
        
        # Delete the auxiliary files
        delete_assetAuxiliary_files(full_key)
        
        # Send email notification for asset change
        send_subscription_email(databaseId, assetId)
        
        return DeleteAuxiliaryPreviewAssetFilesResponseModel(
            success=True,
            message=f"Successfully deleted {file_count} auxiliary preview files under prefix",
            filePath=file_path,
            deletedCount=file_count
        )
    except Exception as e:
        if isinstance(e, VAMSGeneralErrorResponse):
            raise e
        logger.exception(f"Error deleting auxiliary preview files: {e}")
        raise VAMSGeneralErrorResponse(f"Failed to delete auxiliary preview files.")

def delete_asset_preview(databaseId: str, assetId: str, claims_and_roles: Dict) -> DeleteAssetPreviewResponseModel:
    """Delete an asset preview file and clear the previewLocation from the asset record
    
    Args:
        databaseId: The database ID
        assetId: The asset ID
        claims_and_roles: The claims and roles from the request
        
    Returns:
        DeleteAssetPreviewResponseModel with the result of the operation
    """
    # Get asset and verify permissions (need POST permission to modify)
    asset = get_asset_with_permissions(databaseId, assetId, "POST", claims_and_roles)
    
    # Check if asset has a preview
    preview_location = asset.get('previewLocation')
    if not preview_location or not preview_location.get('Key'):
        raise VAMSGeneralErrorResponse(f"Asset {assetId} does not have a preview file")
    
    # Get bucket details
    bucketDetails = get_default_bucket_details(asset.get('bucketId'))
    bucket = bucketDetails['bucketName']
    preview_key = preview_location.get('Key')
    
    # Delete the preview file from S3
    try:
        # Delete all versions of the preview file
        delete_s3_object_all_versions(bucket, preview_key)
        
        # Clear the previewLocation from the asset record
        asset.pop('previewLocation', None)
        
        # Update the asset record in DynamoDB
        asset_table.put_item(Item=asset)
        
        # Send email notification for asset change
        send_subscription_email(databaseId, assetId)
        
        return DeleteAssetPreviewResponseModel(
            success=True,
            message=f"Successfully deleted preview file for asset {assetId}",
            assetId=assetId
        )
    except Exception as e:
        logger.exception(f"Error deleting asset preview: {e}")
        raise VAMSGeneralErrorResponse(f"Failed to delete asset preview.")

def handle_delete_asset_preview(event, context) -> APIGatewayProxyResponseV2:
    """Handle DELETE /deleteAssetPreview requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Process request
        response = delete_asset_preview(
            path_params['databaseId'],
            path_params['assetId'],
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def handle_delete_auxiliary_preview_asset_files(event, context) -> APIGatewayProxyResponseV2:
    """Handle DELETE /deleteAuxiliaryPreviewAssetFiles requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Parse request model
        request_model = parse(body, model=DeleteAuxiliaryPreviewAssetFilesRequestModel)
        
        # Process request
        response = delete_auxiliary_preview_asset_files(
            path_params['databaseId'],
            path_params['assetId'],
            request_model.filePath,
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def handle_set_primary_file(event, context) -> APIGatewayProxyResponseV2:
    """Handle PUT /setPrimaryFile requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Parse request model
        request_model = parse(body, model=SetPrimaryFileRequestModel)
        
        # Process request
        response = set_primary_file(
            path_params['databaseId'],
            path_params['assetId'],
            request_model.filePath,
            request_model.primaryType,
            request_model.primaryTypeOther,
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

def handle_list_files(event, context) -> APIGatewayProxyResponseV2:
    """Handle GET /listFiles requests
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get claims and roles
        claims_and_roles = request_to_claims(event)
        
        # Check API authorization
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Get query parameters
        query_params = event.get('queryStringParameters', {}) or {}
        validate_pagination_info(query_params)
        
        # Process request
        response = list_asset_files(
            path_params['databaseId'],
            path_params['assetId'],
            query_params,
            claims_and_roles
        )
        
        return success(body=response.dict())
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for asset file operations
    
    Args:
        event: The API Gateway event
        context: The Lambda context
        
    Returns:
        APIGatewayProxyResponseV2 with the response
    """
    try:
        # Get API path and method
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']
        
        # Route to appropriate handler based on path pattern
        if method == 'GET' and path.endswith('/listFiles'):
            return handle_list_files(event, context)
        elif method == 'GET' and path.endswith('/fileInfo'):
            return handle_file_info(event, context)
        elif method == 'POST' and path.endswith('/moveFile'):
            return handle_move_file(event, context)
        elif method == 'POST' and path.endswith('/copyFile'):
            return handle_copy_file(event, context)
        elif method == 'POST' and path.endswith('/unarchiveFile'):
            return handle_unarchive_file(event, context)
        elif method == 'POST' and path.endswith('/createFolder'):
            return handle_create_folder(event, context)
        elif method == 'DELETE' and path.endswith('/archiveFile'):
            return handle_archive_file(event, context)
        elif method == 'DELETE' and path.endswith('/deleteFile'):
            return handle_delete_file(event, context)
        elif method == 'DELETE' and path.endswith('/deleteAssetPreview'):
            return handle_delete_asset_preview(event, context)
        elif method == 'DELETE' and path.endswith('/deleteAuxiliaryPreviewAssetFiles'):
            return handle_delete_auxiliary_preview_asset_files(event, context)
        elif method == 'POST' and '/revertFileVersion/' in path:
            return handle_revert_file_version(event, context)
        elif method == 'PUT' and path.endswith('/setPrimaryFile'):
            return handle_set_primary_file(event, context)
        else:
            return validation_error(body={'message': "Invalid API path or method"})
    
    except Exception as e:
        logger.exception(f"Unhandled error in lambda_handler: {e}")
        return internal_error()
