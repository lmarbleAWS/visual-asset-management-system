import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";
import { Duration, Stack } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import { NagSuppressions } from "cdk-nag";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../helper/security";

export function buildChatHandler(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    databaseStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "chatHandler";
    const chatHandlerFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend/handlers/chat`)),
        handler: `chatHandler.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.seconds(60),
        memorySize: 512,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            STORAGE_TABLE_NAME: databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
        },
    });

    // Grant DynamoDB permissions
    databaseStorageTable.grantReadData(chatHandlerFunction);
    assetStorageTable.grantReadData(chatHandlerFunction);

    // Suppress cdk-nag warning for DynamoDB index wildcard permissions
    // The chat handler needs read access to table indexes to efficiently query asset metadata
    NagSuppressions.addResourceSuppressions(
        chatHandlerFunction,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "The chat handler needs read access to DynamoDB table indexes (GSIs) to query asset metadata efficiently. The wildcard permission for /index/* is required to access all Global Secondary Indexes on the asset storage table.",
                appliesTo: ["Resource::<AssetStorageTable53D33AD8.Arn>/index/*"],
            },
        ],
        true
    );

    // Grant AWS Bedrock permissions for Claude 4
    const region = Stack.of(scope).region;
    chatHandlerFunction.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources: [
                `arn:aws:bedrock:${region}::foundation-model/anthropic.claude-sonnet-4-20250514-v1:0`,
                `arn:aws:bedrock:${region}::foundation-model/anthropic.claude-opus-4-20250514-v1:0`,
            ],
        })
    );

    kmsKeyLambdaPermissionAddToResourcePolicy(chatHandlerFunction, kmsKey);
    globalLambdaEnvironmentsAndPermissions(chatHandlerFunction, config);

    return chatHandlerFunction;
}
