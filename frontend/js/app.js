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
            verbosity: 'medium',
        };
        this.isLoading = false;
        this.retrievedMemories = [];
        this.expandedMemoryIds = new Set();

        // Multi-entity state
        this.isMultiEntityMode = false;
        this.currentConversationEntities = [];  // Entities in current conversation
        this.pendingResponderId = null;  // Entity selected to respond next

        // Cache DOM elements
        this.elements = {
            conversationList: document.getElementById('conversation-list'),
            messages: document.getElementById('messages'),
            messagesContainer: document.getElementById('messages-container'),
            messageInput: document.getElementById('message-input'),
            voiceBtn: document.getElementById('voice-btn'),
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
            deleteModal: document.getElementById('delete-modal'),
            deleteConversationTitle: document.getElementById('delete-conversation-title'),
            archivedModal: document.getElementById('archived-modal'),
            archivedList: document.getElementById('archived-list'),

            // Settings
            modelSelect: document.getElementById('model-select'),
            temperatureInput: document.getElementById('temperature-input'),
            temperatureNumber: document.getElementById('temperature-number'),
            verbositySelect: document.getElementById('verbosity-select'),
            verbosityGroup: document.getElementById('verbosity-group'),
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

            // Voice (TTS)
            ttsProviderGroup: document.getElementById('tts-provider-group'),
            ttsProviderName: document.getElementById('tts-provider-name'),
            ttsProviderStatus: document.getElementById('tts-provider-status'),
            voiceSelectGroup: document.getElementById('voice-select-group'),
            voiceSelect: document.getElementById('voice-select'),
            voiceCloneGroup: document.getElementById('voice-clone-group'),
            openVoiceCloneBtn: document.getElementById('open-voice-clone-btn'),
            voiceManageGroup: document.getElementById('voice-manage-group'),
            voiceList: document.getElementById('voice-list'),

            // Voice Cloning Modal
            voiceCloneModal: document.getElementById('voice-clone-modal'),
            voiceCloneFile: document.getElementById('voice-clone-file'),
            voiceCloneName: document.getElementById('voice-clone-name'),
            voiceCloneDescription: document.getElementById('voice-clone-description'),
            voiceCloneTemperature: document.getElementById('voice-clone-temperature'),
            voiceCloneSpeed: document.getElementById('voice-clone-speed'),
            voiceCloneLengthPenalty: document.getElementById('voice-clone-length-penalty'),
            voiceCloneRepetitionPenalty: document.getElementById('voice-clone-repetition-penalty'),
            voiceCloneStatus: document.getElementById('voice-clone-status'),
            createVoiceCloneBtn: document.getElementById('create-voice-clone'),
            cancelVoiceCloneBtn: document.getElementById('cancel-voice-clone'),

            // Voice Edit Modal
            voiceEditModal: document.getElementById('voice-edit-modal'),
            voiceEditId: document.getElementById('voice-edit-id'),
            voiceEditName: document.getElementById('voice-edit-name'),
            voiceEditDescription: document.getElementById('voice-edit-description'),
            voiceEditTemperature: document.getElementById('voice-edit-temperature'),
            voiceEditSpeed: document.getElementById('voice-edit-speed'),
            voiceEditLengthPenalty: document.getElementById('voice-edit-length-penalty'),
            voiceEditRepetitionPenalty: document.getElementById('voice-edit-repetition-penalty'),
            voiceEditStatus: document.getElementById('voice-edit-status'),
            saveVoiceEditBtn: document.getElementById('save-voice-edit'),
            cancelVoiceEditBtn: document.getElementById('cancel-voice-edit'),

            // Import
            importSource: document.getElementById('import-source'),
            importFile: document.getElementById('import-file'),
            importAllowReimport: document.getElementById('import-allow-reimport'),
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
            importCancelBtn: document.getElementById('import-cancel-btn'),
            importProgress: document.getElementById('import-progress'),
            importProgressBar: document.getElementById('import-progress-bar'),
            importProgressText: document.getElementById('import-progress-text'),

            // Multi-entity
            multiEntityModal: document.getElementById('multi-entity-modal'),
            multiEntityList: document.getElementById('multi-entity-list'),
            confirmMultiEntityBtn: document.getElementById('confirm-multi-entity'),
            cancelMultiEntityBtn: document.getElementById('cancel-multi-entity'),
            closeMultiEntityBtn: document.getElementById('close-multi-entity'),
            entityResponderSelector: document.getElementById('entity-responder-selector'),
            entityResponderButtons: document.getElementById('entity-responder-buttons'),
        };

        // Import state
        this.importFileContent = null;
        this.importPreviewData = null;
        this.importAbortController = null;

        // Voice dictation state
        this.recognition = null;
        this.isRecording = false;
        this.textBeforeDictation = '';

        // TTS state
        this.ttsEnabled = false;
        this.ttsProvider = 'none'; // 'none', 'elevenlabs', or 'xtts'
        this.ttsVoices = [];
        this.selectedVoiceId = null;
        this.currentAudio = null;
        this.currentSpeakingBtn = null;
        this.audioCache = new Map(); // Cache: messageId -> { blob, url, voiceId }
        this.xttsServerHealthy = false;

        this.init();
    }

    async init() {
        this.loadTheme();
        this.bindEvents();
        this.initVoiceDictation();
        await this.loadEntities();
        await this.loadConversations();
        await this.loadConfig();
        await this.checkTTSStatus();
        this.updateModelIndicator();
    }

    async checkTTSStatus() {
        try {
            const status = await api.getTTSStatus();
            this.ttsEnabled = status.configured;
            this.ttsProvider = status.provider || 'none';

            if (status.configured) {
                this.ttsVoices = status.voices || [];
                this.selectedVoiceId = status.default_voice_id;

                // Track XTTS server health
                if (this.ttsProvider === 'xtts') {
                    this.xttsServerHealthy = status.server_healthy || false;
                }

                this.updateTTSUI();
            } else {
                this.updateTTSUI();
            }
        } catch (error) {
            console.warn('TTS status check failed:', error);
            this.ttsEnabled = false;
            this.ttsProvider = 'none';
            this.ttsVoices = [];
            this.updateTTSUI();
        }
    }

    updateTTSUI() {
        // Show provider info if TTS is configured
        if (this.ttsEnabled) {
            this.elements.ttsProviderGroup.style.display = 'block';

            // Set provider name
            if (this.ttsProvider === 'xtts') {
                this.elements.ttsProviderName.textContent = 'XTTS v2 (Local)';
                if (this.xttsServerHealthy) {
                    this.elements.ttsProviderStatus.textContent = 'Connected';
                    this.elements.ttsProviderStatus.className = 'tts-status healthy';
                } else {
                    this.elements.ttsProviderStatus.textContent = 'Server Unavailable';
                    this.elements.ttsProviderStatus.className = 'tts-status unhealthy';
                }
            } else if (this.ttsProvider === 'elevenlabs') {
                this.elements.ttsProviderName.textContent = 'ElevenLabs';
                this.elements.ttsProviderStatus.textContent = 'Cloud';
                this.elements.ttsProviderStatus.className = 'tts-status cloud';
            }
        } else {
            this.elements.ttsProviderGroup.style.display = 'none';
        }

        // Update voice selector
        this.updateVoiceSelector();

        // Show voice cloning options for XTTS
        if (this.ttsProvider === 'xtts') {
            this.elements.voiceCloneGroup.style.display = 'block';
            this.elements.voiceManageGroup.style.display = 'block';
            this.updateVoiceList();
        } else {
            this.elements.voiceCloneGroup.style.display = 'none';
            this.elements.voiceManageGroup.style.display = 'none';
        }
    }

    updateVoiceSelector() {
        // Show/hide voice selector based on available voices
        if (this.ttsVoices.length > 0) {
            this.elements.voiceSelectGroup.style.display = 'block';

            // Populate voice options
            this.elements.voiceSelect.innerHTML = this.ttsVoices.map(voice => `
                <option value="${voice.voice_id}" ${voice.voice_id === this.selectedVoiceId ? 'selected' : ''}>
                    ${voice.label}${voice.description ? ` - ${voice.description}` : ''}
                </option>
            `).join('');
        } else {
            this.elements.voiceSelectGroup.style.display = 'none';
        }
    }

    updateVoiceList() {
        // Update the voice management list for XTTS
        if (this.ttsProvider !== 'xtts') {
            this.elements.voiceList.innerHTML = '';
            return;
        }

        if (this.ttsVoices.length === 0) {
            this.elements.voiceList.innerHTML = '<div class="voice-list-empty">No voices configured. Clone a voice to get started.</div>';
            return;
        }

        this.elements.voiceList.innerHTML = this.ttsVoices.map(voice => `
            <div class="voice-item" data-voice-id="${voice.voice_id}">
                <div class="voice-item-info">
                    <span class="voice-item-name">${voice.label}</span>
                    ${voice.description ? `<span class="voice-item-description">${voice.description}</span>` : ''}
                    <span class="voice-item-params">T:${voice.temperature ?? 0.75} S:${voice.speed ?? 1.0}</span>
                </div>
                <div class="voice-item-actions">
                    <button class="voice-item-btn settings" title="Edit voice settings" data-action="edit">Edit</button>
                    <button class="voice-item-btn delete" title="Delete voice" data-action="delete">Delete</button>
                </div>
            </div>
        `).join('');

        // Add event listeners
        this.elements.voiceList.querySelectorAll('.voice-item-btn[data-action="delete"]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const voiceId = e.target.closest('.voice-item').dataset.voiceId;
                this.deleteVoice(voiceId);
            });
        });

        this.elements.voiceList.querySelectorAll('.voice-item-btn[data-action="edit"]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const voiceId = e.target.closest('.voice-item').dataset.voiceId;
                this.showVoiceEditModal(voiceId);
            });
        });
    }

    bindEvents() {
        // Entity selector
        this.elements.entitySelect.addEventListener('change', (e) => this.handleEntityChange(e.target.value));

        // Message input
        this.elements.messageInput.addEventListener('input', () => this.handleInputChange());
        this.elements.messageInput.addEventListener('keydown', (e) => this.handleKeyDown(e));
        this.elements.sendBtn.addEventListener('click', () => this.sendMessage());
        this.elements.voiceBtn.addEventListener('click', () => this.toggleVoiceDictation());

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
            this.elements.temperatureNumber.value = e.target.value;
        });
        this.elements.temperatureNumber.addEventListener('input', (e) => {
            let value = parseFloat(e.target.value);
            const maxTemp = this.getMaxTemperatureForCurrentEntity();
            if (isNaN(value)) value = 1.0;
            if (value < 0) value = 0;
            if (value > maxTemp) value = maxTemp;
            this.elements.temperatureInput.value = value;
        });
        this.elements.modelSelect.addEventListener('change', () => {
            this.updateTemperatureControlState();
            this.updateVerbosityControlState();
        });
        this.elements.presetSelect.addEventListener('change', (e) => this.loadPreset(e.target.value));

        // Memories modal
        document.getElementById('close-memories').addEventListener('click', () => this.hideModal('memoriesModal'));
        document.getElementById('memory-search-btn').addEventListener('click', () => this.searchMemories());
        document.getElementById('check-orphans-btn').addEventListener('click', () => this.checkForOrphans());
        document.getElementById('cleanup-orphans-btn').addEventListener('click', () => this.cleanupOrphans());

        // Archive modal
        document.getElementById('close-archive').addEventListener('click', () => this.hideModal('archiveModal'));
        document.getElementById('cancel-archive').addEventListener('click', () => this.hideModal('archiveModal'));
        document.getElementById('confirm-archive').addEventListener('click', () => this.archiveConversation());

        // Archived modal
        document.getElementById('close-archived').addEventListener('click', () => this.hideModal('archivedModal'));

        // Delete modal
        document.getElementById('close-delete').addEventListener('click', () => this.hideModal('deleteModal'));
        document.getElementById('cancel-delete').addEventListener('click', () => this.hideModal('deleteModal'));
        document.getElementById('confirm-delete').addEventListener('click', () => this.deleteConversation());

        // Import functionality
        this.elements.importFile.addEventListener('change', () => this.handleImportFileChange());
        this.elements.importPreviewBtn.addEventListener('click', () => this.previewImportFile());
        this.elements.importBackBtn.addEventListener('click', () => this.resetImportToStep1());
        this.elements.importBtn.addEventListener('click', () => this.importExternalConversations());
        this.elements.importCancelBtn.addEventListener('click', () => this.cancelImport());
        this.elements.importSelectAllMemory.addEventListener('change', (e) => this.toggleAllImportCheckboxes('memory', e.target.checked));
        this.elements.importSelectAllHistory.addEventListener('change', (e) => this.toggleAllImportCheckboxes('history', e.target.checked));

        // Multi-entity modal
        this.elements.closeMultiEntityBtn.addEventListener('click', () => this.hideMultiEntityModal());
        this.elements.cancelMultiEntityBtn.addEventListener('click', () => this.hideMultiEntityModal());
        this.elements.confirmMultiEntityBtn.addEventListener('click', () => this.confirmMultiEntitySelection());

        // Voice cloning modal
        this.elements.openVoiceCloneBtn.addEventListener('click', () => this.showVoiceCloneModal());
        document.getElementById('close-voice-clone').addEventListener('click', () => this.hideVoiceCloneModal());
        this.elements.cancelVoiceCloneBtn.addEventListener('click', () => this.hideVoiceCloneModal());
        this.elements.createVoiceCloneBtn.addEventListener('click', () => this.createVoiceClone());
        this.elements.voiceCloneFile.addEventListener('change', () => this.updateVoiceCloneButton());
        this.elements.voiceCloneName.addEventListener('input', () => this.updateVoiceCloneButton());

        // Voice edit modal
        document.getElementById('close-voice-edit').addEventListener('click', () => this.hideVoiceEditModal());
        this.elements.cancelVoiceEditBtn.addEventListener('click', () => this.hideVoiceEditModal());
        this.elements.saveVoiceEditBtn.addEventListener('click', () => this.saveVoiceEdit());
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

    /**
     * Initialize Web Speech API for voice dictation.
     * Hides the voice button if the browser doesn't support it.
     */
    initVoiceDictation() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

        if (!SpeechRecognition) {
            console.warn('Speech recognition not supported in this browser');
            this.elements.voiceBtn.classList.add('unsupported');
            return;
        }

        this.recognition = new SpeechRecognition();
        this.recognition.continuous = true;
        this.recognition.interimResults = true;
        this.recognition.lang = 'en-US';

        this.recognition.onstart = () => {
            this.isRecording = true;
            this.elements.voiceBtn.classList.add('recording');
            this.elements.voiceBtn.title = 'Stop dictation';
            this.elements.messageInput.classList.add('transcribing');
            // Save the text that was in the input before we started dictating
            this.textBeforeDictation = this.elements.messageInput.value;
        };

        this.recognition.onend = () => {
            this.isRecording = false;
            this.elements.voiceBtn.classList.remove('recording');
            this.elements.voiceBtn.title = 'Voice dictation';
            this.elements.messageInput.classList.remove('transcribing');
            this.handleInputChange();
        };

        this.recognition.onresult = (event) => {
            let interimTranscript = '';
            let finalTranscript = '';

            for (let i = event.resultIndex; i < event.results.length; i++) {
                const transcript = event.results[i][0].transcript;
                if (event.results[i].isFinal) {
                    finalTranscript += transcript;
                } else {
                    interimTranscript += transcript;
                }
            }

            // Combine: text before dictation + final results so far + current interim
            // Build the complete final transcript from all final results
            let allFinalTranscripts = '';
            for (let i = 0; i < event.results.length; i++) {
                if (event.results[i].isFinal) {
                    allFinalTranscripts += event.results[i][0].transcript;
                }
            }

            // Construct the full text: previous text + final transcripts + interim
            const separator = this.textBeforeDictation && !this.textBeforeDictation.endsWith(' ') ? ' ' : '';
            const newText = this.textBeforeDictation + separator + allFinalTranscripts + interimTranscript;

            this.elements.messageInput.value = newText;
            this.handleInputChange();
        };

        this.recognition.onerror = (event) => {
            console.error('Speech recognition error:', event.error);

            if (event.error === 'not-allowed') {
                this.showToast('Microphone access denied. Please allow microphone access.', 'error');
            } else if (event.error === 'no-speech') {
                this.showToast('No speech detected. Try again.', 'warning');
            } else if (event.error !== 'aborted') {
                this.showToast(`Voice dictation error: ${event.error}`, 'error');
            }

            this.isRecording = false;
            this.elements.voiceBtn.classList.remove('recording');
            this.elements.messageInput.classList.remove('transcribing');
        };
    }

    /**
     * Toggle voice dictation on/off.
     */
    toggleVoiceDictation() {
        if (!this.recognition) {
            this.showToast('Voice dictation is not supported in this browser', 'warning');
            return;
        }

        if (this.isRecording) {
            this.recognition.stop();
        } else {
            try {
                this.recognition.start();
            } catch (e) {
                // May throw if already started
                console.error('Failed to start recognition:', e);
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

            // Render entity selector with multi-entity option at bottom
            let options = this.entities.map(entity => `
                <option value="${entity.index_name}" ${entity.is_default ? 'selected' : ''}>
                    ${this.escapeHtml(entity.label)}
                </option>
            `).join('');

            // Add multi-entity option if there are at least 2 entities
            if (this.entities.length >= 2) {
                options += `
                    <option disabled>───────────────</option>
                    <option value="multi-entity">Multi-Entity Conversation</option>
                `;
            }

            this.elements.entitySelect.innerHTML = options;

            // Set default entity
            this.selectedEntityId = response.default_entity;
            this.updateEntityDescription();
            this.updateTemperatureMax();

            // Always show entity selector so users know which entity they're working with
            this.elements.entitySelector.style.display = 'block';
        } catch (error) {
            console.error('Failed to load entities:', error);
            // Hide entity selector on error
            this.elements.entitySelector.style.display = 'none';
        }
    }

    handleEntityChange(entityId) {
        // Check if multi-entity was selected
        if (entityId === 'multi-entity') {
            this.showMultiEntityModal();
            return;
        }

        this.isMultiEntityMode = false;
        this.currentConversationEntities = [];
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
            this.updateTemperatureMax();
        }

        // Clear current conversation when switching entities
        this.currentConversationId = null;
        this.retrievedMemories = [];
        this.expandedMemoryIds.clear();
        this.clearMessages();
        this.elements.conversationTitle.textContent = 'Select a conversation';
        this.elements.conversationMeta.textContent = '';
        this.updateMemoriesPanel();
        this.hideEntityResponderSelector();

        // Reload conversations for the new entity
        this.loadConversations();

        this.showToast(`Switched to ${this.getEntityLabel(entityId)}`, 'success');
    }

    updateEntityDescription() {
        // Handle multi-entity mode
        if (this.isMultiEntityMode && this.currentConversationEntities.length > 0) {
            const labels = this.currentConversationEntities.map(e => e.label).join(', ');
            this.elements.entityDescription.textContent = `Multi-entity: ${labels}`;
            this.elements.entityDescription.style.display = 'block';
            return;
        }

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
        if (entityId === 'multi-entity') {
            return 'Multi-Entity';
        }
        const entity = this.entities.find(e => e.index_name === entityId);
        return entity ? entity.label : entityId;
    }

    // ==================== Multi-Entity Methods ====================

    showMultiEntityModal() {
        // Populate the entity list with checkboxes
        this.elements.multiEntityList.innerHTML = this.entities.map(entity => `
            <label class="multi-entity-item">
                <input type="checkbox" value="${entity.index_name}">
                <div class="multi-entity-item-info">
                    <div class="multi-entity-item-label">${this.escapeHtml(entity.label)}</div>
                    ${entity.description ? `<div class="multi-entity-item-description">${this.escapeHtml(entity.description)}</div>` : ''}
                    <div class="multi-entity-item-model">
                        ${entity.llm_provider === 'openai' ? 'OpenAI' : 'Anthropic'}: ${entity.default_model || 'default'}
                    </div>
                </div>
            </label>
        `).join('');

        // Add event listeners to update confirm button state
        this.elements.multiEntityList.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.addEventListener('change', () => this.updateMultiEntityConfirmButton());
        });

        this.elements.confirmMultiEntityBtn.disabled = true;
        this.elements.multiEntityModal.classList.add('active');
    }

    hideMultiEntityModal() {
        this.elements.multiEntityModal.classList.remove('active');
        // Reset entity selector to previous value if nothing was selected
        if (!this.isMultiEntityMode) {
            this.elements.entitySelect.value = this.selectedEntityId || this.entities[0]?.index_name;
        }
    }

    updateMultiEntityConfirmButton() {
        const checkedCount = this.elements.multiEntityList.querySelectorAll('input[type="checkbox"]:checked').length;
        this.elements.confirmMultiEntityBtn.disabled = checkedCount < 2;
    }

    confirmMultiEntitySelection() {
        const selectedEntityIds = Array.from(
            this.elements.multiEntityList.querySelectorAll('input[type="checkbox"]:checked')
        ).map(cb => cb.value);

        if (selectedEntityIds.length < 2) {
            this.showToast('Please select at least 2 entities', 'error');
            return;
        }

        // Set multi-entity mode
        this.isMultiEntityMode = true;
        this.selectedEntityId = 'multi-entity';
        this.currentConversationEntities = selectedEntityIds.map(id =>
            this.entities.find(e => e.index_name === id)
        ).filter(Boolean);

        this.hideMultiEntityModal();

        // Clear current conversation
        this.currentConversationId = null;
        this.retrievedMemories = [];
        this.expandedMemoryIds.clear();
        this.clearMessages();
        this.elements.conversationTitle.textContent = 'New Multi-Entity Conversation';
        this.elements.conversationMeta.textContent = '';
        this.updateMemoriesPanel();
        this.updateEntityDescription();

        // Load conversations for multi-entity view
        this.loadConversations();

        const labels = this.currentConversationEntities.map(e => e.label).join(' & ');
        this.showToast(`Multi-entity mode: ${labels}`, 'success');
    }

    showEntityResponderSelector() {
        if (!this.isMultiEntityMode || this.currentConversationEntities.length === 0) {
            return;
        }

        // Populate responder buttons
        this.elements.entityResponderButtons.innerHTML = this.currentConversationEntities.map(entity => `
            <button class="entity-responder-btn" data-entity-id="${entity.index_name}">
                ${this.escapeHtml(entity.label)}
            </button>
        `).join('');

        // Add click handlers
        this.elements.entityResponderButtons.querySelectorAll('.entity-responder-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this.pendingResponderId = btn.dataset.entityId;
                this.hideEntityResponderSelector();
                this.sendMessageWithResponder();
            });
        });

        this.elements.entityResponderSelector.style.display = 'block';
    }

    hideEntityResponderSelector() {
        this.elements.entityResponderSelector.style.display = 'none';
    }

    async sendMessageWithResponder() {
        if (!this.pendingResponderId || !this.pendingMessageContent) {
            this.showToast('No entity selected or no message pending', 'error');
            return;
        }

        const content = this.pendingMessageContent;
        const responderId = this.pendingResponderId;
        const userMessageEl = this.pendingUserMessageEl;

        // Clear pending state
        this.pendingMessageContent = null;
        this.pendingResponderId = null;
        this.pendingUserMessageEl = null;

        this.isLoading = true;
        this.elements.sendBtn.disabled = true;

        // Get the responding entity's label
        const responderEntity = this.currentConversationEntities.find(e => e.index_name === responderId);
        const responderLabel = responderEntity?.label || responderId;

        // Create streaming message element with speaker label
        const streamingMessage = this.createStreamingMessage('assistant', responderLabel);
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
                    verbosity: this.settings.verbosity,
                    responding_entity_id: responderId,
                },
                {
                    onMemories: (data) => {
                        let hasChanges = false;

                        if (data.trimmed_memory_ids && data.trimmed_memory_ids.length > 0) {
                            const trimmedSet = new Set(data.trimmed_memory_ids);
                            this.retrievedMemories = this.retrievedMemories.filter(
                                mem => !trimmedSet.has(mem.id)
                            );
                            hasChanges = true;
                        }

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
                        if (data.content) {
                            streamingMessage.updateContent(data.content);
                        }
                    },
                    onDone: (data) => {
                        streamingMessage.finalize({
                            showTimestamp: true,
                            speakerLabel: responderLabel,
                        });
                        usageData = data.usage;

                        if (usageData) {
                            this.elements.tokenCount.textContent = `Tokens: ${usageData.input_tokens} in / ${usageData.output_tokens} out`;
                        }
                    },
                    onStored: async (data) => {
                        // Update user message with ID
                        if (data.human_message_id && userMessageEl) {
                            userMessageEl.dataset.messageId = data.human_message_id;
                            const userBubble = userMessageEl.querySelector('.message-bubble');
                            if (userBubble) {
                                const actionsDiv = document.createElement('div');
                                actionsDiv.className = 'message-bubble-actions';
                                actionsDiv.innerHTML = `
                                    <button class="message-action-btn copy-btn" title="Copy to clipboard">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                                        </svg>
                                    </button>
                                    <button class="message-action-btn edit-btn" title="Edit message">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                                            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                                        </svg>
                                    </button>
                                `;
                                userBubble.appendChild(actionsDiv);
                            }
                        }

                        // Update assistant message with ID and speaker info
                        if (data.assistant_message_id) {
                            streamingMessage.element.dataset.messageId = data.assistant_message_id;
                            streamingMessage.element.dataset.speakerEntityId = responderId;
                            this.updateAssistantMessageActions(streamingMessage.element, data.assistant_message_id);
                        }

                        // Auto-generate title for new conversations
                        const conv = this.conversations.find(c => c.id === this.currentConversationId);
                        if (conv && !conv.title && content) {
                            const autoTitle = content.slice(0, 50) + (content.length > 50 ? '...' : '');
                            try {
                                await api.updateConversation(this.currentConversationId, { title: autoTitle });
                                conv.title = autoTitle;
                                this.renderConversationList();
                                this.elements.conversationTitle.textContent = autoTitle;
                            } catch (e) {
                                console.error('Failed to auto-set title:', e);
                            }
                        }

                        // Show responder selector for next turn
                        this.showEntityResponderSelector();
                    },
                    onError: (data) => {
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

    getMaxTemperatureForCurrentEntity() {
        const entity = this.entities.find(e => e.index_name === this.selectedEntityId);
        // OpenAI supports temperature 0-2, Anthropic only 0-1
        if (entity && entity.llm_provider === 'openai') {
            return 2.0;
        }
        return 1.0;
    }

    modelSupportsTemperature(modelId) {
        // Check if the model supports temperature parameter
        const model = this.availableModels.find(m => m.id === modelId);
        // Default to true if model not found (safer default)
        return model ? model.temperature_supported !== false : true;
    }

    updateTemperatureControlState() {
        const selectedModel = this.elements.modelSelect.value;
        const supportsTemp = this.modelSupportsTemperature(selectedModel);

        // Disable or enable temperature controls
        this.elements.temperatureInput.disabled = !supportsTemp;
        this.elements.temperatureNumber.disabled = !supportsTemp;

        // Add visual indication to the form group
        const formGroup = this.elements.temperatureInput.closest('.form-group');
        if (formGroup) {
            if (supportsTemp) {
                formGroup.classList.remove('disabled');
                formGroup.title = '';
            } else {
                formGroup.classList.add('disabled');
                formGroup.title = 'Temperature is not supported by this model';
            }
        }
    }

    modelSupportsVerbosity(modelId) {
        // Check if the model supports verbosity parameter
        const model = this.availableModels.find(m => m.id === modelId);
        // Default to false if model not found
        return model ? model.verbosity_supported === true : false;
    }

    updateVerbosityControlState() {
        const selectedModel = this.elements.modelSelect.value;
        const supportsVerbosity = this.modelSupportsVerbosity(selectedModel);

        // Disable or enable verbosity control
        this.elements.verbositySelect.disabled = !supportsVerbosity;

        // Add visual indication to the form group
        if (this.elements.verbosityGroup) {
            if (supportsVerbosity) {
                this.elements.verbosityGroup.classList.remove('disabled');
                this.elements.verbosityGroup.title = '';
            } else {
                this.elements.verbosityGroup.classList.add('disabled');
                this.elements.verbosityGroup.title = 'Verbosity is only supported by GPT-5.1 models';
            }
        }
    }

    updateTemperatureMax() {
        const maxTemp = this.getMaxTemperatureForCurrentEntity();
        this.elements.temperatureInput.max = maxTemp;
        this.elements.temperatureNumber.max = maxTemp;

        // Clamp current value if it exceeds new max
        if (this.settings.temperature > maxTemp) {
            this.settings.temperature = maxTemp;
            this.elements.temperatureInput.value = maxTemp;
            this.elements.temperatureNumber.value = maxTemp;
        }
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
            const isMulti = conv.conversation_type === 'multi_entity';
            const item = document.createElement('div');
            item.className = `conversation-item${conv.id === this.currentConversationId ? ' active' : ''}${isMulti ? ' multi-entity' : ''}`;
            item.dataset.id = conv.id;

            const date = new Date(conv.created_at);
            const dateStr = date.toLocaleDateString();

            // Build entity labels for multi-entity conversations
            let entityLabels = '';
            if (isMulti && conv.entities && conv.entities.length > 0) {
                entityLabels = conv.entities.map(e => e.label).join(' & ');
            }

            item.innerHTML = `
                <div class="conversation-item-content">
                    <div class="conversation-item-title">
                        ${conv.title || 'Untitled'}
                        ${isMulti ? '<span class="multi-entity-badge">Multi</span>' : ''}
                    </div>
                    <div class="conversation-item-meta">
                        ${dateStr} · ${conv.message_count} messages
                        ${entityLabels ? ` · ${this.escapeHtml(entityLabels)}` : ''}
                    </div>
                    ${conv.preview ? `<div class="conversation-item-preview">${this.escapeHtml(conv.preview)}</div>` : ''}
                </div>
                <div class="conversation-item-menu">
                    <button class="conversation-menu-btn" data-id="${conv.id}" title="More options">⋮</button>
                    <div class="conversation-dropdown" data-id="${conv.id}">
                        <button class="conversation-dropdown-item" data-action="archive" data-id="${conv.id}">Archive</button>
                    </div>
                </div>
            `;

            // Click on item content to load conversation
            const contentEl = item.querySelector('.conversation-item-content');
            contentEl.addEventListener('click', () => this.loadConversation(conv.id));

            // Click on menu button to toggle dropdown
            const menuBtn = item.querySelector('.conversation-menu-btn');
            menuBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.toggleConversationDropdown(conv.id);
            });

            // Click on archive option
            const archiveBtn = item.querySelector('[data-action="archive"]');
            archiveBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.showArchiveModalForConversation(conv.id, conv.title);
            });

            this.elements.conversationList.appendChild(item);
        });

        // Close dropdowns when clicking outside
        document.addEventListener('click', () => this.closeAllDropdowns());
    }

    toggleConversationDropdown(conversationId) {
        // Close all other dropdowns first
        const allDropdowns = document.querySelectorAll('.conversation-dropdown');
        allDropdowns.forEach(dropdown => {
            if (dropdown.dataset.id !== conversationId) {
                dropdown.classList.remove('open');
            }
        });

        // Toggle the clicked dropdown
        const dropdown = document.querySelector(`.conversation-dropdown[data-id="${conversationId}"]`);
        if (dropdown) {
            dropdown.classList.toggle('open');
        }
    }

    closeAllDropdowns() {
        const allDropdowns = document.querySelectorAll('.conversation-dropdown');
        allDropdowns.forEach(dropdown => dropdown.classList.remove('open'));
    }

    async createNewConversation() {
        try {
            let conversationData;

            if (this.isMultiEntityMode && this.currentConversationEntities.length >= 2) {
                // Create multi-entity conversation
                conversationData = {
                    model: this.settings.model,
                    system_prompt: this.settings.systemPrompt,
                    conversation_type: 'multi_entity',
                    entity_ids: this.currentConversationEntities.map(e => e.index_name),
                };
            } else {
                // Create standard conversation
                conversationData = {
                    model: this.settings.model,
                    system_prompt: this.settings.systemPrompt,
                    conversation_type: this.settings.conversationType,
                    entity_id: this.selectedEntityId,
                };
            }

            const conversation = await api.createConversation(conversationData);

            this.conversations.unshift(conversation);
            this.currentConversationId = conversation.id;
            this.retrievedMemories = [];
            this.expandedMemoryIds.clear();

            // Update entities if this is a multi-entity conversation
            if (conversation.entities && conversation.entities.length > 0) {
                this.currentConversationEntities = conversation.entities.map(e => ({
                    index_name: e.entity_id,
                    label: e.label,
                    description: e.description,
                    llm_provider: e.llm_provider,
                    default_model: e.default_model,
                }));
            }

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
            this.expandedMemoryIds.clear();

            // Handle multi-entity conversation
            if (conversation.conversation_type === 'multi_entity' && conversation.entities) {
                this.isMultiEntityMode = true;
                this.currentConversationEntities = conversation.entities.map(e => ({
                    index_name: e.entity_id,
                    label: e.label,
                    description: e.description,
                    llm_provider: e.llm_provider,
                    default_model: e.default_model,
                }));
                this.selectedEntityId = 'multi-entity';
                this.elements.entitySelect.value = 'multi-entity';
            } else {
                this.isMultiEntityMode = false;
                this.currentConversationEntities = [];
            }

            this.renderConversationList();
            this.clearMessages();
            this.updateHeader(conversation);
            this.updateMemoriesPanel();
            this.updateEntityDescription();

            // Find the last assistant message index
            let lastAssistantIndex = -1;
            for (let i = messages.length - 1; i >= 0; i--) {
                if (messages[i].role === 'assistant') {
                    lastAssistantIndex = i;
                    break;
                }
            }

            // Render messages
            messages.forEach((msg, index) => {
                this.addMessage(msg.role, msg.content, {
                    timestamp: msg.created_at,
                    showTimestamp: true,
                    messageId: msg.id,
                    isLatestAssistant: msg.role === 'assistant' && index === lastAssistantIndex,
                    speakerEntityId: msg.speaker_entity_id,
                    speakerLabel: msg.speaker_label,
                });
            });

            this.scrollToBottom();

            // Show responder selector if multi-entity and conversation has messages
            if (this.isMultiEntityMode && messages.length > 0) {
                this.showEntityResponderSelector();
            }
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

        // In multi-entity mode, store message and show responder selector
        if (this.isMultiEntityMode) {
            this.pendingMessageContent = content;
            this.elements.messageInput.value = '';
            this.elements.messageInput.style.height = 'auto';

            // Add user message visually immediately
            this.pendingUserMessageEl = this.addMessage('human', content);
            this.scrollToBottom();

            // Show responder selector
            this.showEntityResponderSelector();
            return;
        }

        // Standard single-entity flow
        this.isLoading = true;
        this.elements.sendBtn.disabled = true;
        this.elements.messageInput.value = '';
        this.elements.messageInput.style.height = 'auto';

        // Add user message (without ID initially - will be updated when stored)
        const userMessageEl = this.addMessage('human', content);
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
                    verbosity: this.settings.verbosity,
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
                        // Messages have been stored - update DOM with IDs and add action buttons
                        if (data.human_message_id && userMessageEl) {
                            userMessageEl.dataset.messageId = data.human_message_id;
                            // Add action buttons inside user message bubble
                            const userBubble = userMessageEl.querySelector('.message-bubble');
                            if (userBubble) {
                                const actionsDiv = document.createElement('div');
                                actionsDiv.className = 'message-bubble-actions';
                                actionsDiv.innerHTML = `
                                    <button class="message-action-btn copy-btn" title="Copy to clipboard">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                                        </svg>
                                    </button>
                                    <button class="message-action-btn edit-btn" title="Edit message">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                                            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                                        </svg>
                                    </button>
                                `;
                                userBubble.appendChild(actionsDiv);
                                const copyBtn = actionsDiv.querySelector('.copy-btn');
                                copyBtn.addEventListener('click', (e) => {
                                    e.stopPropagation();
                                    this.copyMessage(content, copyBtn);
                                });
                                const editBtn = actionsDiv.querySelector('.edit-btn');
                                editBtn.addEventListener('click', (e) => {
                                    e.stopPropagation();
                                    this.startEditMessage(userMessageEl, data.human_message_id, content);
                                });
                            }
                        }

                        if (data.assistant_message_id) {
                            streamingMessage.element.dataset.messageId = data.assistant_message_id;
                            // Remove regenerate buttons from previous assistant messages
                            this.removeRegenerateButtons();
                            // Add action buttons inside assistant message bubble
                            const assistantBubble = streamingMessage.element.querySelector('.message-bubble');
                            if (assistantBubble) {
                                const actionsDiv = document.createElement('div');
                                actionsDiv.className = 'message-bubble-actions';
                                const speakBtnHtml = this.ttsEnabled ? `
                                    <button class="message-action-btn speak-btn" title="Read aloud">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                                            <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
                                            <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
                                        </svg>
                                    </button>
                                ` : '';
                                actionsDiv.innerHTML = `
                                    <button class="message-action-btn copy-btn" title="Copy to clipboard">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                                        </svg>
                                    </button>
                                    ${speakBtnHtml}
                                    <button class="message-action-btn regenerate-btn" title="Regenerate response">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <path d="M23 4v6h-6"/>
                                            <path d="M1 20v-6h6"/>
                                            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
                                        </svg>
                                    </button>
                                `;
                                assistantBubble.appendChild(actionsDiv);
                                const messageContent = streamingMessage.getContent();
                                const msgId = data.assistant_message_id;
                                const copyBtn = actionsDiv.querySelector('.copy-btn');
                                copyBtn.addEventListener('click', (e) => {
                                    e.stopPropagation();
                                    this.copyMessage(messageContent, copyBtn);
                                });
                                const regenerateBtn = actionsDiv.querySelector('.regenerate-btn');
                                regenerateBtn.addEventListener('click', (e) => {
                                    e.stopPropagation();
                                    this.regenerateMessage(data.assistant_message_id);
                                });
                                const speakBtn = actionsDiv.querySelector('.speak-btn');
                                if (speakBtn) {
                                    speakBtn.addEventListener('click', (e) => {
                                        e.stopPropagation();
                                        this.speakMessage(messageContent, speakBtn, msgId);
                                    });
                                }
                            }
                        }

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

        // Store message ID and role as data attributes
        if (options.messageId) {
            message.dataset.messageId = options.messageId;
        }
        message.dataset.role = role;

        // Store speaker entity info for multi-entity conversations
        if (options.speakerEntityId) {
            message.dataset.speakerEntityId = options.speakerEntityId;
        }

        const timestamp = options.timestamp ? new Date(options.timestamp) : new Date();
        const timeStr = timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

        // Build speaker label for multi-entity assistant messages
        let speakerLabelHtml = '';
        if (role === 'assistant' && options.speakerLabel) {
            speakerLabelHtml = `<span class="message-speaker-label">${this.escapeHtml(options.speakerLabel)}</span>`;
        }

        // Build action buttons based on role (now inside the bubble)
        let actionButtons = '';
        const copyBtn = `<button class="message-action-btn copy-btn" title="Copy to clipboard"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>`;
        if (options.messageId && !options.isError) {
            if (role === 'human') {
                actionButtons = `<div class="message-bubble-actions">${copyBtn}<button class="message-action-btn edit-btn" title="Edit message"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button></div>`;
            } else if (role === 'assistant') {
                const speakBtn = this.ttsEnabled ? `<button class="message-action-btn speak-btn" title="Read aloud"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg></button>` : '';
                // Only show regenerate button on the latest assistant message
                const regenerateBtn = options.isLatestAssistant ? `<button class="message-action-btn regenerate-btn" title="Regenerate response"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg></button>` : '';
                actionButtons = `<div class="message-bubble-actions">${copyBtn}${speakBtn}${regenerateBtn}</div>`;
            }
        }

        message.innerHTML = `
            <div class="message-bubble ${options.isError ? 'error' : ''}">${speakerLabelHtml}${this.renderMarkdown(content)}${actionButtons}</div>
            ${options.showTimestamp !== false ? `
                <div class="message-meta">
                    <span>${timeStr}</span>
                </div>
            ` : ''}
        `;

        // Bind action button events
        if (options.messageId) {
            const copyBtn = message.querySelector('.copy-btn');
            if (copyBtn) {
                copyBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.copyMessage(content, copyBtn);
                });
            }

            const editBtn = message.querySelector('.edit-btn');
            if (editBtn) {
                editBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.startEditMessage(message, options.messageId, content);
                });
            }

            const regenerateBtn = message.querySelector('.regenerate-btn');
            if (regenerateBtn) {
                regenerateBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.regenerateMessage(options.messageId);
                });
            }

            const speakBtn = message.querySelector('.speak-btn');
            if (speakBtn) {
                speakBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.speakMessage(content, speakBtn, options.messageId);
                });
            }
        }

        this.elements.messages.appendChild(message);
        return message;
    }

    /**
     * Read a message aloud using text-to-speech.
     * @param {string} content - The message content to speak
     * @param {HTMLElement} btn - The speak button element
     * @param {string} messageId - Optional message ID for caching
     */
    async speakMessage(content, btn, messageId = null) {
        // If currently playing, stop it
        if (this.currentAudio && this.currentSpeakingBtn === btn) {
            this.stopSpeaking();
            return;
        }

        // Stop any other playing audio
        this.stopSpeaking();

        // Check cache first (only if same voice)
        const cacheKey = messageId || content;
        const cached = this.audioCache.get(cacheKey);
        if (cached && cached.voiceId === this.selectedVoiceId) {
            // Use cached audio
            this.playAudioFromCache(cached, btn);
            return;
        }

        // Update button state to loading
        btn.classList.add('loading');
        btn.title = 'Loading...';
        this.currentSpeakingBtn = btn;

        try {
            // Strip markdown for cleaner speech
            const textContent = this.stripMarkdown(content);

            // Get audio from API with selected voice
            const audioBlob = await api.textToSpeech(textContent, this.selectedVoiceId);
            const audioUrl = URL.createObjectURL(audioBlob);

            // Cache the audio
            this.audioCache.set(cacheKey, {
                blob: audioBlob,
                url: audioUrl,
                voiceId: this.selectedVoiceId
            });

            // Create and play audio
            this.currentAudio = new Audio(audioUrl);

            // Update button to playing state
            btn.classList.remove('loading');
            btn.classList.add('speaking');
            btn.title = 'Stop';

            // Handle audio end (don't revoke URL since it's cached)
            this.currentAudio.onended = () => {
                this.stopSpeaking();
            };

            this.currentAudio.onerror = () => {
                this.showToast('Failed to play audio', 'error');
                this.stopSpeaking();
            };

            await this.currentAudio.play();
        } catch (error) {
            console.error('TTS error:', error);
            this.showToast('Failed to generate speech', 'error');
            this.stopSpeaking();
        }
    }

    /**
     * Play audio from cache.
     * @param {Object} cached - Cached audio object with blob and url
     * @param {HTMLElement} btn - The speak button element
     */
    playAudioFromCache(cached, btn) {
        this.currentSpeakingBtn = btn;
        this.currentAudio = new Audio(cached.url);

        btn.classList.add('speaking');
        btn.title = 'Stop';

        this.currentAudio.onended = () => {
            this.stopSpeaking();
        };

        this.currentAudio.onerror = () => {
            this.showToast('Failed to play audio', 'error');
            this.stopSpeaking();
        };

        this.currentAudio.play();
    }

    /**
     * Stop the currently playing audio.
     */
    stopSpeaking() {
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio = null;
        }
        if (this.currentSpeakingBtn) {
            this.currentSpeakingBtn.classList.remove('loading', 'speaking');
            this.currentSpeakingBtn.title = 'Read aloud';
            this.currentSpeakingBtn = null;
        }
    }

    /**
     * Remove regenerate buttons from all existing assistant messages.
     * Called before adding a new assistant message to ensure only the latest has the button.
     */
    removeRegenerateButtons() {
        const regenerateBtns = this.elements.messages.querySelectorAll('.message.assistant .regenerate-btn');
        regenerateBtns.forEach(btn => btn.remove());
    }

    /**
     * Copy message content to clipboard.
     * @param {string} content - The message content to copy
     * @param {HTMLElement} btn - The copy button element
     */
    async copyMessage(content, btn) {
        try {
            await navigator.clipboard.writeText(content);
            btn.classList.add('copied');
            btn.title = 'Copied!';

            // Update icon to checkmark temporarily
            const originalSvg = btn.innerHTML;
            btn.innerHTML = `
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="20 6 9 17 4 12"/>
                </svg>
            `;

            setTimeout(() => {
                btn.classList.remove('copied');
                btn.title = 'Copy to clipboard';
                btn.innerHTML = originalSvg;
            }, 2000);
        } catch (error) {
            console.error('Failed to copy:', error);
            this.showToast('Failed to copy to clipboard', 'error');
        }
    }

    /**
     * Strip markdown formatting from text for cleaner TTS.
     * @param {string} text - Text with markdown
     * @returns {string} - Plain text
     */
    stripMarkdown(text) {
        if (!text) return '';
        return text
            // Remove code blocks
            .replace(/```[\s\S]*?```/g, '')
            // Remove inline code
            .replace(/`[^`]+`/g, '')
            // Remove headers
            .replace(/^#{1,6}\s+/gm, '')
            // Remove bold/italic
            .replace(/\*\*([^*]+)\*\*/g, '$1')
            .replace(/__([^_]+)__/g, '$1')
            .replace(/\*([^*]+)\*/g, '$1')
            .replace(/_([^_]+)_/g, '$1')
            // Remove links, keep text
            .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
            // Remove blockquotes
            .replace(/^>\s+/gm, '')
            // Remove list markers
            .replace(/^[-*]\s+/gm, '')
            .replace(/^\d+\.\s+/gm, '')
            // Remove horizontal rules
            .replace(/^[-*]{3,}$/gm, '')
            // Clean up extra whitespace
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    }

    /**
     * Create a streaming message element that can be updated progressively.
     * @param {string} role - Message role (assistant)
     * @returns {Object} - Object with element, updateContent, and finalize methods
     */
    createStreamingMessage(role, speakerLabel = null) {
        // Hide welcome message
        if (this.elements.welcomeMessage) {
            this.elements.welcomeMessage.style.display = 'none';
        }

        const message = document.createElement('div');
        message.className = `message ${role}`;

        const bubble = document.createElement('div');
        bubble.className = 'message-bubble streaming';

        // Add speaker label for multi-entity conversations
        if (speakerLabel && role === 'assistant') {
            const labelSpan = document.createElement('span');
            labelSpan.className = 'message-speaker-label';
            labelSpan.textContent = speakerLabel;
            bubble.appendChild(labelSpan);
        }

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

    /**
     * Start editing a human message.
     * @param {HTMLElement} messageElement - The message DOM element
     * @param {string} messageId - The message ID
     * @param {string} currentContent - Current message content
     */
    startEditMessage(messageElement, messageId, currentContent) {
        if (this.isLoading) return;

        // Check if already editing
        if (messageElement.classList.contains('editing')) return;

        messageElement.classList.add('editing');
        const bubble = messageElement.querySelector('.message-bubble');
        const originalContent = currentContent;

        // Replace bubble content with edit form
        bubble.innerHTML = `
            <div class="message-edit-form">
                <textarea class="message-edit-textarea">${this.escapeHtml(originalContent)}</textarea>
                <div class="message-edit-actions">
                    <button class="message-edit-btn cancel-edit">Cancel</button>
                    <button class="message-edit-btn save-edit primary">Save & Regenerate</button>
                </div>
            </div>
        `;

        const textarea = bubble.querySelector('.message-edit-textarea');
        const cancelBtn = bubble.querySelector('.cancel-edit');
        const saveBtn = bubble.querySelector('.save-edit');

        // Auto-resize textarea
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 300) + 'px';
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);

        textarea.addEventListener('input', () => {
            textarea.style.height = 'auto';
            textarea.style.height = Math.min(textarea.scrollHeight, 300) + 'px';
        });

        // Cancel editing
        cancelBtn.addEventListener('click', () => {
            messageElement.classList.remove('editing');
            bubble.innerHTML = this.renderMarkdown(originalContent);
            // Re-add the action buttons
            this.rebindMessageActions(messageElement, messageId, originalContent);
        });

        // Save and regenerate
        saveBtn.addEventListener('click', async () => {
            const newContent = textarea.value.trim();
            if (!newContent) {
                this.showToast('Message cannot be empty', 'error');
                return;
            }

            if (newContent === originalContent) {
                // No changes, just cancel
                cancelBtn.click();
                return;
            }

            await this.saveEditAndRegenerate(messageElement, messageId, newContent);
        });

        // Handle Escape key to cancel
        textarea.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                cancelBtn.click();
            }
        });
    }

    /**
     * Re-bind action buttons after canceling edit.
     */
    rebindMessageActions(messageElement, messageId, content) {
        const editBtn = messageElement.querySelector('.edit-btn');
        if (editBtn) {
            editBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.startEditMessage(messageElement, messageId, content);
            });
        }
    }

    /**
     * Save an edited message and regenerate the AI response.
     */
    async saveEditAndRegenerate(messageElement, messageId, newContent) {
        this.isLoading = true;
        this.elements.sendBtn.disabled = true;

        try {
            // Update the message on the server
            const result = await api.updateMessage(messageId, newContent);

            // Update the UI
            messageElement.classList.remove('editing');
            const bubble = messageElement.querySelector('.message-bubble');
            bubble.innerHTML = this.renderMarkdown(newContent);

            // Re-add the action buttons
            const meta = messageElement.querySelector('.message-meta');
            if (meta) {
                const actionsSpan = meta.querySelector('.message-actions');
                if (actionsSpan) {
                    actionsSpan.innerHTML = `
                        <button class="message-action-btn edit-btn" title="Edit message">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                            </svg>
                        </button>
                    `;
                    const editBtn = actionsSpan.querySelector('.edit-btn');
                    editBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        this.startEditMessage(messageElement, messageId, newContent);
                    });
                }
            }

            // Remove the old assistant message from UI if it was deleted
            if (result.deleted_assistant_message_id) {
                const assistantEl = this.elements.messages.querySelector(
                    `[data-message-id="${result.deleted_assistant_message_id}"]`
                );
                if (assistantEl) {
                    assistantEl.remove();
                }
            }

            // Now regenerate the response
            await this.regenerateMessage(messageId);

        } catch (error) {
            this.showToast('Failed to update message', 'error');
            console.error('Failed to update message:', error);

            // Restore original state
            messageElement.classList.remove('editing');
            const bubble = messageElement.querySelector('.message-bubble');
            bubble.innerHTML = this.renderMarkdown(newContent);
        } finally {
            this.isLoading = false;
            this.handleInputChange();
        }
    }

    /**
     * Regenerate an AI response for a given message.
     * @param {string} messageId - ID of the message to regenerate from
     */
    async regenerateMessage(messageId) {
        if (this.isLoading) {
            this.showToast('Please wait for the current operation to complete', 'warning');
            return;
        }

        this.isLoading = true;
        this.elements.sendBtn.disabled = true;

        // Find the assistant message element to replace
        const messageEl = this.elements.messages.querySelector(`[data-message-id="${messageId}"]`);
        let assistantEl = null;

        if (messageEl && messageEl.dataset.role === 'assistant') {
            // Clicked on assistant message directly
            assistantEl = messageEl;
        } else if (messageEl && messageEl.dataset.role === 'human') {
            // Find the next assistant message
            assistantEl = messageEl.nextElementSibling;
            while (assistantEl && assistantEl.dataset.role !== 'assistant') {
                assistantEl = assistantEl.nextElementSibling;
            }
        }

        // Remove the old assistant message from UI
        if (assistantEl) {
            assistantEl.remove();
        }

        // Create streaming message element
        const streamingMessage = this.createStreamingMessage('assistant');

        try {
            await api.regenerateStream(
                {
                    message_id: messageId,
                    model: this.settings.model,
                    temperature: this.settings.temperature,
                    max_tokens: this.settings.maxTokens,
                    system_prompt: this.settings.systemPrompt,
                    verbosity: this.settings.verbosity,
                },
                {
                    onMemories: (data) => {
                        // Handle memory updates same as sendMessage
                        let hasChanges = false;

                        if (data.trimmed_memory_ids && data.trimmed_memory_ids.length > 0) {
                            const trimmedSet = new Set(data.trimmed_memory_ids);
                            this.retrievedMemories = this.retrievedMemories.filter(
                                mem => !trimmedSet.has(mem.id)
                            );
                            hasChanges = true;
                        }

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
                        if (data.content) {
                            streamingMessage.updateContent(data.content);
                        }
                    },
                    onDone: (data) => {
                        streamingMessage.finalize({ showTimestamp: true });

                        if (data.usage) {
                            this.elements.tokenCount.textContent = `Tokens: ${data.usage.input_tokens} in / ${data.usage.output_tokens} out`;
                        }
                    },
                    onStored: (data) => {
                        // Update the message element with the new ID
                        streamingMessage.element.dataset.messageId = data.assistant_message_id;

                        // Remove regenerate buttons from any previous assistant messages
                        this.removeRegenerateButtons();

                        // Add action buttons inside the message bubble
                        const bubble = streamingMessage.element.querySelector('.message-bubble');
                        if (bubble) {
                            const actionsDiv = document.createElement('div');
                            actionsDiv.className = 'message-bubble-actions';
                            const speakBtnHtml = this.ttsEnabled ? `
                                <button class="message-action-btn speak-btn" title="Read aloud">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                                        <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
                                        <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
                                    </svg>
                                </button>
                            ` : '';
                            actionsDiv.innerHTML = `
                                <button class="message-action-btn copy-btn" title="Copy to clipboard">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                                    </svg>
                                </button>
                                ${speakBtnHtml}
                                <button class="message-action-btn regenerate-btn" title="Regenerate response">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <path d="M23 4v6h-6"/>
                                        <path d="M1 20v-6h6"/>
                                        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
                                    </svg>
                                </button>
                            `;
                            bubble.appendChild(actionsDiv);

                            const messageContent = streamingMessage.getContent();
                            const msgId = data.assistant_message_id;

                            const copyBtn = actionsDiv.querySelector('.copy-btn');
                            copyBtn.addEventListener('click', (e) => {
                                e.stopPropagation();
                                this.copyMessage(messageContent, copyBtn);
                            });

                            const regenerateBtn = actionsDiv.querySelector('.regenerate-btn');
                            regenerateBtn.addEventListener('click', (e) => {
                                e.stopPropagation();
                                this.regenerateMessage(data.assistant_message_id);
                            });

                            const speakBtn = actionsDiv.querySelector('.speak-btn');
                            if (speakBtn) {
                                speakBtn.addEventListener('click', (e) => {
                                    e.stopPropagation();
                                    this.speakMessage(messageContent, speakBtn, msgId);
                                });
                            }
                        }

                        this.showToast('Response regenerated', 'success');
                    },
                    onError: (data) => {
                        streamingMessage.element.remove();
                        this.addMessage('assistant', `Error: ${data.error}`, { isError: true });
                        this.showToast('Failed to regenerate response', 'error');
                        console.error('Regeneration error:', data.error);
                    },
                }
            );

            this.scrollToBottom();

        } catch (error) {
            streamingMessage.element.remove();
            this.addMessage('assistant', `Error: ${error.message}`, { isError: true });
            this.showToast('Failed to regenerate response', 'error');
            console.error('Failed to regenerate:', error);
        } finally {
            this.isLoading = false;
            this.handleInputChange();
        }
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
        let meta = '';

        // Handle multi-entity conversations
        if (conversation.conversation_type === 'multi_entity' && conversation.entities) {
            const entityLabels = conversation.entities.map(e => e.label).join(' & ');
            meta = `multi-entity · ${entityLabels}`;
        } else {
            meta = `${conversation.conversation_type} · ${conversation.llm_model_used}`;

            // Add entity label if multiple entities exist
            if (this.entities.length > 1 && conversation.entity_id) {
                const entityLabel = this.getEntityLabel(conversation.entity_id);
                meta += ` · ${entityLabel}`;
            }
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

        this.elements.memoriesContent.innerHTML = this.retrievedMemories.map(mem => {
            const isExpanded = this.expandedMemoryIds.has(mem.id);
            const fullContent = mem.content || mem.content_preview || '';
            const truncatedContent = this.truncateText(fullContent, 100);
            const expandedContent = this.truncateText(fullContent, 3000);
            const displayContent = isExpanded ? expandedContent : truncatedContent;
            const canExpand = fullContent.length > 100;
            const expandHint = canExpand && !isExpanded ? '<span class="memory-item-expand-hint">(click to expand)</span>' : '';

            return `
                <div class="memory-item${isExpanded ? ' expanded' : ''}" data-memory-id="${mem.id}">
                    <div class="memory-item-header">
                        <span>${mem.role}${expandHint}</span>
                        <span>Retrieved ${mem.times_retrieved}× · Score: ${(mem.score || 0).toFixed(2)}</span>
                    </div>
                    <div class="memory-item-content">${this.escapeHtml(displayContent)}</div>
                </div>
            `;
        }).join('');

        // Add click handlers for expanding/collapsing
        this.elements.memoriesContent.querySelectorAll('.memory-item').forEach(item => {
            item.addEventListener('click', () => {
                const memoryId = item.dataset.memoryId;
                if (this.expandedMemoryIds.has(memoryId)) {
                    this.expandedMemoryIds.delete(memoryId);
                } else {
                    this.expandedMemoryIds.add(memoryId);
                }
                this.updateMemoriesPanel();
            });
        });
    }

    truncateText(text, maxLength) {
        if (!text || text.length <= maxLength) return text;
        return text.substring(0, maxLength) + '...';
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
        this.updateTemperatureMax();
        this.elements.modelSelect.value = this.settings.model;
        this.elements.temperatureInput.value = this.settings.temperature;
        this.elements.temperatureNumber.value = this.settings.temperature;
        this.elements.verbositySelect.value = this.settings.verbosity;
        this.elements.maxTokensInput.value = this.settings.maxTokens;
        this.elements.systemPromptInput.value = this.settings.systemPrompt || '';
        this.elements.conversationTypeSelect.value = this.settings.conversationType;
        this.elements.themeSelect.value = this.getCurrentTheme();
        // Update temperature control state based on current model
        this.updateTemperatureControlState();
        // Update verbosity control state based on current model
        this.updateVerbosityControlState();
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
        // Save verbosity value
        this.settings.verbosity = this.elements.verbositySelect.value;

        // Apply theme
        this.setTheme(this.elements.themeSelect.value);

        // Apply voice selection
        if (this.ttsVoices.length > 1) {
            const newVoiceId = this.elements.voiceSelect.value;
            if (newVoiceId !== this.selectedVoiceId) {
                this.selectedVoiceId = newVoiceId;
                // Clear audio cache when voice changes
                this.clearAudioCache();
            }
        }

        this.updateModelIndicator();
        this.hideModal('settingsModal');
        this.showToast('Settings applied', 'success');
    }

    clearAudioCache() {
        // Revoke all cached audio URLs
        for (const [key, cached] of this.audioCache) {
            if (cached.url) {
                URL.revokeObjectURL(cached.url);
            }
        }
        this.audioCache.clear();
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
            const allowReimport = this.elements.importAllowReimport.checked;

            // Call API to preview
            this.importPreviewData = await api.previewExternalConversations({
                content: this.importFileContent,
                entity_id: this.selectedEntityId,
                source: source,
                allow_reimport: allowReimport,
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

        // Create abort controller for cancellation
        this.importAbortController = new AbortController();

        // Show loading state with progress bar and cancel button
        this.elements.importBtn.disabled = true;
        this.elements.importBtn.style.display = 'none';
        this.elements.importCancelBtn.style.display = 'inline-block';
        this.elements.importProgress.style.display = 'block';
        this.elements.importProgressBar.style.width = '0%';
        this.elements.importProgressText.textContent = 'Starting import...';
        this.elements.importStatus.style.display = 'none';

        let conversationsToHistory = 0;

        try {
            const source = this.elements.importSource.value || null;
            const allowReimport = this.elements.importAllowReimport.checked;

            // Call streaming API to import
            await api.importExternalConversationsStream(
                {
                    content: this.importFileContent,
                    entity_id: this.selectedEntityId,
                    source: source,
                    selected_conversations: selectedConversations,
                    allow_reimport: allowReimport,
                },
                {
                    onStart: (data) => {
                        this.elements.importProgressText.textContent =
                            `Importing ${data.total_conversations} conversations (${data.total_messages} messages)...`;
                    },
                    onProgress: (data) => {
                        this.elements.importProgressBar.style.width = `${data.progress_percent}%`;
                        this.elements.importProgressText.textContent =
                            `${data.messages_processed} / ${data.total_messages} messages (${data.progress_percent}%)`;
                    },
                    onDone: (result) => {
                        conversationsToHistory = result.conversations_to_history;

                        // Hide progress, show success
                        this.elements.importProgress.style.display = 'none';
                        this.elements.importStatus.style.display = 'block';
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
                    },
                    onCancelled: (data) => {
                        this.elements.importProgress.style.display = 'none';
                        this.elements.importStatus.style.display = 'block';
                        this.elements.importStatus.className = 'import-status warning';
                        this.elements.importStatus.innerHTML = `<strong>Import cancelled</strong><br>
                            Some messages may have been imported before cancellation.`;
                        this.showToast('Import cancelled', 'warning');
                    },
                    onError: (data) => {
                        this.elements.importProgress.style.display = 'none';
                        this.elements.importStatus.style.display = 'block';
                        this.elements.importStatus.className = 'import-status error';
                        this.elements.importStatus.textContent = `Error: ${data.error}`;
                        this.showToast('Import failed', 'error');
                    },
                },
                this.importAbortController.signal
            );

            // Reload conversations if any were added to history
            if (conversationsToHistory > 0) {
                await this.loadConversations();
            }

        } catch (error) {
            if (error.name !== 'AbortError') {
                this.elements.importProgress.style.display = 'none';
                this.elements.importStatus.style.display = 'block';
                this.elements.importStatus.className = 'import-status error';
                this.elements.importStatus.textContent = `Error: ${error.message}`;
                this.showToast('Import failed', 'error');
                console.error('Import failed:', error);
            }
        } finally {
            this.importAbortController = null;
            this.elements.importBtn.disabled = false;
            this.elements.importBtn.style.display = 'inline-block';
            this.elements.importBtn.textContent = 'Import Selected';
            this.elements.importCancelBtn.style.display = 'none';
        }
    }

    cancelImport() {
        if (this.importAbortController) {
            this.importAbortController.abort();
            this.showToast('Cancelling import...', 'info');
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
            const stats = await api.getMemoryStats(this.selectedEntityId);
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
            const memories = await api.listMemories({ limit: 50, sortBy: 'significance', entityId: this.selectedEntityId });
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

    // =========================================================================
    // Orphan Maintenance
    // =========================================================================

    async checkForOrphans() {
        const statusEl = document.getElementById('orphan-status');
        const detailsEl = document.getElementById('orphan-details');
        const cleanupBtn = document.getElementById('cleanup-orphans-btn');
        const checkBtn = document.getElementById('check-orphans-btn');

        try {
            checkBtn.disabled = true;
            checkBtn.textContent = 'Scanning...';
            statusEl.innerHTML = '<span class="orphan-count">Scanning for orphaned records...</span>';
            detailsEl.style.display = 'none';

            const result = await api.listOrphanedRecords(this.selectedEntityId);

            if (result.orphans_found === 0) {
                statusEl.innerHTML = '<span class="orphan-count orphan-ok">No orphaned records found</span>';
                cleanupBtn.disabled = true;
                this._orphanData = null;
            } else {
                statusEl.innerHTML = `<span class="orphan-count orphan-warning">${result.orphans_found} orphaned record(s) found</span>`;
                cleanupBtn.disabled = false;
                this._orphanData = result;

                // Show details
                detailsEl.style.display = 'block';
                detailsEl.innerHTML = `
                    <div class="orphan-details-header">Orphaned Records:</div>
                    <div class="orphan-list">
                        ${result.orphans.slice(0, 10).map(orphan => `
                            <div class="orphan-item">
                                <span class="orphan-id">${orphan.id.substring(0, 8)}...</span>
                                ${orphan.metadata ? `
                                    <span class="orphan-meta">
                                        ${orphan.metadata.role || 'unknown'} ·
                                        ${orphan.metadata.created_at ? new Date(orphan.metadata.created_at).toLocaleDateString() : 'unknown date'}
                                    </span>
                                    <span class="orphan-preview">${this.escapeHtml(orphan.metadata.content_preview || '')}</span>
                                ` : '<span class="orphan-meta">No metadata available</span>'}
                            </div>
                        `).join('')}
                        ${result.orphans_found > 10 ? `<div class="orphan-more">... and ${result.orphans_found - 10} more</div>` : ''}
                    </div>
                `;
            }
        } catch (error) {
            statusEl.innerHTML = '<span class="orphan-count orphan-error">Error scanning for orphans</span>';
            this.showToast('Failed to check for orphaned records', 'error');
            console.error('Failed to check for orphans:', error);
        } finally {
            checkBtn.disabled = false;
            checkBtn.textContent = 'Check for Orphans';
        }
    }

    async cleanupOrphans() {
        if (!this._orphanData || this._orphanData.orphans_found === 0) {
            this.showToast('No orphans to clean up', 'info');
            return;
        }

        const count = this._orphanData.orphans_found;
        if (!confirm(`Are you sure you want to delete ${count} orphaned record(s) from Pinecone?\n\nThis action cannot be undone.`)) {
            return;
        }

        const statusEl = document.getElementById('orphan-status');
        const cleanupBtn = document.getElementById('cleanup-orphans-btn');
        const checkBtn = document.getElementById('check-orphans-btn');

        try {
            cleanupBtn.disabled = true;
            checkBtn.disabled = true;
            cleanupBtn.textContent = 'Cleaning up...';
            statusEl.innerHTML = '<span class="orphan-count">Deleting orphaned records...</span>';

            const result = await api.cleanupOrphanedRecords(this.selectedEntityId, false);

            if (result.errors && result.errors.length > 0) {
                statusEl.innerHTML = `<span class="orphan-count orphan-warning">Cleaned ${result.orphans_deleted} records with errors</span>`;
                this.showToast(`Cleanup completed with errors: ${result.errors.join(', ')}`, 'warning');
            } else {
                statusEl.innerHTML = `<span class="orphan-count orphan-ok">Successfully deleted ${result.orphans_deleted} orphaned record(s)</span>`;
                this.showToast(`Cleaned up ${result.orphans_deleted} orphaned records`, 'success');
            }

            // Hide details and reset
            document.getElementById('orphan-details').style.display = 'none';
            this._orphanData = null;
            cleanupBtn.disabled = true;
        } catch (error) {
            statusEl.innerHTML = '<span class="orphan-count orphan-error">Error during cleanup</span>';
            this.showToast('Failed to clean up orphaned records', 'error');
            console.error('Failed to cleanup orphans:', error);
        } finally {
            checkBtn.disabled = false;
            cleanupBtn.textContent = 'Clean Up Orphans';
        }
    }

    // =========================================================================
    // Voice Cloning (XTTS)
    // =========================================================================

    showVoiceCloneModal() {
        // Reset form
        this.elements.voiceCloneFile.value = '';
        this.elements.voiceCloneName.value = '';
        this.elements.voiceCloneDescription.value = '';
        // Reset voice parameters to defaults
        this.elements.voiceCloneTemperature.value = '0.75';
        this.elements.voiceCloneSpeed.value = '1.0';
        this.elements.voiceCloneLengthPenalty.value = '1.0';
        this.elements.voiceCloneRepetitionPenalty.value = '5.0';
        this.elements.voiceCloneStatus.style.display = 'none';
        this.elements.voiceCloneStatus.className = 'voice-clone-status';
        this.elements.createVoiceCloneBtn.disabled = true;

        this.elements.voiceCloneModal.classList.add('active');
    }

    hideVoiceCloneModal() {
        this.elements.voiceCloneModal.classList.remove('active');
    }

    updateVoiceCloneButton() {
        const hasFile = this.elements.voiceCloneFile.files.length > 0;
        const hasName = this.elements.voiceCloneName.value.trim().length > 0;
        this.elements.createVoiceCloneBtn.disabled = !(hasFile && hasName);
    }

    async createVoiceClone() {
        const file = this.elements.voiceCloneFile.files[0];
        const name = this.elements.voiceCloneName.value.trim();
        const description = this.elements.voiceCloneDescription.value.trim();

        if (!file || !name) {
            return;
        }

        // Get voice synthesis parameters
        const options = {
            temperature: parseFloat(this.elements.voiceCloneTemperature.value) || 0.75,
            length_penalty: parseFloat(this.elements.voiceCloneLengthPenalty.value) || 1.0,
            repetition_penalty: parseFloat(this.elements.voiceCloneRepetitionPenalty.value) || 5.0,
            speed: parseFloat(this.elements.voiceCloneSpeed.value) || 1.0,
        };

        // Show loading status
        this.elements.voiceCloneStatus.textContent = 'Creating voice... This may take a moment.';
        this.elements.voiceCloneStatus.className = 'voice-clone-status loading';
        this.elements.voiceCloneStatus.style.display = 'block';
        this.elements.createVoiceCloneBtn.disabled = true;

        try {
            const result = await api.cloneVoice(file, name, description, options);

            if (result.success) {
                // Show success
                this.elements.voiceCloneStatus.textContent = `Voice "${name}" created successfully!`;
                this.elements.voiceCloneStatus.className = 'voice-clone-status success';

                // Refresh TTS status to get updated voice list
                await this.checkTTSStatus();

                // Close modal after a short delay
                setTimeout(() => {
                    this.hideVoiceCloneModal();
                    this.showToast(`Voice "${name}" created`, 'success');
                }, 1500);
            } else {
                throw new Error(result.message || 'Failed to create voice');
            }
        } catch (error) {
            console.error('Voice cloning failed:', error);
            this.elements.voiceCloneStatus.textContent = error.message || 'Failed to create voice. Please try again.';
            this.elements.voiceCloneStatus.className = 'voice-clone-status error';
            this.elements.createVoiceCloneBtn.disabled = false;
        }
    }

    async deleteVoice(voiceId) {
        const voice = this.ttsVoices.find(v => v.voice_id === voiceId);
        const voiceName = voice ? voice.label : 'this voice';

        if (!confirm(`Delete ${voiceName}? This cannot be undone.`)) {
            return;
        }

        try {
            await api.deleteTTSVoice(voiceId);
            this.showToast(`Voice "${voiceName}" deleted`, 'success');

            // Refresh TTS status
            await this.checkTTSStatus();
        } catch (error) {
            console.error('Failed to delete voice:', error);
            this.showToast('Failed to delete voice', 'error');
        }
    }

    showVoiceEditModal(voiceId) {
        const voice = this.ttsVoices.find(v => v.voice_id === voiceId);
        if (!voice) {
            this.showToast('Voice not found', 'error');
            return;
        }

        // Populate form with current values
        this.elements.voiceEditId.value = voice.voice_id;
        this.elements.voiceEditName.value = voice.label || '';
        this.elements.voiceEditDescription.value = voice.description || '';
        this.elements.voiceEditTemperature.value = voice.temperature ?? 0.75;
        this.elements.voiceEditSpeed.value = voice.speed ?? 1.0;
        this.elements.voiceEditLengthPenalty.value = voice.length_penalty ?? 1.0;
        this.elements.voiceEditRepetitionPenalty.value = voice.repetition_penalty ?? 5.0;

        // Reset status
        this.elements.voiceEditStatus.style.display = 'none';
        this.elements.voiceEditStatus.className = 'voice-clone-status';
        this.elements.saveVoiceEditBtn.disabled = false;

        this.elements.voiceEditModal.classList.add('active');
    }

    hideVoiceEditModal() {
        this.elements.voiceEditModal.classList.remove('active');
    }

    async saveVoiceEdit() {
        const voiceId = this.elements.voiceEditId.value;
        if (!voiceId) return;

        const updates = {
            label: this.elements.voiceEditName.value.trim() || null,
            description: this.elements.voiceEditDescription.value.trim() || null,
            temperature: parseFloat(this.elements.voiceEditTemperature.value) || null,
            speed: parseFloat(this.elements.voiceEditSpeed.value) || null,
            length_penalty: parseFloat(this.elements.voiceEditLengthPenalty.value) || null,
            repetition_penalty: parseFloat(this.elements.voiceEditRepetitionPenalty.value) || null,
        };

        // Show loading status
        this.elements.voiceEditStatus.textContent = 'Saving changes...';
        this.elements.voiceEditStatus.className = 'voice-clone-status loading';
        this.elements.voiceEditStatus.style.display = 'block';
        this.elements.saveVoiceEditBtn.disabled = true;

        try {
            const result = await api.updateVoice(voiceId, updates);

            if (result.success) {
                this.elements.voiceEditStatus.textContent = 'Voice updated successfully!';
                this.elements.voiceEditStatus.className = 'voice-clone-status success';

                // Refresh TTS status
                await this.checkTTSStatus();

                setTimeout(() => {
                    this.hideVoiceEditModal();
                    this.showToast('Voice settings updated', 'success');
                }, 1000);
            } else {
                throw new Error(result.message || 'Failed to update voice');
            }
        } catch (error) {
            console.error('Failed to update voice:', error);
            this.elements.voiceEditStatus.textContent = error.message || 'Failed to update voice. Please try again.';
            this.elements.voiceEditStatus.className = 'voice-clone-status error';
            this.elements.saveVoiceEditBtn.disabled = false;
        }
    }

    showArchiveModal() {
        if (!this.currentConversationId) return;
        const conv = this.conversations.find(c => c.id === this.currentConversationId);
        this.showArchiveModalForConversation(this.currentConversationId, conv?.title);
    }

    showArchiveModalForConversation(conversationId, conversationTitle) {
        this.pendingArchiveId = conversationId;
        // Update modal text to show which conversation
        const modalBody = document.querySelector('#archive-modal .modal-body');
        modalBody.innerHTML = `
            <p><strong>${this.escapeHtml(conversationTitle || 'Untitled')}</strong></p>
            <p>This conversation will be hidden from the main list and its memories will be excluded from retrieval.</p>
            <p>You can restore it later from the Archived section.</p>
        `;
        this.closeAllDropdowns();
        this.showModal('archiveModal');
    }

    async archiveConversation() {
        const conversationId = this.pendingArchiveId || this.currentConversationId;
        if (!conversationId) return;

        try {
            await api.archiveConversation(conversationId);

            // Remove from list
            this.conversations = this.conversations.filter(c => c.id !== conversationId);

            // Clear current view if we archived the active conversation
            if (conversationId === this.currentConversationId) {
                this.currentConversationId = null;
                this.retrievedMemories = [];
                this.expandedMemoryIds.clear();
                this.clearMessages();
                this.updateMemoriesPanel();
                this.elements.conversationTitle.textContent = 'Select a conversation';
                this.elements.conversationMeta.textContent = '';
            }

            this.renderConversationList();
            this.hideModal('archiveModal');
            this.pendingArchiveId = null;
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
                        <button class="delete-btn" onclick="app.showDeleteModal('${conv.id}', '${this.escapeHtml(conv.title || 'Untitled').replace(/'/g, "\\'")}')">Delete</button>
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

    showDeleteModal(conversationId, conversationTitle) {
        this.pendingDeleteId = conversationId;
        this.elements.deleteConversationTitle.textContent = conversationTitle;
        this.showModal('deleteModal');
    }

    async deleteConversation() {
        const conversationId = this.pendingDeleteId;
        if (!conversationId) return;

        try {
            await api.deleteConversation(conversationId);
            await this.loadArchivedConversations();
            this.hideModal('deleteModal');
            this.pendingDeleteId = null;
            this.showToast('Conversation permanently deleted', 'success');
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
        // Wrap consecutive list items in <ul> (use .*? to allow HTML tags like <strong> inside)
        html = html.replace(/(<li class="md-list-item">.*?<\/li>\n?)+/g, '<ul class="md-list">$&</ul>');

        // Ordered lists (1. item)
        html = html.replace(/^\d+\. (.+)$/gm, '<li class="md-list-item-ordered">$1</li>');
        // Wrap consecutive ordered list items in <ol> (use .*? to allow HTML tags like <strong> inside)
        html = html.replace(/(<li class="md-list-item-ordered">.*?<\/li>\n?)+/g, '<ol class="md-list">$&</ol>');

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
