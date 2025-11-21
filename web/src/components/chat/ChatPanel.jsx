/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useRef, useEffect } from 'react';
import {
    Container,
    Header,
    SpaceBetween,
    Input,
    Button,
    Box,
    Spinner,
    Alert,
    Icon
} from '@cloudscape-design/components';
import { sendChatMessage } from './chatApi';
import ChatMessage from './ChatMessage';
import './ChatPanel.css';

const ChatPanel = ({ onClose, currentContext = {} }) => {
    const [messages, setMessages] = useState([
        {
            role: 'assistant',
            content: 'Hello! I\'m your VAMS AI assistant. I can help you search for assets, view information, manage files, and more. What would you like to do?',
            timestamp: new Date().toISOString()
        }
    ]);
    const [input, setInput] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const messagesEndRef = useRef(null);
    const inputRef = useRef(null);

    // Auto-scroll to bottom when new messages arrive
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    // Focus input on mount
    useEffect(() => {
        inputRef.current?.focus();
    }, []);

    const handleSendMessage = async () => {
        if (!input.trim() || loading) return;

        const userMessage = input.trim();
        setInput('');
        setError(null);

        // Add user message to chat
        setMessages(prev => [...prev, {
            role: 'user',
            content: userMessage,
            timestamp: new Date().toISOString()
        }]);

        setLoading(true);

        try {
            // Call backend API
            const response = await sendChatMessage(userMessage, currentContext);

            // Add assistant response
            setMessages(prev => [...prev, {
                role: 'assistant',
                content: response.message,
                timestamp: response.timestamp,
                operations: response.operations,
                operations_summary: response.operations_summary
            }]);

        } catch (err) {
            console.error('Chat error:', err);
            setError(err.message || 'Failed to send message. Please try again.');
            
            // Add error message
            setMessages(prev => [...prev, {
                role: 'assistant',
                content: 'I\'m sorry, I encountered an error processing your request. Please try again.',
                timestamp: new Date().toISOString(),
                isError: true
            }]);
        } finally {
            setLoading(false);
        }
    };

    const handleKeyPress = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSendMessage();
        }
    };

    const handleQuickAction = (question) => {
        setInput(question);
        inputRef.current?.focus();
    };

    const quickActions = [
        'List all databases',
        'Show recent assets',
        'What can you help me with?'
    ];

    return (
        <div className="chat-panel">
            <Container
                header={
                    <Header
                        variant="h2"
                        actions={
                            <Button
                                variant="icon"
                                iconName="close"
                                onClick={onClose}
                                ariaLabel="Close chat"
                            />
                        }
                    >
                        VAMS AI Assistant
                    </Header>
                }
            >
                <SpaceBetween size="m">
                    {/* Context indicator */}
                    {(currentContext.databaseId || currentContext.assetId) && (
                        <Alert type="info" dismissible={false}>
                            <Box variant="small">
                                Current context:
                                {currentContext.databaseId && ` Database: ${currentContext.databaseId}`}
                                {currentContext.assetId && ` | Asset: ${currentContext.assetId}`}
                            </Box>
                        </Alert>
                    )}

                    {/* Error display */}
                    {error && (
                        <Alert
                            type="error"
                            dismissible
                            onDismiss={() => setError(null)}
                        >
                            {error}
                        </Alert>
                    )}

                    {/* Messages container */}
                    <div className="chat-messages">
                        <SpaceBetween size="s">
                            {messages.map((message, index) => (
                                <ChatMessage
                                    key={index}
                                    message={message}
                                />
                            ))}
                            {loading && (
                                <Box padding="s" textAlign="center">
                                    <Spinner size="normal" />
                                    <Box variant="small" color="text-status-info" margin={{ top: "xs" }}>
                                        Thinking...
                                    </Box>
                                </Box>
                            )}
                            <div ref={messagesEndRef} />
                        </SpaceBetween>
                    </div>

                    {/* Quick actions */}
                    {messages.length === 1 && !loading && (
                        <Box>
                            <Box variant="small" margin={{ bottom: "xs" }}>
                                Try asking:
                            </Box>
                            <SpaceBetween size="xs" direction="horizontal">
                                {quickActions.map((action, index) => (
                                    <Button
                                        key={index}
                                        variant="inline-link"
                                        iconName="status-positive"
                                        onClick={() => handleQuickAction(action)}
                                    >
                                        {action}
                                    </Button>
                                ))}
                            </SpaceBetween>
                        </Box>
                    )}

                    {/* Input area */}
                    <div className="chat-input-container">
                        <SpaceBetween size="xs" direction="horizontal">
                            <div className="chat-input-wrapper">
                                <Input
                                    ref={inputRef}
                                    value={input}
                                    onChange={({ detail }) => setInput(detail.value)}
                                    onKeyDown={handleKeyPress}
                                    placeholder="Ask about assets, databases, workflows..."
                                    disabled={loading}
                                    ariaLabel="Chat message input"
                                />
                            </div>
                            <Button
                                variant="primary"
                                onClick={handleSendMessage}
                                disabled={!input.trim() || loading}
                                iconName="send"
                                ariaLabel="Send message"
                            >
                                Send
                            </Button>
                        </SpaceBetween>
                    </div>
                </SpaceBetween>
            </Container>
        </div>
    );
};

export default ChatPanel;
