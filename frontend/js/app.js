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
            model: 'claude-sonnet-4-5-20250929',
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
            archiveModal: document.getElementById('archive-modal'),
            archivedModal: document.getElementById('archived-modal'),
            archivedList: document.getElementById('archived-list'),

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
            archivedBtn: document.getElementById('archived-btn'),
            exportBtn: document.getElementById('export-btn'),
            archiveBtn: document.getElementById('archive-btn'),

            // Theme
            themeSelect: document.getElementById('theme-select'),

            // Import
            importSource: document.getElementById('import-source'),
            importFile: document.getElementById('import-file'),
            importPreviewBtn: document.getElementById('import-preview-btn'),
            importBtn: document.getElementById('import-btn'),
            importStatus: document.getElementById('import-status'),
            importStep1: document.getElementById('import-step-1'),
            importStep2: document.getElementById('import-step-2'),
            importBackBtn: document.getElementById('import-back-btn'),
            importPreviewInfo: document.getElementById('import-preview-info'),
            importConversationList: document.getElementById('import-conversation-list'),
            importSelectAllMemory: document.getElementById('import-select-all-memory'),
            importSelectAllHistory: document.getElementById('import-select-all-history'),
        };

        // Import state
        this.importFileContent = null;
        this.importPreviewData = null;

        this.init();
    }

    async init() {
        this.loadTheme();
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
        this.elements.archiveBtn.addEventListener('click', () => this.showArchiveModal());

        // Sidebar buttons
        this.elements.settingsBtn.addEventListener('click', () => this.showSettingsModal());
        this.elements.memoriesBtn.addEventListener('click', () => this.showMemoriesModal());
        this.elements.archivedBtn.addEventListener('click', () => this.showArchivedModal());

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

        // Archive modal
        document.getElementById('close-archive').addEventListener('click', () => this.hideModal('archiveModal'));
        document.getElementById('cancel-archive').addEventListener('click', () => this.hideModal('archiveModal'));
        document.getElementById('confirm-archive').addEventListener('click', () => this.archiveConversation());

        // Archived modal
        document.getElementById('close-archived').addEventListener('click', () => this.hideModal('archivedModal'));

        // Import functionality
        this.elements.importFile.addEventListener('change', () => this.handleImportFileChange());
        this.elements.importPreviewBtn.addEventListener('click', () => this.previewImportFile());
        this.elements.importBackBtn.addEventListener('click', () => this.resetImportToStep1());
        this.elements.importBtn.addEventListener('click', () => this.importExternalConversations());
        this.elements.importSelectAllMemory.addEventListener('change', (e) => this.toggleAllImportCheckboxes('memory', e.target.checked));
        this.elements.importSelectAllHistory.addEventListener('change', (e) => this.toggleAllImportCheckboxes('history', e.target.checked));
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

            // Always show entity selector so users know which entity they're working with
            this.elements.entitySelector.style.display = 'block';
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
                const provider = this.providers.find(p => p.id === entity.llm_provider);
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
            const providerName = entity.llm_provider === 'openai' ? 'OpenAI' : 'Anthropic';
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

        // Create streaming message element
        const streamingMessage = this.createStreamingMessage('assistant');
        let usageData = null;

        try {
            await api.sendMessageStream(
                {
                    conversation_id: this.currentConversationId,
                    message: content,
                    model: this.settings.model,
                    temperature: this.settings.temperature,
                    max_tokens: this.settings.maxTokens,
                    system_prompt: this.settings.systemPrompt,
                },
                {
                    onMemories: (data) => {
                        let hasChanges = false;

                        // Remove trimmed memories (FIFO trimming for token limits)
                        if (data.trimmed_memory_ids && data.trimmed_memory_ids.length > 0) {
                            const trimmedSet = new Set(data.trimmed_memory_ids);
                            this.retrievedMemories = this.retrievedMemories.filter(
                                mem => !trimmedSet.has(mem.id)
                            );
                            hasChanges = true;
                        }

                        // Add new memories (with deduplication for restored memories)
                        if (data.new_memories && data.new_memories.length > 0) {
                            const existingIds = new Set(this.retrievedMemories.map(m => m.id));
                            data.new_memories.forEach(mem => {
                                if (!existingIds.has(mem.id)) {
                                    this.retrievedMemories.push(mem);
                                }
                            });
                            hasChanges = true;
                        }

                        if (hasChanges) {
                            this.updateMemoriesPanel();
                        }
                    },
                    onStart: (data) => {
                        // Stream has started
                    },
                    onToken: (data) => {
                        // Update message content progressively
                        if (data.content) {
                            streamingMessage.updateContent(data.content);
                        }
                    },
                    onDone: (data) => {
                        // Stream complete - finalize message
                        streamingMessage.finalize({ showTimestamp: true });
                        usageData = data.usage;

                        // Update token display
                        if (usageData) {
                            this.elements.tokenCount.textContent = `Tokens: ${usageData.input_tokens} in / ${usageData.output_tokens} out`;
                        }
                    },
                    onStored: async (data) => {
                        // Messages have been stored
                        // Update conversation title if it's the first message
                        const conv = this.conversations.find(c => c.id === this.currentConversationId);
                        if (conv && !conv.title) {
                            const title = content.substring(0, 50) + (content.length > 50 ? '...' : '');
                            await api.updateConversation(this.currentConversationId, { title });
                            conv.title = title;
                            this.renderConversationList();
                            this.elements.conversationTitle.textContent = title;
                        }
                    },
                    onError: (data) => {
                        // Handle error
                        streamingMessage.element.remove();
                        this.addMessage('assistant', `Error: ${data.error}`, { isError: true });
                        this.showToast('Failed to send message', 'error');
                        console.error('Streaming error:', data.error);
                    },
                }
            );

            this.scrollToBottom();

        } catch (error) {
            streamingMessage.element.remove();
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
            <div class="message-bubble ${options.isError ? 'error' : ''}">${this.renderMarkdown(content)}</div>
            ${options.showTimestamp !== false ? `
                <div class="message-meta">
                    <span>${timeStr}</span>
                </div>
            ` : ''}
        `;

        this.elements.messages.appendChild(message);
        return message;
    }

    /**
     * Create a streaming message element that can be updated progressively.
     * @param {string} role - Message role (assistant)
     * @returns {Object} - Object with element, updateContent, and finalize methods
     */
    createStreamingMessage(role) {
        // Hide welcome message
        if (this.elements.welcomeMessage) {
            this.elements.welcomeMessage.style.display = 'none';
        }

        const message = document.createElement('div');
        message.className = `message ${role}`;

        const bubble = document.createElement('div');
        bubble.className = 'message-bubble streaming';

        const contentSpan = document.createElement('span');
        contentSpan.className = 'message-content';
        bubble.appendChild(contentSpan);

        // Add cursor element for visual feedback
        const cursor = document.createElement('span');
        cursor.className = 'streaming-cursor';
        cursor.textContent = '\u258c'; // Block cursor character
        bubble.appendChild(cursor);

        message.appendChild(bubble);
        this.elements.messages.appendChild(message);

        let accumulatedContent = '';

        return {
            element: message,
            updateContent: (newToken) => {
                accumulatedContent += newToken;
                contentSpan.textContent = accumulatedContent;
                this.scrollToBottom();
            },
            finalize: (options = {}) => {
                // Remove cursor
                cursor.remove();
                bubble.classList.remove('streaming');

                // Render final content with markdown
                contentSpan.innerHTML = this.renderMarkdown(accumulatedContent);

                // Add timestamp
                const timestamp = options.timestamp ? new Date(options.timestamp) : new Date();
                const timeStr = timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

                const meta = document.createElement('div');
                meta.className = 'message-meta';
                meta.innerHTML = `<span>${timeStr}</span>`;
                message.appendChild(meta);

                return accumulatedContent;
            },
            getContent: () => accumulatedContent,
        };
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
        let meta = `${conversation.conversation_type} · ${conversation.llm_model_used}`;

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
        this.elements.themeSelect.value = this.getCurrentTheme();
        // Reset import section to step 1
        this.resetImportToStep1();
        this.showModal('settingsModal');
    }

    resetImportToStep1() {
        this.elements.importFile.value = '';
        this.elements.importPreviewBtn.disabled = true;
        this.elements.importStatus.style.display = 'none';
        this.elements.importStep1.style.display = 'block';
        this.elements.importStep2.style.display = 'none';
        this.elements.importSelectAllMemory.checked = true;
        this.elements.importSelectAllHistory.checked = false;
        this.importFileContent = null;
        this.importPreviewData = null;
    }

    applySettings() {
        this.settings.model = this.elements.modelSelect.value;
        this.settings.temperature = parseFloat(this.elements.temperatureInput.value);
        this.settings.maxTokens = parseInt(this.elements.maxTokensInput.value);
        this.settings.systemPrompt = this.elements.systemPromptInput.value.trim() || null;
        this.settings.conversationType = this.elements.conversationTypeSelect.value;

        // Apply theme
        this.setTheme(this.elements.themeSelect.value);

        this.updateModelIndicator();
        this.hideModal('settingsModal');
        this.showToast('Settings applied', 'success');
    }

    handleImportFileChange() {
        const file = this.elements.importFile.files[0];
        this.elements.importPreviewBtn.disabled = !file;
        this.elements.importStatus.style.display = 'none';
    }

    async previewImportFile() {
        const file = this.elements.importFile.files[0];
        if (!file) {
            this.showToast('Please select a file to import', 'error');
            return;
        }

        if (!this.selectedEntityId) {
            this.showToast('Please select an entity first', 'error');
            return;
        }

        // Show loading state
        this.elements.importPreviewBtn.disabled = true;
        this.elements.importPreviewBtn.textContent = 'Loading...';
        this.elements.importStatus.style.display = 'block';
        this.elements.importStatus.className = 'import-status loading';
        this.elements.importStatus.textContent = 'Reading file...';

        try {
            // Read file content
            this.importFileContent = await this.readFileAsText(file);

            this.elements.importStatus.textContent = 'Analyzing conversations...';

            // Get source hint from select
            const source = this.elements.importSource.value || null;

            // Call API to preview
            this.importPreviewData = await api.previewExternalConversations({
                content: this.importFileContent,
                entity_id: this.selectedEntityId,
                source: source,
            });

            // Show step 2
            this.elements.importStatus.style.display = 'none';
            this.elements.importStep1.style.display = 'none';
            this.elements.importStep2.style.display = 'block';

            // Update preview info
            this.elements.importPreviewInfo.textContent = `${this.importPreviewData.total_conversations} conversations found (${this.importPreviewData.source_format})`;

            // Render conversation list
            this.renderImportConversationList();

        } catch (error) {
            this.elements.importStatus.className = 'import-status error';
            this.elements.importStatus.textContent = `Error: ${error.message}`;
            this.showToast('Failed to load conversations', 'error');
            console.error('Preview failed:', error);
        } finally {
            this.elements.importPreviewBtn.disabled = false;
            this.elements.importPreviewBtn.textContent = 'Load Conversations';
        }
    }

    renderImportConversationList() {
        if (!this.importPreviewData || !this.importPreviewData.conversations) {
            this.elements.importConversationList.innerHTML = '<p>No conversations found</p>';
            return;
        }

        const html = this.importPreviewData.conversations.map(conv => {
            const alreadyImported = conv.already_imported;
            const partiallyImported = conv.imported_count > 0 && !alreadyImported;

            let statusText = '';
            let statusClass = '';
            if (alreadyImported) {
                statusText = ' (already imported)';
                statusClass = 'imported';
            } else if (partiallyImported) {
                statusText = ` (${conv.imported_count}/${conv.message_count} imported)`;
                statusClass = 'partial';
            }

            return `
                <div class="import-conversation-item ${statusClass}" data-index="${conv.index}">
                    <div class="import-conversation-info">
                        <div class="import-conversation-title">${this.escapeHtml(conv.title)}</div>
                        <div class="import-conversation-meta">
                            ${conv.message_count} messages${statusText}
                        </div>
                    </div>
                    <div class="import-conversation-options">
                        <label title="Import as searchable memories">
                            <input type="checkbox" class="import-cb-memory" data-index="${conv.index}" ${alreadyImported ? '' : 'checked'} ${alreadyImported ? 'disabled' : ''}>
                            Memory
                        </label>
                        <label title="Also add to conversation history">
                            <input type="checkbox" class="import-cb-history" data-index="${conv.index}" ${alreadyImported ? 'disabled' : ''}>
                            History
                        </label>
                    </div>
                </div>
            `;
        }).join('');

        this.elements.importConversationList.innerHTML = html;
    }

    toggleAllImportCheckboxes(type, checked) {
        const selector = type === 'memory' ? '.import-cb-memory' : '.import-cb-history';
        const checkboxes = this.elements.importConversationList.querySelectorAll(selector + ':not(:disabled)');
        checkboxes.forEach(cb => cb.checked = checked);
    }

    async importExternalConversations() {
        if (!this.importFileContent || !this.importPreviewData) {
            this.showToast('Please load a file first', 'error');
            return;
        }

        if (!this.selectedEntityId) {
            this.showToast('Please select an entity first', 'error');
            return;
        }

        // Gather selected conversations
        const selectedConversations = [];
        this.importPreviewData.conversations.forEach(conv => {
            const memoryCheckbox = this.elements.importConversationList.querySelector(`.import-cb-memory[data-index="${conv.index}"]`);
            const historyCheckbox = this.elements.importConversationList.querySelector(`.import-cb-history[data-index="${conv.index}"]`);

            const importAsMemory = memoryCheckbox && memoryCheckbox.checked;
            const importToHistory = historyCheckbox && historyCheckbox.checked;

            if (importAsMemory || importToHistory) {
                selectedConversations.push({
                    index: conv.index,
                    import_as_memory: importAsMemory,
                    import_to_history: importToHistory,
                });
            }
        });

        if (selectedConversations.length === 0) {
            this.showToast('Please select at least one conversation to import', 'warning');
            return;
        }

        // Show loading state
        this.elements.importBtn.disabled = true;
        this.elements.importBtn.textContent = 'Importing...';
        this.elements.importStatus.style.display = 'block';
        this.elements.importStatus.className = 'import-status loading';
        this.elements.importStatus.textContent = `Importing ${selectedConversations.length} conversations...`;

        try {
            const source = this.elements.importSource.value || null;

            // Call API to import
            const result = await api.importExternalConversations({
                content: this.importFileContent,
                entity_id: this.selectedEntityId,
                source: source,
                selected_conversations: selectedConversations,
            });

            // Show success
            this.elements.importStatus.className = 'import-status success';
            let statusHtml = `<strong>Import successful!</strong><br>
                Conversations: ${result.conversations_imported}<br>
                Messages: ${result.messages_imported}`;

            if (result.messages_skipped > 0) {
                statusHtml += `<br>Skipped (duplicates): ${result.messages_skipped}`;
            }
            if (result.conversations_to_history > 0) {
                statusHtml += `<br>Added to history: ${result.conversations_to_history}`;
            }
            statusHtml += `<br>Memories stored: ${result.memories_stored}`;

            this.elements.importStatus.innerHTML = statusHtml;

            this.showToast(`Imported ${result.messages_imported} messages`, 'success');

            // Reload conversations if any were added to history
            if (result.conversations_to_history > 0) {
                await this.loadConversations();
            }

        } catch (error) {
            this.elements.importStatus.className = 'import-status error';
            this.elements.importStatus.textContent = `Error: ${error.message}`;
            this.showToast('Import failed', 'error');
            console.error('Import failed:', error);
        } finally {
            this.elements.importBtn.disabled = false;
            this.elements.importBtn.textContent = 'Import Selected';
        }
    }

    readFileAsText(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(new Error('Failed to read file'));
            reader.readAsText(file);
        });
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

    showArchiveModal() {
        if (!this.currentConversationId) return;
        this.showModal('archiveModal');
    }

    async archiveConversation() {
        if (!this.currentConversationId) return;

        try {
            await api.archiveConversation(this.currentConversationId);

            // Remove from list
            this.conversations = this.conversations.filter(c => c.id !== this.currentConversationId);
            this.currentConversationId = null;
            this.retrievedMemories = [];

            this.renderConversationList();
            this.clearMessages();
            this.updateMemoriesPanel();
            this.elements.conversationTitle.textContent = 'Select a conversation';
            this.elements.conversationMeta.textContent = '';

            this.hideModal('archiveModal');
            this.showToast('Conversation archived', 'success');
        } catch (error) {
            this.showToast('Failed to archive conversation', 'error');
            console.error('Failed to archive conversation:', error);
        }
    }

    async showArchivedModal() {
        this.showModal('archivedModal');
        await this.loadArchivedConversations();
    }

    async loadArchivedConversations() {
        try {
            const conversations = await api.listArchivedConversations(50, 0, this.selectedEntityId);

            if (conversations.length === 0) {
                this.elements.archivedList.innerHTML = `
                    <div class="archived-empty">
                        <p>No archived conversations</p>
                    </div>
                `;
                return;
            }

            this.elements.archivedList.innerHTML = conversations.map(conv => `
                <div class="archived-item" data-id="${conv.id}">
                    <div class="archived-item-info">
                        <div class="archived-item-title">${this.escapeHtml(conv.title || 'Untitled')}</div>
                        <div class="archived-item-meta">
                            ${conv.message_count} messages · ${new Date(conv.created_at).toLocaleDateString()}
                        </div>
                    </div>
                    <div class="archived-item-actions">
                        <button class="unarchive-btn" onclick="app.unarchiveConversation('${conv.id}')">Restore</button>
                    </div>
                </div>
            `).join('');
        } catch (error) {
            this.elements.archivedList.innerHTML = `
                <div class="archived-empty">
                    <p>Failed to load archived conversations</p>
                </div>
            `;
            console.error('Failed to load archived conversations:', error);
        }
    }

    async unarchiveConversation(conversationId) {
        try {
            await api.unarchiveConversation(conversationId);
            await this.loadArchivedConversations();
            await this.loadConversations();
            this.showToast('Conversation restored', 'success');
        } catch (error) {
            this.showToast('Failed to restore conversation', 'error');
            console.error('Failed to unarchive conversation:', error);
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

    // Theme management
    loadTheme() {
        const savedTheme = localStorage.getItem('here-i-am-theme');
        if (savedTheme && savedTheme !== 'system') {
            document.documentElement.classList.remove('theme-light', 'theme-dark');
            document.documentElement.classList.add(`theme-${savedTheme}`);
        }
        // If no saved theme or 'system', let the CSS @media query handle it
    }

    getCurrentTheme() {
        const savedTheme = localStorage.getItem('here-i-am-theme');
        if (savedTheme) {
            return savedTheme;
        }
        return 'system';
    }

    setTheme(theme) {
        const root = document.documentElement;
        root.classList.remove('theme-light', 'theme-dark');

        if (theme === 'dark') {
            root.classList.add('theme-dark');
            localStorage.setItem('here-i-am-theme', 'dark');
        } else if (theme === 'light') {
            root.classList.add('theme-light');
            localStorage.setItem('here-i-am-theme', 'light');
        } else {
            // 'system' - remove manual override, use CSS @media query
            localStorage.setItem('here-i-am-theme', 'system');
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

    /**
     * Render markdown to HTML for message display.
     * Handles: bold, italic, inline code, code blocks, links, and line breaks.
     * @param {string} text - The raw text to render
     * @returns {string} - HTML string with markdown rendered
     */
    renderMarkdown(text) {
        if (!text) return '';

        // First escape HTML to prevent XSS
        let html = this.escapeHtml(text);

        // Code blocks (```language\ncode\n```) - must be processed before inline code
        html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (match, lang, code) => {
            const langClass = lang ? ` data-language="${lang}"` : '';
            return `<pre class="md-code-block"${langClass}><code>${code.trim()}</code></pre>`;
        });

        // Inline code (`code`) - but not inside code blocks
        html = html.replace(/`([^`\n]+)`/g, '<code class="md-inline-code">$1</code>');

        // Bold (**text** or __text__) - process before italic
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');

        // Italic (*text*) - single asterisks
        html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

        // Italic (_text_) - underscores at word boundaries (without lookbehind for browser compatibility)
        html = html.replace(/(^|[\s\(\[])_([^_]+)_([\s\)\]\.,!?;:]|$)/g, '$1<em>$2</em>$3');

        // Links [text](url)
        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" class="md-link">$1</a>');

        // Headers (## text) - only at start of line
        html = html.replace(/^### (.+)$/gm, '<h4 class="md-header">$1</h4>');
        html = html.replace(/^## (.+)$/gm, '<h3 class="md-header">$1</h3>');
        html = html.replace(/^# (.+)$/gm, '<h2 class="md-header">$1</h2>');

        // Unordered lists (- item or * item) - but not if * is for bold/italic
        html = html.replace(/^- (.+)$/gm, '<li class="md-list-item">$1</li>');
        // Wrap consecutive list items in <ul>
        html = html.replace(/(<li class="md-list-item">[^<]*<\/li>\n?)+/g, '<ul class="md-list">$&</ul>');

        // Ordered lists (1. item)
        html = html.replace(/^\d+\. (.+)$/gm, '<li class="md-list-item-ordered">$1</li>');
        // Wrap consecutive ordered list items in <ol>
        html = html.replace(/(<li class="md-list-item-ordered">[^<]*<\/li>\n?)+/g, '<ol class="md-list">$&</ol>');

        // Blockquotes (> text)
        html = html.replace(/^&gt; (.+)$/gm, '<blockquote class="md-blockquote">$1</blockquote>');
        // Merge consecutive blockquotes
        html = html.replace(/<\/blockquote>\n<blockquote class="md-blockquote">/g, '<br>');

        // Horizontal rules (---, ***) - must be 3+ characters, alone on a line
        html = html.replace(/^\s*([-*])\1{2,}\s*$/gm, '<hr class="md-hr">');

        return html;
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new App();
});
