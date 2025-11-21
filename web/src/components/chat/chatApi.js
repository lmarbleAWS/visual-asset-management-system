/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { API } from "aws-amplify";

/**
 * Send a chat message to the VAMS AI backend
 * @param {string} message - The user's message
 * @param {object} context - Current page context (databaseId, assetId, etc.)
 * @returns {Promise} - Response from the chat API
 */
export async function sendChatMessage(message, context = {}) {
    try {
        const response = await API.post("api", "chat/message", {
            body: {
                message: message,
                context: {
                    currentPage: context.currentPage || window.location.hash,
                    databaseId: context.databaseId,
                    assetId: context.assetId,
                    ...context
                },
                conversationId: context.conversationId || generateConversationId(),
            },
        });
        return response;

    } catch (error) {
        console.error("Error sending chat message:", error);
        throw new Error(
            error.response?.data?.error || 
            error.message || 
            "Failed to communicate with the AI assistant"
        );
    }
}

/**
 * Generate a unique conversation ID for tracking chat sessions
 * @returns {string} - UUID-like conversation ID
 */
function generateConversationId() {
    return `conv-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Get chat history (placeholder for future implementation)
 * @returns {Promise<Array>} - Array of previous messages
 */
export async function getChatHistory() {
    // TODO: Implement chat history storage and retrieval
    return [];
}

/**
 * Clear chat history (placeholder for future implementation)
 */
export async function clearChatHistory() {
    // TODO: Implement chat history clearing
    return true;
}
