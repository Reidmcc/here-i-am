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
     * @param {Function} callbacks.onToolStart - Called when a tool starts executing
     * @param {Function} callbacks.onToolResult - Called with tool execution result
     * @param {Function} callbacks.onDone - Called when streaming completes
     * @param {Function} callbacks.onStored - Called when messages are stored
     * @param {Function} callbacks.onError - Called on error
     * @param {Function} callbacks.onAborted - Called when request is aborted
     * @param {AbortSignal} signal - Optional AbortSignal for cancellation
     * @returns {Promise<void>}
     */
    async sendMessageStream(data, callbacks = {}, signal = null) {
        const url = `${API_BASE}/chat/stream`;

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
        } catch (e) {
            if (e.name === 'AbortError') {
                if (callbacks.onAborted) callbacks.onAborted({ status: 'aborted' });
                return;
            }
            throw e;
        }
    }

    _handleStreamEvent(eventType, data, callbacks) {
        console.log('[API] SSE event received:', eventType, data);
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
                console.log('[API] Stored event - calling onStored callback');
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

    async getMemoryStats(entityId = null) {
        let url = '/memories/stats';
        if (entityId) {
            url += `?entity_id=${entityId}`;
        }
        return this.request(url);
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

    async listOrphanedRecords(entityId = null) {
        let url = '/memories/orphans';
        if (entityId) {
            url += `?entity_id=${entityId}`;
        }
        return this.request(url);
    }

    async cleanupOrphanedRecords(entityId = null, dryRun = true) {
        return this.request('/memories/orphans/cleanup', {
            method: 'POST',
            body: {
                entity_id: entityId,
                dry_run: dryRun,
            },
        });
    }

    // Presets
    async getPresets() {
        return this.request('/config/presets');
    }

    // Messages
    async updateMessage(messageId, content) {
        return this.request(`/messages/${messageId}`, {
            method: 'PUT',
            body: { content },
        });
    }

    async deleteMessage(messageId) {
        return this.request(`/messages/${messageId}`, {
            method: 'DELETE',
        });
    }

    // TTS
    async getTTSStatus() {
        return this.request('/tts/status');
    }

    async textToSpeech(text, voiceId = null, styletts2Params = null) {
        const url = `${API_BASE}/tts/speak`;
        const body = { text };
        if (voiceId) {
            body.voice_id = voiceId;
        }
        // Add StyleTTS 2 parameters if provided
        if (styletts2Params) {
            if (styletts2Params.alpha !== undefined) body.alpha = styletts2Params.alpha;
            if (styletts2Params.beta !== undefined) body.beta = styletts2Params.beta;
            if (styletts2Params.diffusion_steps !== undefined) body.diffusion_steps = styletts2Params.diffusion_steps;
            if (styletts2Params.embedding_scale !== undefined) body.embedding_scale = styletts2Params.embedding_scale;
            if (styletts2Params.speed !== undefined) body.speed = styletts2Params.speed;
        }

        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(body),
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(error.detail || `HTTP ${response.status}`);
        }

        return response.blob();
    }

    async listTTSVoices() {
        return this.request('/tts/voices');
    }

    async cloneVoice(audioFile, label, description = '', options = {}) {
        const url = `${API_BASE}/tts/voices/clone`;
        const formData = new FormData();
        formData.append('audio_file', audioFile);
        formData.append('label', label);
        formData.append('description', description);

        // Add voice synthesis parameters with defaults
        formData.append('temperature', options.temperature ?? 0.75);
        formData.append('length_penalty', options.length_penalty ?? 1.0);
        formData.append('repetition_penalty', options.repetition_penalty ?? 5.0);
        formData.append('speed', options.speed ?? 1.0);

        const response = await fetch(url, {
            method: 'POST',
            body: formData,
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(error.detail || `HTTP ${response.status}`);
        }

        return response.json();
    }

    async updateVoice(voiceId, updates) {
        return this.request(`/tts/voices/${voiceId}`, {
            method: 'PUT',
            body: updates,
        });
    }

    async deleteTTSVoice(voiceId) {
        return this.request(`/tts/voices/${voiceId}`, {
            method: 'DELETE',
        });
    }

    async getXTTSHealth() {
        return this.request('/tts/xtts/health');
    }

    // STT (Speech-to-Text)
    async getSTTStatus() {
        return this.request('/stt/status');
    }

    /**
     * Transcribe audio to text using Whisper.
     * @param {Blob} audioBlob - Audio blob to transcribe
     * @param {string} language - Optional language code (auto-detect if not specified)
     * @returns {Promise<Object>} Transcription result with text, language, duration
     */
    async transcribeAudio(audioBlob, language = null) {
        const url = `${API_BASE}/stt/transcribe`;
        const formData = new FormData();
        
        // Determine filename based on MIME type
        const ext = audioBlob.type.includes('webm') ? 'webm' : 'wav';
        formData.append('audio_file', audioBlob, `recording.${ext}`);
        
        if (language) {
            formData.append('language', language);
        }

        const response = await fetch(url, {
            method: 'POST',
            body: formData,
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(error.detail || `HTTP ${response.status}`);
        }

        return response.json();
    }

    // GitHub Integration
    async listGitHubRepos() {
        return this.request('/github/repos');
    }

    async getGitHubRateLimits() {
        return this.request('/github/rate-limit');
    }

    async regenerateStream(data, callbacks = {}) {
        const url = `${API_BASE}/chat/regenerate`;

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

    // === Go Game API ===

    /**
     * Create a new Go game linked to a conversation.
     * @param {string} conversationId - ID of the conversation to link
     * @param {Object} options - Game options
     * @param {number} options.boardSize - Board size (9, 13, or 19)
     * @param {number} options.komi - Komi (compensation for white)
     * @param {string} options.entityColor - Color for AI ('black' or 'white')
     * @param {string} options.entityId - Entity ID (defaults to conversation entity)
     * @returns {Promise<Object>} Created game
     */
    async createGoGame(conversationId, options = {}) {
        return this.request('/go/games', {
            method: 'POST',
            body: {
                conversation_id: conversationId,
                board_size: options.boardSize || 19,
                komi: options.komi || 6.5,
                entity_color: options.entityColor || 'black',
                entity_id: options.entityId || null,
            },
        });
    }

    /**
     * Get a Go game by ID.
     * @param {string} gameId - Game ID
     * @returns {Promise<Object>} Game data including board state
     */
    async getGoGame(gameId) {
        return this.request(`/go/games/${gameId}`);
    }

    /**
     * List Go games with optional filters.
     * @param {string} conversationId - Filter by conversation
     * @param {string} status - Filter by status ('active', 'scoring', 'finished')
     * @returns {Promise<Array>} List of games
     */
    async listGoGames(conversationId = null, status = null) {
        let url = '/go/games';
        const params = [];
        if (conversationId) params.push(`conversation_id=${conversationId}`);
        if (status) params.push(`status=${status}`);
        if (params.length) url += '?' + params.join('&');
        return this.request(url);
    }

    /**
     * Get the active Go game for a conversation, if any.
     * @param {string} conversationId - Conversation ID
     * @returns {Promise<Object|null>} Active game or null
     */
    async getActiveGoGame(conversationId) {
        return this.request(`/go/conversation/${conversationId}/active`);
    }

    /**
     * Make a move in a Go game.
     * @param {string} gameId - Game ID
     * @param {string} coordinate - Move coordinate (e.g., 'D4', 'Q16')
     * @returns {Promise<Object>} Move result with updated game
     */
    async makeGoMove(gameId, coordinate) {
        return this.request(`/go/games/${gameId}/move`, {
            method: 'POST',
            body: { coordinate },
        });
    }

    /**
     * Pass turn in a Go game. Two passes end the game.
     * @param {string} gameId - Game ID
     * @returns {Promise<Object>} Result with updated game
     */
    async passGoTurn(gameId) {
        return this.request(`/go/games/${gameId}/pass`, {
            method: 'POST',
        });
    }

    /**
     * Resign from a Go game.
     * @param {string} gameId - Game ID
     * @returns {Promise<Object>} Result with winner
     */
    async resignGoGame(gameId) {
        return this.request(`/go/games/${gameId}/resign`, {
            method: 'POST',
        });
    }

    /**
     * Calculate and optionally finalize the score of a Go game.
     * @param {string} gameId - Game ID
     * @param {boolean} finalize - Whether to finalize the game with this score
     * @returns {Promise<Object>} Score breakdown and winner
     */
    async scoreGoGame(gameId, finalize = true) {
        return this.request(`/go/games/${gameId}/score?finalize=${finalize}`, {
            method: 'POST',
        });
    }

    /**
     * Delete a Go game.
     * @param {string} gameId - Game ID
     * @returns {Promise<Object>} Success message
     */
    async deleteGoGame(gameId) {
        return this.request(`/go/games/${gameId}`, {
            method: 'DELETE',
        });
    }
}

// Export singleton instance as global (needed for ES6 modules)
const api = new ApiClient();
window.api = api;
