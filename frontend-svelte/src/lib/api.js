/**
 * API Client for Here I Am backend
 * Ported for Svelte frontend
 */

const API_BASE = '/api';

/** Default timeout for API requests (10 seconds) */
const DEFAULT_TIMEOUT_MS = 10000;

/**
 * Create a timeout promise that rejects after specified milliseconds
 */
function createTimeout(ms, controller) {
    return new Promise((_, reject) => {
        setTimeout(() => {
            controller.abort();
            reject(new Error(`Request timed out after ${ms}ms`));
        }, ms);
    });
}

/**
 * Base request helper with timeout protection
 * Timeout covers both the fetch AND the response body parsing
 */
async function request(endpoint, options = {}) {
    const url = `${API_BASE}${endpoint}`;
    const timeout = options.timeout ?? DEFAULT_TIMEOUT_MS;
    const controller = new AbortController();

    const defaultHeaders = {
        'Content-Type': 'application/json',
    };

    const config = {
        ...options,
        headers: {
            ...defaultHeaders,
            ...options.headers,
        },
        signal: controller.signal,
    };

    // Remove custom timeout option before passing to fetch
    delete config.timeout;

    if (config.body && typeof config.body === 'object' && !(config.body instanceof FormData)) {
        config.body = JSON.stringify(config.body);
    }

    // Set up timeout that aborts the request
    const timeoutId = setTimeout(() => {
        controller.abort();
    }, timeout);

    try {
        const response = await fetch(url, config);

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(error.detail || `HTTP ${response.status}`);
        }

        const data = await response.json();
        return data;
    } catch (error) {
        if (error.name === 'AbortError') {
            throw new Error(`Request timed out after ${timeout}ms`);
        }
        throw error;
    } finally {
        clearTimeout(timeoutId);
    }
}

/**
 * Handle SSE stream event
 */
function handleStreamEvent(eventType, data, callbacks) {
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
        default:
            console.warn('Unknown SSE event type:', eventType);
    }
}

/**
 * Handle import stream event
 */
function handleImportStreamEvent(eventType, data, callbacks) {
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

/**
 * Process SSE stream
 */
async function processSSEStream(response, callbacks, eventHandler, onAbort = null) {
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
                        eventHandler(eventType, parsedData, callbacks);
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
                    eventHandler(eventType, parsedData, callbacks);
                } catch (e) {
                    console.error('Failed to parse final SSE data:', e, eventData);
                }
            }
        }
    } catch (e) {
        if (e.name === 'AbortError') {
            if (onAbort) onAbort({ status: 'aborted' });
            return;
        }
        throw e;
    }
}

// ============== Health ==============

export async function healthCheck() {
    return request('/health');
}

// ============== Entities ==============

export async function listEntities() {
    return request('/entities/');
}

export async function getEntity(entityId) {
    return request(`/entities/${entityId}`);
}

export async function getEntityStatus(entityId) {
    return request(`/entities/${entityId}/status`);
}

// ============== Conversations ==============

export async function listConversations(limit = 50, offset = 0, entityId = null) {
    let url = `/conversations/?limit=${limit}&offset=${offset}`;
    if (entityId) {
        url += `&entity_id=${entityId}`;
    }
    return request(url);
}

export async function createConversation(data = {}) {
    return request('/conversations/', {
        method: 'POST',
        body: data,
    });
}

export async function getConversation(id) {
    return request(`/conversations/${id}`);
}

export async function getConversationMessages(id) {
    return request(`/conversations/${id}/messages`);
}

export async function updateConversation(id, data) {
    return request(`/conversations/${id}`, {
        method: 'PATCH',
        body: data,
    });
}

export async function archiveConversation(id) {
    return request(`/conversations/${id}/archive`, {
        method: 'POST',
    });
}

export async function unarchiveConversation(id) {
    return request(`/conversations/${id}/unarchive`, {
        method: 'POST',
    });
}

export async function listArchivedConversations(limit = 50, offset = 0, entityId = null) {
    let url = `/conversations/archived?limit=${limit}&offset=${offset}`;
    if (entityId) {
        url += `&entity_id=${entityId}`;
    }
    return request(url);
}

export async function deleteConversation(id) {
    return request(`/conversations/${id}`, {
        method: 'DELETE',
    });
}

export async function exportConversation(id) {
    return request(`/conversations/${id}/export`);
}

export async function importSeedConversation(data) {
    return request('/conversations/import-seed', {
        method: 'POST',
        body: data,
    });
}

export async function importExternalConversations(data) {
    return request('/conversations/import-external', {
        method: 'POST',
        body: data,
    });
}

