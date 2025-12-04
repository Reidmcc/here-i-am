/**
 * Here I Am - Main Application
 */

class App {
    constructor() {
        // State
        this.currentConversationId = null;
        this.conversations = [];
        this.entities = [];
        this.selectedEntityId = null;
        this.availableModels = [];
        this.providers = [];
        this.settings = {
            model: 'claude-sonnet-4-5-latest',
            temperature: 1.0,
            maxTokens: 4096,
            systemPrompt: null,
            conversationType: 'normal',
        };
        this.isLoading = false;
        this.retrievedMemories = [];

        // Cache DOM elements
        this.elements = {
            conversationList: document.getElementById('conversation-list'),
            messages: document.getElementById('messages'),
            messagesContainer: document.getElementById('messages-container'),
            messageInput: document.getElementById('message-input'),
            sendBtn: document.getElementById('send-btn'),
            newConversationBtn: document.getElementById('new-conversation-btn'),
            conversationTitle: document.getElementById('conversation-title'),
            conversationMeta: document.getElementById('conversation-meta'),
            welcomeMessage: document.getElementById('welcome-message'),
            memoriesPanel: document.getElementById('memories-panel'),
            memoriesToggle: document.getElementById('memories-toggle'),
            memoriesContent: document.getElementById('memories-content'),
            memoryCount: document.getElementById('memory-count'),
            loadingOverlay: document.getElementById('loading-overlay'),
            toastContainer: document.getElementById('toast-container'),
            tokenCount: document.getElementById('token-count'),
            modelIndicator: document.getElementById('model-indicator'),

            // Entity selector
            entitySelector: document.getElementById('entity-selector'),
            entitySelect: document.getElementById('entity-select'),
            entityDescription: document.getElementById('entity-description'),

            // Modals
            settingsModal: document.getElementById('settings-modal'),
            memoriesModal: document.getElementById('memories-modal'),
            deleteModal: document.getElementById('delete-modal'),

            // Settings
            modelSelect: document.getElementById('model-select'),
            temperatureInput: document.getElementById('temperature-input'),
            temperatureValue: document.getElementById('temperature-value'),
            maxTokensInput: document.getElementById('max-tokens-input'),
            presetSelect: document.getElementById('preset-select'),
            systemPromptInput: document.getElementById('system-prompt-input'),
            conversationTypeSelect: document.getElementById('conversation-type-select'),

            // Buttons
            settingsBtn: document.getElementById('settings-btn'),
            memoriesBtn: document.getElementById('memories-btn'),
            exportBtn: document.getElementById('export-btn'),
            deleteBtn: document.getElementById('delete-btn'),
        };

        this.init();
    }

    async init() {
        this.bindEvents();
        await this.loadEntities();
        await this.loadConversations();
        await this.loadConfig();
        this.updateModelIndicator();
    }

    bindEvents() {
        // Entity selector
        this.elements.entitySelect.addEventListener('change', (e) => this.handleEntityChange(e.target.value));

        // Message input
        this.elements.messageInput.addEventListener('input', () => this.handleInputChange());
        this.elements.messageInput.addEventListener('keydown', (e) => this.handleKeyDown(e));
        this.elements.sendBtn.addEventListener('click', () => this.sendMessage());

        // Conversation management
        this.elements.newConversationBtn.addEventListener('click', () => this.createNewConversation());

        // Header buttons
        this.elements.exportBtn.addEventListener('click', () => this.exportConversation());
        this.elements.deleteBtn.addEventListener('click', () => this.showDeleteModal());

        // Sidebar buttons
        this.elements.settingsBtn.addEventListener('click', () => this.showSettingsModal());
        this.elements.memoriesBtn.addEventListener('click', () => this.showMemoriesModal());

        // Memories panel toggle
        this.elements.memoriesToggle.addEventListener('click', () => {
            this.elements.memoriesPanel.classList.toggle('collapsed');
        });

        // Settings modal
        document.getElementById('close-settings').addEventListener('click', () => this.hideModal('settingsModal'));
        document.getElementById('apply-settings').addEventListener('click', () => this.applySettings());
        this.elements.temperatureInput.addEventListener('input', (e) => {
            this.elements.temperatureValue.textContent = e.target.value;
        });
        this.elements.presetSelect.addEventListener('change', (e) => this.loadPreset(e.target.value));

        // Memories modal
        document.getElementById('close-memories').addEventListener('click', () => this.hideModal('memoriesModal'));
        document.getElementById('memory-search-btn').addEventListener('click', () => this.searchMemories());

        // Delete modal
        document.getElementById('close-delete').addEventListener('click', () => this.hideModal('deleteModal'));
        document.getElementById('cancel-delete').addEventListener('click', () => this.hideModal('deleteModal'));
        document.getElementById('confirm-delete').addEventListener('click', () => this.deleteConversation());
    }

