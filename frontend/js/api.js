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

    // Conversations
    async listConversations(limit = 50, offset = 0) {
        return this.request(`/conversations/?limit=${limit}&offset=${offset}`);
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

    // Chat
    async sendMessage(data) {
        return this.request('/chat/send', {
            method: 'POST',
            body: data,
        });
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

        const query = params.toString();
        return this.request(`/memories/${query ? '?' + query : ''}`);
    }

    async searchMemories(query, topK = 10) {
        return this.request('/memories/search', {
            method: 'POST',
            body: {
                query,
                top_k: topK,
                include_content: true,
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
