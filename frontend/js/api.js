/**
 * API Client for Here I Am backend
 */
const API_BASE = '/api';

class ApiClient {
    async request(endpoint, options = {}) {
        const url = `${API_BASE}${endpoint}`;
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
            throw new Error(error.detail || `HTTP ${response.status}`);
        }

        return response.json();
    }

    // Health check
    async healthCheck() {
        return this.request('/health');
    }

    // Entities
    async listEntities() {
        return this.request('/entities/');
    }

    async getEntity(entityId) {
        return this.request(`/entities/${entityId}`);
    }

    async getEntityStatus(entityId) {
        return this.request(`/entities/${entityId}/status`);
    }

    // Conversations
    async listConversations(limit = 50, offset = 0, entityId = null) {
        let url = `/conversations/?limit=${limit}&offset=${offset}`;
        if (entityId) {
            url += `&entity_id=${entityId}`;
        }
        return this.request(url);
    }

    async createConversation(data = {}) {
        return this.request('/conversations/', {
            method: 'POST',
            body: data,
        });
    }

    async getConversation(id) {
        return this.request(`/conversations/${id}`);
    }

    async getConversationMessages(id) {
        return this.request(`/conversations/${id}/messages`);
    }

    async updateConversation(id, data) {
        return this.request(`/conversations/${id}`, {
            method: 'PATCH',
            body: data,
        });
    }

    async archiveConversation(id) {
        return this.request(`/conversations/${id}/archive`, {
            method: 'POST',
        });
    }

    async unarchiveConversation(id) {
        return this.request(`/conversations/${id}/unarchive`, {
            method: 'POST',
        });
    }

    async listArchivedConversations(limit = 50, offset = 0, entityId = null) {
        let url = `/conversations/archived?limit=${limit}&offset=${offset}`;
        if (entityId) {
            url += `&entity_id=${entityId}`;
        }
        return this.request(url);
    }

    async deleteConversation(id) {
        return this.request(`/conversations/${id}`, {
            method: 'DELETE',
        });
    }

    async exportConversation(id) {
        return this.request(`/conversations/${id}/export`);
    }

    async importSeedConversation(data) {
        return this.request('/conversations/import-seed', {
            method: 'POST',
            body: data,
        });
    }

    async importExternalConversations(data) {
        return this.request('/conversations/import-external', {
            method: 'POST',
            body: data,
        });
    }

    /**
     * Import conversations with streaming progress updates.
     * @param {Object} data - Import request data
     * @param {Object} callbacks - Event callbacks
     * @param {Function} callbacks.onStart - Called when import starts with total counts
     * @param {Function} callbacks.onProgress - Called with progress updates
     * @param {Function} callbacks.onDone - Called when import completes
     * @param {Function} callbacks.onError - Called on error
     * @param {Function} callbacks.onCancelled - Called when import is cancelled
     * @param {AbortSignal} signal - Optional AbortSignal for cancellation
     * @returns {Promise<void>}
     */
    async importExternalConversationsStream(data, callbacks = {}, signal = null) {
        const url = `${API_BASE}/conversations/import-external/stream`;

        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(data),
            signal: signal,
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(error.detail || `HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });

                // Process complete SSE events in buffer
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                let eventType = null;
                let eventData = null;

                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        eventType = line.slice(7).trim();
                    } else if (line.startsWith('data: ')) {
                        eventData = line.slice(6);
                    } else if (line === '' && eventType && eventData) {
                        try {
                            const parsedData = JSON.parse(eventData);
                            this._handleImportStreamEvent(eventType, parsedData, callbacks);
                        } catch (e) {
                            console.error('Failed to parse SSE data:', e, eventData);
                        }
                        eventType = null;
                        eventData = null;
                    }
                }
            }
        } catch (e) {
            if (e.name === 'AbortError') {
                if (callbacks.onCancelled) callbacks.onCancelled({ status: 'cancelled' });
            } else {
                throw e;
            }
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
            default:
                console.warn('Unknown import SSE event type:', eventType);
        }
    }

    async previewExternalConversations(data) {
        return this.request('/conversations/import-external/preview', {
            method: 'POST',
            body: data,
        });
    }

    // Chat
    async sendMessage(data) {
        return this.request('/chat/send', {
            method: 'POST',
            body: data,
        });
    }

    /**
     * Send a message with streaming response.
     * @param {Object} data - Chat request data
     * @param {Object} callbacks - Event callbacks
     * @param {Function} callbacks.onMemories - Called with memory retrieval info
     * @param {Function} callbacks.onStart - Called when streaming starts
     * @param {Function} callbacks.onToken - Called for each token
     * @param {Function} callbacks.onDone - Called when streaming completes
     * @param {Function} callbacks.onStored - Called when messages are stored
     * @param {Function} callbacks.onError - Called on error
     * @returns {Promise<void>}
     */
    async sendMessageStream(data, callbacks = {}) {
        const url = `${API_BASE}/chat/stream`;

        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(data),
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(error.detail || `HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            // Process complete SSE events in buffer
            const lines = buffer.split('\n');
            buffer = lines.pop() || ''; // Keep incomplete line in buffer

            let eventType = null;
            let eventData = null;

            for (const line of lines) {
                if (line.startsWith('event: ')) {
                    eventType = line.slice(7).trim();
                } else if (line.startsWith('data: ')) {
                    eventData = line.slice(6);
                } else if (line === '' && eventType && eventData) {
                    // Empty line marks end of event
                    try {
                        const parsedData = JSON.parse(eventData);
                        this._handleStreamEvent(eventType, parsedData, callbacks);
                    } catch (e) {
                        console.error('Failed to parse SSE data:', e, eventData);
                    }
                    eventType = null;
                    eventData = null;
                }
            }
        }

        // Process any remaining data in buffer
        if (buffer.trim()) {
            const lines = buffer.split('\n');
            let eventType = null;
            let eventData = null;

            for (const line of lines) {
                if (line.startsWith('event: ')) {
                    eventType = line.slice(7).trim();
                } else if (line.startsWith('data: ')) {
                    eventData = line.slice(6);
                }
            }

            if (eventType && eventData) {
                try {
                    const parsedData = JSON.parse(eventData);
                    this._handleStreamEvent(eventType, parsedData, callbacks);
                } catch (e) {
                    console.error('Failed to parse final SSE data:', e, eventData);
                }
            }
        }
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
            case 'done':
                if (callbacks.onDone) callbacks.onDone(data);
                break;
            case 'stored':
                if (callbacks.onStored) callbacks.onStored(data);
                break;
            case 'error':
                if (callbacks.onError) callbacks.onError(data);
                break;
            default:
                console.warn('Unknown SSE event type:', eventType);
        }
    }

    async quickChat(data) {
        return this.request('/chat/quick', {
            method: 'POST',
            body: data,
        });
    }

    async getSessionInfo(conversationId) {
        return this.request(`/chat/session/${conversationId}`);
    }

    async closeSession(conversationId) {
        return this.request(`/chat/session/${conversationId}`, {
            method: 'DELETE',
        });
    }

    async getChatConfig() {
        return this.request('/chat/config');
    }

    // Memories
    async listMemories(options = {}) {
        const params = new URLSearchParams();
        if (options.limit) params.set('limit', options.limit);
        if (options.offset) params.set('offset', options.offset);
        if (options.role) params.set('role', options.role);
        if (options.sortBy) params.set('sort_by', options.sortBy);
        if (options.entityId) params.set('entity_id', options.entityId);

        const query = params.toString();
        return this.request(`/memories/${query ? '?' + query : ''}`);
    }

    async searchMemories(query, topK = 10, includeContent = true, entityId = null) {
        return this.request('/memories/search', {
            method: 'POST',
            body: {
                query,
                top_k: topK,
                include_content: includeContent,
                entity_id: entityId,
            },
        });
    }

    async getMemoryStats() {
        return this.request('/memories/stats');
    }

    async getMemory(id) {
        return this.request(`/memories/${id}`);
    }

    async deleteMemory(id) {
        return this.request(`/memories/${id}`, {
            method: 'DELETE',
        });
    }

    async getMemoryHealth() {
        return this.request('/memories/status/health');
    }

    // Presets
    async getPresets() {
        return this.request('/config/presets');
    }
}

// Export singleton instance
const api = new ApiClient();
