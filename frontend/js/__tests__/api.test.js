/**
 * Tests for api.js - API client singleton
 *
 * Tests the ACTUAL ApiClient implementation by importing the real module.
 * Only the fetch boundary is mocked - everything else tests real code.
 *
 * Tests cover:
 * - request(): Base request method, error handling, body serialization
 * - _formatErrorDetail(): Error detail formatting (arrays, strings)
 * - Endpoint methods: Correct URL, method, and body construction
 * - _handleStreamEvent(): SSE event dispatching
 * - _handleImportStreamEvent(): Import SSE event dispatching
 * - sendMessageStream(): Full SSE streaming pipeline
 * - regenerateStream(): Regeneration SSE streaming
 * - importExternalConversationsStream(): Import SSE streaming
 * - textToSpeech(): Direct fetch with blob response
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// Import the ACTUAL api.js module - this sets window.api to a real ApiClient instance
import '../api.js';

describe('ApiClient', () => {
    let api;

    beforeEach(() => {
        // Use the real ApiClient instance set by the module
        api = window.api;

        // Mock fetch at the boundary - this is the only mock needed
        global.fetch = vi.fn();

        // Suppress console output from the real implementation
        vi.spyOn(console, 'log').mockImplementation(() => {});
        vi.spyOn(console, 'warn').mockImplementation(() => {});
        vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    // ============================================================
    // Tests for _formatErrorDetail
    // ============================================================

    describe('_formatErrorDetail', () => {
        it('should format string detail', () => {
            const result = api._formatErrorDetail('Something went wrong', 500);
            expect(result).toBe('Something went wrong');
        });

        it('should format array of error objects with msg', () => {
            const detail = [
                { msg: 'Field required', loc: ['body', 'name'] },
                { msg: 'Invalid email', loc: ['body', 'email'] },
            ];
            const result = api._formatErrorDetail(detail, 422);
            expect(result).toBe('Field required; Invalid email');
        });

        it('should format array of error objects without msg', () => {
            const detail = [{ error: 'something' }];
            const result = api._formatErrorDetail(detail, 422);
            expect(result).toContain('something');
        });

        it('should use HTTP status when detail is null', () => {
            const result = api._formatErrorDetail(null, 404);
            expect(result).toBe('HTTP 404');
        });

        it('should use HTTP status when detail is undefined', () => {
            const result = api._formatErrorDetail(undefined, 500);
            expect(result).toBe('HTTP 500');
        });

        it('should use HTTP status when detail is empty string', () => {
            const result = api._formatErrorDetail('', 403);
            expect(result).toBe('HTTP 403');
        });
    });

    // ============================================================
    // Tests for request method
    // ============================================================

    describe('request', () => {
        it('should make GET request with correct URL', async () => {
            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                json: () => Promise.resolve({ data: 'test' }),
            });

            const result = await api.request('/entities/');
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/entities/',
                expect.objectContaining({
                    headers: expect.objectContaining({
                        'Content-Type': 'application/json',
                    }),
                }),
            );
            expect(result).toEqual({ data: 'test' });
        });

        it('should serialize object body to JSON string', async () => {
            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                json: () => Promise.resolve({ id: '1' }),
            });

            await api.request('/test', { method: 'POST', body: { key: 'value' } });

            expect(global.fetch).toHaveBeenCalledWith(
                '/api/test',
                expect.objectContaining({
                    body: '{"key":"value"}',
                }),
            );
        });

        it('should not serialize non-object body', async () => {
            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                json: () => Promise.resolve({}),
            });

            await api.request('/test', { method: 'POST', body: 'plain text' });

            expect(global.fetch).toHaveBeenCalledWith(
                '/api/test',
                expect.objectContaining({
                    body: 'plain text',
                }),
            );
        });

        it('should throw on non-ok response', async () => {
            global.fetch = vi.fn().mockResolvedValue({
                ok: false,
                status: 404,
                json: () => Promise.resolve({ detail: 'Not found' }),
            });

            await expect(api.request('/missing')).rejects.toThrow('Not found');
        });

        it('should handle JSON parse error on non-ok response', async () => {
            global.fetch = vi.fn().mockResolvedValue({
                ok: false,
                status: 500,
                json: () => Promise.reject(new Error('Invalid JSON')),
            });

            await expect(api.request('/broken')).rejects.toThrow('Unknown error');
        });

        it('should merge custom headers', async () => {
            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                json: () => Promise.resolve({}),
            });

            await api.request('/test', {
                headers: { 'X-Custom': 'value' },
            });

            expect(global.fetch).toHaveBeenCalledWith(
                '/api/test',
                expect.objectContaining({
                    headers: expect.objectContaining({
                        'Content-Type': 'application/json',
                        'X-Custom': 'value',
                    }),
                }),
            );
        });
    });

    // ============================================================
    // Tests for _handleStreamEvent
    // ============================================================

    describe('_handleStreamEvent', () => {
        it('should dispatch memories event', () => {
            const callbacks = { onMemories: vi.fn() };
            api._handleStreamEvent('memories', { count: 5 }, callbacks);
            expect(callbacks.onMemories).toHaveBeenCalledWith({ count: 5 });
        });

        it('should dispatch start event', () => {
            const callbacks = { onStart: vi.fn() };
            api._handleStreamEvent('start', { model: 'claude' }, callbacks);
            expect(callbacks.onStart).toHaveBeenCalledWith({ model: 'claude' });
        });

        it('should dispatch token event', () => {
            const callbacks = { onToken: vi.fn() };
            api._handleStreamEvent('token', { token: 'Hello' }, callbacks);
            expect(callbacks.onToken).toHaveBeenCalledWith({ token: 'Hello' });
        });

        it('should dispatch tool_start event', () => {
            const callbacks = { onToolStart: vi.fn() };
            api._handleStreamEvent('tool_start', { name: 'search' }, callbacks);
            expect(callbacks.onToolStart).toHaveBeenCalledWith({ name: 'search' });
        });

        it('should dispatch tool_result event', () => {
            const callbacks = { onToolResult: vi.fn() };
            api._handleStreamEvent('tool_result', { result: 'data' }, callbacks);
            expect(callbacks.onToolResult).toHaveBeenCalledWith({ result: 'data' });
        });

        it('should dispatch done event', () => {
            const callbacks = { onDone: vi.fn() };
            api._handleStreamEvent('done', { total: 100 }, callbacks);
            expect(callbacks.onDone).toHaveBeenCalledWith({ total: 100 });
        });

        it('should dispatch stored event', () => {
            const callbacks = { onStored: vi.fn() };
            api._handleStreamEvent('stored', { ids: ['1'] }, callbacks);
            expect(callbacks.onStored).toHaveBeenCalledWith({ ids: ['1'] });
        });

        it('should dispatch error event', () => {
            const callbacks = { onError: vi.fn() };
            api._handleStreamEvent('error', { message: 'fail' }, callbacks);
            expect(callbacks.onError).toHaveBeenCalledWith({ message: 'fail' });
        });

        it('should not throw for missing callbacks', () => {
            expect(() => {
                api._handleStreamEvent('token', { token: 'x' }, {});
            }).not.toThrow();
        });

        it('should not throw for unknown event type', () => {
            const callbacks = {};
            expect(() => {
                api._handleStreamEvent('unknown_event', {}, callbacks);
            }).not.toThrow();
        });

        it('should warn on unknown event type', () => {
            api._handleStreamEvent('unknown_event', {}, {});
            expect(console.warn).toHaveBeenCalledWith('Unknown SSE event type:', 'unknown_event');
        });
    });

    // ============================================================
    // Tests for _handleImportStreamEvent
    // ============================================================

    describe('_handleImportStreamEvent', () => {
        it('should dispatch start event', () => {
            const callbacks = { onStart: vi.fn() };
            api._handleImportStreamEvent('start', { total: 10 }, callbacks);
            expect(callbacks.onStart).toHaveBeenCalledWith({ total: 10 });
        });

        it('should dispatch progress event', () => {
            const callbacks = { onProgress: vi.fn() };
            api._handleImportStreamEvent('progress', { current: 3 }, callbacks);
            expect(callbacks.onProgress).toHaveBeenCalledWith({ current: 3 });
        });

        it('should dispatch done event', () => {
            const callbacks = { onDone: vi.fn() };
            api._handleImportStreamEvent('done', { imported: 10 }, callbacks);
            expect(callbacks.onDone).toHaveBeenCalledWith({ imported: 10 });
        });

        it('should dispatch cancelled event', () => {
            const callbacks = { onCancelled: vi.fn() };
            api._handleImportStreamEvent('cancelled', { status: 'cancelled' }, callbacks);
            expect(callbacks.onCancelled).toHaveBeenCalledWith({ status: 'cancelled' });
        });

        it('should dispatch error event', () => {
            const callbacks = { onError: vi.fn() };
            api._handleImportStreamEvent('error', { message: 'fail' }, callbacks);
            expect(callbacks.onError).toHaveBeenCalledWith({ message: 'fail' });
        });

        it('should not throw for missing callbacks', () => {
            expect(() => {
                api._handleImportStreamEvent('progress', {}, {});
            }).not.toThrow();
        });

        it('should warn on unknown import event type', () => {
            api._handleImportStreamEvent('unknown_event', {}, {});
            expect(console.warn).toHaveBeenCalledWith('Unknown import SSE event type:', 'unknown_event');
        });
    });

    // ============================================================
    // Tests for endpoint methods
    // ============================================================

    describe('endpoint methods', () => {
        beforeEach(() => {
            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                json: () => Promise.resolve({}),
            });
        });

        it('listEntities should call /entities/', async () => {
            await api.listEntities();
            expect(global.fetch).toHaveBeenCalledWith('/api/entities/', expect.any(Object));
        });

        it('getEntity should call /entities/{id}', async () => {
            await api.getEntity('claude-test');
            expect(global.fetch).toHaveBeenCalledWith('/api/entities/claude-test', expect.any(Object));
        });

        it('getEntityStatus should call /entities/{id}/status', async () => {
            await api.getEntityStatus('claude-test');
            expect(global.fetch).toHaveBeenCalledWith('/api/entities/claude-test/status', expect.any(Object));
        });

        it('listConversations should include query params', async () => {
            await api.listConversations(10, 5, 'entity-1');
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/conversations/?limit=10&offset=5&entity_id=entity-1',
                expect.any(Object),
            );
        });

        it('listConversations should omit entity_id when null', async () => {
            await api.listConversations(50, 0, null);
            const url = global.fetch.mock.calls[0][0];
            expect(url).not.toContain('entity_id');
        });

        it('createConversation should POST with body', async () => {
            await api.createConversation({ title: 'Test' });
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/conversations/',
                expect.objectContaining({
                    method: 'POST',
                    body: '{"title":"Test"}',
                }),
            );
        });

        it('deleteConversation should DELETE', async () => {
            await api.deleteConversation('conv-1');
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/conversations/conv-1',
                expect.objectContaining({ method: 'DELETE' }),
            );
        });

        it('archiveConversation should POST', async () => {
            await api.archiveConversation('conv-1');
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/conversations/conv-1/archive',
                expect.objectContaining({ method: 'POST' }),
            );
        });

        it('searchMemories should POST with correct body', async () => {
            await api.searchMemories('AI ethics', 5, true, 'entity-1');
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/memories/search',
                expect.objectContaining({
                    method: 'POST',
                    body: JSON.stringify({
                        query: 'AI ethics',
                        top_k: 5,
                        include_content: true,
                        entity_id: 'entity-1',
                    }),
                }),
            );
        });

        it('healthCheck should call /health', async () => {
            await api.healthCheck();
            expect(global.fetch).toHaveBeenCalledWith('/api/health', expect.any(Object));
        });

        it('getConversation should call /conversations/{id}', async () => {
            await api.getConversation('conv-123');
            expect(global.fetch).toHaveBeenCalledWith('/api/conversations/conv-123', expect.any(Object));
        });

        it('getConversationMessages should call /conversations/{id}/messages', async () => {
            await api.getConversationMessages('conv-123');
            expect(global.fetch).toHaveBeenCalledWith('/api/conversations/conv-123/messages', expect.any(Object));
        });

        it('updateConversation should PATCH with body', async () => {
            await api.updateConversation('conv-123', { title: 'New Title' });
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/conversations/conv-123',
                expect.objectContaining({
                    method: 'PATCH',
                    body: '{"title":"New Title"}',
                }),
            );
        });

        it('unarchiveConversation should POST', async () => {
            await api.unarchiveConversation('conv-1');
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/conversations/conv-1/unarchive',
                expect.objectContaining({ method: 'POST' }),
            );
        });

        it('listArchivedConversations should include query params', async () => {
            await api.listArchivedConversations(10, 5, 'entity-1');
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/conversations/archived?limit=10&offset=5&entity_id=entity-1',
                expect.any(Object),
            );
        });

        it('exportConversation should call /conversations/{id}/export', async () => {
            await api.exportConversation('conv-123');
            expect(global.fetch).toHaveBeenCalledWith('/api/conversations/conv-123/export', expect.any(Object));
        });

        it('sendMessage should POST to /chat/send', async () => {
            await api.sendMessage({ message: 'hello' });
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/chat/send',
                expect.objectContaining({
                    method: 'POST',
                    body: '{"message":"hello"}',
                }),
            );
        });

        it('quickChat should POST to /chat/quick', async () => {
            await api.quickChat({ message: 'hi' });
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/chat/quick',
                expect.objectContaining({
                    method: 'POST',
                    body: '{"message":"hi"}',
                }),
            );
        });

        it('getSessionInfo should call /chat/session/{id}', async () => {
            await api.getSessionInfo('conv-123');
            expect(global.fetch).toHaveBeenCalledWith('/api/chat/session/conv-123', expect.any(Object));
        });

        it('closeSession should DELETE /chat/session/{id}', async () => {
            await api.closeSession('conv-123');
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/chat/session/conv-123',
                expect.objectContaining({ method: 'DELETE' }),
            );
        });

        it('getChatConfig should call /chat/config', async () => {
            await api.getChatConfig();
            expect(global.fetch).toHaveBeenCalledWith('/api/chat/config', expect.any(Object));
        });

        it('getMemoryStats should call /memories/stats with entity_id', async () => {
            await api.getMemoryStats('entity-1');
            expect(global.fetch).toHaveBeenCalledWith('/api/memories/stats?entity_id=entity-1', expect.any(Object));
        });

        it('getMemoryStats should call /memories/stats without entity_id when null', async () => {
            await api.getMemoryStats(null);
            expect(global.fetch).toHaveBeenCalledWith('/api/memories/stats', expect.any(Object));
        });

        it('deleteMemory should DELETE /memories/{id}', async () => {
            await api.deleteMemory('mem-123');
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/memories/mem-123',
                expect.objectContaining({ method: 'DELETE' }),
            );
        });

        it('updateMessage should PUT /messages/{id}', async () => {
            await api.updateMessage('msg-123', 'new content');
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/messages/msg-123',
                expect.objectContaining({
                    method: 'PUT',
                    body: '{"content":"new content"}',
                }),
            );
        });

        it('deleteMessage should DELETE /messages/{id}', async () => {
            await api.deleteMessage('msg-123');
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/messages/msg-123',
                expect.objectContaining({ method: 'DELETE' }),
            );
        });

        it('getTTSStatus should call /tts/status', async () => {
            await api.getTTSStatus();
            expect(global.fetch).toHaveBeenCalledWith('/api/tts/status', expect.any(Object));
        });

        it('listTTSVoices should call /tts/voices', async () => {
            await api.listTTSVoices();
            expect(global.fetch).toHaveBeenCalledWith('/api/tts/voices', expect.any(Object));
        });

        it('getSTTStatus should call /stt/status', async () => {
            await api.getSTTStatus();
            expect(global.fetch).toHaveBeenCalledWith('/api/stt/status', expect.any(Object));
        });

        it('listGitHubRepos should call /github/repos', async () => {
            await api.listGitHubRepos();
            expect(global.fetch).toHaveBeenCalledWith('/api/github/repos', expect.any(Object));
        });

        it('getGitHubRateLimits should call /github/rate-limit', async () => {
            await api.getGitHubRateLimits();
            expect(global.fetch).toHaveBeenCalledWith('/api/github/rate-limit', expect.any(Object));
        });

        it('getPresets should call /config/presets', async () => {
            await api.getPresets();
            expect(global.fetch).toHaveBeenCalledWith('/api/config/presets', expect.any(Object));
        });

        it('getMemoryHealth should call /memories/status/health', async () => {
            await api.getMemoryHealth();
            expect(global.fetch).toHaveBeenCalledWith('/api/memories/status/health', expect.any(Object));
        });

        it('listOrphanedRecords should include entity_id when provided', async () => {
            await api.listOrphanedRecords('entity-1');
            expect(global.fetch).toHaveBeenCalledWith('/api/memories/orphans?entity_id=entity-1', expect.any(Object));
        });

        it('listOrphanedRecords should omit entity_id when null', async () => {
            await api.listOrphanedRecords(null);
            expect(global.fetch).toHaveBeenCalledWith('/api/memories/orphans', expect.any(Object));
        });

        it('cleanupOrphanedRecords should POST with correct body', async () => {
            await api.cleanupOrphanedRecords('entity-1', false);
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/memories/orphans/cleanup',
                expect.objectContaining({
                    method: 'POST',
                    body: JSON.stringify({ entity_id: 'entity-1', dry_run: false }),
                }),
            );
        });
    });

    // ============================================================
    // Tests for textToSpeech (uses fetch directly, not request())
    // ============================================================

    describe('textToSpeech', () => {
        it('should POST to /api/tts/speak with text', async () => {
            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                blob: () => Promise.resolve(new Blob(['audio'])),
            });

            await api.textToSpeech('Hello world');

            expect(global.fetch).toHaveBeenCalledWith(
                '/api/tts/speak',
                expect.objectContaining({
                    method: 'POST',
                    body: JSON.stringify({ text: 'Hello world' }),
                }),
            );
        });

        it('should include voice_id when provided', async () => {
            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                blob: () => Promise.resolve(new Blob()),
            });

            await api.textToSpeech('Hello', 'voice-1');

            const body = JSON.parse(global.fetch.mock.calls[0][1].body);
            expect(body.voice_id).toBe('voice-1');
        });

        it('should include StyleTTS2 params when provided', async () => {
            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                blob: () => Promise.resolve(new Blob()),
            });

            await api.textToSpeech('Hello', null, { alpha: 0.5, beta: 0.8 });

            const body = JSON.parse(global.fetch.mock.calls[0][1].body);
            expect(body.alpha).toBe(0.5);
            expect(body.beta).toBe(0.8);
        });

        it('should throw on non-ok response', async () => {
            global.fetch = vi.fn().mockResolvedValue({
                ok: false,
                status: 429,
                json: () => Promise.resolve({ detail: 'Rate limited' }),
            });

            await expect(api.textToSpeech('Hello')).rejects.toThrow('Rate limited');
        });
    });

    // ============================================================
    // Tests for streaming methods
    // ============================================================

    describe('sendMessageStream', () => {
        it('should POST to /api/chat/stream and process SSE events', async () => {
            const encoder = new TextEncoder();
            const sseData = 'event: token\ndata: {"content":"Hello"}\n\nevent: done\ndata: {"usage":{}}\n\n';

            const stream = new ReadableStream({
                start(controller) {
                    controller.enqueue(encoder.encode(sseData));
                    controller.close();
                },
            });

            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                body: stream,
            });

            const callbacks = {
                onToken: vi.fn(),
                onDone: vi.fn(),
            };

            await api.sendMessageStream({ message: 'test' }, callbacks);

            expect(callbacks.onToken).toHaveBeenCalledWith({ content: 'Hello' });
            expect(callbacks.onDone).toHaveBeenCalledWith({ usage: {} });
        });

        it('should throw on non-ok response', async () => {
            global.fetch = vi.fn().mockResolvedValue({
                ok: false,
                status: 500,
                json: () => Promise.resolve({ detail: 'Server error' }),
            });

            await expect(api.sendMessageStream({ message: 'test' })).rejects.toThrow('Server error');
        });

        it('should call onAborted on AbortError', async () => {
            const stream = new ReadableStream({
                pull() {
                    const err = new Error('Aborted');
                    err.name = 'AbortError';
                    throw err;
                },
            });

            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                body: stream,
            });

            const callbacks = { onAborted: vi.fn() };
            await api.sendMessageStream({ message: 'test' }, callbacks);

            expect(callbacks.onAborted).toHaveBeenCalledWith({ status: 'aborted' });
        });
    });

    describe('regenerateStream', () => {
        it('should POST to /api/chat/regenerate and process SSE events', async () => {
            const encoder = new TextEncoder();
            const sseData = 'event: start\ndata: {"model":"claude"}\n\nevent: token\ndata: {"content":"Hi"}\n\nevent: done\ndata: {"usage":{}}\n\n';

            const stream = new ReadableStream({
                start(controller) {
                    controller.enqueue(encoder.encode(sseData));
                    controller.close();
                },
            });

            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                body: stream,
            });

            const callbacks = {
                onStart: vi.fn(),
                onToken: vi.fn(),
                onDone: vi.fn(),
            };

            await api.regenerateStream({ message_id: 'msg-1' }, callbacks);

            expect(global.fetch).toHaveBeenCalledWith(
                '/api/chat/regenerate',
                expect.objectContaining({ method: 'POST' }),
            );
            expect(callbacks.onStart).toHaveBeenCalledWith({ model: 'claude' });
            expect(callbacks.onToken).toHaveBeenCalledWith({ content: 'Hi' });
            expect(callbacks.onDone).toHaveBeenCalledWith({ usage: {} });
        });
    });

    describe('importExternalConversationsStream', () => {
        it('should POST and process import SSE events', async () => {
            const encoder = new TextEncoder();
            const sseData = 'event: start\ndata: {"total":5}\n\nevent: progress\ndata: {"current":1}\n\nevent: done\ndata: {"imported":5}\n\n';

            const stream = new ReadableStream({
                start(controller) {
                    controller.enqueue(encoder.encode(sseData));
                    controller.close();
                },
            });

            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                body: stream,
            });

            const callbacks = {
                onStart: vi.fn(),
                onProgress: vi.fn(),
                onDone: vi.fn(),
            };

            await api.importExternalConversationsStream({ data: 'test' }, callbacks);

            expect(callbacks.onStart).toHaveBeenCalledWith({ total: 5 });
            expect(callbacks.onProgress).toHaveBeenCalledWith({ current: 1 });
            expect(callbacks.onDone).toHaveBeenCalledWith({ imported: 5 });
        });
    });
});
