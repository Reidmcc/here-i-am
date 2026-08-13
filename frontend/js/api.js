/**
 * API Client for Here I Am backend
 */
const API_BASE = '/api';

class ApiClient {
    /**
     * Read an SSE response body and dispatch every complete event.
     *
     * The parser state (event name, accumulated data lines) deliberately lives
     * outside the read loop: an SSE frame can straddle any number of network
     * chunks. The earlier per-chunk parser re-initialized that state on every
     * read(), so a frame split across a chunk boundary was silently dropped —
     * the half-parsed `event:` line was thrown away and the `data:` line that
     * arrived next had no event name to dispatch under. The `done` frame of a
     * tool-using turn is large enough to split reliably, which is why a
     * finished response could keep its streaming cursor and never render.
     *
     * @param {Response} response - Fetch response with an SSE body
     * @param {Function} dispatch - Called as (eventType, parsedData)
     * @param {Function|null} onAbort - Called instead of rethrowing on AbortError
     * @returns {Promise<void>}
     */
    async _readSSEStream(response, dispatch, onAbort = null) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let eventType = null;
        let dataLines = [];

        // Dispatch the frame accumulated so far (called on the blank line that
        // terminates it, and once more at end of stream in case the server
        // closed without a trailing blank line).
        const flushEvent = () => {
            if (dataLines.length === 0) {
                eventType = null;
                return;
            }
            const raw = dataLines.join('\n');
            const type = eventType || 'message';
            dataLines = [];
            eventType = null;
            try {
                dispatch(type, JSON.parse(raw));
            } catch (e) {
                console.error('Failed to parse SSE data:', e, raw);
            }
        };

        const handleLine = (rawLine) => {
            // Tolerate CRLF line endings.
            const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
            if (line === '') {
                flushEvent();
                return;
            }
            // Comment / keep-alive line.
            if (line.startsWith(':')) return;

            const colon = line.indexOf(':');
            const field = colon === -1 ? line : line.slice(0, colon);
            let value = colon === -1 ? '' : line.slice(colon + 1);
            if (value.startsWith(' ')) value = value.slice(1);

            if (field === 'event') {
                eventType = value;
            } else if (field === 'data') {
                dataLines.push(value);
            }
            // 'id' and 'retry' are not used by this API.
        };

        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });

                const lines = buffer.split('\n');
                buffer = lines.pop() || ''; // Keep the incomplete trailing line
                for (const line of lines) {
                    handleLine(line);
                }
            }

            // Flush any bytes held by the decoder, then the final partial line.
            buffer += decoder.decode();
            if (buffer) handleLine(buffer);
            flushEvent();
        } catch (e) {
            if (e.name === 'AbortError' && onAbort) {
                onAbort();
                return;
            }
            throw e;
        }
    }

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
            throw new Error(this._formatErrorDetail(error.detail, response.status));
        }

        return response.json();
    }

    /**
     * Format error detail from API response.
     * Handles Pydantic validation errors (array of objects) and plain strings.
     */
    _formatErrorDetail(detail, status) {
        if (Array.isArray(detail)) {
            return detail.map(e => e.msg || JSON.stringify(e)).join('; ');
        }
        return detail || `HTTP ${status}`;
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

    // Persist an entity's default system prompt (backend is the source of
    // truth). Pass null/empty to clear it.
    async updateEntitySystemPrompt(entityId, systemPrompt) {
        return this.request(`/entities/${entityId}/system-prompt`, {
            method: 'PUT',
            body: { system_prompt: systemPrompt },
        });
    }

    // Persist how hard this entity's model should think (low, medium, high,
    // xhigh, max). Pass null to clear it and follow the backend default.
    async updateEntityThinkingEffort(entityId, thinkingEffort) {
        return this.request(`/entities/${entityId}/thinking-effort`, {
            method: 'PUT',
            body: { thinking_effort: thinkingEffort },
        });
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
            throw new Error(this._formatErrorDetail(error.detail, response.status));
        }

        await this._readSSEStream(
            response,
            (eventType, data) => this._handleImportStreamEvent(eventType, data, callbacks),
            () => {
                if (callbacks.onCancelled) callbacks.onCancelled({ status: 'cancelled' });
            },
        );
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
            throw new Error(this._formatErrorDetail(error.detail, response.status));
        }

        await this._readSSEStream(
            response,
            (eventType, data) => this._handleStreamEvent(eventType, data, callbacks),
            () => {
                if (callbacks.onAborted) callbacks.onAborted({ status: 'aborted' });
            },
        );
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
            case 'thinking_start':
                if (callbacks.onThinkingStart) callbacks.onThinkingStart(data);
                break;
            case 'thinking':
                if (callbacks.onThinking) callbacks.onThinking(data);
                break;
            case 'thinking_stop':
                if (callbacks.onThinkingStop) callbacks.onThinkingStop(data);
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

    // Memories with an entity-set status (pinned or released)
    async listMemoryOverrides(entityId = null) {
        let url = '/memories/overrides';
        if (entityId) {
            url += `?entity_id=${entityId}`;
        }
        return this.request(url);
    }

    // Set or clear a memory's status: 'pinned', 'released', or null
    async setMemoryStatus(id, status) {
        return this.request(`/memories/${id}/status`, {
            method: 'PUT',
            body: { status },
        });
    }

    // Notes maintenance
    async reindexNotes() {
        return this.request('/notes/reindex', {
            method: 'POST',
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

    async rebuildVectors(entityId = null, dryRun = true, wipeFirst = false) {
        return this.request('/memories/rebuild-vectors', {
            method: 'POST',
            body: {
                entity_id: entityId,
                dry_run: dryRun,
                wipe_first: wipeFirst,
            },
        });
    }

    async restoreFromVectors(entityId = null, dryRun = true) {
        return this.request('/memories/restore-from-vectors', {
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

    /**
     * Regenerate an AI response with streaming.
     * @param {Object} data - Regenerate request data
     * @param {string} data.message_id - ID of message to regenerate from
     * @param {Object} callbacks - Event callbacks (same as sendMessageStream)
     * @returns {Promise<void>}
     */
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
            throw new Error(this._formatErrorDetail(error.detail, response.status));
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
            throw new Error(this._formatErrorDetail(error.detail, response.status));
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
            throw new Error(this._formatErrorDetail(error.detail, response.status));
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

    async regenerateStream(data, callbacks = {}, signal = null) {
        const url = `${API_BASE}/chat/regenerate`;

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
            throw new Error(this._formatErrorDetail(error.detail, response.status));
        }

        await this._readSSEStream(
            response,
            (eventType, data) => this._handleStreamEvent(eventType, data, callbacks),
            () => {
                if (callbacks.onAborted) callbacks.onAborted({ status: 'aborted' });
            },
        );
    }
}

// Export singleton instance as global (needed for ES6 modules)
const api = new ApiClient();
window.api = api;
