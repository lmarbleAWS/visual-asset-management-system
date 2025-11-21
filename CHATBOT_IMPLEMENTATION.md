# VAMS AI Chatbot Implementation Guide

## Overview

This document describes the AI-powered chatbot feature that has been added to VAMS, allowing users to interact with the system using natural language.

**Status:** Frontend Complete ✓ | Backend Code Complete ✓ | Infrastructure Pending ⚠️

---

## What's Been Built

### Frontend Components (✓ Complete)

**Location:** `web/src/components/chat/`

1. **ChatButton.jsx** - Floating action button that appears on all pages
2. **ChatPanel.jsx** - Main chat interface with message history
3. **ChatMessage.jsx** - Individual message display component
4. **chatApi.js** - API integration layer for backend communication
5. **ChatPanel.css** - Styling for chat interface
6. **ChatButton.css** - Styling for floating button and overlay

**Integration:** Already integrated into `App.js` - chat button appears for all authenticated users

### Backend Handler (✓ Complete)

**Location:** `backend/backend/handlers/chat/`

1. **chatHandler.py** - Lambda function that:
   - Processes natural language queries
   - Integrates with AWS Bedrock (Claude 3)
   - Executes VAMS operations via function calling
   - Returns formatted responses

**Available Tools:**
- `search_assets` - Search for assets
- `get_asset_details` - Get asset information
- `list_databases` - List all databases
- `list_database_assets` - List assets in a database
- `list_asset_files` - List files in an asset
- `get_asset_relationships` - Get asset relationships

---

## What Still Needs to Be Done

### 1. Infrastructure (AWS CDK) ⚠️ REQUIRED

You need to add the Lambda function and API Gateway endpoint to the CDK infrastructure:

#### A. Add Lambda Function to CDK

**File to modify:** `infra/lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts`

Add this Lambda function definition:

```typescript
// Chat Handler Lambda
const chatHandlerLambda = new lambda.Function(this, 'ChatHandler', {
    runtime: lambda.Runtime.PYTHON_3_12,
    handler: 'chatHandler.lambda_handler',
    code: lambda.Code.fromAsset(path.join(__dirname, '../../../backend/backend/handlers/chat')),
    layers: [lambdaCommonBaseLayer],
    environment: {
        STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
        ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
        AWS_REGION: this.region,
    },
    timeout: cdk.Duration.seconds(60),
    memorySize: 512,
    vpc: vpc,
    vpcSubnets: subnets ? { subnets } : undefined,
});

// Grant Bedrock permissions
chatHandlerLambda.addToRolePolicy(new iam.PolicyStatement({
    actions: [
        'bedrock:InvokeModel',
        'bedrock:InvokeModelWithResponseStream',
    ],
    resources: [
        `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-3-sonnet-20240229-v1:0`,
        `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-3-haiku-20240307-v1:0`,
    ],
}));

// Grant DynamoDB permissions
storageResources.dynamo.databaseStorageTable.grantReadWriteData(chatHandlerLambda);
storageResources.dynamo.assetStorageTable.grantReadWriteData(chatHandlerLambda);
```

#### B. Add API Gateway Route

Add this to the API Gateway configuration:

```typescript
// Chat endpoint
const chatRoute = apiGatewayV2.addRoutes({
    path: '/chat/message',
    methods: [apigatewayv2.HttpMethod.POST],
    integration: new integrations.HttpLambdaIntegration('ChatIntegration', chatHandlerLambda),
    authorizer: authorizer, // Use existing Cognito authorizer
});
```

### 2. AWS Bedrock Setup ⚠️ REQUIRED

**Before deploying, you MUST:**

1. **Enable AWS Bedrock** in your AWS account
2. **Request Model Access:**
   - Go to AWS Console → Bedrock → Model access
   - Request access to: **Anthropic Claude 3 Sonnet** (recommended)
   - Alternative: **Anthropic Claude 3 Haiku** (faster, cheaper)
   - Wait for approval (usually instant for commercial regions)

