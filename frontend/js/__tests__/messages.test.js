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
    addThinkingMessage,
    resetThinkingMessage,
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

    describe('addThinkingMessage', () => {
        // The open-block handle is module state; clear it so these tests do not
        // depend on each other's ordering.
        beforeEach(() => {
            resetThinkingMessage();
        });

        it('should render streamed reasoning into a single panel', () => {
            const message = addThinkingMessage('start');
            addThinkingMessage('delta', { content: 'weighing ' });
            addThinkingMessage('delta', { content: 'the options' });
            addThinkingMessage('stop');

            expect(message.classList.contains('thinking-message')).toBe(true);
            expect(message.querySelector('.thinking-body').textContent).toBe(
                'weighing the options'
            );
            expect(mockElements.messages.querySelectorAll('.thinking-message')).toHaveLength(1);

            const status = message.querySelector('.tool-status');
            expect(status.classList.contains('loading')).toBe(false);
            expect(status.classList.contains('success')).toBe(true);
        });

        it('should not render reasoning as markup', () => {
            const message = addThinkingMessage('start');
            addThinkingMessage('delta', { content: '<img src=x onerror=alert(1)>' });
            addThinkingMessage('stop');

            const body = message.querySelector('.thinking-body');
            expect(body.querySelector('img')).toBeNull();
            expect(body.textContent).toBe('<img src=x onerror=alert(1)>');
        });

        it('should label a block that returned no summary', () => {
            const message = addThinkingMessage('start');
            addThinkingMessage('stop');

            expect(message.querySelector('.thinking-summary').textContent).toBe(
                '(no summary returned)'
            );
            expect(message.querySelector('.thinking-details').hidden).toBe(true);
        });

        it('should show only the summary line and collapse the rest', () => {
            const message = addThinkingMessage('start');
            addThinkingMessage('delta', { content: 'Weighing the options\n' });
            addThinkingMessage('delta', { content: 'First I considered the cost, then\n' });
            addThinkingMessage('delta', { content: 'I checked the schedule.' });
            addThinkingMessage('stop');

            // Inline view is the first line only...
            expect(message.querySelector('.thinking-summary').textContent).toBe(
                'Weighing the options'
            );
            // ...with the rest kept, collapsed, behind the expander
            const details = message.querySelector('.thinking-details');
            expect(details.hidden).toBe(false);
            expect(details.open).toBe(false);
            expect(message.querySelector('.thinking-body').textContent).toContain(
                'I checked the schedule.'
            );
        });

        it('should truncate a long summary line', () => {
            const message = addThinkingMessage('start');
            addThinkingMessage('delta', { content: 'x'.repeat(200) });
            addThinkingMessage('stop');

            const summary = message.querySelector('.thinking-summary').textContent;
            expect(summary).toBe(`${'x'.repeat(120)}…`);
            // Truncated means there is more to see, so the expander is offered
            expect(message.querySelector('.thinking-details').hidden).toBe(false);
        });

        it('should not offer the expander when the summary is the whole block', () => {
            const message = addThinkingMessage('start');
            addThinkingMessage('delta', { content: 'Checking the schedule.' });
            addThinkingMessage('stop');

            expect(message.querySelector('.thinking-summary').textContent).toBe(
                'Checking the schedule.'
            );
            expect(message.querySelector('.thinking-details').hidden).toBe(true);
        });

        it('should not render the summary as markup', () => {
            const message = addThinkingMessage('start');
            addThinkingMessage('delta', { content: '<img src=x onerror=alert(1)>' });
            addThinkingMessage('stop');

            const summary = message.querySelector('.thinking-summary');
            expect(summary.querySelector('img')).toBeNull();
            expect(summary.textContent).toBe('<img src=x onerror=alert(1)>');
        });

        it('should ignore deltas with no open block', () => {
            expect(addThinkingMessage('delta', { content: 'orphan' })).toBeNull();
            expect(mockElements.messages.querySelectorAll('.thinking-message')).toHaveLength(0);
        });

        it('should not let an interrupted block collect the next turn', () => {
            const first = addThinkingMessage('start');
            addThinkingMessage('delta', { content: 'interrupted' });
            // Turn ends without a thinking_stop, as on an abort or a dead stream
            resetThinkingMessage();

            const second = addThinkingMessage('start');
            addThinkingMessage('delta', { content: 'fresh' });

            expect(second).not.toBe(first);
            expect(first.querySelector('.thinking-body').textContent).toBe('interrupted');
            expect(second.querySelector('.thinking-body').textContent).toBe('fresh');
            expect(first.querySelector('.tool-status').classList.contains('loading')).toBe(false);
        });

        it('should self-heal when a block is left open', () => {
            const first = addThinkingMessage('start');
            addThinkingMessage('delta', { content: 'stale' });

            // No reset — a new block opening must still not append to the old one
            addThinkingMessage('start');
            addThinkingMessage('delta', { content: 'current' });

            expect(first.querySelector('.thinking-body').textContent).toBe('stale');
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

    describe('streaming turn ordering', () => {
        // Read a streaming turn as a flat list of what it renders, in DOM
        // order: 't:<text>' for a text bubble, 'tool:<id>' / 'thinking' for a
        // card. The whole point of the turn container is that this list matches
        // the order the events arrived in.
        const layoutOf = (element) =>
            Array.from(element.children)
                .map((child) => {
                    if (child.classList.contains('message-bubble')) {
                        return `t:${child.querySelector('.message-content').textContent}`;
                    }
                    if (child.classList.contains('tool-message')) {
                        return `tool:${child.dataset.toolId}`;
                    }
                    if (child.classList.contains('thinking-message')) return 'thinking';
                    if (child.classList.contains('message-meta')) return 'meta';
                    return child.className;
                })
                .filter((entry) => entry !== 'meta');

        it('should keep tool cards between the text that streamed around them', () => {
            const stream = createStreamingMessage('assistant');

            stream.updateContent('Looking that up.');
            addToolMessage('start', 'web_search', { tool_id: 'tool-a', input: { q: 'x' } });
            addToolMessage('result', 'web_search', { tool_id: 'tool-a', content: 'hits' });
            stream.updateContent('Found it.');
            addToolMessage('start', 'web_fetch', { tool_id: 'tool-b', input: { url: 'u' } });
            addToolMessage('result', 'web_fetch', { tool_id: 'tool-b', content: 'page' });
            stream.updateContent('Done.');

            expect(layoutOf(stream.element)).toEqual([
                't:Looking that up.',
                'tool:tool-a',
                't:Found it.',
                'tool:tool-b',
                't:Done.',
            ]);
        });

        it('should keep the ordering after the turn is finalized', () => {
            const stream = createStreamingMessage('assistant');

            stream.updateContent('Before. ');
            addToolMessage('start', 'web_search', { tool_id: 'tool-a', input: {} });
            stream.updateContent('After.');
            stream.finalize();

            expect(layoutOf(stream.element)).toEqual(['t:Before. ', 'tool:tool-a', 't:After.']);
        });

        it('should keep thinking cards in position too', () => {
            const stream = createStreamingMessage('assistant');

            addThinkingMessage('start');
            addThinkingMessage('delta', { content: 'weighing it' });
            addThinkingMessage('stop');
            stream.updateContent('Here goes.');
            addThinkingMessage('start');
            addThinkingMessage('delta', { content: 'reconsidering' });
            addThinkingMessage('stop');
            stream.updateContent('Actually, this.');

            expect(layoutOf(stream.element)).toEqual([
                'thinking',
                't:Here goes.',
                'thinking',
                't:Actually, this.',
            ]);
        });

        it('should not leave an empty bubble above a leading card', () => {
            const stream = createStreamingMessage('assistant');

            addThinkingMessage('start');
            addThinkingMessage('stop');
            stream.updateContent('Text.');
            stream.finalize();

            expect(layoutOf(stream.element)).toEqual(['thinking', 't:Text.']);
        });

        it('should carry the speaker label on the first surviving bubble', () => {
            const stream = createStreamingMessage('assistant', 'Claude');

            addThinkingMessage('start');
            addThinkingMessage('stop');
            stream.updateContent('Hello.');
            stream.finalize();

            const labels = stream.element.querySelectorAll('.message-speaker-label');
            expect(labels).toHaveLength(1);
            expect(labels[0].textContent).toBe('Claude');
            expect(labels[0].closest('.message-bubble')).toBe(stream.element.querySelector('.message-bubble'));
        });

        it('should accumulate the whole turn as one string across segments', () => {
            const stream = createStreamingMessage('assistant');

            stream.updateContent('One ');
            addToolMessage('start', 'web_search', { tool_id: 'tool-a', input: {} });
            stream.updateContent('two ');
            addThinkingMessage('start');
            addThinkingMessage('stop');
            stream.updateContent('three');

            expect(stream.getContent()).toBe('One two three');
            expect(stream.finalize()).toBe('One two three');
        });

        it('should render markdown in every segment on finalize', () => {
            const stream = createStreamingMessage('assistant');

            stream.updateContent('**bold**');
            addToolMessage('start', 'web_search', { tool_id: 'tool-a', input: {} });
            stream.updateContent('*italic*');
            stream.finalize();

            expect(stream.element.innerHTML).toContain('<strong>bold</strong>');
            expect(stream.element.innerHTML).toContain('<em>italic</em>');
            expect(stream.element.querySelector('.streaming')).toBeFalsy();
            expect(stream.element.querySelector('.streaming-cursor')).toBeFalsy();
        });

        it('should expose the last bubble for action buttons', () => {
            const stream = createStreamingMessage('assistant');

            stream.updateContent('First');
            addToolMessage('start', 'web_search', { tool_id: 'tool-a', input: {} });
            stream.updateContent('Last');
            stream.finalize();

            const bubbles = stream.element.querySelectorAll('.message-bubble');
            expect(bubbles).toHaveLength(2);
            expect(stream.bubble).toBe(bubbles[1]);
        });

        it('should keep a bubble on a turn that produced only cards', () => {
            const stream = createStreamingMessage('assistant');

            addToolMessage('start', 'web_search', { tool_id: 'tool-a', input: {} });
            stream.finalize();

            expect(stream.bubble).toBeTruthy();
            expect(layoutOf(stream.element)).toEqual(['tool:tool-a', 't:']);
        });

        it('should send cards back to the message list once the turn ends', () => {
            const stream = createStreamingMessage('assistant');
            stream.updateContent('Done.');
            stream.finalize();

            addToolMessage('start', 'web_search', { tool_id: 'tool-later', input: {} });

            expect(stream.element.querySelector('.tool-message')).toBeFalsy();
            expect(mockElements.messages.querySelector('.tool-message').parentElement)
                .toBe(mockElements.messages);
        });

        it('should render cards into the message list when nothing is streaming', () => {
            addToolMessage('start', 'web_search', { tool_id: 'tool-a', input: {} });
            addThinkingMessage('start');
            addThinkingMessage('stop');

            expect(mockElements.messages.querySelector('.tool-message').parentElement)
                .toBe(mockElements.messages);
            expect(mockElements.messages.querySelector('.thinking-message').parentElement)
                .toBe(mockElements.messages);
        });

        it('should not render a second card when a tool is announced twice', () => {
            const stream = createStreamingMessage('assistant');

            // The backend announces the block opening (no input yet), then the
            // same call again once its arguments have been parsed.
            const first = addToolMessage('start', 'web_search', { tool_id: 'tool-a', input: {} });
            const second = addToolMessage('start', 'web_search', {
                tool_id: 'tool-a',
                input: { query: 'kingfishers' },
            });

            expect(second).toBe(first);
            expect(stream.element.querySelectorAll('.tool-message')).toHaveLength(1);
            expect(first.querySelector('.tool-input').textContent).toContain('kingfishers');

            addToolMessage('result', 'web_search', { tool_id: 'tool-a', content: 'ok' });
            expect(first.querySelector('.tool-status.success')).toBeTruthy();
        });

        it('should not adopt cards into a turn whose element was removed', () => {
            const stream = createStreamingMessage('assistant');
            stream.updateContent('partial');
            stream.element.remove();

            addToolMessage('start', 'web_search', { tool_id: 'tool-a', input: {} });

            expect(mockElements.messages.querySelector('.tool-message').parentElement)
                .toBe(mockElements.messages);
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