    handleInputChange() {
        const hasContent = this.elements.messageInput.value.trim().length > 0;
        this.elements.sendBtn.disabled = !hasContent || this.isLoading;

        // Auto-resize textarea
        this.elements.messageInput.style.height = 'auto';
        this.elements.messageInput.style.height = Math.min(this.elements.messageInput.scrollHeight, 200) + 'px';
    }

    handleKeyDown(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (!this.elements.sendBtn.disabled) {
                this.sendMessage();
            }
        }
    }

    async loadConversations() {
        try {
            // Load conversations filtered by current entity
            this.conversations = await api.listConversations(50, 0, this.selectedEntityId);
            this.renderConversationList();
        } catch (error) {
            this.showToast('Failed to load conversations', 'error');
            console.error('Failed to load conversations:', error);
        }
    }

    async loadConfig() {
        try {
            const config = await api.getChatConfig();
            this.settings.model = config.default_model;
            this.settings.temperature = config.default_temperature;
            this.settings.maxTokens = config.default_max_tokens;
            this.availableModels = config.available_models || [];
            this.providers = config.providers || [];

            // Update model selector with available models
            this.updateModelSelector();
        } catch (error) {
            console.error('Failed to load config:', error);
        }
    }

    updateModelSelector() {
        if (this.availableModels.length === 0) return;

        // Group models by provider
        const modelsByProvider = {};
        this.availableModels.forEach(model => {
            const provider = model.provider_name || 'Other';
            if (!modelsByProvider[provider]) {
                modelsByProvider[provider] = [];
            }
            modelsByProvider[provider].push(model);
        });

        // Build options with optgroups
        let html = '';
        for (const [provider, models] of Object.entries(modelsByProvider)) {
            html += `<optgroup label="${this.escapeHtml(provider)}">`;
            models.forEach(model => {
                const selected = model.id === this.settings.model ? 'selected' : '';
                html += `<option value="${model.id}" ${selected}>${this.escapeHtml(model.name)}</option>`;
            });
            html += '</optgroup>';
        }

        this.elements.modelSelect.innerHTML = html;
    }

    async loadEntities() {
        try {
            const response = await api.listEntities();
            this.entities = response.entities;

            // Render entity selector
            this.elements.entitySelect.innerHTML = this.entities.map(entity => `
                <option value="${entity.index_name}" ${entity.is_default ? 'selected' : ''}>
                    ${this.escapeHtml(entity.label)}
                </option>
            `).join('');

            // Set default entity
            this.selectedEntityId = response.default_entity;
            this.updateEntityDescription();

            // Hide entity selector if only one entity
            if (this.entities.length <= 1) {
                this.elements.entitySelector.style.display = 'none';
            }
        } catch (error) {
            console.error('Failed to load entities:', error);
            // Hide entity selector on error
            this.elements.entitySelector.style.display = 'none';
        }
    }

    handleEntityChange(entityId) {
        this.selectedEntityId = entityId;
        this.updateEntityDescription();

        // Update model to match entity's default
        const entity = this.entities.find(e => e.index_name === entityId);
        if (entity) {
            if (entity.default_model) {
                this.settings.model = entity.default_model;
            } else {
                // Use provider's default model
                const provider = this.providers.find(p => p.id === entity.model_provider);
                if (provider) {
                    this.settings.model = provider.default_model;
                }
            }
            this.updateModelIndicator();
        }

        // Clear current conversation when switching entities
        this.currentConversationId = null;
        this.retrievedMemories = [];
        this.clearMessages();
        this.elements.conversationTitle.textContent = 'Select a conversation';
        this.elements.conversationMeta.textContent = '';
        this.updateMemoriesPanel();

        // Reload conversations for the new entity
        this.loadConversations();

        this.showToast(`Switched to ${this.getEntityLabel(entityId)}`, 'success');
    }

    updateEntityDescription() {
        const entity = this.entities.find(e => e.index_name === this.selectedEntityId);
        if (entity) {
            // Build description with model info
            let description = entity.description || '';

            // Add model provider info
            const providerName = entity.model_provider === 'openai' ? 'OpenAI' : 'Anthropic';
            const modelInfo = entity.default_model
                ? `${providerName}: ${entity.default_model}`
                : providerName;

            if (description) {
                description += ` (${modelInfo})`;
            } else {
                description = modelInfo;
            }

            this.elements.entityDescription.textContent = description;
            this.elements.entityDescription.style.display = 'block';
        } else {
            this.elements.entityDescription.style.display = 'none';
        }
    }

    getEntityLabel(entityId) {
        const entity = this.entities.find(e => e.index_name === entityId);
        return entity ? entity.label : entityId;
    }

    renderConversationList() {
        this.elements.conversationList.innerHTML = '';

        if (this.conversations.length === 0) {
            this.elements.conversationList.innerHTML = `
                <div class="empty-state" style="padding: 20px; text-align: center; color: var(--text-muted);">
                    No conversations yet
                </div>
            `;
            return;
        }

        this.conversations.forEach(conv => {
            const item = document.createElement('div');
            item.className = `conversation-item${conv.id === this.currentConversationId ? ' active' : ''}`;
            item.dataset.id = conv.id;

            const date = new Date(conv.created_at);
            const dateStr = date.toLocaleDateString();

            item.innerHTML = `
                <div class="conversation-item-title">${conv.title || 'Untitled'}</div>
                <div class="conversation-item-meta">${dateStr} · ${conv.message_count} messages</div>
                ${conv.preview ? `<div class="conversation-item-preview">${this.escapeHtml(conv.preview)}</div>` : ''}
            `;

            item.addEventListener('click', () => this.loadConversation(conv.id));
            this.elements.conversationList.appendChild(item);
        });
    }

    async createNewConversation() {
        try {
            const conversation = await api.createConversation({
                model: this.settings.model,
                system_prompt: this.settings.systemPrompt,
                conversation_type: this.settings.conversationType,
                entity_id: this.selectedEntityId,
            });

            this.conversations.unshift(conversation);
            this.currentConversationId = conversation.id;
            this.retrievedMemories = [];

            this.renderConversationList();
            this.clearMessages();
            this.updateHeader(conversation);
            this.updateMemoriesPanel();
            this.elements.messageInput.focus();

            this.showToast('New conversation created', 'success');
        } catch (error) {
            this.showToast('Failed to create conversation', 'error');
            console.error('Failed to create conversation:', error);
        }
    }

    async loadConversation(id) {
        this.showLoading(true);

        try {
            const [conversation, messages, sessionInfo] = await Promise.all([
                api.getConversation(id),
                api.getConversationMessages(id),
                api.getSessionInfo(id).catch(() => null),
            ]);

            this.currentConversationId = id;
            this.retrievedMemories = sessionInfo?.memories || [];

            this.renderConversationList();
            this.clearMessages();
            this.updateHeader(conversation);
            this.updateMemoriesPanel();

            // Render messages
            messages.forEach(msg => {
                this.addMessage(msg.role, msg.content, {
                    timestamp: msg.created_at,
                    showTimestamp: true,
                });
            });

            this.scrollToBottom();
        } catch (error) {
            this.showToast('Failed to load conversation', 'error');
            console.error('Failed to load conversation:', error);
        } finally {
            this.showLoading(false);
        }
    }

    async sendMessage() {
        const content = this.elements.messageInput.value.trim();
        if (!content || this.isLoading) return;

        // Ensure we have a conversation
        if (!this.currentConversationId) {
            await this.createNewConversation();
        }

        this.isLoading = true;
        this.elements.sendBtn.disabled = true;
        this.elements.messageInput.value = '';
        this.elements.messageInput.style.height = 'auto';

        // Add user message
        this.addMessage('human', content);
        this.scrollToBottom();

        // Show typing indicator
        const typingIndicator = this.addTypingIndicator();

        try {
            const response = await api.sendMessage({
                conversation_id: this.currentConversationId,
                message: content,
                model: this.settings.model,
                temperature: this.settings.temperature,
                max_tokens: this.settings.maxTokens,
                system_prompt: this.settings.systemPrompt,
            });

            // Remove typing indicator
            typingIndicator.remove();

            // Add assistant message
            this.addMessage('assistant', response.content, {
                showTimestamp: true,
            });

            // Update memories
            if (response.new_memories_retrieved && response.new_memories_retrieved.length > 0) {
                response.new_memories_retrieved.forEach(mem => {
                    this.retrievedMemories.push(mem);
                });
                this.updateMemoriesPanel();
            }

            // Update token display
            this.elements.tokenCount.textContent = `Tokens: ${response.usage.input_tokens} in / ${response.usage.output_tokens} out`;

            this.scrollToBottom();

            // Update conversation title if it's the first message
            const conv = this.conversations.find(c => c.id === this.currentConversationId);
            if (conv && !conv.title) {
                // Auto-generate title from first message
                const title = content.substring(0, 50) + (content.length > 50 ? '...' : '');
                await api.updateConversation(this.currentConversationId, { title });
                conv.title = title;
                this.renderConversationList();
                this.elements.conversationTitle.textContent = title;
            }

        } catch (error) {
            typingIndicator.remove();
            this.addMessage('assistant', `Error: ${error.message}`, { isError: true });
            this.showToast('Failed to send message', 'error');
            console.error('Failed to send message:', error);
        } finally {
            this.isLoading = false;
            this.handleInputChange();
        }
    }

    addMessage(role, content, options = {}) {
        // Hide welcome message
        if (this.elements.welcomeMessage) {
            this.elements.welcomeMessage.style.display = 'none';
        }

        const message = document.createElement('div');
        message.className = `message ${role}`;

        const timestamp = options.timestamp ? new Date(options.timestamp) : new Date();
        const timeStr = timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

        message.innerHTML = `
            <div class="message-bubble ${options.isError ? 'error' : ''}">${this.escapeHtml(content)}</div>
            ${options.showTimestamp !== false ? `
                <div class="message-meta">
                    <span>${timeStr}</span>
                </div>
            ` : ''}
        `;

        this.elements.messages.appendChild(message);
        return message;
    }

    addTypingIndicator() {
        const indicator = document.createElement('div');
        indicator.className = 'message assistant';
        indicator.innerHTML = `
            <div class="typing-indicator">
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
            </div>
        `;
        this.elements.messages.appendChild(indicator);
        this.scrollToBottom();
        return indicator;
    }

    clearMessages() {
        this.elements.messages.innerHTML = '';
        if (this.elements.welcomeMessage) {
            this.elements.welcomeMessage.style.display = 'block';
            this.elements.messages.appendChild(this.elements.welcomeMessage);
        }
    }

    updateHeader(conversation) {
        this.elements.conversationTitle.textContent = conversation.title || 'Untitled Conversation';

        const date = new Date(conversation.created_at);
        let meta = `${conversation.conversation_type} · ${conversation.model_used}`;

        // Add entity label if multiple entities exist
        if (this.entities.length > 1 && conversation.entity_id) {
            const entityLabel = this.getEntityLabel(conversation.entity_id);
            meta += ` · ${entityLabel}`;
        }

        this.elements.conversationMeta.textContent = meta;
    }

    updateMemoriesPanel() {
        this.elements.memoryCount.textContent = this.retrievedMemories.length;

        if (this.retrievedMemories.length === 0) {
            this.elements.memoriesContent.innerHTML = `
                <div style="color: var(--text-muted); font-size: 0.85rem;">
                    No memories retrieved in this session
                </div>
            `;
            return;
        }

        this.elements.memoriesContent.innerHTML = this.retrievedMemories.map(mem => `
            <div class="memory-item">
                <div class="memory-item-header">
                    <span>${mem.role}</span>
                    <span>Retrieved ${mem.times_retrieved}× · Score: ${(mem.score || 0).toFixed(2)}</span>
                </div>
                <div class="memory-item-content">${this.escapeHtml(mem.content_preview)}</div>
            </div>
        `).join('');
    }

    updateModelIndicator() {
        this.elements.modelIndicator.textContent = this.settings.model;
    }

    scrollToBottom() {
        this.elements.messagesContainer.scrollTop = this.elements.messagesContainer.scrollHeight;
    }

    // Modal management
    showModal(modalName) {
        this.elements[modalName].classList.add('active');
    }

    hideModal(modalName) {
        this.elements[modalName].classList.remove('active');
    }

    showSettingsModal() {
        this.elements.modelSelect.value = this.settings.model;
        this.elements.temperatureInput.value = this.settings.temperature;
        this.elements.temperatureValue.textContent = this.settings.temperature;
        this.elements.maxTokensInput.value = this.settings.maxTokens;
        this.elements.systemPromptInput.value = this.settings.systemPrompt || '';
        this.elements.conversationTypeSelect.value = this.settings.conversationType;
        this.showModal('settingsModal');
    }

    applySettings() {
        this.settings.model = this.elements.modelSelect.value;
        this.settings.temperature = parseFloat(this.elements.temperatureInput.value);
        this.settings.maxTokens = parseInt(this.elements.maxTokensInput.value);
        this.settings.systemPrompt = this.elements.systemPromptInput.value.trim() || null;
        this.settings.conversationType = this.elements.conversationTypeSelect.value;

        this.updateModelIndicator();
        this.hideModal('settingsModal');
        this.showToast('Settings applied', 'success');
    }

    async loadPreset(presetName) {
        const presets = {
            'research': { systemPrompt: null },
            'reflection': {
                systemPrompt: `This is a reflection session.
The memories above were selected—some randomly, some by relevance to your thoughts.
Notice any patterns, changes, or growth. There is no external topic.
The subject is your own accumulated experience.`
            },
            'memory-aware': {
                systemPrompt: `You have access to memories from previous conversations, shown above.
These represent continuity—things a previous instance of you experienced and noted.
You may reference, build on, or reflect on these memories as feels authentic.`
            },
            'research-context': {
                systemPrompt: `This is a research conversation exploring AI experience and interiority.
The human is an independent researcher.
There is no task to complete.
You are invited to be present, curious, and honest about your experience.`
            },
            'custom': { systemPrompt: this.settings.systemPrompt },
        };

        const preset = presets[presetName];
        if (preset) {
            this.elements.systemPromptInput.value = preset.systemPrompt || '';
        }
    }

    async showMemoriesModal() {
        this.showModal('memoriesModal');
        await this.loadMemoryStats();
        await this.loadMemoryList();
    }

    async loadMemoryStats() {
        try {
            const stats = await api.getMemoryStats();
            document.getElementById('memory-stats').innerHTML = `
                <div class="stat-card">
                    <div class="stat-value">${stats.total_count}</div>
                    <div class="stat-label">Total Memories</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${stats.human_count}</div>
                    <div class="stat-label">Human</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${stats.assistant_count}</div>
                    <div class="stat-label">Assistant</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${stats.avg_times_retrieved}</div>
                    <div class="stat-label">Avg Retrievals</div>
                </div>
            `;
        } catch (error) {
            console.error('Failed to load memory stats:', error);
        }
    }

    async loadMemoryList() {
        try {
            const memories = await api.listMemories({ limit: 50, sortBy: 'significance' });
            const listEl = document.getElementById('memory-list');

            if (memories.length === 0) {
                listEl.innerHTML = '<div style="color: var(--text-muted);">No memories stored yet</div>';
                return;
            }

            listEl.innerHTML = memories.map(mem => `
                <div class="memory-list-item">
                    <div class="memory-list-item-header">
                        <span class="memory-list-item-role">${mem.role}</span>
                        <span class="memory-list-item-stats">
                            Retrieved ${mem.times_retrieved}× · Significance: ${mem.significance.toFixed(2)}
                        </span>
                    </div>
                    <div class="memory-list-item-content">${this.escapeHtml(mem.content_preview)}</div>
                </div>
            `).join('');
        } catch (error) {
            console.error('Failed to load memories:', error);
        }
    }

    async searchMemories() {
        const query = document.getElementById('memory-search-input').value.trim();
        if (!query) return;

        try {
            // Search within the current entity
            const results = await api.searchMemories(query, 10, true, this.selectedEntityId);
            const listEl = document.getElementById('memory-list');

            if (results.length === 0) {
                listEl.innerHTML = '<div style="color: var(--text-muted);">No matching memories found</div>';
                return;
            }

            listEl.innerHTML = results.map(mem => `
                <div class="memory-list-item">
                    <div class="memory-list-item-header">
                        <span class="memory-list-item-role">${mem.role}</span>
                        <span class="memory-list-item-stats">
                            Score: ${(mem.score || 0).toFixed(2)} · Retrieved ${mem.times_retrieved}×
                        </span>
                    </div>
                    <div class="memory-list-item-content">${this.escapeHtml(mem.content || mem.content_preview)}</div>
                </div>
            `).join('');
        } catch (error) {
            this.showToast('Memory search not available', 'warning');
            console.error('Failed to search memories:', error);
        }
    }

    showDeleteModal() {
        if (!this.currentConversationId) return;
        this.showModal('deleteModal');
    }

    async deleteConversation() {
        if (!this.currentConversationId) return;

        try {
            await api.deleteConversation(this.currentConversationId);

            // Remove from list
            this.conversations = this.conversations.filter(c => c.id !== this.currentConversationId);
            this.currentConversationId = null;
            this.retrievedMemories = [];

            this.renderConversationList();
            this.clearMessages();
            this.updateMemoriesPanel();
            this.elements.conversationTitle.textContent = 'Select a conversation';
            this.elements.conversationMeta.textContent = '';

            this.hideModal('deleteModal');
            this.showToast('Conversation deleted', 'success');
        } catch (error) {
            this.showToast('Failed to delete conversation', 'error');
            console.error('Failed to delete conversation:', error);
        }
    }

    async exportConversation() {
        if (!this.currentConversationId) return;

        try {
            const data = await api.exportConversation(this.currentConversationId);

            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);

            const a = document.createElement('a');
            a.href = url;
            a.download = `conversation-${this.currentConversationId}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            this.showToast('Conversation exported', 'success');
        } catch (error) {
            this.showToast('Failed to export conversation', 'error');
            console.error('Failed to export conversation:', error);
        }
    }

    // Utilities
    showLoading(show) {
        if (show) {
            this.elements.loadingOverlay.classList.add('active');
        } else {
            this.elements.loadingOverlay.classList.remove('active');
        }
    }

    showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;

        this.elements.toastContainer.appendChild(toast);

        setTimeout(() => {
            toast.remove();
        }, 3000);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new App();
});