3. **Verify Region Support:**
   - Bedrock is available in: `us-east-1`, `us-west-2`, `eu-west-1`, `ap-northeast-1`, etc.
   - Ensure your VAMS deployment region supports Bedrock
   - If not, you may need to configure cross-region Bedrock access

### 3. IAM Permissions ⚠️ REQUIRED

The Lambda execution role needs these permissions:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream"
            ],
            "Resource": [
                "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-*"
            ]
        },
        {
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:Query",
                "dynamodb:Scan"
            ],
            "Resource": [
                "arn:aws:dynamodb:*:*:table/YourDatabaseTable",
                "arn:aws:dynamodb:*:*:table/YourAssetTable"
            ]
        }
    ]
}
```

### 4. Configuration (Optional but Recommended)

#### A. Model Configuration

You can change the Bedrock model in `chatHandler.py`:

```python
# Line 21
BEDROCK_MODEL_ID = "anthropic.claude-3-sonnet-20240229-v1:0"  # Default

# Options:
# - anthropic.claude-3-sonnet-20240229-v1:0 (Balanced, recommended)
# - anthropic.claude-3-haiku-20240307-v1:0 (Faster, cheaper)
# - anthropic.claude-3-opus-20240229-v1:0 (Most capable, expensive)
```

#### B. Environment Variables

Add these to your CDK configuration if needed:

```typescript
environment: {
    BEDROCK_MODEL_ID: 'anthropic.claude-3-sonnet-20240229-v1:0',
    BEDROCK_REGION: 'us-east-1', // If different from deployment region
    MAX_TOKENS: '2000',
    TEMPERATURE: '0.7',
}
```

---

## Deployment Steps

### Step 1: Pre-Deployment Checklist

- [ ] AWS Bedrock enabled in your account
- [ ] Model access approved for Claude 3
- [ ] CDK infrastructure code updated (Lambda + API Gateway)
- [ ] IAM permissions configured
- [ ] Frontend code committed to your branch

### Step 2: Build Frontend

```bash
cd visual-asset-management-system/web
yarn install
npm run build
```

### Step 3: Deploy Infrastructure

```bash
cd visual-asset-management-system/infra
npm install
cdk deploy --all --require-approval never
```

### Step 4: Verify Deployment

1. Check CloudFormation stack completed successfully
2. Verify Lambda function exists: `[StackName]-ChatHandler`
3. Verify API Gateway has `/chat/message` endpoint
4. Check Lambda has Bedrock permissions in IAM console

### Step 5: Test the Chatbot

1. Log into VAMS web application
2. Click the "AI Assistant" button (bottom-right corner)
3. Try these test queries:
   - "List all databases"
   - "What can you help me with?"
   - "Show me recent assets"

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  VAMS React Web App                     │
│              (ChatButton component)                     │
└──────────────────┬──────────────────────────────────────┘
                   │ HTTPS POST /chat/message
                   ↓
┌─────────────────────────────────────────────────────────┐
│              API Gateway (Cognito Auth)                 │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────────────────┐
│         Lambda: chatHandler                             │
│  ┌──────────────────────────────────────────────────┐  │
│  │  1. Parse user message                           │  │
│  │  2. Call AWS Bedrock (Claude 3)                  │  │
│  │  3. Execute VAMS operations (tools)              │  │
│  │  4. Return formatted response                    │  │
│  └──────────────────────────────────────────────────┘  │
└──────────────┬─────────────────┬────────────────────────┘
               │                 │
               ↓                 ↓
    ┌──────────────────┐  ┌──────────────────┐
    │  AWS Bedrock     │  │  DynamoDB Tables │
    │  (Claude 3)      │  │  - Databases     │
    │                  │  │  - Assets        │
    └──────────────────┘  └──────────────────┘
```

---

## Cost Considerations

