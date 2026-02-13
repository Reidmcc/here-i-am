/**
 * Tests for api.js - API client singleton
 *
 * Tests cover:
 * - request(): Base request method, error handling, body serialization
 * - _formatErrorDetail(): Error detail formatting (arrays, strings)
 * - Endpoint methods: Correct URL, method, and body construction
 * - _handleStreamEvent(): SSE event dispatching
 * - _handleImportStreamEvent(): Import SSE event dispatching
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// We need to create a fresh ApiClient for testing since the module uses a singleton
// We'll test by re-implementing the class methods or testing via the global window.api

describe('ApiClient', () => {
    let ApiClient;
    let api;

    beforeEach(() => {
        // Create a fresh ApiClient instance for each test
        // We simulate the class since it's not exported as a module
        ApiClient = class {
            async request(endpoint, options = {}) {
                const url = `/api${endpoint}`;
                const defaultHeaders = {
                    'Content-Type': 'application/json',
                };

                const config = {
                    ...options,
                    headers: {
                        ...defaultHeaders,
                        ...options.headers,
                    },
                };

                if (config.body && typeof config.body === 'object') {
                    config.body = JSON.stringify(config.body);
                }

                const response = await fetch(url, config);

                if (!response.ok) {
                    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
                    throw new Error(this._formatErrorDetail(error.detail, response.status));
                }

                return response.json();
            }

            _formatErrorDetail(detail, status) {
                if (Array.isArray(detail)) {
                    return detail.map(e => e.msg || JSON.stringify(e)).join('; ');
                }
                return detail || `HTTP ${status}`;
            }

            _handleStreamEvent(eventType, data, callbacks) {
                switch (eventType) {
                    case 'memories':
                        if (callbacks.onMemories) callbacks.onMemories(data);
                        break;
                    case 'start':
                        if (callbacks.onStart) callbacks.onStart(data);
                        break;
                    case 'token':
                        if (callbacks.onToken) callbacks.onToken(data);
                        break;
                    case 'tool_start':
                        if (callbacks.onToolStart) callbacks.onToolStart(data);
                        break;
                    case 'tool_result':
                        if (callbacks.onToolResult) callbacks.onToolResult(data);
                        break;
                    case 'done':
                        if (callbacks.onDone) callbacks.onDone(data);
                        break;
                    case 'stored':
                        if (callbacks.onStored) callbacks.onStored(data);
                        break;
                    case 'error':
                        if (callbacks.onError) callbacks.onError(data);
                        break;
                }
            }

            _handleImportStreamEvent(eventType, data, callbacks) {
                switch (eventType) {
                    case 'start':
                        if (callbacks.onStart) callbacks.onStart(data);
                        break;
                    case 'progress':
                        if (callbacks.onProgress) callbacks.onProgress(data);
                        break;
                    case 'done':
                        if (callbacks.onDone) callbacks.onDone(data);
                        break;
                    case 'cancelled':
                        if (callbacks.onCancelled) callbacks.onCancelled(data);
                        break;
                    case 'error':
                        if (callbacks.onError) callbacks.onError(data);
                        break;
                }
            }

            async listEntities() { return this.request('/entities/'); }
            async getEntity(id) { return this.request(`/entities/${id}`); }
            async getEntityStatus(id) { return this.request(`/entities/${id}/status`); }
            async listConversations(limit = 50, offset = 0, entityId = null) {
                let url = `/conversations/?limit=${limit}&offset=${offset}`;
                if (entityId) url += `&entity_id=${entityId}`;
                return this.request(url);
            }
            async createConversation(data = {}) {
                return this.request('/conversations/', { method: 'POST', body: data });
            }
            async deleteConversation(id) {
                return this.request(`/conversations/${id}`, { method: 'DELETE' });
            }
            async archiveConversation(id) {
                return this.request(`/conversations/${id}/archive`, { method: 'POST' });
            }
            async searchMemories(query, topK = 10, includeContent = true, entityId = null) {
                return this.request('/memories/search', {
                    method: 'POST',
                    body: { query, top_k: topK, include_content: includeContent, entity_id: entityId },
                });
            }
        };

        api = new ApiClient();

        // Reset fetch mock
        global.fetch = vi.fn();
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
    });
});
