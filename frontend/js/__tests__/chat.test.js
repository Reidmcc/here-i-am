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
    sendMessageWithResponder,
    performRegeneration,
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

    describe('stop button across streaming paths', () => {
        it('shows the stop button and wires an abort signal in multi-entity send', async () => {
            state.isMultiEntityMode = true;
            state.currentConversationEntities = [
                { index_name: 'entity-1', label: 'Claude' },
            ];
            state.pendingResponderId = 'entity-1';
            state.pendingMessageContent = 'Hello';
            state.pendingMessageAttachments = null;

            let stopBtnDisplay = '';
            let receivedSignal = null;
            window.api.sendMessageStream = vi.fn((data, handlers, signal) => {
                stopBtnDisplay = mockElements.stopBtn.style.display;
                receivedSignal = signal;
                handlers.onDone({ usage: {} });
                handlers.onStored({});
                return Promise.resolve();
            });

            await sendMessageWithResponder();

            expect(stopBtnDisplay).toBe('flex');
            expect(receivedSignal).toBeInstanceOf(AbortSignal);
            // Buttons reset afterwards.
            expect(mockElements.stopBtn.style.display).toBe('none');
            expect(mockElements.sendBtn.style.display).toBe('flex');
            expect(state.isLoading).toBe(false);
        });

        it('shows the stop button and wires an abort signal in regenerate', async () => {
            let stopBtnDisplay = '';
            let receivedSignal = null;
            window.api.regenerateStream = vi.fn((data, handlers, signal) => {
                stopBtnDisplay = mockElements.stopBtn.style.display;
                receivedSignal = signal;
                handlers.onDone({ usage: {} });
                handlers.onStored({ assistant_message_id: 'a1' });
                return Promise.resolve();
            });

            await performRegeneration('msg-1');

            expect(stopBtnDisplay).toBe('flex');
            expect(receivedSignal).toBeInstanceOf(AbortSignal);
            expect(mockElements.stopBtn.style.display).toBe('none');
            expect(mockElements.sendBtn.style.display).toBe('flex');
            expect(state.isLoading).toBe(false);
        });

        it('stopGeneration aborts the active multi-entity stream', async () => {
            state.isMultiEntityMode = true;
            state.currentConversationEntities = [
                { index_name: 'entity-1', label: 'Claude' },
            ];
            state.pendingResponderId = 'entity-1';
            state.pendingMessageContent = 'Hello';

            let abortedSignal = null;
            window.api.sendMessageStream = vi.fn((data, handlers, signal) => {
                // Simulate an in-flight stream: stop before resolving.
                stopGeneration();
                abortedSignal = signal;
                if (signal.aborted && handlers.onAborted) handlers.onAborted();
                return Promise.resolve();
            });

            await sendMessageWithResponder();

            expect(abortedSignal.aborted).toBe(true);
            expect(getStreamAbortController()).toBeNull();
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

        it('should discard rewound text on stream_rewind', async () => {
            // A tool-loop iteration that fails mid-text is re-run; the partial
            // text must not survive into the final response
            mockElements.messageInput.value = 'Test message';

            window.api.sendMessageStream = vi.fn((data, handlers) => {
                handlers.onToken({ content: 'Kept text. ' });
                handlers.onToken({ content: 'Half a sent' });
                handlers.onToolStart({ tool_name: 'web_search', tool_id: 'tool-doomed', input: {} });
                handlers.onStreamRewind({
                    checkpoint: 'Kept text. '.length,
                    discard_tool_ids: ['tool-doomed'],
                });
                handlers.onToken({ content: 'Complete answer' });
                handlers.onDone({ usage: {} });
                handlers.onStored({});
                return Promise.resolve();
            });

            await sendMessage();

            const assistantBubble = mockElements.messages
                .querySelector('.message.assistant .message-bubble');
            expect(assistantBubble.textContent).toContain('Kept text. Complete answer');
            expect(assistantBubble.textContent).not.toContain('Half a sent');

            // The tool the failed attempt announced never ran, so its bubble goes too
            expect(mockElements.messages.querySelector('[data-tool-id="tool-doomed"]'))
                .toBeFalsy();
        });

        it('should offer a Resume action when a tool turn is interrupted', async () => {
            mockElements.messageInput.value = 'Test message';

            window.api.sendMessageStream = vi.fn((data, handlers) => {
                handlers.onToken({ content: 'Let me check. ' });
                handlers.onError({
                    error: 'overloaded',
                    error_type: 'interrupted_tool_turn',
                    completed_tool_calls: 2,
                });
                return Promise.resolve();
            });

            await sendMessage();

            // The partial response stays on screen - it is still good
            const assistantMessages = mockElements.messages.querySelectorAll('.message.assistant');
            expect(assistantMessages.length).toBe(2); // partial response + error bubble
            expect(assistantMessages[0].textContent).toContain('Let me check.');

            const errorBubble = mockElements.messages.querySelector('.message-bubble.error')
                || assistantMessages[1].querySelector('.message-bubble');
            expect(errorBubble.textContent).toContain('2 tool calls');

            const resumeBtn = mockElements.messages.querySelector('.error-retry-actions .retry-btn');
            expect(resumeBtn).toBeTruthy();
            expect(resumeBtn.textContent).toBe('Resume');
        });

        it('should resume into the same bubble without restarting the turn', async () => {
            mockElements.messageInput.value = 'Test message';

            const calls = [];
            window.api.sendMessageStream = vi.fn((data, handlers) => {
                calls.push(data);
                if (calls.length === 1) {
                    handlers.onToken({ content: 'Let me check. ' });
                    handlers.onError({
                        error: 'overloaded',
                        error_type: 'interrupted_tool_turn',
                        completed_tool_calls: 1,
                        turn_id: 'turn-abc',
                    });
                } else {
                    handlers.onToken({ content: 'It is sunny.' });
                    handlers.onDone({ usage: {} });
                    handlers.onStored({});
                }
                return Promise.resolve();
            });

            await sendMessage();

            mockElements.messages.querySelector('.error-retry-actions .retry-btn').click();
            await new Promise((resolve) => setTimeout(resolve, 0));

            // The resume asks the backend to continue the stashed turn...
            expect(calls.length).toBe(2);
            expect(calls[0].resume).toBe(false);
            expect(calls[1].resume).toBe(true);
            // ...naming the turn it saw interrupted, so a stale Resume action
            // cannot pick up someone else's stashed turn
            expect(calls[1].resume_turn_id).toBe('turn-abc');

            // ...and its text lands in the bubble that already held the partial
            // response, matching how the backend persists it: one message
            const assistantMessages = mockElements.messages.querySelectorAll('.message.assistant');
            expect(assistantMessages.length).toBe(1);
            expect(assistantMessages[0].textContent).toContain('Let me check. It is sunny.');
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