### AWS Bedrock Pricing (us-east-1, as of 2024)

**Claude 3 Sonnet:**
- Input: $0.003 per 1K tokens
- Output: $0.015 per 1K tokens

**Claude 3 Haiku (Cheaper alternative):**
- Input: $0.00025 per 1K tokens
- Output: $0.00125 per 1K tokens

**Typical conversation:**
- User query: ~50 tokens
- System prompt + tools: ~500 tokens
- Response: ~200 tokens
- **Cost per message: ~$0.01 (Sonnet) or ~$0.001 (Haiku)**

### Additional Costs

- **Lambda:** ~$0.20 per million requests + compute time
- **API Gateway:** $1.00 per million requests
- **DynamoDB:** Covered by existing VAMS usage

**Estimated monthly cost for 1000 users, 10 messages/user:**
- ~$100-200/month (Sonnet) or ~$10-20/month (Haiku)

---

## Troubleshooting

### Issue: "Bedrock model not available"

**Solution:** 
1. Check model access in AWS Console → Bedrock → Model access
2. Verify region supports Bedrock
3. Confirm model ID matches available models

### Issue: "Permission denied" errors

**Solution:**
1. Check Lambda execution role has `bedrock:InvokeModel` permission
2. Verify DynamoDB table permissions
3. Check CloudWatch Logs for specific error

### Issue: Chat button doesn't appear

**Solution:**
1. Verify user is authenticated (button only shows for logged-in users)
2. Check browser console for JavaScript errors
3. Verify frontend build completed successfully

### Issue: "Failed to communicate with AI assistant"

**Solution:**
1. Check API Gateway endpoint is deployed
2. Verify Lambda function exists and is connected
3. Check CloudWatch Logs for Lambda errors
4. Verify Cognito authentication is working

---

## Future Enhancements

### Short-term (Can be added easily)

- [ ] Conversation history persistence (DynamoDB table)
- [ ] File upload support (analyze assets via chat)
- [ ] Workflow execution (run pipelines from chat)
- [ ] Export conversation history
- [ ] Voice input support

### Medium-term

- [ ] Multi-language support
- [ ] Advanced search integration (OpenSearch)
- [ ] Asset preview in chat responses
- [ ] Suggested actions based on context
- [ ] Integration with VAMS notifications

### Long-term

- [ ] Custom training on VAMS-specific data
- [ ] Automated asset tagging suggestions
- [ ] Predictive maintenance recommendations
- [ ] Natural language query builder
- [ ] Integration with external data sources

---

## Testing

### Unit Tests (TODO)

Create tests in `backend/tests/handlers/test_chatHandler.py`:

```python
def test_chat_handler_basic_query():
    # Test basic chat functionality
    pass

def test_bedrock_integration():
    # Test Bedrock API calls
    pass

def test_tool_execution():
    # Test VAMS operation execution
    pass
```

### Integration Tests (TODO)

Test end-to-end flow:
1. Frontend sends message
2. API Gateway routes to Lambda
3. Bedrock processes query
4. VAMS operations execute
5. Response returns to frontend

---

## Security Considerations

### Current Implementation

✓ Cognito authentication required
✓ User permissions respected (planned)
✓ API rate limiting via API Gateway
✓ Input validation in Lambda
✓ Secure token handling

### Recommended Additions

- [ ] Rate limiting per user (prevent abuse)
- [ ] Content filtering (prevent inappropriate queries)
- [ ] Audit logging (track all AI interactions)
- [ ] Data privacy compliance (GDPR, etc.)
- [ ] Bedrock guardrails (AWS feature)

---

## Support & Contribution

### Questions?

- Check CloudWatch Logs for Lambda errors
- Review API Gateway logs for request issues
- Consult AWS Bedrock documentation

### Contributing

If you enhance this feature:
1. Update this documentation
2. Add tests
3. Update cost estimates
4. Submit PR to your fork

---

## License

Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
