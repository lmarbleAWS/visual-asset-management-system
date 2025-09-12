/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { PropsWithChildren, Suspense, useEffect, useState } from "react";
import { API, Cache, Hub, Amplify, Auth as AmplifyAuth } from "aws-amplify";
import { OAuth2Client, OAuth2Token, generateCodeVerifier } from "@badgateway/oauth2-client";
import { getSecureConfig, getAmplifyConfig } from "../services/APIService";
import { default as vamsConfig } from "../config";
import { Authenticator } from "@aws-amplify/ui-react";

import Button from "@cloudscape-design/components/button";
import Box from "@cloudscape-design/components/box";
import loginBgImageSrc from "../resources/img/login_bg.png";
import logoDarkImageSrc from "../resources/img/logo_dark.svg";

import LoadingScreen from "../components/loading/LoadingScreen";
import { Alert } from "@cloudscape-design/components";
import styles from "./loginbox.module.css";
import "@aws-amplify/ui-react/styles.css";
import { Heading, useTheme } from "@aws-amplify/ui-react";

import { GlobalHeader } from "./../common/GlobalHeader";
import { Header } from "./../authenticator/Header";
import { Footer } from "./../authenticator/Footer";
import { SignInHeader } from "./../authenticator/SignInHeader";
import { SignInFooter } from "./../authenticator/SignInFooter";
import { isAxiosError } from "../common/typeUtils";

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
    redirectSignIn: string;
    /**
     * redirect signout url
     */
    redirectSignOut: string;
}

interface Config {
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
     * Additional configuration needed for cognito federated auth
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

    // /**
    //  * External OAUTH IDP ClientSecret Configuration
    //  */
    // externalOAuthIdpClientSecret?: string;

    /**
     * External OAUTH IDP Scope Configuration
     */
    externalOAuthIdpScope?: string;

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
     * VAMS Features that are enabled
     */
    featuresEnabled?: string;

