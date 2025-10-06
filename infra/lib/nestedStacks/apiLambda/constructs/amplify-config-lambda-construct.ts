/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as apigatewayv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as apigwIntegrations from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as apigwAuthorizers from "aws-cdk-lib/aws-apigatewayv2-authorizers";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { LAMBDA_NODE_RUNTIME } from "../../../../config/config";
import { Construct } from "constructs";
import { IHttpRouteAuthorizer } from "aws-cdk-lib/aws-apigatewayv2";
import { Service } from "../../../helper/service-helper";
import { authResources } from "../../auth/authBuilder-nestedStack";
import * as Config from "../../../../config/config";

/**
 * Additional configuration needed to use federated identities
 */
export interface AmplifyConfigFederatedIdentityProps {
    /**
     * The name of the federated identity provider.
     */
    customFederatedIdentityProviderName: string;
    /**
     * The cognito auth domain
     */
    customCognitoAuthDomain: string;
    /**
     * redirect signin url
     */
    redirectSignIn?: string;
    /**
     * redirect signout url
     */
    redirectSignOut?: string;
}

interface InlineLambdaProps {
    /**
     * The ApiGatewayV2 HttpApi to attach the lambda
     */
    api: string;
    /**
     * region
     */
    region: string;

    /**
     * The Cognito UserPoolId to authenticate users in the front-end
     */
    cognitoUserPoolId: string;
    /**
     * The Cognito AppClientId to authenticate users in the front-end
     */
    cognitoAppClientId: string;
    /**
     * The Cognito IdentityPoolId to authenticate users in the front-end
     */
    cognitoIdentityPoolId: string;

    /**
     * Additional configuration needed for federated auth
     */
    cognitoFederatedConfig?: AmplifyConfigFederatedIdentityProps;

    /**
     * External OAUTH IDP URL Configuration
     */
    externalOAuthIdpURL?: string;

    /**
     * External OAUTH IDP ClientID Configuration
     */
    externalOAuthIdpClientId?: string;

    /**
     * External OAUTH IDP Scope Configuration
     */
    externalOAuthIdpScope?: string;

    /**
     * External OAUTH IDP Scope attribute for MFA Configuration
     */
    externalOAuthIdpScopeMfa?: string;

    /**
     * External OAUTH IDP Token Endpoint Configuration
     */
    externalOAuthIdpTokenEndpoint?: string;

    /**
     * External OAUTH IDP Authorization Endpoint Configuration
     */
    externalOAuthIdpAuthorizationEndpoint?: string;

    /**
     * External OAUTH IDP Discovery Endpoint Configuration
     */
    externalOAuthIdpDiscoveryEndpoint?: string;

    /**
     * Name of deployed stack
     */
    stackName: string;

    /**
     * Content Security Policy to apply (generally for ALB deployment where CSP is not injected)
     */
    contentSecurityPolicy?: string;

    /**
     * HTML banner message to be displayed at the top of all web UI pages
     */
    bannerHtmlMessage?: string;
}

export interface AmplifyConfigLambdaConstructProps extends cdk.StackProps {
    /**
     * Main Configuration Provider
     */
    config: Config.Config;

    /**
     * The AuthResources Provider
     */
    authResources: authResources;
    /**
     * The ApiGatewayV2 HttpApi to create route from
     */
    api: apigatewayv2.HttpApi;
    /**
     * The ApiGatewayV2 HttpApi URL to attach the lambda
     */
    apiUrl: string;
    /**
     * region
     */
    region: string;
    /**
     * Additional configuration needed for federated auth
     */
    cognitoFederatedConfig?: AmplifyConfigFederatedIdentityProps;

    /**
     * Content Security Policy to apply at the react level [none headers passed from static webpage service] (generally not used as alreayd provided in Cloudfront and ALB deployment)
     */
    contentSecurityPolicy?: string;
}

/**
 * Deploys a lambda to the api gateway under the path `/api/amplify-config`.
 * The route is unauthenticated.  Use this with `apigatewayv2-cloudfront` for a CORS free
 * amplify configuration setup
 */
