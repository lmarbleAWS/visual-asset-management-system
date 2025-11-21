/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from 'react';
import { Box, SpaceBetween, Alert } from '@cloudscape-design/components';

const ChatMessage = ({ message }) => {
    const isUser = message.role === 'user';
    const isError = message.isError;

    return (
        <div className={`chat-message ${isUser ? 'user-message' : 'assistant-message'}`}>
            <SpaceBetween size="xs">
                <Box
                    variant="small"
                    color={isUser ? 'text-status-info' : 'text-status-success'}
                    fontWeight="bold"
                >
                    {isUser ? 'You' : 'VAMS Assistant'}
                </Box>
                
                <Box
                    className={`message-content ${isUser ? 'user-content' : 'assistant-content'}`}
                    padding={{ vertical: "s", horizontal: "m" }}
                >
                    {isError ? (
                        <Alert type="error" header="Error">
                            {message.content}
                        </Alert>
                    ) : (
                        <Box variant="p">
                            {message.content}
                        </Box>
                    )}
                </Box>

                {/* Show operation summary if available */}
                {message.operations_summary && message.operations_summary.total > 0 && (
                    <Box variant="small" color="text-label">
                        Performed {message.operations_summary.successful} of {message.operations_summary.total} operations successfully
                    </Box>
                )}

                <Box variant="small" color="text-label">
                    {new Date(message.timestamp).toLocaleTimeString()}
                </Box>
            </SpaceBetween>
        </div>
    );
};

export default ChatMessage;
