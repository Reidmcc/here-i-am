/**
 * Unit Tests for Messages Module
 * Tests message rendering and display functionality
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { state } from '../modules/state.js';
import {
    setElements,
    setCallbacks,
    addMessage,
    addToolMessage,
    createStreamingMessage,
    addTypingIndicator,
    clearMessages,
    removeRegenerateButtons,
    copyMessage,
    updateAssistantMessageActions,
    isNearBottom,
    scrollToBottom,
} from '../modules/messages.js';

describe('Messages Module', () => {
    let mockElements;
    let mockCallbacks;

    beforeEach(() => {
        // Reset TTS state
        state.ttsEnabled = false;

        // Create mock elements
        mockElements = {
            messages: document.createElement('div'),
            messagesContainer: document.createElement('div'),
            welcomeMessage: document.createElement('div'),
        };

        mockElements.messages.id = 'messages';
        mockElements.messagesContainer.id = 'messages-container';
        mockElements.welcomeMessage.id = 'welcome-message';
        mockElements.messagesContainer.appendChild(mockElements.messages);

        // Create mock callbacks
        mockCallbacks = {
            onCopyMessage: vi.fn(),
            onEditMessage: vi.fn(),
            onRegenerateMessage: vi.fn(),
            onSpeakMessage: vi.fn(),
            scrollToBottom: vi.fn(),
        };

        setElements(mockElements);
        setCallbacks(mockCallbacks);
    });

    describe('addMessage', () => {
        it('should create a human message element', () => {
            const message = addMessage('human', 'Hello world');

            expect(message.classList.contains('message')).toBe(true);
            expect(message.classList.contains('human')).toBe(true);
            expect(message.textContent).toContain('Hello world');
        });

        it('should create an assistant message element', () => {
            const message = addMessage('assistant', 'Hi there!');

            expect(message.classList.contains('message')).toBe(true);
            expect(message.classList.contains('assistant')).toBe(true);
            expect(message.textContent).toContain('Hi there!');
        });

        it('should hide welcome message', () => {
            mockElements.welcomeMessage.style.display = 'block';

            addMessage('human', 'Test');

            expect(mockElements.welcomeMessage.style.display).toBe('none');
        });

        it('should store message ID in data attribute', () => {
            const message = addMessage('human', 'Test', { messageId: 'msg-123' });

            expect(message.dataset.messageId).toBe('msg-123');
        });

        it('should store role in data attribute', () => {
            const message = addMessage('assistant', 'Test');

            expect(message.dataset.role).toBe('assistant');
        });

        it('should store speaker entity ID for multi-entity', () => {
            const message = addMessage('assistant', 'Test', { speakerEntityId: 'entity-1' });

            expect(message.dataset.speakerEntityId).toBe('entity-1');
        });

        it('should add speaker label for assistant messages', () => {
            const message = addMessage('assistant', 'Test', { speakerLabel: 'Claude' });

            expect(message.innerHTML).toContain('message-speaker-label');
            expect(message.innerHTML).toContain('Claude');
        });

        it('should not add speaker label for human messages', () => {
            const message = addMessage('human', 'Test', { speakerLabel: 'User' });

            expect(message.innerHTML).not.toContain('message-speaker-label');
        });

        it('should add timestamp by default', () => {
            const message = addMessage('human', 'Test');

            expect(message.innerHTML).toContain('message-meta');
        });

        it('should hide timestamp when showTimestamp is false', () => {
            const message = addMessage('human', 'Test', { showTimestamp: false });

            expect(message.innerHTML).not.toContain('message-meta');
        });

        it('should use custom timestamp', () => {
            const customDate = new Date('2024-01-15T10:30:00');
            const message = addMessage('human', 'Test', { timestamp: customDate.toISOString() });

            expect(message.innerHTML).toContain('10:30');
        });

        it('should add error class when isError is true', () => {
            const message = addMessage('assistant', 'Error occurred', { isError: true });

            expect(message.querySelector('.message-bubble.error')).toBeTruthy();
        });

        describe('action buttons', () => {
            it('should add copy button for messages with ID', () => {
                const message = addMessage('human', 'Test', { messageId: 'msg-1' });

                expect(message.querySelector('.copy-btn')).toBeTruthy();
            });

            it('should add edit button for human messages', () => {
                const message = addMessage('human', 'Test', { messageId: 'msg-1' });

                expect(message.querySelector('.edit-btn')).toBeTruthy();
            });

            it('should not add edit button for assistant messages', () => {
                const message = addMessage('assistant', 'Test', { messageId: 'msg-1' });

                expect(message.querySelector('.edit-btn')).toBeFalsy();
            });

            it('should add regenerate button for latest assistant message', () => {
                const message = addMessage('assistant', 'Test', {
                    messageId: 'msg-1',
                    isLatestAssistant: true,
                });

                expect(message.querySelector('.regenerate-btn')).toBeTruthy();
            });

            it('should not add regenerate button when not latest', () => {
                const message = addMessage('assistant', 'Test', {
                    messageId: 'msg-1',
                    isLatestAssistant: false,
                });

                expect(message.querySelector('.regenerate-btn')).toBeFalsy();
            });

            it('should add speak button when TTS is enabled', () => {
                state.ttsEnabled = true;

                const message = addMessage('assistant', 'Test', { messageId: 'msg-1' });

                expect(message.querySelector('.speak-btn')).toBeTruthy();
            });

            it('should not add speak button when TTS is disabled', () => {
                state.ttsEnabled = false;

                const message = addMessage('assistant', 'Test', { messageId: 'msg-1' });

                expect(message.querySelector('.speak-btn')).toBeFalsy();
            });

            it('should not add buttons for error messages', () => {
                const message = addMessage('assistant', 'Error', {
                    messageId: 'msg-1',
                    isError: true,
                });

                expect(message.querySelector('.copy-btn')).toBeFalsy();
            });

            it('should not add buttons when no message ID', () => {
                const message = addMessage('human', 'Test');

                expect(message.querySelector('.copy-btn')).toBeFalsy();
                expect(message.querySelector('.edit-btn')).toBeFalsy();
            });
        });

        describe('button event handlers', () => {
            it('should call onEditMessage when edit button clicked', () => {
                const message = addMessage('human', 'Test content', { messageId: 'msg-1' });
                const editBtn = message.querySelector('.edit-btn');

                editBtn.click();

                expect(mockCallbacks.onEditMessage).toHaveBeenCalledWith(
                    message,
                    'msg-1',
                    'Test content'
                );
            });

            it('should call onRegenerateMessage when regenerate button clicked', () => {
                const message = addMessage('assistant', 'Test', {
                    messageId: 'msg-1',
                    isLatestAssistant: true,
                });
                const regenerateBtn = message.querySelector('.regenerate-btn');

                regenerateBtn.click();

                expect(mockCallbacks.onRegenerateMessage).toHaveBeenCalledWith('msg-1');
            });

            it('should call onSpeakMessage when speak button clicked', () => {
                state.ttsEnabled = true;

                const message = addMessage('assistant', 'Test content', { messageId: 'msg-1' });
                const speakBtn = message.querySelector('.speak-btn');

                speakBtn.click();

                expect(mockCallbacks.onSpeakMessage).toHaveBeenCalledWith(
                    'Test content',
                    speakBtn,
                    'msg-1'
                );
            });
        });

        it('should render markdown in content', () => {
            const message = addMessage('assistant', '**bold** and *italic*');

            expect(message.innerHTML).toContain('<strong>bold</strong>');
            expect(message.innerHTML).toContain('<em>italic</em>');
        });

        it('should append message to messages container', () => {
            addMessage('human', 'Test 1');
            addMessage('assistant', 'Test 2');

            expect(mockElements.messages.children.length).toBe(2);
        });
    });

    describe('addToolMessage', () => {
        it('should create tool start message', () => {
            const message = addToolMessage('start', 'web_search', {
                tool_id: 'tool-1',
                input: { query: 'test query' },
            });

            expect(message.classList.contains('tool-message')).toBe(true);
            expect(message.dataset.toolId).toBe('tool-1');
            expect(message.innerHTML).toContain('web search');
            expect(message.innerHTML).toContain('...');
        });

        it('should include input details when available', () => {
            const message = addToolMessage('start', 'web_fetch', {
                tool_id: 'tool-1',
                input: { url: 'https://example.com' },
            });

            expect(message.innerHTML).toContain('tool-input-details');
            expect(message.innerHTML).toContain('https://example.com');
        });

        it('should update tool start message with result', () => {
            // Create start message
            const startMessage = addToolMessage('start', 'web_search', {
                tool_id: 'tool-1',
                input: {},
            });

            // Add result
            addToolMessage('result', 'web_search', {
                tool_id: 'tool-1',
                content: 'Search results here',
                is_error: false,
            });

            expect(startMessage.querySelector('.tool-status.success')).toBeTruthy();
            expect(startMessage.innerHTML).toContain('✓');
            expect(startMessage.innerHTML).toContain('Search results here');
        });

        it('should show error status for failed tools', () => {
            const startMessage = addToolMessage('start', 'web_fetch', {
                tool_id: 'tool-2',
                input: {},
            });

            addToolMessage('result', 'web_fetch', {
                tool_id: 'tool-2',
                content: 'Connection failed',
                is_error: true,
            });

            expect(startMessage.querySelector('.tool-status.error')).toBeTruthy();
            expect(startMessage.innerHTML).toContain('✗');
            expect(startMessage.innerHTML).toContain('(Error)');
        });

        it('should truncate long tool results', () => {
            const startMessage = addToolMessage('start', 'web_fetch', {
                tool_id: 'tool-3',
                input: {},
            });

            const longContent = 'x'.repeat(3000);
            addToolMessage('result', 'web_fetch', {
                tool_id: 'tool-3',
                content: longContent,
                is_error: false,
            });

            expect(startMessage.innerHTML).toContain('...[truncated]');
        });

        it('should return null for result type', () => {
            addToolMessage('start', 'test', { tool_id: 'tool-1', input: {} });
            const result = addToolMessage('result', 'test', { tool_id: 'tool-1', content: 'ok' });

            expect(result).toBe(null);
        });
    });

    describe('createStreamingMessage', () => {
        it('should create streaming message element', () => {
            const stream = createStreamingMessage('assistant');

            expect(stream.element.classList.contains('message')).toBe(true);
            expect(stream.element.classList.contains('assistant')).toBe(true);
            expect(stream.element.querySelector('.streaming')).toBeTruthy();
        });

        it('should hide welcome message', () => {
            mockElements.welcomeMessage.style.display = 'block';

            createStreamingMessage('assistant');

            expect(mockElements.welcomeMessage.style.display).toBe('none');
        });

        it('should add speaker label for multi-entity', () => {
            const stream = createStreamingMessage('assistant', 'Claude');

            expect(stream.element.innerHTML).toContain('message-speaker-label');
            expect(stream.element.innerHTML).toContain('Claude');
        });

        it('should include streaming cursor', () => {
            const stream = createStreamingMessage('assistant');

            expect(stream.element.querySelector('.streaming-cursor')).toBeTruthy();
        });

        it('should update content with new tokens', () => {
            const stream = createStreamingMessage('assistant');

            stream.updateContent('Hello');
            stream.updateContent(' world');

            expect(stream.getContent()).toBe('Hello world');
        });

        it('should accumulate content correctly', () => {
            const stream = createStreamingMessage('assistant');

            stream.updateContent('Token1 ');
            stream.updateContent('Token2 ');
            stream.updateContent('Token3');

            expect(stream.getContent()).toBe('Token1 Token2 Token3');
        });

        it('should finalize message with markdown rendering', () => {
            const stream = createStreamingMessage('assistant');

            stream.updateContent('**bold** text');
            stream.finalize();

            expect(stream.element.innerHTML).toContain('<strong>bold</strong>');
            expect(stream.element.querySelector('.streaming-cursor')).toBeFalsy();
            expect(stream.element.querySelector('.streaming')).toBeFalsy();
        });

        it('should add timestamp on finalize', () => {
            const stream = createStreamingMessage('assistant');

            stream.updateContent('Test');
            stream.finalize();

            expect(stream.element.innerHTML).toContain('message-meta');
        });

        it('should return accumulated content on finalize', () => {
            const stream = createStreamingMessage('assistant');

            stream.updateContent('Final content');
            const result = stream.finalize();

            expect(result).toBe('Final content');
        });
    });

    describe('addTypingIndicator', () => {
        it('should create typing indicator element', () => {
            const indicator = addTypingIndicator();

            expect(indicator.classList.contains('message')).toBe(true);
            expect(indicator.classList.contains('assistant')).toBe(true);
            expect(indicator.querySelector('.typing-indicator')).toBeTruthy();
        });

        it('should include three typing dots', () => {
            const indicator = addTypingIndicator();

            const dots = indicator.querySelectorAll('.typing-dot');
            expect(dots.length).toBe(3);
        });

        it('should call scrollToBottom callback', () => {
            addTypingIndicator();

            expect(mockCallbacks.scrollToBottom).toHaveBeenCalled();
        });
    });

    describe('clearMessages', () => {
        it('should clear all messages', () => {
            addMessage('human', 'Test 1');
            addMessage('assistant', 'Test 2');

            clearMessages();

            // Should only have welcome message
            expect(mockElements.messages.children.length).toBe(1);
        });

        it('should show welcome message', () => {
            mockElements.welcomeMessage.style.display = 'none';

            clearMessages();

            expect(mockElements.welcomeMessage.style.display).toBe('block');
        });

        it('should handle missing elements gracefully', () => {
            setElements({});
            expect(() => clearMessages()).not.toThrow();
        });
    });

    describe('removeRegenerateButtons', () => {
        it('should remove all regenerate buttons from assistant messages', () => {
            // Add messages with regenerate buttons
            addMessage('assistant', 'Test 1', { messageId: 'msg-1', isLatestAssistant: true });
            addMessage('assistant', 'Test 2', { messageId: 'msg-2', isLatestAssistant: true });

            expect(mockElements.messages.querySelectorAll('.regenerate-btn').length).toBe(2);

            removeRegenerateButtons();

            expect(mockElements.messages.querySelectorAll('.regenerate-btn').length).toBe(0);
        });
    });

    describe('copyMessage', () => {
        it('should copy content to clipboard', async () => {
            const btn = document.createElement('button');
            btn.innerHTML = '<svg>original</svg>';

            await copyMessage('Test content', btn);

            expect(navigator.clipboard.writeText).toHaveBeenCalledWith('Test content');
        });

        it('should show copied state on button', async () => {
            vi.useFakeTimers();
            const btn = document.createElement('button');
            btn.innerHTML = '<svg>original</svg>';

            await copyMessage('Test', btn);

            expect(btn.classList.contains('copied')).toBe(true);
            expect(btn.title).toBe('Copied!');

            vi.advanceTimersByTime(2000);

            expect(btn.classList.contains('copied')).toBe(false);
            expect(btn.title).toBe('Copy to clipboard');

            vi.useRealTimers();
        });
    });

    describe('updateAssistantMessageActions', () => {
        it('should add action buttons to assistant message', () => {
            const messageEl = document.createElement('div');
            messageEl.className = 'message assistant';
            const bubble = document.createElement('div');
            bubble.className = 'message-bubble';
            messageEl.appendChild(bubble);

            updateAssistantMessageActions(messageEl, 'msg-1', 'Content');

            expect(bubble.querySelector('.message-bubble-actions')).toBeTruthy();
            expect(bubble.querySelector('.copy-btn')).toBeTruthy();
            expect(bubble.querySelector('.regenerate-btn')).toBeTruthy();
        });

        it('should include speak button when TTS enabled', () => {
            state.ttsEnabled = true;

            const messageEl = document.createElement('div');
            const bubble = document.createElement('div');
            bubble.className = 'message-bubble';
            messageEl.appendChild(bubble);

            updateAssistantMessageActions(messageEl, 'msg-1', 'Content');

            expect(bubble.querySelector('.speak-btn')).toBeTruthy();
        });

        it('should remove existing regenerate buttons', () => {
            // Add a message with regenerate button
            addMessage('assistant', 'Old', { messageId: 'old-1', isLatestAssistant: true });

            const messageEl = document.createElement('div');
            const bubble = document.createElement('div');
            bubble.className = 'message-bubble';
            messageEl.appendChild(bubble);

            updateAssistantMessageActions(messageEl, 'new-1', 'New content');

            // Old regenerate button should be gone
            expect(mockElements.messages.querySelectorAll('.regenerate-btn').length).toBe(0);
        });
    });

    describe('isNearBottom', () => {
        beforeEach(() => {
            // Setup scrollable container
            Object.defineProperty(mockElements.messagesContainer, 'scrollTop', {
                value: 800,
                writable: true,
            });
            Object.defineProperty(mockElements.messagesContainer, 'clientHeight', {
                value: 100,
                writable: true,
            });
            Object.defineProperty(mockElements.messagesContainer, 'scrollHeight', {
                value: 1000,
                writable: true,
            });
        });

        it('should return true when at bottom', () => {
            expect(isNearBottom()).toBe(true);
        });

        it('should return true when within threshold', () => {
            mockElements.messagesContainer.scrollTop = 850;
            expect(isNearBottom(100)).toBe(true);
        });

        it('should return false when far from bottom', () => {
            mockElements.messagesContainer.scrollTop = 500;
            expect(isNearBottom(100)).toBe(false);
        });

        it('should support custom threshold', () => {
            mockElements.messagesContainer.scrollTop = 700;
            expect(isNearBottom(50)).toBe(false);
            expect(isNearBottom(250)).toBe(true);
        });
    });

    describe('scrollToBottom', () => {
        it('should scroll container to bottom', () => {
            Object.defineProperty(mockElements.messagesContainer, 'scrollHeight', {
                value: 1000,
                writable: true,
            });
            mockElements.messagesContainer.scrollTop = 0;

            scrollToBottom();

            expect(mockElements.messagesContainer.scrollTop).toBe(1000);
        });
    });
});