    /**
     * VAMS Backend Stackname
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

function configureAmplify(config: Config, setAmpInit: (x: boolean) => void) {
    //console.log('configureAmplify', config, vamsConfig);

    let api_path = vamsConfig.DEV_API_ENDPOINT === "" ? config.api : vamsConfig.DEV_API_ENDPOINT;

    //if API path doesn't end in a /, add one
    if (api_path != undefined && api_path.length > 0 && api_path[api_path.length - 1] !== "/") {
        api_path = api_path + "/";
    }

    localStorage.setItem("api_path", api_path);
    //console.log('apiPath', localStorage.getItem('api_path'));

    console.log("DISABLE COGNITO ENV", window.DISABLE_COGNITO);
    console.log("COGNITO_FEDERATED ENV", window.COGNITO_FEDERATED);

    Amplify.configure({
        Auth: {
            mandatorySignIn: window["DISABLE_COGNITO"] ? false : true,
            region: config.region,
            userPoolId: window.DISABLE_COGNITO ? "XX-XXXX-X_abcd1234" : config.cognitoUserPoolId,
            userPoolWebClientId: window.DISABLE_COGNITO ? "1" : config.cognitoAppClientId,
            identityPoolId: window.DISABLE_COGNITO ? undefined : config.cognitoIdentityPoolId,
            cookieStorage: {
                domain: " ", // process.env.REACT_APP_COOKIE_DOMAIN, // Use a single space ' ' for host-only cookies
                expires: null, // null means session cookies
                path: "/",
                secure: vamsConfig.DEV_API_ENDPOINT === "" ? true : false, // for developing on localhost over http: set to false
                sameSite: "lax",
            },
            oauth: {
                domain: config.cognitoFederatedConfig?.customCognitoAuthDomain,
                scope: ["openid", "email", "profile"], //  process.env.REACT_APP_USER_POOL_SCOPES.split(','),
                redirectSignIn: window.location.origin, // config.cognitoFederatedConfig?.redirectSignIn,
                redirectSignOut: window.location.origin, // config.cognitoFederatedConfig?.redirectSignOut,
                responseType: "code",
            },
        },
        API: {
            endpoints: [
                {
                    name: "api",
                    endpoint: api_path,
                    region: config.region,
                    custom_header: async () => {
                        if (window.DISABLE_COGNITO) {
                            const accessToken = getOAuth2Token().accessToken;
                            return { Authorization: `Bearer ${accessToken}` };
                        } else {
                            return {
                                Authorization: `Bearer ${(await AmplifyAuth.currentSession())
                                    .getAccessToken()
                                    .getJwtToken()}`,
                            };
                        }
                    },
                },
            ],
        },
        geo: {
            AmazonLocationService: {
                maps: {
                    items: {
                        [`vams-map-raster-${config.region}-${config.stackName}`]: {
                            // REQUIRED - Amazon Location Service Map resource name
                            style: "RasterEsriImagery", // REQUIRED - String representing the style of map resource
                        },
                        [`vams-map-streets-${config.region}-${config.stackName}`]: {
                            style: "VectorEsriStreets",
                        },
                    },
                    default: `vams-map-raster-${config.region}-${config.stackName}`, // REQUIRED - Amazon Location Service Map resource name to set as default
                },
                // search_indices: {
                //     items: ['vams-index'], // REQUIRED - Amazon Location Service Place Index name
                //     default: 'vams-index', // REQUIRED - Amazon Location Service Place Index name to set as default
                // },
                // geofenceCollections: {
                //     items: ['XXXXXXXXX', 'XXXXXXXXX'], // REQUIRED - Amazon Location Service Geofence Collection name
                //     default: 'XXXXXXXXX', // REQUIRED - Amazon Location Service Geofence Collection name to set as default
                // },
                region: config.region, // REQUIRED - Amazon Location Service Region
            },
        },
    });

    setAmpInit(true);
}

type AuthProps = {};

//Cognito components
const CenteredBox: React.FC<PropsWithChildren<{}>> = ({ children }) => {
    return (
        <div className={styles.container}>
            <div className={styles.centeredBox}>{children}</div>
        </div>
    );
};

const cognitoAuthenticatorComponents = {
    Header,
    SignIn: {
        Header: SignInHeader,
        Footer: SignInFooter,
    },
    Footer,
};

interface CognitoFederatedLoginProps {
    onLogin: () => void;
}

const FedLoginBox: React.FC<CognitoFederatedLoginProps> = ({ onLogin }) => {
    const { tokens } = useTheme();

    return (
        <>
            <div className={styles.container}>
                <div className={styles.centeredBox}>
                    <Heading level={3} padding={`${tokens.space.xl} ${tokens.space.xl} 0`}>
                        <img
                            style={{ width: "100%" }}
                            src={logoDarkImageSrc}
                            alt="Visual Asset Management System Logo"
                        />
                    </Heading>
                    <button
                        className={styles.button}
                        onClick={onLogin}
                        data-testid="federated-login-button"
                    >
                        Login with Federated Identity Provider
                    </button>
                </div>
            </div>
            <img
                alt="background texture"
                src={loginBgImageSrc}
                style={{
                    position: "fixed",
                    top: 0,
                    width: "100vw",
                    left: 0,
                    zIndex: "-100",
                }}
            />
        </>
    );
};

//External OAUTH components
let oauth2Client: OAuth2Client;

const Auth: React.FC<AuthProps> = (props) => {
    //External Oauth Configuration Function
    function configureOAuthClient(config: Config) {
        if (
            config.externalOAuthIdpURL &&
            config.externalOAuthIdpClientId &&
            config.externalOAuthIdpTokenEndpoint &&
            config.externalOAuthIdpAuthorizationEndpoint &&
            config.externalOAuthIdpDiscoveryEndpoint &&
            config.externalOAuthIdpScope
        ) {
            oauth2Client = new OAuth2Client({
                server: config.externalOAuthIdpURL,
                clientId: config.externalOAuthIdpClientId,
                tokenEndpoint: config.externalOAuthIdpTokenEndpoint,
                authorizationEndpoint: config.externalOAuthIdpAuthorizationEndpoint,
                discoveryEndpoint: config.externalOAuthIdpDiscoveryEndpoint,
            });
        } else {
            localStorage.setItem(
                "auth_error",
                "Failed to configure OAuthClient. Missing externalOAuthIdp values in config."
            );
            setauthError(localStorage.getItem("auth_error"));
        }
    }

    const [config, setConfig] = useState(Cache.getItem("config"));
    let [authError, setauthError] = useState<string | null>(() =>
        localStorage.getItem("auth_error")
    );

    const [ampInit, setAmpInit] = useState(false);

    const [isLoggedIn, setisLoggedIn] = useState(false);
    const [isLoading, setIsLoading] = useState(false);

    // TODO: Refactor how/when handleExternalOauthSignIn() & External OAuth Effects are called without needing to do this
    // This line in combination with the useEffect dependency allows for the useEffects to execute in the right order
    const [triggerExternalOAuth, setTriggerExternalOAuth] = useState(false);

    //Both Affect
    //Fetch && Setup Initial global configurations
    useEffect(() => {
        if (config) {
            //Set global variables for cognito mode or external OAUTH.
            //If config.config.cognitoUserPoolId is undefined or empty, then disable cognito and setup external oauth
            if (
                config.cognitoUserPoolId === undefined ||
                config.cognitoUserPoolId === "undefined" ||
                config.cognitoUserPoolId === ""
            ) {
                window.DISABLE_COGNITO = true;
                configureOAuthClient(config);
            } else {
                window.DISABLE_COGNITO = false;
            }

            if (
                config.cognitoFederatedConfig === undefined ||
                config.cognitoFederatedConfig === "undefined" ||
                config.cognitoFederatedConfig === ""
            ) {
                window.COGNITO_FEDERATED = false;
            } else {
                window.COGNITO_FEDERATED = true;
            }

            //Configure Amplify
            configureAmplify(config, setAmpInit);
        } else {
            getAmplifyConfig().then(async (config) => {
                Cache.setItem("config", config);
                setConfig(config);
            });
        }
    }, [ampInit, config, setConfig]);

    //Both Effect
    //Auth session checks
    useEffect(() => {
        if (!ampInit) return;

        AmplifyAuth.currentAuthenticatedUser()
            .then((currentUser) => {
                if (!localStorage.getItem("user")) {
                    // No valid user session state
                    AmplifyAuth.signOut()
                        .then(() => {
                            console.log("User signed out - invalid session state");
                        })
                        .catch((error) => {
                            console.log("User sign out error - invalid session state", error);
                        });
                    setisLoggedIn(false);
                    resetSession();
                }
            })
            .catch((error) => {});
    }, [ampInit]);

    //External OAUTH Effect
    //Set user session once oauth access key login confirmed, start refresh interval
    useEffect(() => {
        if (window.DISABLE_COGNITO === true) {
            // Check if access token or refresh token still valid
            // ( This check is needed on initial login or if a user refreshes the page )
            const [accessTokenValid, refreshTokenValid] = tokenValidation();
            if (accessTokenValid) {
                setisLoggedIn(true); // Since access token has been validated, can deem as logged in
                startAccessTokenRefreshTimer(); // Restart refresh timer
            } else if (refreshTokenValid) {
                // If access token not valid but refresh token exists, attempt to refresh the token
                setIsLoading(true); // show loading page while it's still refreshing the token
                oauth2Client
                    .refreshToken(getOAuth2Token())
                    .then((oauth2Token) => {
                        // Successful token refresh. Update token & user session accordingly
                        setOauth2Token(oauth2Token);
                        setisLoggedIn(true); // Since access token has been validated, can deem as logged in
                    })
                    .catch((error) => {
                        // Failed to refresh the token
                        console.error(error);
                        // Reset amplify info
                        AmplifyAuth.signOut()
                            .then(() => {
                                console.log("User signed out - Unable to refresh token");
                            })
                            .catch((error) => {
                                console.log("User sign out error - Unable to refresh token", error);
                            });
                        setisLoggedIn(false);
                        setIsLoading(false); // hide loading page so it will show login page instead
                        resetSession();
                    });
                return; // since cant do an await in here, need to ensure everything is executed within the then/catch statements so don't execute anything after this
            } else {
                // neither token valid, setisLoggedIn to false
                setisLoggedIn(false);
                resetSession();
            }
        }
    }, [triggerExternalOAuth]); // triggerExternalOAuth dependency is needed to for the useEffect to execute in the right order

    //Cognito Effect
    // Sets the user on login/logout (Authenticator UI Callbacks)
    useEffect(() => {
        if (!ampInit) return;

        if (window.DISABLE_COGNITO === false) {
            const amplifyHubLIstener = Hub.listen("auth", ({ payload: { event, data } }) => {
                switch (event) {
                    case "signIn":
                        localStorage.setItem("user", JSON.stringify({ username: data.username }));
                        setisLoggedIn(true);
                        break;
                    case "signOut":
                        setisLoggedIn(false);
                        resetSession();
                        break;
                }
            });

            //Check/set for being logged for subsequent page loads
            AmplifyAuth.currentAuthenticatedUser()
                .then((currentUser) => {
                    localStorage.setItem(
                        "user",
                        JSON.stringify({ username: currentUser.getUsername() })
                    );
                    setisLoggedIn(true);
                    console.log("Cognito Page Load - Login set for Page Load");
                })
                .catch((error) =>
                    console.log("Cognito Page Load - Not signed in... sending to login - ", error)
                );

            return amplifyHubLIstener;
        }
    }, [ampInit]);

    //External OAUTH Effect
    //Try to get access token at this point after redirect
    useEffect(() => {
        if (window.DISABLE_COGNITO === true) {
            // Try request access token
            const accessToken = getOAuth2Token().accessToken;
            if (accessToken) {
                //already have access token, accessToken
                return;
            }

            if (!localStorage.getItem("auth_state") || !localStorage.getItem("code_verifier")) {
                // no auth_state or code_verifier in local storage
                return;
            }

            // request access token
            setIsLoading(true);
            const redirectUri = window.location.href.split(/[?#]/)[0];

            oauth2Client.authorizationCode
                .getTokenFromCodeRedirect(document.location.toString(), {
                    redirectUri,
                    state: localStorage.getItem("auth_state")!,
                    codeVerifier: localStorage.getItem("code_verifier")!,
                })
                .then((oauth2Token) => {
                    setOauth2Token(oauth2Token);
                    document.location = localStorage.getItem("auth_state")!;
                    // Cleanup localstorage items after they have been used & no longer needed
                    localStorage.removeItem("auth_state");
                    localStorage.removeItem("code_verifier");
                })
                .catch((error) => {
                    console.error(error);
                    signOutWithError("auth_error", "Failed to get access token"); // Catching error when redirect code is missing and start new sign in process.
                });
        }
    }, [triggerExternalOAuth]); // triggerExternalOAuth dependency is needed to for the useEffect to execute in the right order

    //Both Effect
    //Once logged in, get/set other configuration and profile information
    useEffect(() => {
        //Secure Config Fetch
        if (config && !config.featuresEnabled && isLoggedIn) {
            getSecureConfig()
                .then((value) => {
                    config.featuresEnabled = value.featuresEnabled;
                    Cache.setItem("config", config);
                    setConfig(config);
                })
                .catch((error: Error) => {
                    console.error("Error getting secure-config:", error.message);

                    // if response status code was 401 unauthorized, token may be invalid, so sign out
                    if (isAxiosError(error) && error.response?.status === 401) {
                        signOutWithError();
                    }
                });
            console.log("Fetched secure config");
        }

        //Hit login profile end-point to fetch and update latest backend login profiles
        //This could also update roles behind the scenes if configured to synchronize with external systems
        let loginProfile = Cache.getItem("loginProfile");
        if (isLoggedIn && !loginProfile) {
            const user = JSON.parse(localStorage.getItem("user")!);
            API.post("api", `auth/loginProfile/${user.username}`, {})
                .then((value) => {
                    loginProfile = {};
                    loginProfile.userId = value.message.Items[0].userId;
                    loginProfile.email = value.message.Items[0].email;
                    Cache.setItem("loginProfile", loginProfile);
                })
                .catch((error: Error) => {
                    console.error("Error getting login-profile:", error.message);

                    // if response status code was 401 unauthorized, token may be invalid, so sign out
                    if (isAxiosError(error) && error.response?.status === 401) {
                        signOutWithError();
                    }
                });
            console.log("Pinged LoginProfile API");
        }
        if (isLoggedIn) setIsLoading(false); // if logged in, can deem that the loading is complete
    }, [config, isLoggedIn]);

    //External OAUTH Function for handling sign-in
    const handleExternalOauthSignIn = async (require_mfa: boolean = false) => {
        // Sign in
        setIsLoading(true);

        // Check if access token or refresh token still valid
        // ( This check is needed in case the oauth2_token wasnt cleaned up properly / still exists when clicking login button )
        const [accessTokenValid, refreshTokenValid] = tokenValidation();
        if (accessTokenValid) {
            // Access token still valid, continue login process
            setTriggerExternalOAuth(true);
            return;
        }

        // If access token not valid but refresh token exists, attempt to refresh the token
        if (refreshTokenValid) {
            try {
                const oauth2TokenResponse = await oauth2Client.refreshToken(getOAuth2Token());
                setOauth2Token(oauth2TokenResponse); // Set when previous line is successful
                setTriggerExternalOAuth(true); // Access token is now valid, continue login process
                return;
            } catch (error) {
                // Failed to refresh the token (possibly invalid tokens or refresh token expired), continue on
                console.error("refreshtoken error: ", error);
            }
        }

        // Start oauth2 flow from the beginning if attempts above failed or access token doesnt exist

        // Clear existing auth related items in localstorage
        // This is needed in case previous user session wasnt cleaned up properly / still exists when clicking login button
        resetSession();

        // Use the URL as the oauth state.
        localStorage.setItem("auth_state", document.location.href);

        // This generates a security code that must be passed to the various steps.
        // This is used for 'PKCE' which is an advanced security feature.
        const codeVerifier = await generateCodeVerifier();
        localStorage.setItem("code_verifier", codeVerifier);

        try {
            if (config && config.externalOAuthIdpScope && config.externalOAuthIdpScopeMfa) {
                // Start oauth2 flow
                const authorizeUri = await oauth2Client.authorizationCode.getAuthorizeUri({
                    redirectUri: window.location.href,
                    state: document.location.href,
                    codeVerifier,
                    scope: [
                        require_mfa
                            ? config.externalOAuthIdpScope + " " + config.externalOAuthIdpScopeMfa
                            : config.externalOAuthIdpScope,
                    ],
                });

                document.location = authorizeUri;
            } else {
                localStorage.setItem(
                    "auth_error",
                    "Failed to initialize authorization flow. Missing scope or mfa scope in config."
                );
                setauthError(localStorage.getItem("auth_error"));
            }
        } catch (error) {
            localStorage.setItem("auth_error", "Failed to initialize authorization flow.");
            setauthError(localStorage.getItem("auth_error"));
        }
    };

    //Cognito Auth Effect
    //Check access token validity, expirations, and refreshs
    useEffect(() => {
        if (!ampInit || !localStorage.getItem("user")) return;

        // Trying to set amplify session
        const currentUser = localStorage.getItem("user")
            ? JSON.parse(localStorage.getItem("user")!)
            : undefined;

        if (!currentUser) {
            return;
        }

        if (window.DISABLE_COGNITO === false) {
            // Schedule check and refresh (when needed) of JWT's every 5 min:
            const i = setInterval(() => AmplifyAuth.currentSession(), 5 * 60 * 1000);
            return () => clearInterval(i);
        }
    }, [ampInit]);

    //Initial loading screen when going through config init
    if (config === null || !ampInit) {
        return (
            <CenteredBox>
                <p className="explanation">One moment please ...</p>
            </CenteredBox>
        );
    }

    //Show loading after login (mostly for External OAUTH Process)
    if (isLoading) {
        return <LoadingScreen />;
    }

    //External OAUTH Login Page Return
    if (window.DISABLE_COGNITO === true && !isLoggedIn && ampInit) {
        return (
            <>
                <GlobalHeader authorizationHeader={true} />
                <div
                    style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                    }}
                >
                    <div className={styles.container}>
                        <div className={styles.centeredBox}>
                            <Heading level={3}>
                                <img
                                    style={{ width: "100%" }}
                                    src={logoDarkImageSrc}
                                    alt="Visual Asset Management System Logo"
                                />
                            </Heading>
                            <Button variant="primary" onClick={() => handleExternalOauthSignIn()}>
                                Log in with SSO
                            </Button>
                            {config.externalOAuthIdpScopeMfa != undefined &&
                            config.externalOAuthIdpScopeMfa != "undefined" &&
                            config.externalOAuthIdpScopeMfa != "" ? (
                                <>
                                    <Box
                                        fontWeight="normal"
                                        padding={{ top: "xxs", bottom: "xxs" }}
                                    >
                                        <span>or</span>
                                    </Box>
                                    <Button
                                        variant="normal"
                                        onClick={() => handleExternalOauthSignIn(true)}
                                    >
                                        Log in with MFA
                                    </Button>
                                </>
                            ) : null}
                        </div>
                        {authError ? (
                            <div className={styles.alertError}>
                                <Alert type="error" statusIconAriaLabel="Error" header="Error">
                                    Failed to login or session timed out. Please try again.
                                </Alert>
                            </div>
                        ) : null}
                    </div>
                    <img
                        alt="background texture"
                        src={loginBgImageSrc}
                        style={{
                            position: "fixed",
                            top: 0,
                            width: "100vw",
                            left: 0,
                            zIndex: "-100",
                        }}
                    />
                </div>
            </>
        );
    }

    //Cognito Login Page Return
    if (window.DISABLE_COGNITO === false && !isLoggedIn && ampInit) {
        if (window.COGNITO_FEDERATED === false) {
            //Non-federated login
            return (
                <>
                    <GlobalHeader authorizationHeader={true} />
                    <Authenticator
                        components={cognitoAuthenticatorComponents}
                        loginMechanisms={["username"]}
                        hideSignUp={true}
                    />
                </>
            );
        } else {
            //Federated Login
            return (
                <>
                    <GlobalHeader authorizationHeader={true} />
                    <FedLoginBox
                        onLogin={() =>
                            AmplifyAuth.federatedSignIn({
                                customProvider:
                                    config.cognitoFederatedConfig
                                        ?.customFederatedIdentityProviderName,
                            })
                        }
                    />
                </>
            );
        }
    }

    //Show rest of app, we are all logged in.
    if (isLoggedIn && ampInit) {
        return (
            <>
                <GlobalHeader authorizationHeader={false} />
                <Suspense fallback={<LoadingScreen />}>{props.children}</Suspense>
            </>
        );
    }

    //Blank return if other options not available (fallback)
    console.log("Auth.tsx Error - No Valid Workflow Detected");
    return <></>;
};

/////////////////////////////////////////////////
//External OAUTH Helper Functions
const parseJwt = (
    accessToken: string
): {
    sub: string;
} => {
    var jsonPayload = "{}";
    var base64Url = accessToken.split(".")[1];
    if (base64Url) {
        var base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
        jsonPayload = decodeURIComponent(
            window
                .atob(base64)
                .split("")
                .map(function (c) {
                    return "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2);
                })
                .join("")
        );
    }

    return JSON.parse(jsonPayload);
};

let refreshTimer: NodeJS.Timeout | null;
const startAccessTokenRefreshTimer = (startNewTimer: boolean = false) => {
    // If there was a previous refresh timer, the boolean param will clear it
    if (startNewTimer && refreshTimer) {
        clearTimeout(refreshTimer);
        refreshTimer = null;
    }

    const oauth2Token = getOAuth2Token();
    const accessToken = oauth2Token.accessToken;

    let refreshTimeLength = 5 * 60 * 1000; // Default to 5 minutes, if no expiresAt timestamp found below
    const percentOfTokenLengthToRefresh = 0.75; // Percentage of time to trigger refresh, default to 75% per other oauth library recommendations

    // If there is an oauth token and no refresh timer, calculate the percentage of it's lifetime from the expiration time and now
    if (oauth2Token && !refreshTimer) {
        const expiresAtTimestamp = oauth2Token.expiresAt;
        if (expiresAtTimestamp) {
            refreshTimeLength = Math.ceil(
                (expiresAtTimestamp - Date.now()) * percentOfTokenLengthToRefresh
            ); // rounding so we don't have fractions of a millisecond
        }

        // Start the timer that will run at the percentage of the expiration time calculated above
        refreshTimer = setTimeout(async () => {
            // Run the following logic when the timer triggers
            if (!accessToken) {
                // no access token found when trying to refresh
                signOutWithError("auth_error", "No access token found when refreshing");
                return;
            }

            try {
                const oauth2TokenResponse = await oauth2Client.refreshToken(oauth2Token);
                setOauth2Token(oauth2TokenResponse); // Set when previous line is successful and within here, restart this timer
            } catch (error) {
                console.error("error: ", error);
                signOutWithError("auth_error", "Failed to refresh access token.");
            }
        }, refreshTimeLength);
    }
};

const signOutWithError = (key: string = "auth_error", value: string = "Unauthorized") => {
    // Reset amplify info
    AmplifyAuth.signOut()
        .then(() => {
            console.log("User signed out");
        })
        .catch((error) => {
            console.log("User sign out error", error);
        });
    localStorage.clear();
    localStorage.setItem(key, value);
    window.location.href = "/";
};

const resetSession = () => {
    localStorage.removeItem("oauth2_token");
    localStorage.removeItem("user");
    localStorage.removeItem("email");
    Cache.removeItem("loginProfile");
};

const setOauth2Token = (oauth2Token: OAuth2Token) => {
    localStorage.setItem("oauth2_token", JSON.stringify(oauth2Token)); // Update token in local storage

    const jwt = parseJwt(oauth2Token.accessToken); // Decoding access token to get user id
    localStorage.setItem("user", JSON.stringify({ username: jwt.sub })); // Update user in local storage

    // @ts-expect-error
    AmplifyAuth.setUserSession({ username: jwt.sub }, oauth2Token.accessToken); // Update Amplify User Session with latest access token

    clearPreviousLoginErrors(); // Can remove old login errors since have token
    startAccessTokenRefreshTimer(true); // Restart timer upon successfully setting the new oauth token
};

function getOAuth2Token() {
    let oauth2Token = {} as OAuth2Token;
    const oauth2TokenStr = localStorage.getItem("oauth2_token");
    if (oauth2TokenStr) {
        try {
            oauth2Token = JSON.parse(oauth2TokenStr);
        } catch (error) {
            // Catch if token not valid json
            console.error("error: ", error);
        }
    }
    return oauth2Token;
}

function clearPreviousLoginErrors() {
    localStorage.removeItem("auth_error");
}

// Centralize logic for access & refresh token validation
function tokenValidation() {
    let accessTokenValid: boolean = false;
    let refreshTokenValid: boolean = false;
    const oauth2Token = getOAuth2Token();
    // If access token exists and not expired, deem it as still valid
    if (
        oauth2Token.accessToken &&
        oauth2Token.accessToken.length > 0 &&
        oauth2Token.expiresAt &&
        Date.now() < oauth2Token.expiresAt
    ) {
        accessTokenValid = true;
    }
    // If access token expired and refresh token exists, deem it as still valid
    else if (oauth2Token.refreshToken) {
        refreshTokenValid = true;
    }
    return [accessTokenValid, refreshTokenValid];
}

export default Auth;