/**
 * Import conversations with streaming progress updates.
 */
export async function importExternalConversationsStream(data, callbacks = {}, signal = null) {
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

    await processSSEStream(
        response,
        callbacks,
        handleImportStreamEvent,
        callbacks.onCancelled
    );
}

export async function previewExternalConversations(data) {
    return request('/conversations/import-external/preview', {
        method: 'POST',
        body: data,
    });
}

// ============== Chat ==============

export async function sendMessage(data) {
    return request('/chat/send', {
        method: 'POST',
        body: data,
    });
}

/**
 * Send a message with streaming response.
 */
export async function sendMessageStream(data, callbacks = {}, signal = null) {
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

    await processSSEStream(
        response,
        callbacks,
        handleStreamEvent,
        callbacks.onAborted
    );
}

export async function quickChat(data) {
    return request('/chat/quick', {
        method: 'POST',
        body: data,
    });
}

export async function getSessionInfo(conversationId) {
    return request(`/chat/session/${conversationId}`);
}

export async function closeSession(conversationId) {
    return request(`/chat/session/${conversationId}`, {
        method: 'DELETE',
    });
}

export async function getChatConfig() {
    return request('/chat/config');
}

/**
 * Regenerate an AI response with streaming.
 */
export async function regenerateStream(data, callbacks = {}) {
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

    await processSSEStream(response, callbacks, handleStreamEvent);
}

// ============== Memories ==============

export async function listMemories(options = {}) {
    const params = new URLSearchParams();
    if (options.limit) params.set('limit', options.limit);
    if (options.offset) params.set('offset', options.offset);
    if (options.role) params.set('role', options.role);
    if (options.sortBy) params.set('sort_by', options.sortBy);
    if (options.entityId) params.set('entity_id', options.entityId);

    const query = params.toString();
    return request(`/memories/${query ? '?' + query : ''}`);
}

export async function searchMemories(query, topK = 10, includeContent = true, entityId = null) {
    return request('/memories/search', {
        method: 'POST',
        body: {
            query,
            top_k: topK,
            include_content: includeContent,
            entity_id: entityId,
        },
    });
}

export async function getMemoryStats(entityId = null) {
    let url = '/memories/stats';
    if (entityId) {
        url += `?entity_id=${entityId}`;
    }
    return request(url);
}

export async function getMemory(id) {
    return request(`/memories/${id}`);
}

export async function deleteMemory(id) {
    return request(`/memories/${id}`, {
        method: 'DELETE',
    });
}

export async function getMemoryHealth() {
    return request('/memories/status/health');
}

export async function listOrphanedRecords(entityId = null) {
    let url = '/memories/orphans';
    if (entityId) {
        url += `?entity_id=${entityId}`;
    }
    return request(url);
}

export async function cleanupOrphanedRecords(entityId = null, dryRun = true) {
    return request('/memories/orphans/cleanup', {
        method: 'POST',
        body: {
            entity_id: entityId,
            dry_run: dryRun,
        },
    });
}

// ============== Presets ==============

export async function getPresets() {
    return request('/config/presets');
}

// ============== Messages ==============

export async function updateMessage(messageId, content) {
    return request(`/messages/${messageId}`, {
        method: 'PUT',
        body: { content },
    });
}

export async function deleteMessage(messageId) {
    return request(`/messages/${messageId}`, {
        method: 'DELETE',
    });
}

// ============== TTS ==============

export async function getTTSStatus() {
    return request('/tts/status');
}

export async function textToSpeech(text, voiceId = null, styletts2Params = null) {
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

export async function listTTSVoices() {
    return request('/tts/voices');
}

export async function cloneVoice(audioFile, label, description = '', options = {}) {
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

export async function updateVoice(voiceId, updates) {
    return request(`/tts/voices/${voiceId}`, {
        method: 'PUT',
        body: updates,
    });
}

export async function deleteTTSVoice(voiceId) {
    return request(`/tts/voices/${voiceId}`, {
        method: 'DELETE',
    });
}

export async function getXTTSHealth() {
    return request('/tts/xtts/health');
}

// ============== STT ==============

export async function getSTTStatus() {
    return request('/stt/status');
}

/**
 * Transcribe audio to text using Whisper.
 */
export async function transcribeAudio(audioBlob, language = null) {
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

// ============== GitHub ==============

export async function listGitHubRepos() {
    return request('/github/repos');
}

export async function getGitHubRateLimits() {
    return request('/github/rate-limit');
}

// Aliases for backward compatibility
export const getConversations = listConversations;
export const getArchivedConversations = listArchivedConversations;
export const getMemories = listMemories;
export const previewExternalImport = previewExternalConversations;
