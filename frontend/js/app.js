/**
 * Here I Am - Main Application
 */
console.log('[HERE-I-AM] App.js loaded - version with multi-entity debug logging');

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
            systemPrompt: null,  // Current entity's system prompt (for display)
            conversationType: 'normal',
            verbosity: 'medium',
            researcherName: localStorage.getItem('researcher_name') || '',  // Custom display name for user
        };

        // Per-entity system prompts: { entity_id: system_prompt, ... }
        // Stores user-defined system prompts for each entity
        this.entitySystemPrompts = {};
        this.isLoading = false;
        this.retrievedMemories = [];  // For single-entity mode
        this.retrievedMemoriesByEntity = {};  // For multi-entity mode: { entityId: [...memories] }
        this.expandedMemoryIds = new Set();

        // Multi-entity state
        this.isMultiEntityMode = false;
        this.currentConversationEntities = [];  // Entities in current conversation
        this.pendingResponderId = null;  // Entity selected to respond next
        this.pendingActionAfterEntitySelection = null;  // 'createConversation' or 'sendMessage'
        this.pendingMessageForEntitySelection = null;  // Message to send after entity selection
        this.pendingRegenerateMessageId = null;  // Message ID to regenerate (for multi-entity regeneration)
        this.constructedAt = Date.now();  // Used to detect browser form restoration

        // Request tracking to prevent race conditions
        this.loadConversationsRequestId = 0;  // Incremented on each loadConversations call

        // Debug tracking for conversation persistence issue
        this.lastCreatedConversation = null;  // { id, entity_id, createdAt }

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
            renameModal: document.getElementById('rename-modal'),
            renameInput: document.getElementById('rename-input'),
            deleteModal: document.getElementById('delete-modal'),
            deleteConversationTitle: document.getElementById('delete-conversation-title'),
            archivedModal: document.getElementById('archived-modal'),
            archivedList: document.getElementById('archived-list'),

            // Settings
            researcherNameInput: document.getElementById('researcher-name-input'),
            modelSelect: document.getElementById('model-select'),
            temperatureInput: document.getElementById('temperature-input'),
            temperatureNumber: document.getElementById('temperature-number'),
            verbositySelect: document.getElementById('verbosity-select'),
            verbosityGroup: document.getElementById('verbosity-group'),
            maxTokensInput: document.getElementById('max-tokens-input'),
            presetSelect: document.getElementById('preset-select'),
            systemPromptInput: document.getElementById('system-prompt-input'),
            systemPromptLabel: document.getElementById('system-prompt-label'),
            systemPromptHelp: document.getElementById('system-prompt-help'),
            conversationTypeSelect: document.getElementById('conversation-type-select'),

            // Buttons
            settingsBtn: document.getElementById('settings-btn'),
            memoriesBtn: document.getElementById('memories-btn'),
            archivedBtn: document.getElementById('archived-btn'),
            continueBtn: document.getElementById('continue-btn'),
            stopBtn: document.getElementById('stop-btn'),
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

            // StyleTTS 2 Parameters (Settings Modal)
            styletts2ParamsGroup: document.getElementById('styletts2-params-group'),
            styletts2Alpha: document.getElementById('styletts2-alpha'),
            styletts2Beta: document.getElementById('styletts2-beta'),
            styletts2DiffusionSteps: document.getElementById('styletts2-diffusion-steps'),
            styletts2EmbeddingScale: document.getElementById('styletts2-embedding-scale'),
            styletts2Speed: document.getElementById('styletts2-speed'),

            // Voice Edit Modal
            voiceEditModal: document.getElementById('voice-edit-modal'),
            voiceEditId: document.getElementById('voice-edit-id'),
            voiceEditName: document.getElementById('voice-edit-name'),
            voiceEditDescription: document.getElementById('voice-edit-description'),
            // XTTS parameters
            xttsParamsSection: document.getElementById('xtts-params-section'),
            voiceEditTemperature: document.getElementById('voice-edit-temperature'),
            voiceEditSpeed: document.getElementById('voice-edit-speed'),
            voiceEditLengthPenalty: document.getElementById('voice-edit-length-penalty'),
            voiceEditRepetitionPenalty: document.getElementById('voice-edit-repetition-penalty'),
            // StyleTTS 2 parameters
            styletts2ParamsSection: document.getElementById('styletts2-params-section'),
            voiceEditAlpha: document.getElementById('voice-edit-alpha'),
            voiceEditBeta: document.getElementById('voice-edit-beta'),
            voiceEditDiffusionSteps: document.getElementById('voice-edit-diffusion-steps'),
            voiceEditEmbeddingScale: document.getElementById('voice-edit-embedding-scale'),
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

            // Attachments
            attachBtn: document.getElementById('attach-btn'),
            fileInput: document.getElementById('file-input'),
            attachmentPreview: document.getElementById('attachment-preview'),
            attachmentList: document.getElementById('attachment-list'),

            // GitHub Integration
            githubNotConfigured: document.getElementById('github-not-configured'),
            githubReposContainer: document.getElementById('github-repos-container'),
            githubReposList: document.getElementById('github-repos-list'),
            githubRateLimits: document.getElementById('github-rate-limits'),
            refreshRateLimitsBtn: document.getElementById('refresh-rate-limits-btn'),

            // Go Game
            goGameBtn: document.getElementById('go-game-btn'),
            goGameModal: document.getElementById('go-game-modal'),
            closeGoGameBtn: document.getElementById('close-go-game'),
            goBoard: document.getElementById('go-board'),
            goCurrentPlayer: document.getElementById('go-current-player'),
            goMoveCount: document.getElementById('go-move-count'),
            goBlackCaptures: document.getElementById('go-black-captures'),
            goWhiteCaptures: document.getElementById('go-white-captures'),
            goGameStatus: document.getElementById('go-game-status'),
            goMoveInput: document.getElementById('go-move-input'),
            goPlayMoveBtn: document.getElementById('go-play-move-btn'),
            goPassBtn: document.getElementById('go-pass-btn'),
            goResignBtn: document.getElementById('go-resign-btn'),
            goScoreBtn: document.getElementById('go-score-btn'),
            goBoardSize: document.getElementById('go-board-size'),
            goKomi: document.getElementById('go-komi'),
            goScoring: document.getElementById('go-scoring'),
            goNewGameBtn: document.getElementById('go-new-game-btn'),
        };

        // Attachment state
        this.pendingAttachments = {
            images: [],  // { data: base64, media_type: string, filename: string, previewUrl: string }
            files: [],   // { filename: string, content: string, content_type: 'text'|'base64', media_type?: string }
        };

        // Import state
        this.importFileContent = null;
        this.importPreviewData = null;
        this.importAbortController = null;

        // Stream abort controller for stop generation
        this.streamAbortController = null;

        // Voice dictation state
        this.recognition = null;
        this.isRecording = false;
        this.textBeforeDictation = '';
        // STT/Dictation mode state
        this.dictationMode = 'browser';  // 'whisper' or 'browser'
        this.whisperAvailable = false;
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.isTranscribing = false;  // True while waiting for Whisper response

        // TTS state
        this.ttsEnabled = false;
        this.ttsProvider = 'none'; // 'none', 'elevenlabs', or 'xtts'
        this.ttsVoices = [];
        this.selectedVoiceId = null;
        this.currentAudio = null;
        this.currentSpeakingBtn = null;
        this.audioCache = new Map(); // Cache: messageId -> { blob, url, voiceId }
        this.localTtsServerHealthy = false;

        // Go game state
        this.currentGoGameId = null;
        this.currentGoGame = null;

        this.init();
    }

    async init() {
        this.loadTheme();
        this.bindEvents();
        await this.checkSTTStatus();  // Check STT before init (sets dictation mode)
        this.initVoiceDictation();    // Then initialize appropriate dictation
        await this.loadEntities();
        this.loadEntitySystemPromptsFromStorage();  // Load saved system prompts
        await this.loadConversations();
        await this.loadConfig();
        await this.checkTTSStatus();
        this.updateModelIndicator();
    }

    loadEntitySystemPromptsFromStorage() {
        // Load entitySystemPrompts from localStorage
        try {
            const saved = localStorage.getItem('entitySystemPrompts');
            if (saved) {
                this.entitySystemPrompts = JSON.parse(saved);
                // Update current system prompt if we have a selected entity
                if (this.selectedEntityId && this.selectedEntityId !== 'multi-entity') {
                    if (this.entitySystemPrompts[this.selectedEntityId] !== undefined) {
                        this.settings.systemPrompt = this.entitySystemPrompts[this.selectedEntityId];
                    }
                }
            }
        } catch (error) {
            console.warn('Failed to load entitySystemPrompts from localStorage:', error);
        }
    }

    saveEntitySystemPromptsToStorage() {
        // Save entitySystemPrompts to localStorage
        try {
            localStorage.setItem('entitySystemPrompts', JSON.stringify(this.entitySystemPrompts));
        } catch (error) {
            console.warn('Failed to save entitySystemPrompts to localStorage:', error);
        }
    }

    loadSelectedVoiceFromStorage() {
        // Load selected voice ID from localStorage
        try {
            return localStorage.getItem('here-i-am-voice-id');
        } catch (error) {
            console.warn('Failed to load voice selection from localStorage:', error);
            return null;
        }
    }

    saveSelectedVoiceToStorage() {
        // Save selected voice ID to localStorage
        try {
            if (this.selectedVoiceId) {
                localStorage.setItem('here-i-am-voice-id', this.selectedVoiceId);
            }
        } catch (error) {
            console.warn('Failed to save voice selection to localStorage:', error);
        }
    }

    async checkSTTStatus() {
        try {
            const status = await api.getSTTStatus();
            
            // Store Whisper availability
            this.whisperAvailable = status.configured && status.server_healthy;
            
            // Set dictation mode based on server response
            // effective_mode is calculated server-side based on config + health
            if (status.effective_mode === 'whisper') {
                this.dictationMode = 'whisper';
                console.log('[STT] Using Whisper for dictation (model:', status.model, ', device:', status.device, ')');
            } else if (status.effective_mode === 'browser') {
                this.dictationMode = 'browser';
                console.log('[STT] Using browser Web Speech API for dictation');
            } else {
                // 'none' - Whisper required but unavailable
                this.dictationMode = 'none';
                console.warn('[STT] Dictation unavailable - Whisper server not running');
            }
        } catch (error) {
            console.warn('STT status check failed, falling back to browser:', error);
            this.dictationMode = 'browser';
            this.whisperAvailable = false;
        }
    }

    async checkTTSStatus() {
        try {
            const status = await api.getTTSStatus();
            this.ttsEnabled = status.configured;
            this.ttsProvider = status.provider || 'none';

            if (status.configured) {
                this.ttsVoices = status.voices || [];

                // Try to restore saved voice selection, fall back to default
                const savedVoiceId = this.loadSelectedVoiceFromStorage();
                const savedVoiceExists = savedVoiceId && this.ttsVoices.some(v => v.voice_id === savedVoiceId);
                this.selectedVoiceId = savedVoiceExists ? savedVoiceId : status.default_voice_id;

                // Track local TTS server health (XTTS or StyleTTS 2)
                if (this.ttsProvider === 'xtts' || this.ttsProvider === 'styletts2') {
                    this.localTtsServerHealthy = status.server_healthy || false;
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

            // Set provider name and status
            if (this.ttsProvider === 'styletts2') {
                this.elements.ttsProviderName.textContent = 'StyleTTS 2 (Local)';
                if (this.localTtsServerHealthy) {
                    this.elements.ttsProviderStatus.textContent = 'Connected';
                    this.elements.ttsProviderStatus.className = 'tts-status healthy';
                } else {
                    this.elements.ttsProviderStatus.textContent = 'Server Unavailable';
                    this.elements.ttsProviderStatus.className = 'tts-status unhealthy';
                }
            } else if (this.ttsProvider === 'xtts') {
                this.elements.ttsProviderName.textContent = 'XTTS v2 (Local)';
                if (this.localTtsServerHealthy) {
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

        // Show voice cloning options for local TTS providers (XTTS or StyleTTS 2)
        if (this.ttsProvider === 'xtts' || this.ttsProvider === 'styletts2') {
            this.elements.voiceCloneGroup.style.display = 'block';
            this.elements.voiceManageGroup.style.display = 'block';
            this.updateVoiceList();
        } else {
            this.elements.voiceCloneGroup.style.display = 'none';
            this.elements.voiceManageGroup.style.display = 'none';
        }

        // Show StyleTTS 2 parameters section only for StyleTTS 2 provider
        if (this.ttsProvider === 'styletts2') {
            this.elements.styletts2ParamsGroup.style.display = 'block';
            // Load saved parameters
            this.loadStyleTTS2Settings();
        } else {
            this.elements.styletts2ParamsGroup.style.display = 'none';
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
        // Update the voice management list for local TTS providers
        if (this.ttsProvider !== 'xtts' && this.ttsProvider !== 'styletts2') {
            this.elements.voiceList.innerHTML = '';
            return;
        }

        if (this.ttsVoices.length === 0) {
            this.elements.voiceList.innerHTML = '<div class="voice-list-empty">No voices configured. Clone a voice to get started.</div>';
            return;
        }

        this.elements.voiceList.innerHTML = this.ttsVoices.map(voice => {
            // Show provider-specific parameters
            let paramsDisplay;
            if (this.ttsProvider === 'styletts2') {
                paramsDisplay = `α:${voice.alpha ?? 0.3} β:${voice.beta ?? 0.7}`;
            } else {
                paramsDisplay = `T:${voice.temperature ?? 0.75} S:${voice.speed ?? 1.0}`;
            }
            return `
            <div class="voice-item" data-voice-id="${voice.voice_id}">
                <div class="voice-item-info">
                    <span class="voice-item-name">${voice.label}</span>
                    ${voice.description ? `<span class="voice-item-description">${voice.description}</span>` : ''}
                    <span class="voice-item-params">${paramsDisplay}</span>
                </div>
                <div class="voice-item-actions">
                    <button class="voice-item-btn settings" title="Edit voice settings" data-action="edit">Edit</button>
                    <button class="voice-item-btn delete" title="Delete voice" data-action="delete">Delete</button>
                </div>
            </div>
        `}).join('');

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
        this.elements.stopBtn.addEventListener('click', () => this.stopGeneration());
        this.elements.voiceBtn.addEventListener('click', () => this.toggleVoiceDictation());

        // Attachments
        if (this.elements.attachBtn) {
            this.elements.attachBtn.addEventListener('click', () => this.elements.fileInput?.click());
        }
        if (this.elements.fileInput) {
            this.elements.fileInput.addEventListener('change', (e) => this.handleFileSelect(e));
        }
        // Drag and drop on input area
        const inputArea = document.querySelector('.input-area');
        if (inputArea) {
            inputArea.addEventListener('dragover', (e) => this.handleDragOver(e));
            inputArea.addEventListener('dragleave', (e) => this.handleDragLeave(e));
            inputArea.addEventListener('drop', (e) => this.handleDrop(e));
        }

        // Conversation management
        this.elements.newConversationBtn.addEventListener('click', () => this.createNewConversation());

        // Header buttons
        this.elements.continueBtn.addEventListener('click', () => this.continueMultiEntityConversation());
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

        // Rename modal
        document.getElementById('close-rename').addEventListener('click', () => this.hideModal('renameModal'));
        document.getElementById('cancel-rename').addEventListener('click', () => this.hideModal('renameModal'));
        document.getElementById('confirm-rename').addEventListener('click', () => this.renameConversation());
        this.elements.renameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this.renameConversation();
            }
        });

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

        // GitHub Integration
        if (this.elements.refreshRateLimitsBtn) {
            this.elements.refreshRateLimitsBtn.addEventListener('click', () => this.loadGitHubRateLimits());
        }

        // Go Game
        if (this.elements.goGameBtn) {
            this.elements.goGameBtn.addEventListener('click', () => this.openGoGameModal());
        }
        if (this.elements.closeGoGameBtn) {
            this.elements.closeGoGameBtn.addEventListener('click', () => this.closeGoGameModal());
        }
        if (this.elements.goPlayMoveBtn) {
            this.elements.goPlayMoveBtn.addEventListener('click', () => this.playGoMove());
        }
        if (this.elements.goMoveInput) {
            this.elements.goMoveInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') this.playGoMove();
            });
        }
        if (this.elements.goPassBtn) {
            this.elements.goPassBtn.addEventListener('click', () => this.passGoTurn());
        }
        if (this.elements.goResignBtn) {
            this.elements.goResignBtn.addEventListener('click', () => this.resignGoGame());
        }
        if (this.elements.goScoreBtn) {
            this.elements.goScoreBtn.addEventListener('click', () => this.scoreGoGame());
        }
        if (this.elements.goNewGameBtn) {
            this.elements.goNewGameBtn.addEventListener('click', () => this.createNewGoGame());
        }

        // Global Escape key to close modals
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.closeActiveModal();
            }
        });
    }

    handleInputChange() {
        const hasContent = this.elements.messageInput.value.trim().length > 0;
        const hasAttachments = this.hasAttachments();
        // Can send if there's text content OR attachments (or both)
        this.elements.sendBtn.disabled = (!hasContent && !hasAttachments) || this.isLoading;

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

    // ==================== ATTACHMENT HANDLING ====================

    /**
     * Handle file selection from the file input.
     */
    handleFileSelect(e) {
        const files = Array.from(e.target.files);
        if (files.length > 0) {
            this.processFiles(files);
        }
        // Reset input so the same file can be selected again
        e.target.value = '';
    }

    /**
     * Handle drag over the input area.
     */
    handleDragOver(e) {
        e.preventDefault();
        e.stopPropagation();
        e.currentTarget.classList.add('drag-over');
    }

    /**
     * Handle drag leave from the input area.
     */
    handleDragLeave(e) {
        e.preventDefault();
        e.stopPropagation();
        e.currentTarget.classList.remove('drag-over');
    }

    /**
     * Handle file drop on the input area.
     */
    handleDrop(e) {
        e.preventDefault();
        e.stopPropagation();
        e.currentTarget.classList.remove('drag-over');

        const files = Array.from(e.dataTransfer.files);
        if (files.length > 0) {
            this.processFiles(files);
        }
    }

    /**
     * Process selected files, separating images from text files.
     */
    async processFiles(files) {
        const MAX_SIZE = 5 * 1024 * 1024; // 5MB
        const allowedImageTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
        const allowedTextExtensions = ['.txt', '.md', '.py', '.js', '.ts', '.json', '.yaml', '.yml', '.html', '.css', '.xml', '.csv', '.log'];
        const binaryExtensions = ['.pdf', '.docx'];

        for (const file of files) {
            // Check file size
            if (file.size > MAX_SIZE) {
                this.showToast(`File "${file.name}" exceeds 5MB limit`, 'error');
                continue;
            }

            const ext = '.' + file.name.split('.').pop().toLowerCase();

            // Process image files
            if (allowedImageTypes.includes(file.type)) {
                try {
                    const { data, previewUrl } = await this.readFileAsBase64(file);
                    this.pendingAttachments.images.push({
                        data: data,
                        media_type: file.type,
                        filename: file.name,
                        previewUrl: previewUrl,
                    });
                } catch (error) {
                    this.showToast(`Failed to read image "${file.name}"`, 'error');
                    console.error('Failed to read image:', error);
                }
            }
            // Process text files
            else if (allowedTextExtensions.includes(ext)) {
                try {
                    const content = await this.readFileAsText(file);
                    this.pendingAttachments.files.push({
                        filename: file.name,
                        content: content,
                        content_type: 'text',
                        media_type: file.type || 'text/plain',
                    });
                } catch (error) {
                    this.showToast(`Failed to read file "${file.name}"`, 'error');
                    console.error('Failed to read file:', error);
                }
            }
            // Process PDF/DOCX (send as base64 for server-side extraction)
            else if (binaryExtensions.includes(ext)) {
                try {
                    const { data } = await this.readFileAsBase64(file);
                    this.pendingAttachments.files.push({
                        filename: file.name,
                        content: data,
                        content_type: 'base64',
                        media_type: file.type,
                    });
                } catch (error) {
                    this.showToast(`Failed to read file "${file.name}"`, 'error');
                    console.error('Failed to read file:', error);
                }
            }
            else {
                this.showToast(`Unsupported file type: ${file.name}`, 'error');
            }
        }

        this.updateAttachmentPreview();
        this.handleInputChange();
    }

    /**
     * Read a file as base64-encoded data.
     * Returns an object with data (base64 without prefix) and previewUrl.
     */
    readFileAsBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                const result = reader.result;
                // Remove data URL prefix (e.g., "data:image/jpeg;base64,")
                const base64Data = result.split(',')[1];
                resolve({
                    data: base64Data,
                    previewUrl: result, // Full data URL for preview
                });
            };
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(file);
        });
    }

    /**
     * Read a file as text.
     */
    readFileAsText(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(reader.error);
            reader.readAsText(file);
        });
    }

    /**
     * Update the attachment preview area.
     */
    updateAttachmentPreview() {
        const hasAttachments = this.pendingAttachments.images.length > 0 || this.pendingAttachments.files.length > 0;

        if (!hasAttachments) {
            this.elements.attachmentPreview.style.display = 'none';
            this.elements.attachmentList.innerHTML = '';
            return;
        }

        this.elements.attachmentPreview.style.display = 'flex';

        let html = '';

        // Images
        for (let i = 0; i < this.pendingAttachments.images.length; i++) {
            const img = this.pendingAttachments.images[i];
            html += `
                <div class="attachment-item image" data-type="image" data-index="${i}">
                    <img src="${img.previewUrl}" alt="${img.filename}" title="${img.filename}">
                    <button class="attachment-remove" title="Remove">&times;</button>
                </div>
            `;
        }

        // Files
        for (let i = 0; i < this.pendingAttachments.files.length; i++) {
            const file = this.pendingAttachments.files[i];
            const ext = file.filename.split('.').pop().toUpperCase();
            html += `
                <div class="attachment-item file" data-type="file" data-index="${i}">
                    <span class="attachment-file-icon">${ext}</span>
                    <span class="attachment-file-name" title="${file.filename}">${file.filename}</span>
                    <button class="attachment-remove" title="Remove">&times;</button>
                </div>
            `;
        }

        this.elements.attachmentList.innerHTML = html;

        // Add remove handlers
        this.elements.attachmentList.querySelectorAll('.attachment-remove').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const item = e.target.closest('.attachment-item');
                const type = item.dataset.type;
                const index = parseInt(item.dataset.index, 10);
                this.removeAttachment(type, index);
            });
        });
    }

    /**
     * Remove an attachment by type and index.
     */
    removeAttachment(type, index) {
        if (type === 'image') {
            // Revoke object URL to free memory
            const img = this.pendingAttachments.images[index];
            if (img && img.previewUrl && img.previewUrl.startsWith('blob:')) {
                URL.revokeObjectURL(img.previewUrl);
            }
            this.pendingAttachments.images.splice(index, 1);
        } else if (type === 'file') {
            this.pendingAttachments.files.splice(index, 1);
        }
        this.updateAttachmentPreview();
        this.handleInputChange();
    }

    /**
     * Clear all pending attachments.
     */
    clearAttachments() {
        // Revoke object URLs
        for (const img of this.pendingAttachments.images) {
            if (img.previewUrl && img.previewUrl.startsWith('blob:')) {
                URL.revokeObjectURL(img.previewUrl);
            }
        }
        this.pendingAttachments = { images: [], files: [] };
        this.updateAttachmentPreview();
    }

    /**
     * Check if there are any pending attachments.
     */
    hasAttachments() {
        return this.pendingAttachments.images.length > 0 || this.pendingAttachments.files.length > 0;
    }

    /**
     * Get attachments formatted for the API request.
     * Returns null if no attachments, otherwise the attachments object.
     */
    getAttachmentsForRequest() {
        if (!this.hasAttachments()) {
            return null;
        }

        return {
            images: this.pendingAttachments.images.map(img => ({
                data: img.data,
                media_type: img.media_type,
                filename: img.filename,
            })),
            files: this.pendingAttachments.files.map(f => ({
                filename: f.filename,
                content: f.content,
                content_type: f.content_type,
                media_type: f.media_type,
            })),
        };
    }

    /**
     * Build display content that includes attachment indicators.
     * Attachments are ephemeral and not stored, so we add visual indicators.
     */
    buildDisplayContentWithAttachments(textContent, attachments) {
        if (!attachments) return textContent;

        const parts = [];

        // Add attachment indicator
        const imageCount = attachments.images?.length || 0;
        const fileCount = attachments.files?.length || 0;

        if (imageCount > 0 || fileCount > 0) {
            const indicators = [];
            if (imageCount > 0) {
                indicators.push(`${imageCount} image${imageCount > 1 ? 's' : ''}`);
            }
            if (fileCount > 0) {
                const fileNames = attachments.files.map(f => f.filename).join(', ');
                indicators.push(`${fileCount} file${fileCount > 1 ? 's' : ''}: ${fileNames}`);
            }
            parts.push(`📎 *Attachments: ${indicators.join(', ')}*`);
        }

        if (textContent) {
            parts.push(textContent);
        }

        return parts.join('\n\n') || '[Attachments only]';
    }

    // ==================== END ATTACHMENT HANDLING ====================

    /**
     * Initialize voice dictation.
     * Supports two modes: Whisper (local server) or Browser (Web Speech API).
     * Mode is determined by checkSTTStatus() which runs before this.
     */
    initVoiceDictation() {
        if (this.dictationMode === 'whisper') {
            // Whisper mode - uses MediaRecorder to capture audio, sends to server
            this.initWhisperDictation();
        } else if (this.dictationMode === 'browser') {
            // Browser mode - uses Web Speech API
            this.initBrowserDictation();
        } else {
            // No dictation available
            console.warn('[STT] Dictation disabled');
            this.elements.voiceBtn.classList.add('unsupported');
            this.elements.voiceBtn.title = 'Voice dictation unavailable';
        }
    }

    /**
     * Initialize Whisper-based dictation using MediaRecorder.
     */
    initWhisperDictation() {
        console.log('[STT] Initializing Whisper dictation mode');
        this.elements.voiceBtn.title = 'Voice dictation (Whisper)';
    }

    /**
     * Initialize browser-based dictation using Web Speech API.
     */
    initBrowserDictation() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

        if (!SpeechRecognition) {
            console.warn('[STT] Browser speech recognition not supported');
            this.elements.voiceBtn.classList.add('unsupported');
            this.elements.voiceBtn.title = 'Voice dictation not supported';
            return;
        }

        console.log('[STT] Initializing browser dictation mode');
        this.recognition = new SpeechRecognition();
        this.recognition.continuous = true;
        this.recognition.interimResults = true;
        this.recognition.lang = 'en-US';

        this.recognition.onstart = () => {
            this.isRecording = true;
            this.elements.voiceBtn.classList.add('recording');
            this.elements.voiceBtn.title = 'Stop dictation';
            this.elements.messageInput.classList.add('transcribing');
            this.textBeforeDictation = this.elements.messageInput.value;
        };

        this.recognition.onend = () => {
            this.isRecording = false;
            this.elements.voiceBtn.classList.remove('recording');
            this.elements.voiceBtn.title = 'Voice dictation (Browser)';
            this.elements.messageInput.classList.remove('transcribing');
            this.handleInputChange();
        };

        this.recognition.onresult = (event) => {
            let allFinalTranscripts = '';
            let interimTranscript = '';

            for (let i = 0; i < event.results.length; i++) {
                if (event.results[i].isFinal) {
                    allFinalTranscripts += event.results[i][0].transcript;
                } else if (i >= event.resultIndex) {
                    interimTranscript += event.results[i][0].transcript;
                }
            }

            const separator = this.textBeforeDictation && !this.textBeforeDictation.endsWith(' ') ? ' ' : '';
            const newText = this.textBeforeDictation + separator + allFinalTranscripts + interimTranscript;
            this.elements.messageInput.value = newText;
            this.handleInputChange();
        };

        this.recognition.onerror = (event) => {
            console.error('[STT] Browser recognition error:', event.error);
            if (event.error === 'not-allowed') {
                this.showToast('Microphone access denied', 'error');
            } else if (event.error === 'no-speech') {
                this.showToast('No speech detected', 'warning');
            } else if (event.error !== 'aborted') {
                this.showToast(`Dictation error: ${event.error}`, 'error');
            }
            this.isRecording = false;
            this.elements.voiceBtn.classList.remove('recording');
            this.elements.messageInput.classList.remove('transcribing');
        };

        this.elements.voiceBtn.title = 'Voice dictation (Browser)';
    }

    /**
     * Toggle voice dictation on/off based on current mode.
     */
    toggleVoiceDictation() {
        if (this.dictationMode === 'whisper') {
            this.toggleWhisperDictation();
        } else if (this.dictationMode === 'browser' && this.recognition) {
            this.toggleBrowserDictation();
        } else {
            this.showToast('Voice dictation is not available', 'warning');
        }
    }

    /**
     * Toggle browser-based dictation.
     */
    toggleBrowserDictation() {
        if (this.isRecording) {
            this.recognition.stop();
        } else {
            try {
                this.recognition.start();
            } catch (e) {
                console.error('[STT] Failed to start browser recognition:', e);
            }
        }
    }

    /**
     * Toggle Whisper-based dictation (record audio, send to server).
     */
    async toggleWhisperDictation() {
        if (this.isRecording) {
            this.stopWhisperRecording();
        } else if (this.isTranscribing) {
            return;
        } else {
            await this.startWhisperRecording();
        }
    }

    /**
     * Start recording audio for Whisper transcription.
     */
    async startWhisperRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : 'audio/wav';
            this.mediaRecorder = new MediaRecorder(stream, { mimeType });
            this.audioChunks = [];

            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    this.audioChunks.push(event.data);
                }
            };

            this.mediaRecorder.onstop = () => {
                stream.getTracks().forEach(track => track.stop());
                this.processWhisperRecording();
            };

            this.mediaRecorder.start();
            this.isRecording = true;
            this.textBeforeDictation = this.elements.messageInput.value;
            this.elements.voiceBtn.classList.add('recording');
            this.elements.voiceBtn.title = 'Stop recording';
            this.elements.messageInput.classList.add('transcribing');
            this.elements.messageInput.placeholder = 'Recording... Click mic to stop';

        } catch (error) {
            console.error('[STT] Failed to start recording:', error);
            if (error.name === 'NotAllowedError') {
                this.showToast('Microphone access denied', 'error');
            } else {
                this.showToast('Failed to start recording', 'error');
            }
        }
    }

    stopWhisperRecording() {
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
            this.isRecording = false;
            this.elements.voiceBtn.classList.remove('recording');
            this.elements.voiceBtn.classList.add('transcribing');
            this.elements.voiceBtn.title = 'Transcribing...';
            this.elements.messageInput.placeholder = 'Transcribing...';
        }
    }

    async processWhisperRecording() {
        if (this.audioChunks.length === 0) {
            this.resetWhisperUI();
            return;
        }

        this.isTranscribing = true;
        const mimeType = this.mediaRecorder?.mimeType || 'audio/webm';
        const audioBlob = new Blob(this.audioChunks, { type: mimeType });

        try {
            const result = await api.transcribeAudio(audioBlob);
            const separator = this.textBeforeDictation && !this.textBeforeDictation.endsWith(' ') ? ' ' : '';
            const newText = this.textBeforeDictation + separator + result.text;
            this.elements.messageInput.value = newText;
            this.handleInputChange();
            console.log(`[STT] Transcribed ${result.duration.toFixed(1)}s audio in ${result.processing_time.toFixed(2)}s (${result.language})`);
        } catch (error) {
            console.error('[STT] Transcription failed:', error);
            this.showToast('Transcription failed: ' + error.message, 'error');
        } finally {
            this.resetWhisperUI();
        }
    }

    resetWhisperUI() {
        this.isTranscribing = false;
        this.audioChunks = [];
        this.elements.voiceBtn.classList.remove('recording', 'transcribing');
        this.elements.voiceBtn.title = 'Voice dictation (Whisper)';
        this.elements.messageInput.classList.remove('transcribing');
        this.elements.messageInput.placeholder = 'Type your message...';
    }

    async loadConversations() {
        // Increment request ID to track this specific request
        const requestId = ++this.loadConversationsRequestId;
        const requestEntityId = this.selectedEntityId;

        console.log('[DEBUG] loadConversations started:', {
            requestId,
            entityId: requestEntityId,
        });

        try {
            // Load conversations filtered by current entity
            const conversations = await api.listConversations(50, 0, requestEntityId);

            // Check if this request is still current (prevents race conditions)
            // If the user switched entities while the request was in flight,
            // the response should be ignored
            if (requestId !== this.loadConversationsRequestId) {
                console.log('[DEBUG] Ignoring stale loadConversations response for entity:', requestEntityId);
                return;
            }

            // Check if the last created conversation is in the response (for debugging)
            let lastCreatedCheck = null;
            if (this.lastCreatedConversation) {
                const found = conversations.find(c => c.id === this.lastCreatedConversation.id);
                const shouldBeHere = this.lastCreatedConversation.entity_id === requestEntityId;
                lastCreatedCheck = {
                    lastCreatedId: this.lastCreatedConversation.id,
                    lastCreatedEntityId: this.lastCreatedConversation.entity_id,
                    requestEntityId,
                    shouldBeHere,
                    foundInResponse: !!found,
                    isConsistent: !shouldBeHere || !!found,  // OK if it shouldn't be here, or if it is found
                };
                if (shouldBeHere && !found) {
                    console.warn('[DEBUG] PERSISTENCE BUG DETECTED! Recently created conversation not found in response:', lastCreatedCheck);
                }
            }

            console.log('[DEBUG] loadConversations completed:', {
                requestId,
                entityId: requestEntityId,
                count: conversations.length,
                ids: conversations.map(c => c.id).slice(0, 5),
                entityIds: conversations.slice(0, 5).map(c => ({ id: c.id.slice(0, 8), entity_id: c.entity_id })),
                lastCreatedCheck,
            });

            this.conversations = conversations;
            this.renderConversationList();
        } catch (error) {
            // Only show error if this request is still current
            if (requestId === this.loadConversationsRequestId) {
                this.showToast('Failed to load conversations', 'error');
                console.error('Failed to load conversations:', error);
            }
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

            // Initialize system prompt for the default entity
            if (this.selectedEntityId) {
                const defaultEntity = this.entities.find(e => e.index_name === this.selectedEntityId);
                if (defaultEntity && defaultEntity.default_system_prompt) {
                    this.settings.systemPrompt = defaultEntity.default_system_prompt;
                }
            }

            // Always show entity selector so users know which entity they're working with
            this.elements.entitySelector.style.display = 'block';
        } catch (error) {
            console.error('Failed to load entities:', error);
            // Hide entity selector on error
            this.elements.entitySelector.style.display = 'none';
        }
    }

    handleEntityChange(entityId) {
        console.log('[DEBUG] handleEntityChange called:', {
            newEntityId: entityId,
            previousEntityId: this.selectedEntityId,
            lastCreatedConversation: this.lastCreatedConversation,
        });

        // Check if multi-entity was selected
        if (entityId === 'multi-entity') {
            // Don't process during browser form restoration (happens within ~100ms of page load)
            const timeSinceConstruction = Date.now() - this.constructedAt;
            if (timeSinceConstruction < 500) {
                console.log('[DEBUG] Ignoring multi-entity selection during page load (browser form restoration)');
                this.elements.entitySelect.value = this.selectedEntityId || this.entities[0]?.index_name;
                return;
            }

            // Switch to multi-entity view mode (don't show modal yet - that happens on New Conversation)
            this.isMultiEntityMode = true;
            this.selectedEntityId = 'multi-entity';
            this.currentConversationEntities = [];  // Will be set when creating new conversation
            // Clear system prompt so it doesn't carry over from the previous single-entity selection
            // Multi-entity conversations use entity_system_prompts, not this fallback
            this.settings.systemPrompt = null;

            // Clear current conversation
            this.currentConversationId = null;
            this.retrievedMemories = [];
            this.retrievedMemoriesByEntity = {};
            this.expandedMemoryIds.clear();
            this.clearMessages();
            this.elements.conversationTitle.textContent = 'Select a conversation';
            this.elements.conversationMeta.textContent = '';
            this.updateMemoriesPanel();
            this.updateEntityDescription();
            this.hideEntityResponderSelector();
            // Clear pending state from any previous conversation
            this.pendingMessageContent = null;
            this.pendingResponderId = null;
            this.pendingUserMessageEl = null;
            // Hide continue button when no conversation is selected
            if (this.elements.continueBtn) {
                this.elements.continueBtn.style.display = 'none';
            }

            // Load multi-entity conversations
            this.loadConversations();

            this.showToast('Switched to Multi-Entity view', 'success');
            return;
        }

        this.isMultiEntityMode = false;
        this.currentConversationEntities = [];
        this.selectedEntityId = entityId;
        this.updateEntityDescription();
        // Hide continue button when switching to single-entity mode
        if (this.elements.continueBtn) {
            this.elements.continueBtn.style.display = 'none';
        }

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

            // Load system prompt for this entity
            // Priority: user-defined prompt > entity's default prompt > null
            if (this.entitySystemPrompts[entityId] !== undefined) {
                this.settings.systemPrompt = this.entitySystemPrompts[entityId];
            } else if (entity.default_system_prompt) {
                this.settings.systemPrompt = entity.default_system_prompt;
            } else {
                this.settings.systemPrompt = null;
            }
        }

        // Clear current conversation when switching entities
        this.currentConversationId = null;
        this.retrievedMemories = [];
        this.retrievedMemoriesByEntity = {};
        this.expandedMemoryIds.clear();
        this.clearMessages();
        this.elements.conversationTitle.textContent = 'Select a conversation';
        this.elements.conversationMeta.textContent = '';
        this.updateMemoriesPanel();
        this.hideEntityResponderSelector();
        // Clear pending state from any previous conversation
        this.pendingMessageContent = null;
        this.pendingResponderId = null;
        this.pendingUserMessageEl = null;

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
        console.log('[DEBUG] showMultiEntityModal called from:', new Error().stack);
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
        // Clear any pending actions when modal is closed
        this.pendingActionAfterEntitySelection = null;
        this.pendingMessageForEntitySelection = null;
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

        // Read pending action BEFORE hiding modal (hideMultiEntityModal clears these)
        const pendingAction = this.pendingActionAfterEntitySelection;
        const pendingMessage = this.pendingMessageForEntitySelection;

        this.hideMultiEntityModal();

        if (pendingAction === 'createConversation') {
            // Continue with conversation creation (skip modal since we just selected entities)
            this.createNewConversation(true);
            return;
        } else if (pendingAction === 'sendMessage' && pendingMessage) {
            // Restore message and continue with send (skip modal since we just selected entities)
            this.elements.messageInput.value = pendingMessage;
            this.sendMessage(true);
            return;
        }

        // Default behavior: clear current conversation and show ready state
        this.currentConversationId = null;
        this.retrievedMemories = [];
        this.retrievedMemoriesByEntity = {};
        this.expandedMemoryIds.clear();
        this.clearMessages();
        this.elements.conversationTitle.textContent = 'New Multi-Entity Conversation';
        this.elements.conversationMeta.textContent = '';
        this.updateMemoriesPanel();
        this.updateEntityDescription();
        // Hide continue button when no conversation is selected
        if (this.elements.continueBtn) {
            this.elements.continueBtn.style.display = 'none';
        }

        // Load conversations for multi-entity view
        this.loadConversations();

        const labels = this.currentConversationEntities.map(e => e.label).join(' & ');
        this.showToast(`Multi-entity mode: ${labels}`, 'success');
    }

    /**
     * Show the entity responder selector modal.
     * @param {string} mode - 'respond' (default), 'continuation', or 'regenerate'
     */
    showEntityResponderSelector(mode = 'respond') {
        console.log('[MULTI-ENTITY] showEntityResponderSelector called:', {
            mode,
            isMultiEntityMode: this.isMultiEntityMode,
            entitiesCount: this.currentConversationEntities?.length,
            entities: this.currentConversationEntities,
            pendingRegenerateMessageId: this.pendingRegenerateMessageId
        });

        if (!this.isMultiEntityMode || this.currentConversationEntities.length === 0) {
            console.log('[MULTI-ENTITY] Selector not shown - conditions not met');
            return;
        }

        // Store the mode for legacy compatibility
        this.responderSelectorContinuationMode = (mode === 'continuation');
        this.responderSelectorMode = mode;

        // Update prompt text based on mode
        const promptEl = this.elements.entityResponderSelector.querySelector('.responder-prompt');
        if (promptEl) {
            if (mode === 'continuation') {
                promptEl.textContent = 'Select an entity to continue the conversation:';
            } else if (mode === 'regenerate') {
                promptEl.textContent = 'Select which entity should regenerate the response:';
            } else {
                promptEl.textContent = 'Select which entity should respond:';
            }
        }

        // Populate responder buttons
        this.elements.entityResponderButtons.innerHTML = this.currentConversationEntities.map(entity => `
            <button class="entity-responder-btn" data-entity-id="${entity.index_name}">
                ${this.escapeHtml(entity.label)}
            </button>
        `).join('');

        // Add click handlers based on mode
        this.elements.entityResponderButtons.querySelectorAll('.entity-responder-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this.pendingResponderId = btn.dataset.entityId;
                this.hideEntityResponderSelector();

                if (this.responderSelectorMode === 'regenerate') {
                    this.regenerateMessageWithEntity();
                } else {
                    this.sendMessageWithResponder();
                }
            });
        });

        this.elements.entityResponderSelector.style.display = 'block';
    }

    hideEntityResponderSelector() {
        this.elements.entityResponderSelector.style.display = 'none';
    }

    /**
     * Continue a multi-entity conversation by prompting for which entity should respond.
     * Called when user clicks the "Continue" button in the header.
     */
    continueMultiEntityConversation() {
        if (!this.isMultiEntityMode || !this.currentConversationId) {
            this.showToast('No multi-entity conversation loaded', 'error');
            return;
        }

        if (this.currentConversationEntities.length === 0) {
            this.showToast('No entities found for this conversation', 'error');
            return;
        }

        // Clear any pending message state (continuation doesn't have a message)
        this.pendingMessageContent = null;
        this.pendingUserMessageEl = null;

        // Show the responder selector in continuation mode
        this.showEntityResponderSelector('continuation');
    }

    async sendMessageWithResponder() {
        if (!this.pendingResponderId) {
            this.showToast('No entity selected', 'error');
            return;
        }

        // Content can be null for continuation mode
        const content = this.pendingMessageContent;
        const attachments = this.pendingMessageAttachments;
        const responderId = this.pendingResponderId;
        const userMessageEl = this.pendingUserMessageEl;
        const isContinuation = !content && !attachments;

        // Clear pending state
        this.pendingMessageContent = null;
        this.pendingMessageAttachments = null;
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
            // Build request - don't send model override in multi-entity mode
            // so each entity uses its own configured model
            const request = {
                conversation_id: this.currentConversationId,
                message: content,
                temperature: this.settings.temperature,
                max_tokens: this.settings.maxTokens,
                system_prompt: this.settings.systemPrompt,
                verbosity: this.settings.verbosity,
                responding_entity_id: responderId,
                user_display_name: this.settings.researcherName || null,
                attachments: attachments,  // Include attachments
            };
            // Only include model override if NOT in multi-entity mode
            if (!this.isMultiEntityMode) {
                request.model = this.settings.model;
            }

            await api.sendMessageStream(
                request,
                {
                    onMemories: (data) => {
                        this.handleMemoryUpdate(data);
                    },
                    onStart: (data) => {
                        // Stream has started
                    },
                    onToken: (data) => {
                        if (data.content) {
                            streamingMessage.updateContent(data.content);
                        }
                    },
                    onToolStart: (data) => {
                        this.addToolMessage('start', data.tool_name, data);
                    },
                    onToolResult: (data) => {
                        this.addToolMessage('result', data.tool_name, data);
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
                        console.log('[MULTI-ENTITY] onStored callback triggered:', data);

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
                            this.updateAssistantMessageActions(streamingMessage.element, data.assistant_message_id, streamingMessage.getContent());
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

                        // Show responder selector for next turn (continuation mode since no new human message)
                        this.showEntityResponderSelector(true);
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
                this.elements.verbosityGroup.title = 'Verbosity is only supported by GPT-5.x models';
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
                        <button class="conversation-dropdown-item" data-action="rename" data-id="${conv.id}">Rename</button>
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

            // Click on rename option
            const renameBtn = item.querySelector('[data-action="rename"]');
            renameBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.showRenameModalForConversation(conv.id, conv.title);
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

    async createNewConversation(skipEntityModal = false) {
        // Hide responder selector and clear pending state
        this.hideEntityResponderSelector();
        this.pendingMessageContent = null;
        this.pendingResponderId = null;
        this.pendingUserMessageEl = null;

        // In multi-entity mode, show entity selection modal for new conversations
        // (unless we just came from the modal confirmation)
        if (this.isMultiEntityMode && !skipEntityModal) {
            console.log('[DEBUG] showMultiEntityModal triggered by: createNewConversation (multi-entity mode)');
            this.pendingActionAfterEntitySelection = 'createConversation';
            this.showMultiEntityModal();
            return;
        }

        try {
            let conversationData;

            if (this.isMultiEntityMode && this.currentConversationEntities.length >= 2) {
                // Create multi-entity conversation
                // Build entity_system_prompts for participating entities
                const entityPrompts = {};
                for (const entity of this.currentConversationEntities) {
                    if (this.entitySystemPrompts[entity.index_name] !== undefined) {
                        entityPrompts[entity.index_name] = this.entitySystemPrompts[entity.index_name];
                    }
                }
                conversationData = {
                    model: this.settings.model,
                    system_prompt: null,  // Multi-entity conversations use entity_system_prompts, not a fallback
                    conversation_type: 'multi_entity',
                    entity_ids: this.currentConversationEntities.map(e => e.index_name),
                    entity_system_prompts: Object.keys(entityPrompts).length > 0 ? entityPrompts : null,
                };
            } else {
                // Standard single-entity conversation
                // Validate that we have a selected entity
                if (!this.selectedEntityId || this.selectedEntityId === 'multi-entity') {
                    console.error('[DEBUG] BUG: Creating single-entity conversation without valid selectedEntityId:', {
                        selectedEntityId: this.selectedEntityId,
                        isMultiEntityMode: this.isMultiEntityMode,
                    });
                }

                // Include per-entity system prompt if set
                const entityPrompts = {};
                if (this.selectedEntityId && this.entitySystemPrompts[this.selectedEntityId] !== undefined) {
                    entityPrompts[this.selectedEntityId] = this.entitySystemPrompts[this.selectedEntityId];
                }
                conversationData = {
                    model: this.settings.model,
                    system_prompt: this.settings.systemPrompt,  // Fallback
                    conversation_type: this.settings.conversationType,
                    entity_id: this.selectedEntityId,
                    entity_system_prompts: Object.keys(entityPrompts).length > 0 ? entityPrompts : null,
                };
            }

            console.log('[DEBUG] Creating conversation with data:', {
                entity_id: conversationData.entity_id,
                conversation_type: conversationData.conversation_type,
                selectedEntityId: this.selectedEntityId,
            });

            const conversation = await api.createConversation(conversationData);

            console.log('[DEBUG] Conversation created:', {
                id: conversation.id,
                entity_id: conversation.entity_id,
                selectedEntityId: this.selectedEntityId,
            });

            // Track last created conversation for debugging persistence issues
            this.lastCreatedConversation = {
                id: conversation.id,
                entity_id: conversation.entity_id,
                createdAt: Date.now(),
            };

            this.conversations.unshift(conversation);
            this.currentConversationId = conversation.id;
            this.retrievedMemories = [];
            this.retrievedMemoriesByEntity = {};
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

        // Hide responder selector and clear pending state from previous conversation
        this.hideEntityResponderSelector();
        this.pendingMessageContent = null;
        this.pendingResponderId = null;
        this.pendingUserMessageEl = null;

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

            // Note: We do NOT merge conversation's entity_system_prompts into entitySystemPrompts
            // entitySystemPrompts holds the user's default settings for NEW conversations
            // Each conversation stores its own entity_system_prompts independently

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
                // Handle tool exchange messages with interactive UI
                if (msg.role === 'tool_use') {
                    try {
                        const contentBlocks = JSON.parse(msg.content);
                        for (const block of contentBlocks) {
                            if (block.type === 'tool_use') {
                                this.addToolMessage('start', block.name, {
                                    tool_id: block.id,
                                    input: block.input,
                                });
                            }
                        }
                    } catch (e) {
                        console.error('Failed to parse tool_use content:', e);
                    }
                    return;
                }

                if (msg.role === 'tool_result') {
                    try {
                        const contentBlocks = JSON.parse(msg.content);
                        for (const block of contentBlocks) {
                            if (block.type === 'tool_result') {
                                this.addToolMessage('result', '', {
                                    tool_id: block.tool_use_id,
                                    content: block.content,
                                    is_error: block.is_error || false,
                                });
                            }
                        }
                    } catch (e) {
                        console.error('Failed to parse tool_result content:', e);
                    }
                    return;
                }

                // Regular message (human, assistant, system)
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

            // Don't auto-show responder selector when loading conversation
            // User should click "New Conversation" or type a message to trigger entity selection
        } catch (error) {
            this.showToast('Failed to load conversation', 'error');
            console.error('Failed to load conversation:', error);
        } finally {
            this.showLoading(false);
        }
    }

    async sendMessage(skipEntityModal = false) {
        const content = this.elements.messageInput.value.trim();
        const hasAttachments = this.hasAttachments();

        // Need either content or attachments to send
        if ((!content && !hasAttachments) || this.isLoading) return;

        // Capture attachments before clearing (they'll be cleared after message is sent)
        const attachments = this.getAttachmentsForRequest();

        // In multi-entity mode without a conversation, show entity selection modal
        // (unless we just came from the modal confirmation)
        if (!this.currentConversationId && this.isMultiEntityMode && !skipEntityModal) {
            this.pendingActionAfterEntitySelection = 'sendMessage';
            this.pendingMessageForEntitySelection = content;
            this.pendingAttachmentsForEntitySelection = attachments;
            this.showMultiEntityModal();
            return;
        }

        // Ensure we have a conversation
        if (!this.currentConversationId) {
            await this.createNewConversation(true);  // Skip modal since we already selected entities
        }

        // In multi-entity mode, store message and show responder selector
        if (this.isMultiEntityMode) {
            this.pendingMessageContent = content;
            this.pendingMessageAttachments = attachments;
            this.elements.messageInput.value = '';
            this.elements.messageInput.style.height = 'auto';
            this.clearAttachments();

            // Add user message visually immediately (with attachment indicator if present)
            const displayContent = this.buildDisplayContentWithAttachments(content, attachments);
            this.pendingUserMessageEl = this.addMessage('human', displayContent);
            this.scrollToBottom();

            // Show responder selector
            this.showEntityResponderSelector();
            return;
        }

        // Standard single-entity flow
        this.isLoading = true;
        this.elements.sendBtn.disabled = true;
        this.elements.sendBtn.style.display = 'none';
        this.elements.stopBtn.style.display = 'flex';
        this.elements.messageInput.value = '';
        this.elements.messageInput.style.height = 'auto';
        this.clearAttachments();

        // Create abort controller for stop functionality
        this.streamAbortController = new AbortController();

        // Add user message (without ID initially - will be updated when stored)
        // Include attachment indicator in display
        const displayContent = this.buildDisplayContentWithAttachments(content, attachments);
        const userMessageEl = this.addMessage('human', displayContent);
        this.scrollToBottom();

        // Create streaming message element
        const streamingMessage = this.createStreamingMessage('assistant');
        let usageData = null;

        try {
            await api.sendMessageStream(
                {
                    conversation_id: this.currentConversationId,
                    message: content || null,  // Can be null if only attachments
                    model: this.settings.model,
                    temperature: this.settings.temperature,
                    max_tokens: this.settings.maxTokens,
                    system_prompt: this.settings.systemPrompt,
                    verbosity: this.settings.verbosity,
                    user_display_name: this.settings.researcherName || null,
                    attachments: attachments,  // Include attachments
                },
                {
                    onMemories: (data) => {
                        this.handleMemoryUpdate(data);
                    },
                    onStart: (data) => {
                        // Stream has started
                    },
                    onAborted: () => {
                        // Stream was aborted by user
                        streamingMessage.finalize({ showTimestamp: true, aborted: true });
                    },
                    onToken: (data) => {
                        // Update message content progressively
                        if (data.content) {
                            streamingMessage.updateContent(data.content);
                        }
                    },
                    onToolStart: (data) => {
                        this.addToolMessage('start', data.tool_name, data);
                    },
                    onToolResult: (data) => {
                        this.addToolMessage('result', data.tool_name, data);
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
                },
                this.streamAbortController.signal
            );

            this.scrollToBottom();

        } catch (error) {
            // Don't show error for abort
            if (error.name !== 'AbortError') {
                streamingMessage.element.remove();
                this.addMessage('assistant', `Error: ${error.message}`, { isError: true });
                this.showToast('Failed to send message', 'error');
                console.error('Failed to send message:', error);
            }
        } finally {
            this.isLoading = false;
            this.streamAbortController = null;
            this.elements.stopBtn.style.display = 'none';
            this.elements.sendBtn.style.display = 'flex';
            this.handleInputChange();
        }
    }

    /**
     * Stop the current generation/stream.
     */
    stopGeneration() {
        if (this.streamAbortController) {
            this.streamAbortController.abort();
            this.streamAbortController = null;
        }
        this.elements.stopBtn.style.display = 'none';
        this.elements.sendBtn.style.display = 'flex';
        this.isLoading = false;
        this.handleInputChange();
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
     * Add a tool message to the chat (tool start or result).
     * @param {string} type - 'start' or 'result'
     * @param {string} toolName - Name of the tool
     * @param {Object} data - Tool data (input for start, content/is_error for result)
     * @returns {HTMLElement|null} - The tool message element (only for start) or null
     */
    addToolMessage(type, toolName, data) {
        // Check scroll position before adding content
        const wasNearBottom = this.isNearBottom();

        const message = document.createElement('div');
        message.className = 'tool-message';
        message.dataset.toolId = data.tool_id || '';
        message.dataset.toolName = toolName;

        if (type === 'start') {
            // Format tool name for display
            const displayName = toolName.replace(/_/g, ' ');

            // Create collapsible structure
            let inputContent = '';
            if (data.input && Object.keys(data.input).length > 0) {
                const inputStr = JSON.stringify(data.input, null, 2);
                inputContent = `
                    <details class="tool-input-details">
                        <summary>Input</summary>
                        <pre class="tool-input">${this.escapeHtml(inputStr)}</pre>
                    </details>
                `;
            }

            message.innerHTML = `
                <div class="tool-indicator">
                    <span class="tool-icon">🔧</span>
                    <span class="tool-name">Using: ${this.escapeHtml(displayName)}</span>
                    <span class="tool-status loading">...</span>
                </div>
                ${inputContent}
            `;

            this.elements.messages.appendChild(message);
            // Only scroll if user was already near the bottom
            if (wasNearBottom) {
                this.scrollToBottom();
            }
            return message;
        } else if (type === 'result') {
            // Find the corresponding start message
            const startMessage = this.elements.messages.querySelector(
                `.tool-message[data-tool-id="${data.tool_id}"]`
            );

            if (startMessage) {
                // Update the status indicator
                const statusEl = startMessage.querySelector('.tool-status');
                if (statusEl) {
                    statusEl.classList.remove('loading');
                    statusEl.classList.add(data.is_error ? 'error' : 'success');
                    statusEl.textContent = data.is_error ? '✗' : '✓';
                }

                // Add result content in collapsible
                const resultDetails = document.createElement('details');
                resultDetails.className = 'tool-result-details';

                // Truncate very long results for display
                let resultContent = data.content || '';
                const maxDisplayLength = 2000;
                if (resultContent.length > maxDisplayLength) {
                    resultContent = resultContent.substring(0, maxDisplayLength) + '\n...[truncated]';
                }

                resultDetails.innerHTML = `
                    <summary>Result${data.is_error ? ' (Error)' : ''}</summary>
                    <pre class="tool-result ${data.is_error ? 'error' : ''}">${this.escapeHtml(resultContent)}</pre>
                `;

                startMessage.appendChild(resultDetails);
                // Only scroll if user was already near the bottom
                if (wasNearBottom) {
                    this.scrollToBottom();
                }
            }
            return null;
        }
        return null;
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

            // Get StyleTTS 2 parameters if using StyleTTS 2 provider
            const styletts2Params = this.ttsProvider === 'styletts2' ? this.getStyleTTS2Params() : null;

            // Get audio from API with selected voice and parameters
            const audioBlob = await api.textToSpeech(textContent, this.selectedVoiceId, styletts2Params);
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
        message.dataset.role = role;

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
                // Note: Auto-scroll disabled to let users read beginning of long messages during generation
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
     * Add action buttons (copy, speak, regenerate) to an assistant message.
     * Used in multi-entity mode after message is stored.
     */
    updateAssistantMessageActions(messageElement, messageId, messageContent) {
        // Remove regenerate buttons from previous assistant messages
        this.removeRegenerateButtons();

        // Add action buttons inside assistant message bubble
        const assistantBubble = messageElement.querySelector('.message-bubble');
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

            const copyBtn = actionsDiv.querySelector('.copy-btn');
            copyBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.copyMessage(messageContent, copyBtn);
            });
            const regenerateBtn = actionsDiv.querySelector('.regenerate-btn');
            regenerateBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.regenerateMessage(messageId);
            });
            const speakBtn = actionsDiv.querySelector('.speak-btn');
            if (speakBtn) {
                speakBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.speakMessage(messageContent, speakBtn, messageId);
                });
            }
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

            // Reset loading state before regenerating - regenerateMessage manages its own loading state
            this.isLoading = false;

            // Now regenerate the response
            await this.regenerateMessage(messageId);

        } catch (error) {
            this.showToast('Failed to update message', 'error');
            console.error('Failed to update message:', error);

            // Restore original state
            messageElement.classList.remove('editing');
            const bubble = messageElement.querySelector('.message-bubble');
            bubble.innerHTML = this.renderMarkdown(newContent);
            this.isLoading = false;
            this.handleInputChange();
        }
    }

    /**
     * Regenerate an AI response for a given message.
     * In multi-entity mode, prompts user to select which entity should respond.
     * @param {string} messageId - ID of the message to regenerate from
     */
    async regenerateMessage(messageId) {
        if (this.isLoading) {
            this.showToast('Please wait for the current operation to complete', 'warning');
            return;
        }

        // In multi-entity mode, show entity selector first
        if (this.isMultiEntityMode && this.currentConversationEntities.length > 0) {
            this.pendingRegenerateMessageId = messageId;
            this.showEntityResponderSelector('regenerate');
            return;
        }

        // Single-entity mode: proceed with regeneration directly
        await this.performRegeneration(messageId);
    }

    /**
     * Regenerate a message after entity selection in multi-entity mode.
     * Called by the entity responder selector when in regenerate mode.
     */
    async regenerateMessageWithEntity() {
        const messageId = this.pendingRegenerateMessageId;
        const responderId = this.pendingResponderId;

        // Clear pending state
        this.pendingRegenerateMessageId = null;
        this.pendingResponderId = null;

        if (!messageId || !responderId) {
            this.showToast('Missing message or entity selection', 'error');
            return;
        }

        await this.performRegeneration(messageId, responderId);
    }

    /**
     * Perform the actual message regeneration.
     * @param {string} messageId - ID of the message to regenerate from
     * @param {string|null} respondingEntityId - Entity to generate response (multi-entity only)
     */
    async performRegeneration(messageId, respondingEntityId = null) {
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

        // Get the responding entity's label for multi-entity mode
        let responderLabel = null;
        if (this.isMultiEntityMode && respondingEntityId) {
            const responderEntity = this.currentConversationEntities.find(e => e.index_name === respondingEntityId);
            responderLabel = responderEntity?.label || respondingEntityId;
        }

        // Create streaming message element (with speaker label for multi-entity)
        const streamingMessage = this.createStreamingMessage('assistant', responderLabel);

        try {
            const requestData = {
                message_id: messageId,
                temperature: this.settings.temperature,
                max_tokens: this.settings.maxTokens,
                system_prompt: this.settings.systemPrompt,
                verbosity: this.settings.verbosity,
                user_display_name: this.settings.researcherName || null,
            };

            // Only include model override for single-entity mode
            if (!this.isMultiEntityMode) {
                requestData.model = this.settings.model;
            }

            // Include responding_entity_id for multi-entity mode
            if (respondingEntityId) {
                requestData.responding_entity_id = respondingEntityId;
            }

            await api.regenerateStream(
                requestData,
                {
                    onMemories: (data) => {
                        this.handleMemoryUpdate(data);
                    },
                    onStart: (data) => {
                        // Stream has started
                    },
                    onToken: (data) => {
                        if (data.content) {
                            streamingMessage.updateContent(data.content);
                        }
                    },
                    onToolStart: (data) => {
                        this.addToolMessage('start', data.tool_name, data);
                    },
                    onToolResult: (data) => {
                        this.addToolMessage('result', data.tool_name, data);
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

                        // Update speaker entity ID if provided (multi-entity mode)
                        if (data.speaker_entity_id) {
                            streamingMessage.element.dataset.speakerEntityId = data.speaker_entity_id;
                        }

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

                        // In multi-entity mode, show responder selector for next turn
                        if (this.isMultiEntityMode) {
                            this.showEntityResponderSelector('continuation');
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
        let isMultiEntity = false;

        // Handle multi-entity conversations
        if (conversation.conversation_type === 'multi_entity' && conversation.entities) {
            const entityLabels = conversation.entities.map(e => e.label).join(' & ');
            meta = `multi-entity · ${entityLabels}`;
            isMultiEntity = true;
        } else {
            meta = `${conversation.conversation_type} · ${conversation.llm_model_used}`;

            // Add entity label if multiple entities exist
            if (this.entities.length > 1 && conversation.entity_id) {
                const entityLabel = this.getEntityLabel(conversation.entity_id);
                meta += ` · ${entityLabel}`;
            }
        }

        this.elements.conversationMeta.textContent = meta;

        // Show/hide Continue button based on conversation type
        if (this.elements.continueBtn) {
            this.elements.continueBtn.style.display = isMultiEntity ? 'inline-block' : 'none';
        }
    }

    updateMemoriesPanel() {
        // Check if we have multi-entity memories
        const hasMultiEntityMemories = Object.keys(this.retrievedMemoriesByEntity).length > 0;

        if (hasMultiEntityMemories) {
            // Multi-entity mode: display memories grouped by entity
            let totalCount = 0;
            Object.values(this.retrievedMemoriesByEntity).forEach(e => {
                totalCount += e.memories.length;
            });

            this.elements.memoryCount.textContent = totalCount;

            if (totalCount === 0) {
                this.elements.memoriesContent.innerHTML = `
                    <div style="color: var(--text-muted); font-size: 0.85rem;">
                        No memories retrieved in this session
                    </div>
                `;
                return;
            }

            // Build HTML with entity sections
            let html = '';
            for (const [entityId, entityData] of Object.entries(this.retrievedMemoriesByEntity)) {
                if (entityData.memories.length === 0) continue;

                html += `
                    <div class="memory-entity-section">
                        <div class="memory-entity-header">${this.escapeHtml(entityData.label)} (${entityData.memories.length})</div>
                        ${entityData.memories.map(mem => this.renderMemoryItem(mem)).join('')}
                    </div>
                `;
            }

            this.elements.memoriesContent.innerHTML = html;
        } else {
            // Single-entity mode: use flat array
            this.elements.memoryCount.textContent = this.retrievedMemories.length;

            if (this.retrievedMemories.length === 0) {
                this.elements.memoriesContent.innerHTML = `
                    <div style="color: var(--text-muted); font-size: 0.85rem;">
                        No memories retrieved in this session
                    </div>
                `;
                return;
            }

            this.elements.memoriesContent.innerHTML = this.retrievedMemories.map(
                mem => this.renderMemoryItem(mem)
            ).join('');
        }

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

    /**
     * Render a single memory item HTML.
     */
    renderMemoryItem(mem) {
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
    }

    /**
     * Handle incoming memory data from streaming events.
     * For multi-entity conversations, stores memories per-entity.
     * For single-entity, uses the flat array.
     */
    handleMemoryUpdate(data) {
        let hasChanges = false;
        const entityId = data.entity_id;  // Present for multi-entity, null for single-entity
        const entityLabel = data.entity_label;

        if (entityId) {
            // Multi-entity mode: store memories by entity
            if (!this.retrievedMemoriesByEntity[entityId]) {
                this.retrievedMemoriesByEntity[entityId] = {
                    label: entityLabel || entityId,
                    memories: []
                };
            }

            const entityMemories = this.retrievedMemoriesByEntity[entityId].memories;

            if (data.trimmed_memory_ids && data.trimmed_memory_ids.length > 0) {
                const trimmedSet = new Set(data.trimmed_memory_ids);
                this.retrievedMemoriesByEntity[entityId].memories = entityMemories.filter(
                    mem => !trimmedSet.has(mem.id)
                );
                hasChanges = true;
            }

            if (data.new_memories && data.new_memories.length > 0) {
                const existingIds = new Set(entityMemories.map(m => m.id));
                data.new_memories.forEach(mem => {
                    if (!existingIds.has(mem.id)) {
                        this.retrievedMemoriesByEntity[entityId].memories.push(mem);
                    }
                });
                hasChanges = true;
            }
        } else {
            // Single-entity mode: use flat array
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
        }

        if (hasChanges) {
            this.updateMemoriesPanel();
        }
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

    /**
     * Check if the user is near the bottom of the messages container.
     * Used to determine whether auto-scroll should occur.
     * @param {number} threshold - Pixels from bottom to consider "near" (default 100)
     * @returns {boolean} - True if user is near the bottom
     */
    isNearBottom(threshold = 100) {
        const container = this.elements.messagesContainer;
        return container.scrollTop + container.clientHeight >= container.scrollHeight - threshold;
    }

    // Modal management
    showModal(modalName) {
        this.elements[modalName].classList.add('active');
    }

    hideModal(modalName) {
        this.elements[modalName].classList.remove('active');
    }

    closeActiveModal() {
        // List of all modal element names
        const modalNames = [
            'settingsModal',
            'memoriesModal',
            'archiveModal',
            'renameModal',
            'deleteModal',
            'archivedModal',
            'voiceCloneModal',
            'voiceEditModal',
            'multiEntityModal'
        ];

        // Find and close the first active modal
        for (const modalName of modalNames) {
            if (this.elements[modalName]?.classList.contains('active')) {
                this.hideModal(modalName);
                return;
            }
        }
    }

    showSettingsModal() {
        this.updateTemperatureMax();
        this.elements.researcherNameInput.value = this.settings.researcherName || '';
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

        // Update system prompt label to show current entity
        if (this.selectedEntityId && this.selectedEntityId !== 'multi-entity') {
            const entity = this.entities.find(e => e.index_name === this.selectedEntityId);
            if (entity) {
                this.elements.systemPromptLabel.textContent = `System Prompt for ${entity.label} (optional)`;
                this.elements.systemPromptHelp.textContent = `This prompt applies to ${entity.label} and will be used for new conversations with this entity.`;
            } else {
                this.elements.systemPromptLabel.textContent = 'System Prompt (optional)';
                this.elements.systemPromptHelp.textContent = 'This prompt applies to the currently selected entity and will be used for new conversations.';
            }
        } else if (this.selectedEntityId === 'multi-entity') {
            this.elements.systemPromptLabel.textContent = 'Fallback System Prompt (optional)';
            this.elements.systemPromptHelp.textContent = 'This fallback prompt is used when an entity has no specific system prompt configured.';
        } else {
            this.elements.systemPromptLabel.textContent = 'System Prompt (optional)';
            this.elements.systemPromptHelp.textContent = 'This prompt applies to the currently selected entity and will be used for new conversations.';
        }

        // Load GitHub settings
        this.loadGitHubSettings();

        this.showModal('settingsModal');
    }

    /**
     * Load GitHub integration settings and display in the settings modal.
     */
    async loadGitHubSettings() {
        try {
            const repos = await api.listGitHubRepos();

            if (!repos || repos.length === 0) {
                // No repos configured
                if (this.elements.githubNotConfigured) {
                    this.elements.githubNotConfigured.style.display = 'block';
                }
                if (this.elements.githubReposContainer) {
                    this.elements.githubReposContainer.style.display = 'none';
                }
                return;
            }

            // Show repos container, hide not configured message
            if (this.elements.githubNotConfigured) {
                this.elements.githubNotConfigured.style.display = 'none';
            }
            if (this.elements.githubReposContainer) {
                this.elements.githubReposContainer.style.display = 'block';
            }

            // Render repos list
            if (this.elements.githubReposList) {
                this.elements.githubReposList.innerHTML = repos.map(repo => `
                    <div class="github-repo-item">
                        <div class="github-repo-header">
                            <span class="github-repo-label">${this.escapeHtml(repo.label)}</span>
                            <span class="github-repo-name">${this.escapeHtml(repo.owner)}/${this.escapeHtml(repo.repo)}</span>
                        </div>
                        <div class="github-repo-capabilities">
                            ${this.renderCapabilities(repo.capabilities)}
                        </div>
                        <div class="github-repo-protected">
                            <span class="protected-label">Protected branches:</span>
                            <span class="protected-branches">${repo.protected_branches.map(b => this.escapeHtml(b)).join(', ')}</span>
                        </div>
                    </div>
                `).join('');
            }

            // Load rate limits
            await this.loadGitHubRateLimits();

        } catch (error) {
            console.warn('Failed to load GitHub settings:', error);
            if (this.elements.githubNotConfigured) {
                this.elements.githubNotConfigured.style.display = 'block';
            }
            if (this.elements.githubReposContainer) {
                this.elements.githubReposContainer.style.display = 'none';
            }
        }
    }

    /**
     * Render capability indicators for a GitHub repo.
     */
    renderCapabilities(capabilities) {
        const allCapabilities = ['read', 'branch', 'commit', 'pr', 'issue'];
        return allCapabilities.map(cap => {
            const enabled = capabilities.includes(cap);
            const icon = enabled ? '✓' : '○';
            const className = enabled ? 'capability-enabled' : 'capability-disabled';
            return `<span class="github-capability ${className}">${icon} ${cap}</span>`;
        }).join('');
    }

    /**
     * Load and display GitHub rate limit status.
     */
    async loadGitHubRateLimits() {
        if (!this.elements.githubRateLimits) return;

        try {
            const data = await api.getGitHubRateLimits();

            if (!data.enabled) {
                this.elements.githubRateLimits.innerHTML = '<span class="rate-limit-disabled">GitHub tools disabled</span>';
                return;
            }

            const repos = Object.entries(data.repos);
            if (repos.length === 0) {
                this.elements.githubRateLimits.innerHTML = '<span class="rate-limit-none">No repositories configured</span>';
                return;
            }

            this.elements.githubRateLimits.innerHTML = repos.map(([label, info]) => {
                if (info.remaining === null) {
                    return `
                        <div class="rate-limit-item">
                            <span class="rate-limit-label">${this.escapeHtml(label)}</span>
                            <span class="rate-limit-status unknown">No data yet</span>
                        </div>
                    `;
                }

                const percentage = (info.remaining / info.limit) * 100;
                const statusClass = percentage < 10 ? 'danger' : percentage < 25 ? 'warning' : 'good';

                return `
                    <div class="rate-limit-item">
                        <span class="rate-limit-label">${this.escapeHtml(label)}</span>
                        <div class="rate-limit-bar">
                            <div class="rate-limit-bar-fill ${statusClass}" style="width: ${percentage}%"></div>
                        </div>
                        <span class="rate-limit-text ${statusClass}">${info.remaining}/${info.limit}</span>
                    </div>
                `;
            }).join('');

        } catch (error) {
            console.warn('Failed to load GitHub rate limits:', error);
            this.elements.githubRateLimits.innerHTML = '<span class="rate-limit-error">Failed to load rate limits</span>';
        }
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
        // Save researcher name
        this.settings.researcherName = this.elements.researcherNameInput.value.trim() || '';
        localStorage.setItem('researcher_name', this.settings.researcherName);

        // Save system prompt per-entity (for single-entity mode)
        if (this.selectedEntityId && this.selectedEntityId !== 'multi-entity') {
            this.entitySystemPrompts[this.selectedEntityId] = this.settings.systemPrompt;
        }

        // Persist entity system prompts to localStorage
        this.saveEntitySystemPromptsToStorage();

        // Apply theme
        this.setTheme(this.elements.themeSelect.value);

        // Apply voice selection
        if (this.ttsVoices.length > 1) {
            const newVoiceId = this.elements.voiceSelect.value;
            if (newVoiceId !== this.selectedVoiceId) {
                this.selectedVoiceId = newVoiceId;
                // Persist voice selection and clear audio cache when voice changes
                this.saveSelectedVoiceToStorage();
                this.clearAudioCache();
            }
        }

        // Save StyleTTS 2 parameters if using StyleTTS 2
        if (this.ttsProvider === 'styletts2') {
            this.saveStyleTTS2Settings();
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

    loadStyleTTS2Settings() {
        // Load StyleTTS 2 parameters from localStorage
        const stored = localStorage.getItem('styletts2_params');
        if (stored) {
            try {
                const params = JSON.parse(stored);
                this.elements.styletts2Alpha.value = params.alpha ?? 0.3;
                this.elements.styletts2Beta.value = params.beta ?? 0.7;
                this.elements.styletts2DiffusionSteps.value = params.diffusion_steps ?? 10;
                this.elements.styletts2EmbeddingScale.value = params.embedding_scale ?? 1.0;
                this.elements.styletts2Speed.value = params.speed ?? 1.0;
            } catch (e) {
                console.warn('Failed to load StyleTTS 2 settings:', e);
            }
        }
    }

    saveStyleTTS2Settings() {
        // Save StyleTTS 2 parameters to localStorage
        const params = {
            alpha: parseFloat(this.elements.styletts2Alpha.value) || 0.3,
            beta: parseFloat(this.elements.styletts2Beta.value) || 0.7,
            diffusion_steps: parseInt(this.elements.styletts2DiffusionSteps.value) || 10,
            embedding_scale: parseFloat(this.elements.styletts2EmbeddingScale.value) || 1.0,
            speed: parseFloat(this.elements.styletts2Speed.value) || 1.0,
        };
        localStorage.setItem('styletts2_params', JSON.stringify(params));
        // Clear audio cache since parameters changed
        this.clearAudioCache();
    }

    getStyleTTS2Params() {
        // Get current StyleTTS 2 parameters for use in TTS requests
        const stored = localStorage.getItem('styletts2_params');
        if (stored) {
            try {
                return JSON.parse(stored);
            } catch (e) {
                // Fall through to defaults
            }
        }
        return {
            alpha: 0.3,
            beta: 0.7,
            diffusion_steps: 10,
            embedding_scale: 1.0,
            speed: 1.0,
        };
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

        // Show/hide provider-specific parameter sections
        if (this.ttsProvider === 'styletts2') {
            this.elements.xttsParamsSection.style.display = 'none';
            this.elements.styletts2ParamsSection.style.display = 'block';
            // Populate StyleTTS 2 parameters
            this.elements.voiceEditAlpha.value = voice.alpha ?? 0.3;
            this.elements.voiceEditBeta.value = voice.beta ?? 0.7;
            this.elements.voiceEditDiffusionSteps.value = voice.diffusion_steps ?? 10;
            this.elements.voiceEditEmbeddingScale.value = voice.embedding_scale ?? 1.0;
        } else {
            this.elements.xttsParamsSection.style.display = 'block';
            this.elements.styletts2ParamsSection.style.display = 'none';
            // Populate XTTS parameters
            this.elements.voiceEditTemperature.value = voice.temperature ?? 0.75;
            this.elements.voiceEditSpeed.value = voice.speed ?? 1.0;
            this.elements.voiceEditLengthPenalty.value = voice.length_penalty ?? 1.0;
            this.elements.voiceEditRepetitionPenalty.value = voice.repetition_penalty ?? 5.0;
        }

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

        // Build updates with common fields
        const updates = {
            label: this.elements.voiceEditName.value.trim() || null,
            description: this.elements.voiceEditDescription.value.trim() || null,
        };

        // Add provider-specific parameters
        if (this.ttsProvider === 'styletts2') {
            updates.alpha = parseFloat(this.elements.voiceEditAlpha.value) || null;
            updates.beta = parseFloat(this.elements.voiceEditBeta.value) || null;
            updates.diffusion_steps = parseInt(this.elements.voiceEditDiffusionSteps.value) || null;
            updates.embedding_scale = parseFloat(this.elements.voiceEditEmbeddingScale.value) || null;
        } else {
            updates.temperature = parseFloat(this.elements.voiceEditTemperature.value) || null;
            updates.speed = parseFloat(this.elements.voiceEditSpeed.value) || null;
            updates.length_penalty = parseFloat(this.elements.voiceEditLengthPenalty.value) || null;
            updates.repetition_penalty = parseFloat(this.elements.voiceEditRepetitionPenalty.value) || null;
        }

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
                this.retrievedMemoriesByEntity = {};
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

    showRenameModalForConversation(conversationId, conversationTitle) {
        this.pendingRenameId = conversationId;
        this.elements.renameInput.value = conversationTitle || '';
        this.closeAllDropdowns();
        this.showModal('renameModal');
        // Focus the input field after a short delay to ensure modal is visible
        setTimeout(() => this.elements.renameInput.focus(), 50);
    }

    async renameConversation() {
        const conversationId = this.pendingRenameId;
        if (!conversationId) return;

        const newTitle = this.elements.renameInput.value.trim();
        if (!newTitle) {
            this.showToast('Please enter a title', 'error');
            return;
        }

        try {
            await api.updateConversation(conversationId, { title: newTitle });

            // Update in local list
            const conv = this.conversations.find(c => c.id === conversationId);
            if (conv) {
                conv.title = newTitle;
            }

            // Update header if this is the current conversation
            if (conversationId === this.currentConversationId) {
                this.elements.conversationTitle.textContent = newTitle;
            }

            this.renderConversationList();
            this.hideModal('renameModal');
            this.pendingRenameId = null;
            this.showToast('Conversation renamed', 'success');
        } catch (error) {
            this.showToast('Failed to rename conversation', 'error');
            console.error('Failed to rename conversation:', error);
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

    // =========================================================================
    // Go Game Methods
    // =========================================================================

    async openGoGameModal() {
        if (!this.currentConversationId) {
            this.showToast('Please select or create a conversation first', 'warning');
            return;
        }

        this.elements.goGameModal.classList.add('active');

        // Try to load existing game for this conversation
        try {
            const games = await api.listGoGames(this.currentConversationId, 'in_progress');
            if (games && games.length > 0) {
                // Load the most recent in-progress game
                await this.loadGoGame(games[0].id);
            } else {
                // No game, show empty board
                this.currentGoGame = null;
                this.currentGoGameId = null;
                this.renderEmptyGoBoard(19);
                this.updateGoGameUI();
            }
        } catch (error) {
            console.error('Error loading Go games:', error);
            this.renderEmptyGoBoard(19);
            this.updateGoGameUI();
        }
    }

    closeGoGameModal() {
        this.elements.goGameModal.classList.remove('active');
    }

    async loadGoGame(gameId) {
        try {
            const game = await api.getGoGame(gameId);
            this.currentGoGame = game;
            this.currentGoGameId = game.id;
            this.renderGoBoard(game);
            this.updateGoGameUI();
        } catch (error) {
            console.error('Error loading Go game:', error);
            this.showToast('Failed to load Go game', 'error');
        }
    }

    renderGoBoardWithLabels(size, boardState = null, lastMoveRow = null, lastMoveCol = null, koRow = null, koCol = null) {
        const container = this.elements.goBoard.parentElement;

        // Remove existing wrapper if present
        const existingWrapper = container.querySelector('.go-board-wrapper');
        if (existingWrapper) {
            existingWrapper.remove();
        }

        const board = this.elements.goBoard;
        board.innerHTML = '';
        board.className = `go-board size-${size}`;

        const colLabels = 'ABCDEFGHJKLMNOPQRST'.slice(0, size);

        // Star point positions for different board sizes
        const starPoints = {
            9: [[2, 2], [2, 6], [4, 4], [6, 2], [6, 6]],
            13: [[3, 3], [3, 9], [6, 6], [9, 3], [9, 9]],
            19: [[3, 3], [3, 9], [3, 15], [9, 3], [9, 9], [9, 15], [15, 3], [15, 9], [15, 15]]
        };

        for (let row = 0; row < size; row++) {
            for (let col = 0; col < size; col++) {
                const cell = document.createElement('div');
                cell.className = 'go-intersection';
                cell.dataset.row = row;
                cell.dataset.col = col;
                cell.dataset.coord = colLabels[col] + (size - row);

                // Mark edges
                if (row === 0) cell.classList.add('edge-top');
                if (row === size - 1) cell.classList.add('edge-bottom');
                if (col === 0) cell.classList.add('edge-left');
                if (col === size - 1) cell.classList.add('edge-right');

                // Mark star points (only on empty intersections)
                const stone = boardState ? boardState[row][col] : 0;
                const isStarPoint = starPoints[size]?.some(([r, c]) => r === row && c === col);
                if (isStarPoint && stone === 0) {
                    cell.classList.add('star-point');
                    const marker = document.createElement('div');
                    marker.className = 'star-marker';
                    cell.appendChild(marker);
                }

                // Mark ko point
                if (koRow === row && koCol === col) {
                    cell.classList.add('ko-point');
                }

                // Add stone if occupied
                if (stone === 1 || stone === 2) {
                    cell.classList.add('occupied');
                    const stoneEl = document.createElement('div');
                    stoneEl.className = `go-stone ${stone === 1 ? 'black' : 'white'}`;

                    // Mark last move
                    if (lastMoveRow === row && lastMoveCol === col) {
                        stoneEl.classList.add('last-move');
                    }

                    cell.appendChild(stoneEl);
                }

                // Click handler for making moves
                cell.addEventListener('click', () => this.handleBoardClick(row, col));

                board.appendChild(cell);
            }
        }

        // Create wrapper with coordinate labels
        const wrapper = document.createElement('div');
        wrapper.className = 'go-board-wrapper';

        // Top column labels
        const topLabels = document.createElement('div');
        topLabels.className = 'go-coord-labels go-col-labels';
        for (let col = 0; col < size; col++) {
            const label = document.createElement('span');
            label.className = 'go-coord-label';
            label.textContent = colLabels[col];
            topLabels.appendChild(label);
        }

        // Bottom column labels
        const bottomLabels = document.createElement('div');
        bottomLabels.className = 'go-coord-labels go-col-labels';
        for (let col = 0; col < size; col++) {
            const label = document.createElement('span');
            label.className = 'go-coord-label';
            label.textContent = colLabels[col];
            bottomLabels.appendChild(label);
        }

        // Left row labels
        const leftLabels = document.createElement('div');
        leftLabels.className = 'go-coord-labels go-row-labels';
        for (let row = 0; row < size; row++) {
            const label = document.createElement('span');
            label.className = 'go-coord-label';
            label.textContent = (size - row).toString();
            leftLabels.appendChild(label);
        }

        // Right row labels
        const rightLabels = document.createElement('div');
        rightLabels.className = 'go-coord-labels go-row-labels';
        for (let row = 0; row < size; row++) {
            const label = document.createElement('span');
            label.className = 'go-coord-label';
            label.textContent = (size - row).toString();
            rightLabels.appendChild(label);
        }

        // Build wrapper structure
        wrapper.appendChild(topLabels);

        const middleRow = document.createElement('div');
        middleRow.className = 'go-board-middle';
        middleRow.appendChild(leftLabels);

        // Move board into wrapper
        board.remove();
        middleRow.appendChild(board);
        middleRow.appendChild(rightLabels);
        wrapper.appendChild(middleRow);

        wrapper.appendChild(bottomLabels);

        // Insert wrapper before coordinate input
        const coordInput = container.querySelector('.go-coordinate-input');
        container.insertBefore(wrapper, coordInput);
    }

    renderEmptyGoBoard(size) {
        this.renderGoBoardWithLabels(size);
    }

    renderGoBoard(game) {
        if (!game) return;

        const size = game.board_size;
        const boardState = game.board_state;

        // Parse last move from move history
        let lastMoveRow = null, lastMoveCol = null;
        if (game.move_history) {
            const moves = game.move_history.split(';').filter(m => m);
            if (moves.length > 0) {
                const lastMove = moves[moves.length - 1];
                const coordMatch = lastMove.match(/\[([a-s])([a-s])\]/);
                if (coordMatch) {
                    const sgfCols = 'abcdefghijklmnopqrs';
                    lastMoveCol = sgfCols.indexOf(coordMatch[1]);
                    lastMoveRow = sgfCols.indexOf(coordMatch[2]);
                }
            }
        }

        // Parse ko point
        let koRow = null, koCol = null;
        if (game.ko_point) {
            const parts = game.ko_point.split(',');
            koRow = parseInt(parts[0]);
            koCol = parseInt(parts[1]);
        }

        this.renderGoBoardWithLabels(size, boardState, lastMoveRow, lastMoveCol, koRow, koCol);
    }

    updateGoGameUI() {
        const game = this.currentGoGame;

        if (!game) {
            this.elements.goCurrentPlayer.textContent = 'No game active';
            this.elements.goCurrentPlayer.className = 'go-player-indicator';
            this.elements.goMoveCount.textContent = 'Move: 0';
            this.elements.goBlackCaptures.textContent = '0';
            this.elements.goWhiteCaptures.textContent = '0';
            this.elements.goGameStatus.textContent = '';
            this.elements.goPassBtn.disabled = true;
            this.elements.goResignBtn.disabled = true;
            this.elements.goScoreBtn.disabled = true;
            this.elements.goPlayMoveBtn.disabled = true;
            return;
        }

        const isInProgress = game.game_status === 'in_progress';

        // Current player
        const playerText = game.current_player === 'black' ? 'Black to play' : 'White to play';
        this.elements.goCurrentPlayer.textContent = playerText;
        this.elements.goCurrentPlayer.className = `go-player-indicator ${game.current_player}`;

        // Move count
        this.elements.goMoveCount.textContent = `Move: ${game.move_count}`;

        // Captures
        this.elements.goBlackCaptures.textContent = game.black_captures;
        this.elements.goWhiteCaptures.textContent = game.white_captures;

        // Game status
        let statusText = '';
        if (game.game_status === 'finished_resignation') {
            statusText = `${game.winner.charAt(0).toUpperCase() + game.winner.slice(1)} wins by resignation`;
        } else if (game.game_status === 'finished_pass') {
            statusText = 'Game ended - both players passed';
        } else if (game.game_status === 'finished_scored') {
            statusText = `${game.winner.charAt(0).toUpperCase() + game.winner.slice(1)} wins! Black: ${game.black_score}, White: ${game.white_score}`;
        }
        this.elements.goGameStatus.textContent = statusText;

        // Button states
        this.elements.goPassBtn.disabled = !isInProgress;
        this.elements.goResignBtn.disabled = !isInProgress;
        this.elements.goScoreBtn.disabled = isInProgress;
        this.elements.goPlayMoveBtn.disabled = !isInProgress;
    }

    getHumanPlayerColor() {
        // Determine which color the human plays
        // Human plays black unless black_entity_id is set and white_entity_id is not
        const game = this.currentGoGame;
        if (!game) return 'black';

        if (game.black_entity_id && !game.white_entity_id) {
            return 'white';
        }
        return 'black'; // Default: human plays black
    }

    isHumanTurn() {
        const game = this.currentGoGame;
        if (!game) return false;

        // If both colors have entities, human can't move (AI vs AI)
        if (game.black_entity_id && game.white_entity_id) {
            return false;
        }

        // If neither color has an entity, human plays both (local multiplayer)
        if (!game.black_entity_id && !game.white_entity_id) {
            return true;
        }

        // Human plays whichever color doesn't have an entity
        const humanColor = this.getHumanPlayerColor();
        return game.current_player === humanColor;
    }

    async handleBoardClick(row, col) {
        if (!this.currentGoGame || this.currentGoGame.game_status !== 'in_progress') {
            return;
        }

        if (!this.isHumanTurn()) {
            this.showToast(`Waiting for ${this.currentGoGame.current_player === 'black' ? 'Black' : 'White'} (AI) to play`, 'info');
            return;
        }

        const size = this.currentGoGame.board_size;
        const colLabels = 'ABCDEFGHJKLMNOPQRST'.slice(0, size);
        const coord = colLabels[col] + (size - row);

        await this.makeGoMove(coord);
    }

    async playGoMove() {
        const coord = this.elements.goMoveInput.value.trim().toUpperCase();
        if (!coord) {
            this.showToast('Enter a coordinate (e.g., D4)', 'warning');
            return;
        }

        if (!this.isHumanTurn()) {
            this.showToast(`Waiting for ${this.currentGoGame.current_player === 'black' ? 'Black' : 'White'} (AI) to play`, 'info');
            return;
        }

        await this.makeGoMove(coord);
        this.elements.goMoveInput.value = '';
    }

    async makeGoMove(coord) {
        if (!this.currentGoGameId) {
            this.showToast('No active game', 'warning');
            return;
        }

        try {
            const result = await api.makeGoMove(this.currentGoGameId, coord);
            if (result.success && result.game) {
                this.currentGoGame = result.game;
                this.renderGoBoard(result.game);
                this.updateGoGameUI();

                // If it's now the AI's turn, trigger AI response
                if (!this.isHumanTurn() && result.game.game_status === 'in_progress') {
                    await this.triggerGoAIMove();
                }
            } else {
                this.showToast(result.error || 'Invalid move', 'error');
            }
        } catch (error) {
            console.error('Error making move:', error);
            this.showToast(error.message || 'Failed to make move', 'error');
        }
    }

    async triggerGoAIMove() {
        // Trigger the AI to make a move in the Go game
        if (!this.currentGoGame || !this.currentConversationId) {
            return;
        }

        // Get the entity that should play
        const aiEntityId = this.currentGoGame.current_player === 'black'
            ? this.currentGoGame.black_entity_id
            : this.currentGoGame.white_entity_id;

        if (!aiEntityId) {
            // No AI assigned to this color
            return;
        }

        // Show loading indicator
        this.showToast('AI is thinking...', 'info');

        // Close the Go game modal and switch to chat
        this.closeGoGameModal();

        // Send a message to trigger the AI to make a move
        const message = `It's your turn in the Go game. The current board position is shown in your available tools. Please analyze the position and make your move using the go_make_move tool. Game ID: ${this.currentGoGameId}`;

        // Set the input and send the message
        this.elements.input.value = message;
        await this.sendMessage();

        // After the AI responds, refresh the Go game state
        // Use a delay to allow the message to complete
        setTimeout(async () => {
            try {
                if (this.currentGoGameId) {
                    await this.loadGoGame(this.currentGoGameId);
                }
            } catch (error) {
                console.error('Error refreshing Go game after AI move:', error);
            }
        }, 2000);
    }

    async passGoTurn() {
        if (!this.currentGoGameId) return;

        try {
            const result = await api.passGoTurn(this.currentGoGameId);
            if (result.success && result.game) {
                this.currentGoGame = result.game;
                this.renderGoBoard(result.game);
                this.updateGoGameUI();

                if (result.game_ended) {
                    this.showToast('Both players passed - game ended', 'info');
                }
            }
        } catch (error) {
            console.error('Error passing:', error);
            this.showToast('Failed to pass', 'error');
        }
    }

    async resignGoGame() {
        if (!this.currentGoGameId) return;

        if (!confirm('Are you sure you want to resign?')) return;

        try {
            const result = await api.resignGoGame(this.currentGoGameId);
            if (result.success && result.game) {
                this.currentGoGame = result.game;
                this.renderGoBoard(result.game);
                this.updateGoGameUI();
                this.showToast(`${result.winner.charAt(0).toUpperCase() + result.winner.slice(1)} wins by resignation`, 'info');
            }
        } catch (error) {
            console.error('Error resigning:', error);
            this.showToast('Failed to resign', 'error');
        }
    }

    async scoreGoGame() {
        if (!this.currentGoGameId) return;

        try {
            const result = await api.scoreGoGame(this.currentGoGameId);
            if (result.success) {
                this.currentGoGame = result.game;
                this.renderGoBoard(result.game);
                this.updateGoGameUI();

                const scoreMsg = `Black: ${result.black_score}, White: ${result.white_score}. ${result.winner.charAt(0).toUpperCase() + result.winner.slice(1)} wins!`;
                this.showToast(scoreMsg, 'success');
            }
        } catch (error) {
            console.error('Error scoring game:', error);
            this.showToast('Failed to score game', 'error');
        }
    }

    async createNewGoGame() {
        if (!this.currentConversationId) {
            this.showToast('Please select or create a conversation first', 'warning');
            return;
        }

        const boardSize = parseInt(this.elements.goBoardSize.value);
        const komi = parseFloat(this.elements.goKomi.value);
        const scoring = this.elements.goScoring.value;

        try {
            const game = await api.createGoGame({
                conversation_id: this.currentConversationId,
                board_size: boardSize,
                komi: komi,
                scoring_method: scoring,
            });

            this.currentGoGame = game;
            this.currentGoGameId = game.id;
            this.renderGoBoard(game);
            this.updateGoGameUI();
            this.showToast('New Go game created', 'success');
        } catch (error) {
            console.error('Error creating Go game:', error);
            this.showToast('Failed to create Go game', 'error');
        }
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new App();
});
