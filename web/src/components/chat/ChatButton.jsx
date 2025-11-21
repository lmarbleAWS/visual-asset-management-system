/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from 'react';
import { Button, Badge } from '@cloudscape-design/components';
import ChatPanel from './ChatPanel';
import './ChatButton.css';

const ChatButton = ({ currentContext = {} }) => {
    const [isOpen, setIsOpen] = useState(false);

    const handleToggle = () => {
        setIsOpen(!isOpen);
    };

    return (
        <>
            {/* Floating chat button */}
            <div className="chat-button-container">
                <Button
                    variant="primary"
                    iconName="contact"
                    onClick={handleToggle}
                    ariaLabel="Open AI chat assistant"
                    className="chat-floating-button"
                >
                    AI Assistant
                </Button>
            </div>

            {/* Chat panel overlay */}
            {isOpen && (
                <div className="chat-overlay">
                    <div className="chat-panel-wrapper">
                        <ChatPanel
                            onClose={handleToggle}
                            currentContext={currentContext}
                        />
                    </div>
                </div>
            )}
        </>
    );
};

export default ChatButton;
