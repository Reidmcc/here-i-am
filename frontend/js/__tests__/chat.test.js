/**
 * Unit Tests for Chat Module
 * Tests message sending, streaming, and regeneration
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { state } from '../modules/state.js';
import {
    setElements,
    setCallbacks,
    sendMessage,
    stopGeneration,
} from '../modules/chat.js';
import { setElements as setMessagesElements, setCallbacks as setMessagesCallbacks } from '../modules/messages.js';

describe('Chat Module', () => {
    let mockElements;
    let mockCallbacks;

    beforeEach(() => {
        // Reset state
        state.currentConversationId = 'test-conv-id';
        state.selectedEntityId = 'entity-1';
        state.isMultiEntityMode = false;
        state.isLoading = false;
        state.pendingAttachments = { images: [], files: [] };
        state.settings = {
            model: 'claude-sonnet-4-5-20250929',
            temperature: 1.0,
            maxTokens: 8192,
            systemPrompt: '',
        };

        // Create mock elements
        mockElements = {
            messageInput: document.createElement('textarea'),
            sendBtn: document.createElement('button'),
            stopBtn: document.createElement('button'),
            messages: document.createElement('div'),
        };

        // Set up messages module elements
        const messagesContainer = document.createElement('div');
        messagesContainer.scrollTop = 0;
        Object.defineProperty(messagesContainer, 'scrollHeight', {
            value: 100,
            configurable: true
        });

        setMessagesElements({
            messages: mockElements.messages,
            messagesContainer: messagesContainer,
            welcomeMessage: document.createElement('div'),
        });
        setMessagesCallbacks({});

        mockCallbacks = {
            onMessageSent: vi.fn(),
            onResponseReceived: vi.fn(),
            onStreamStart: vi.fn(),
            onStreamEnd: vi.fn(),
        };

        setElements(mockElements);
        setCallbacks(mockCallbacks);

        vi.clearAllMocks();
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    describe('sendMessage', () => {
        it('should not send empty messages', async () => {
            mockElements.messageInput.value = '';

            await sendMessage();

            expect(window.api.sendMessageStream).not.toHaveBeenCalled();
        });

        it('should not send when no conversation selected', async () => {
            state.currentConversationId = null;
            mockElements.messageInput.value = 'Test message';

            await sendMessage();

            expect(window.api.sendMessageStream).not.toHaveBeenCalled();
        });

        it('should not send while already loading', async () => {
            state.isLoading = true;
            mockElements.messageInput.value = 'Test message';

            await sendMessage();

            expect(window.api.sendMessageStream).not.toHaveBeenCalled();
        });

        it('should clear input after sending', async () => {
            mockElements.messageInput.value = 'Test message';
            window.api.sendMessageStream = vi.fn(() => Promise.resolve({
                getReader: () => ({
                    read: vi.fn()
                        .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode('data: {"type":"done"}\n\n') })
                        .mockResolvedValueOnce({ done: true })
                })
            }));

            await sendMessage();

            expect(mockElements.messageInput.value).toBe('');
        });

        it('should set loading state during send', async () => {
            mockElements.messageInput.value = 'Test message';
            let loadingDuringSend = false;

            window.api.sendMessageStream = vi.fn(() => {
                loadingDuringSend = state.isLoading;
                return Promise.resolve({
                    getReader: () => ({
                        read: vi.fn()
                            .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode('data: {"type":"done"}\n\n') })
                            .mockResolvedValueOnce({ done: true })
                    })
                });
            });

            await sendMessage();

            expect(loadingDuringSend).toBe(true);
        });
    });

    describe('stopGeneration', () => {
        it('should call abort controller when stopping', () => {
            // Set up abort controller mock
            const abortMock = vi.fn();
            state._abortController = { abort: abortMock };

            stopGeneration();

            expect(abortMock).toHaveBeenCalled();
        });

        it('should reset loading state', () => {
            state.isLoading = true;
            state._abortController = { abort: vi.fn() };

            stopGeneration();

            expect(state.isLoading).toBe(false);
        });
    });

    describe('message with attachments', () => {
        it('should include images in request when present', async () => {
            mockElements.messageInput.value = 'Check this image';
            state.pendingAttachments = {
                images: [{ name: 'test.png', type: 'image/png', base64: 'abc123' }],
                files: [],
            };

            let sentData = null;
            window.api.sendMessageStream = vi.fn((data) => {
                sentData = data;
                return Promise.resolve({
                    getReader: () => ({
                        read: vi.fn()
                            .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode('data: {"type":"done"}\n\n') })
                            .mockResolvedValueOnce({ done: true })
                    })
                });
            });

            await sendMessage();

            expect(sentData.images).toHaveLength(1);
        });

        it('should clear attachments after sending', async () => {
            mockElements.messageInput.value = 'Test';
            state.pendingAttachments = {
                images: [{ name: 'test.png', type: 'image/png', base64: 'abc123' }],
                files: [],
            };

            window.api.sendMessageStream = vi.fn(() => Promise.resolve({
                getReader: () => ({
                    read: vi.fn()
                        .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode('data: {"type":"done"}\n\n') })
                        .mockResolvedValueOnce({ done: true })
                })
            }));

            await sendMessage();

            expect(state.pendingAttachments.images).toHaveLength(0);
        });
    });
});