export class AmplifyConfigLambdaConstruct extends Construct {
    constructor(parent: Construct, name: string, props: AmplifyConfigLambdaConstructProps) {
        super(parent, name);

        props = { ...props };

        const lambdaFn = new lambda.Function(this, "AmplifyConfigLambda", {
            runtime: LAMBDA_NODE_RUNTIME,
            handler: "index.handler",
            code: lambda.Code.fromInline(
                this.getJavascriptInlineFunction({
                    region: props.region,
                    api: props.apiUrl || "us-east-1",
                    cognitoUserPoolId: props.config.app.authProvider.useCognito.enabled
                        ? props.authResources.cognito.userPoolId
                        : "undefined",
                    cognitoAppClientId: props.config.app.authProvider.useCognito.enabled
                        ? props.authResources.cognito.webClientId
                        : "undefined",
                    cognitoIdentityPoolId: props.config.app.authProvider.useCognito.enabled
                        ? props.authResources.cognito.identityPoolId
                        : "undefined",
                    cognitoFederatedConfig: props.cognitoFederatedConfig,
                    externalOAuthIdpURL:
                        props.config.app.authProvider.useExternalOAuthIdp.idpAuthProviderUrl ||
                        "undefined",
                    externalOAuthIdpClientId:
                        props.config.app.authProvider.useExternalOAuthIdp.idpAuthClientId ||
                        "undefined",
                    externalOAuthIdpScope:
                        props.config.app.authProvider.useExternalOAuthIdp.idpAuthProviderScope ||
                        "undefined",
                    externalOAuthIdpScopeMfa:
                        props.config.app.authProvider.useExternalOAuthIdp.idpAuthProviderScopeMfa ||
                        "undefined",
                    externalOAuthIdpTokenEndpoint:
                        props.config.app.authProvider.useExternalOAuthIdp
                            .idpAuthProviderTokenEndpoint || "undefined",
                    externalOAuthIdpAuthorizationEndpoint:
                        props.config.app.authProvider.useExternalOAuthIdp
                            .idpAuthProviderAuthorizationEndpoint || "undefined",
                    externalOAuthIdpDiscoveryEndpoint:
                        props.config.app.authProvider.useExternalOAuthIdp
                            .idpAuthProviderDiscoveryEndpoint || "undefined",
                    stackName: props.stackName!,
                    contentSecurityPolicy: "",
                    bannerHtmlMessage: props.config.app.webUi.optionalBannerHtmlMessage || "",
                }, props.config.app.govCloud?.enabled || false)
            ),
            timeout: cdk.Duration.seconds(15),
        });

        // add lambda policies
        lambdaFn.grantInvoke(Service("APIGATEWAY").Principal);

        // add lambda integration
        const lambdaFnIntegration = new apigwIntegrations.HttpLambdaIntegration(
            "AmplifyConfigLambdaIntegration",
            lambdaFn
        );

        // add route to the api gateway
        props.api.addRoutes({
            path: "/api/amplify-config",
            methods: [apigatewayv2.HttpMethod.GET],
            integration: lambdaFnIntegration,
            authorizer: this.createNoOpAuthorizer(),
        });
    }

    private createNoOpAuthorizer(): IHttpRouteAuthorizer {
        const authorizerFn = new cdk.aws_lambda.Function(this, "AuthorizerLambda", {
            runtime: LAMBDA_NODE_RUNTIME,
            handler: "index.handler",
            code: lambda.Code.fromInline(this.getAuthorizerLambdaCode()),
            timeout: cdk.Duration.seconds(15),
        });

        authorizerFn.grantInvoke(Service("APIGATEWAY").Principal);

        return new apigwAuthorizers.HttpLambdaAuthorizer("authorizer", authorizerFn, {
            authorizerName: "CognitoConfigAuthorizer",
            resultsCacheTtl: cdk.Duration.seconds(3600),
            identitySource: ["$context.routeKey"],
            responseTypes: [apigwAuthorizers.HttpLambdaResponseType.SIMPLE],
        });
    }

    private getJavascriptInlineFunction(props: InlineLambdaProps, isGovCloud: boolean = false) {
        // Create a copy of props to modify for GovCloud compatibility
        const modifiedProps = { ...props };
        
        // Handle GovCloud identity pool ID formatting
        if (modifiedProps.cognitoIdentityPoolId && modifiedProps.cognitoIdentityPoolId !== "undefined") {
            if (isGovCloud) {
                // Ensure the identity pool ID uses the correct GovCloud partition format
                // Identity pool IDs should be in format: us-gov-region:uuid
                // If it's currently in aws format, convert it to aws-us-gov format
                if (modifiedProps.cognitoIdentityPoolId.includes(':')) {
                    const parts = modifiedProps.cognitoIdentityPoolId.split(':');
                    if (parts.length >= 2 && !parts[0].includes('gov')) {
                        // Convert region format for GovCloud (e.g., us-east-1 -> us-gov-east-1)
                        const govRegion = parts[0].replace(/^us-/, 'us-gov-');
                        modifiedProps.cognitoIdentityPoolId = `${govRegion}:${parts[1]}`;
                    }
                }
            }
        }
        
        const resp = JSON.stringify(modifiedProps);

        return `
            exports.handler = async function(event, context) {
                console.log('Amplify Config Request - Region:', '${props.region}');
                console.log('Amplify Config Request - GovCloud Mode:', ${isGovCloud});
                console.log('Amplify Config Request - Identity Pool ID:', '${modifiedProps.cognitoIdentityPoolId}');
                
                return {
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    statusCode: 200,
                    body: JSON.stringify(${resp}),
                };
            };
        `;
    }

    private getAuthorizerLambdaCode(): string {
        return `
            exports.handler = async function(event, context) {
                return {
                    isAuthorized: true
                }
            }
        `;
    }
}
            }
        `;
    }
}
