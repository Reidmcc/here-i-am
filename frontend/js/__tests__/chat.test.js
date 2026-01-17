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
    getStreamAbortController,
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
        state.currentConversationEntities = [];
        state.settings = {
            model: 'claude-sonnet-4-5-20250929',
            temperature: 1.0,
            maxTokens: 8192,
            systemPrompt: '',
            verbosity: 'medium',
            researcherName: '',
        };
        state.ttsEnabled = false;

        // Create mock elements
        mockElements = {
            messageInput: document.createElement('textarea'),
            sendBtn: document.createElement('button'),
            stopBtn: document.createElement('button'),
            messages: document.createElement('div'),
            tokenCount: document.createElement('span'),
            conversationTitle: document.createElement('h2'),
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
            onConversationUpdate: vi.fn(),
            onLoadConversation: vi.fn(),
            renderConversationList: vi.fn(),
            getEntityLabel: vi.fn(() => 'Claude'),
            showEntityResponderSelector: vi.fn(),
            hideEntityResponderSelector: vi.fn(),
            handleInputChange: vi.fn(),
            createNewConversation: vi.fn(() => Promise.resolve()),
            showMultiEntityModal: vi.fn(),
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

            // Mock createNewConversation to fail/not create
            const createNewConversationMock = vi.fn(() => Promise.resolve());
            setCallbacks({
                ...mockCallbacks,
                createNewConversation: createNewConversationMock,
            });

            await sendMessage();

            // Should try to create a new conversation first
            expect(createNewConversationMock).toHaveBeenCalled();
        });

        it('should not send while already loading', async () => {
            state.isLoading = true;
            mockElements.messageInput.value = 'Test message';

            await sendMessage();

            expect(window.api.sendMessageStream).not.toHaveBeenCalled();
        });

        it('should clear input after sending', async () => {
            mockElements.messageInput.value = 'Test message';
            window.api.sendMessageStream = vi.fn((data, handlers) => {
                handlers.onDone({ usage: {} });
                handlers.onStored({});
                return Promise.resolve();
            });

            await sendMessage();

            expect(mockElements.messageInput.value).toBe('');
        });

        it('should set loading state during send', async () => {
            mockElements.messageInput.value = 'Test message';
            let loadingDuringSend = false;

            window.api.sendMessageStream = vi.fn((data, handlers) => {
                loadingDuringSend = state.isLoading;
                handlers.onDone({ usage: {} });
                handlers.onStored({});
                return Promise.resolve();
            });

            await sendMessage();

            expect(loadingDuringSend).toBe(true);
        });

        it('should show stop button and hide send button during streaming', async () => {
            mockElements.messageInput.value = 'Test message';
            mockElements.sendBtn.style.display = 'flex';
            mockElements.stopBtn.style.display = 'none';

            let sendBtnDisplay = '';
            let stopBtnDisplay = '';

            window.api.sendMessageStream = vi.fn((data, handlers) => {
                sendBtnDisplay = mockElements.sendBtn.style.display;
                stopBtnDisplay = mockElements.stopBtn.style.display;
                handlers.onDone({ usage: {} });
                handlers.onStored({});
                return Promise.resolve();
            });

            await sendMessage();

            expect(sendBtnDisplay).toBe('none');
            expect(stopBtnDisplay).toBe('flex');
        });

        it('should reset buttons after streaming completes', async () => {
            mockElements.messageInput.value = 'Test message';

            window.api.sendMessageStream = vi.fn((data, handlers) => {
                handlers.onDone({ usage: {} });
                handlers.onStored({});
                return Promise.resolve();
            });

            await sendMessage();

            expect(mockElements.sendBtn.style.display).toBe('flex');
            expect(mockElements.stopBtn.style.display).toBe('none');
            expect(state.isLoading).toBe(false);
        });

        it('should call handleInputChange callback after sending', async () => {
            mockElements.messageInput.value = 'Test message';

            window.api.sendMessageStream = vi.fn((data, handlers) => {
                handlers.onDone({ usage: {} });
                handlers.onStored({});
                return Promise.resolve();
            });

            await sendMessage();

            expect(mockCallbacks.handleInputChange).toHaveBeenCalled();
        });
    });

    describe('sendMessage in multi-entity mode', () => {
        beforeEach(() => {
            state.isMultiEntityMode = true;
            state.currentConversationEntities = [
                { index_name: 'entity-1', label: 'Claude' },
                { index_name: 'entity-2', label: 'GPT' },
            ];
        });

        it('should show entity responder selector instead of sending directly', async () => {
            mockElements.messageInput.value = 'Test message';

            await sendMessage();

            expect(mockCallbacks.showEntityResponderSelector).toHaveBeenCalled();
            expect(window.api.sendMessageStream).not.toHaveBeenCalled();
        });

        it('should store pending message content', async () => {
            mockElements.messageInput.value = 'Test message';

            await sendMessage();

            expect(state.pendingMessageContent).toBe('Test message');
        });

        it('should clear input after storing pending message', async () => {
            mockElements.messageInput.value = 'Test message';

            await sendMessage();

            expect(mockElements.messageInput.value).toBe('');
        });
    });

    describe('stopGeneration', () => {
        it('should reset loading state', () => {
            state.isLoading = true;

            stopGeneration();

            expect(state.isLoading).toBe(false);
        });

        it('should hide stop button and show send button', () => {
            mockElements.stopBtn.style.display = 'flex';
            mockElements.sendBtn.style.display = 'none';

            stopGeneration();

            expect(mockElements.stopBtn.style.display).toBe('none');
            expect(mockElements.sendBtn.style.display).toBe('flex');
        });

        it('should call handleInputChange callback', () => {
            stopGeneration();

            expect(mockCallbacks.handleInputChange).toHaveBeenCalled();
        });

        it('should return null for abort controller when not streaming', () => {
            const controller = getStreamAbortController();

            expect(controller).toBeNull();
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
            window.api.sendMessageStream = vi.fn((data, handlers) => {
                sentData = data;
                handlers.onDone({ usage: {} });
                handlers.onStored({});
                return Promise.resolve();
            });

            await sendMessage();

            expect(sentData.attachments.images).toHaveLength(1);
        });

        it('should include files in request when present', async () => {
            mockElements.messageInput.value = 'Check this file';
            state.pendingAttachments = {
                images: [],
                files: [{ name: 'test.txt', type: 'text/plain', content: 'Hello' }],
            };

            let sentData = null;
            window.api.sendMessageStream = vi.fn((data, handlers) => {
                sentData = data;
                handlers.onDone({ usage: {} });
                handlers.onStored({});
                return Promise.resolve();
            });

            await sendMessage();

            expect(sentData.attachments.files).toHaveLength(1);
        });

        it('should clear attachments after sending', async () => {
            mockElements.messageInput.value = 'Test';
            state.pendingAttachments = {
                images: [{ name: 'test.png', type: 'image/png', base64: 'abc123' }],
                files: [],
            };

            window.api.sendMessageStream = vi.fn((data, handlers) => {
                handlers.onDone({ usage: {} });
                handlers.onStored({});
                return Promise.resolve();
            });

            await sendMessage();

            expect(state.pendingAttachments.images).toHaveLength(0);
        });

        it('should allow sending with only attachments (no text)', async () => {
            mockElements.messageInput.value = '';
            state.pendingAttachments = {
                images: [{ name: 'test.png', type: 'image/png', base64: 'abc123' }],
                files: [],
            };

            let sentData = null;
            window.api.sendMessageStream = vi.fn((data, handlers) => {
                sentData = data;
                handlers.onDone({ usage: {} });
                handlers.onStored({});
                return Promise.resolve();
            });

            await sendMessage();

            // Should have been called since we have attachments
            expect(window.api.sendMessageStream).toHaveBeenCalled();
        });
    });

    describe('streaming handlers', () => {
        it('should handle onToken events', async () => {
            mockElements.messageInput.value = 'Test message';

            window.api.sendMessageStream = vi.fn((data, handlers) => {
                handlers.onToken({ content: 'Hello' });
                handlers.onToken({ content: ' world' });
                handlers.onDone({ usage: {} });
                handlers.onStored({});
                return Promise.resolve();
            });

            await sendMessage();

            // Verify message element was added
            expect(mockElements.messages.children.length).toBeGreaterThan(0);
        });

        it('should update token count on done', async () => {
            mockElements.messageInput.value = 'Test message';

            window.api.sendMessageStream = vi.fn((data, handlers) => {
                handlers.onDone({ usage: { input_tokens: 100, output_tokens: 50 } });
                handlers.onStored({});
                return Promise.resolve();
            });

            await sendMessage();

            expect(mockElements.tokenCount.textContent).toContain('100');
            expect(mockElements.tokenCount.textContent).toContain('50');
        });
    });
});
