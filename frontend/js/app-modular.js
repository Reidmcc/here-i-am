/**
 * Here I Am - Main Application Entry Point
 * This file orchestrates all modules and serves as the central coordinator.
 */

// Import modules
import { state, resetMemoryState, loadEntitySystemPromptsFromStorage, loadSelectedVoiceFromStorage, loadResearcherName } from './modules/state.js';
import { showToast, showLoading, setToastContainer, escapeHtml, renderMarkdown, truncateText, stripMarkdown } from './modules/utils.js';
import { loadTheme, getCurrentTheme, setTheme } from './modules/theme.js';
import { setElements as setModalElements, showModal, hideModal, closeActiveModal, isModalOpen, closeAllDropdowns } from './modules/modals.js';
import {
    setElements as setEntityElements,
    setCallbacks as setEntityCallbacks,
    loadEntities,
    handleEntityChange,
    updateEntityDescription,
    getEntityLabel,
    showMultiEntityModal,
    hideMultiEntityModal,
    confirmMultiEntitySelection,
    showEntityResponderSelector,
    hideEntityResponderSelector,
    selectResponder,
    cancelResponderSelection
} from './modules/entities.js';
import {
    setElements as setConversationElements,
    setCallbacks as setConversationCallbacks,
    loadConversations,
    renderConversationList,
    toggleConversationDropdown,
    createNewConversation,
    loadConversation,
    showArchiveModalForConversation,
    archiveConversation,
    showRenameModalForConversation,
    renameConversation,
    showDeleteModal,
    deleteConversation,
    showArchivedModal,
    loadArchivedConversations,
    unarchiveConversation,
    exportConversation as exportConv
} from './modules/conversations.js';
import {
    setElements as setMessageElements,
    setCallbacks as setMessageCallbacks,
    addMessage,
    addToolMessage,
    createStreamingMessage,
    addTypingIndicator,
    clearMessages,
    removeRegenerateButtons,
    copyMessage,
    updateAssistantMessageActions,
    isNearBottom,
    scrollToBottom
} from './modules/messages.js';
import {
    setElements as setAttachmentElements,
    setCallbacks as setAttachmentCallbacks,
    initDragAndDrop,
    handleFileSelect,
    processFiles,
    updateAttachmentPreview,
    removeAttachment,
    clearAttachments,
    hasAttachments,
    getAttachmentsForRequest,
    buildDisplayContentWithAttachments
} from './modules/attachments.js';
import {
    setElements as setChatElements,
    setCallbacks as setChatCallbacks,
    sendMessage,
    sendMessageWithResponder,
    stopGeneration,
    regenerateMessage,
    regenerateMessageWithEntity,
    performRegeneration,
    startEditMessage,
    startContinuationMode
} from './modules/chat.js';
import {
    setElements as setMemoryElements,
    updateMemoriesPanel,
    handleMemoryUpdate,
    showMemoriesModal,
    searchMemories,
    checkForOrphans,
    cleanupOrphans,
    loadMemoryStats,
    loadMemoryList
} from './modules/memories.js';
import {
    setElements as setVoiceElements,
    checkTTSStatus,
    updateTTSUI,
    speakMessage,
    stopSpeaking,
    loadStyleTTS2Settings,
    saveStyleTTS2Settings,
    showVoiceCloneModal,
    hideVoiceCloneModal,
    updateVoiceCloneButton,
    createVoiceClone,
    deleteVoice,
    showVoiceEditModal,
    hideVoiceEditModal,
    saveVoiceEdit,
    checkSTTStatus,
    toggleVoiceDictation
} from './modules/voice.js';
import {
    setElements as setSettingsElements,
    setCallbacks as setSettingsCallbacks,
    applySettings,
    loadPreset,
    modelSupportsTemperature,
    updateTemperatureControlState,
    updateVerbosityControlState,
    updateTemperatureRange,
    syncTemperatureInputs,
    updateModelIndicator,
    populateModelSelect,
    initializeSettingsUI
} from './modules/settings.js';
import {
    setElements as setImportExportElements,
    setCallbacks as setImportExportCallbacks,
    exportConversation,
    handleImportFileChange,
    previewImportFile,
    toggleAllImportCheckboxes,
    importExternalConversations,
    cancelImport,
    resetImportModal
} from './modules/import-export.js';
import {
    setElements as setGamesElements,
    setCallbacks as setGamesCallbacks,
    checkGamesStatus,
    loadGames,
    showGamesModal,
    hideGamesModal,
    viewGame,
    hideGameBoardModal,
    linkGameToConversation,
    unlinkGame,
    goToGameConversation,
    updateGameIndicator,
    refreshGames,
    getConversationBoardState
} from './modules/games.js';

// Reference to global API client
const api = window.api;

/**
 * Main Application Class
 * Coordinates all modules and handles initialization
 */
class App {
    constructor() {
        this.cacheElements();
        this.initializeModules();
        this.bindEvents();
        this.initialize();
    }

    /**
     * Cache all DOM element references
     */
    cacheElements() {
        this.elements = {
            // Main layout
            sidebar: document.getElementById('sidebar'),
            mainContent: document.getElementById('main-content'),
            messagesContainer: document.getElementById('messages-container'),
            messages: document.getElementById('messages'),
            welcomeMessage: document.getElementById('welcome-message'),

            // Entity selector
            entitySelect: document.getElementById('entity-select'),
            entityDescription: document.getElementById('entity-description'),

            // Conversation list
            conversationList: document.getElementById('conversation-list'),
            newConversationBtn: document.getElementById('new-conversation'),
            conversationTitle: document.getElementById('conversation-title'),
            conversationMeta: document.getElementById('conversation-meta'),

            // Chat input
            messageInput: document.getElementById('message-input'),
            sendBtn: document.getElementById('send-btn'),
            stopBtn: document.getElementById('stop-btn'),
            continueBtn: document.getElementById('continue-btn'),
            tokenCount: document.getElementById('token-count'),
            modelIndicator: document.getElementById('model-indicator'),

            // Attachments
            attachBtn: document.getElementById('attach-btn'),
            fileInput: document.getElementById('file-input'),
            attachmentPreview: document.getElementById('attachment-preview'),
            attachmentList: document.getElementById('attachment-list'),

            // Voice
            dictationBtn: document.getElementById('dictation-btn'),

            // Sidebar buttons
            settingsBtn: document.getElementById('settings-btn'),
            memoriesBtn: document.getElementById('memories-btn'),
            exportBtn: document.getElementById('export-btn'),
            archivedBtn: document.getElementById('archived-btn'),

            // Settings modal
            settingsModal: document.getElementById('settings-modal'),
            modelSelect: document.getElementById('model-select'),
            temperatureInput: document.getElementById('temperature-input'),
            temperatureNumber: document.getElementById('temperature-number'),
            maxTokensInput: document.getElementById('max-tokens-input'),
            conversationTypeSelect: document.getElementById('conversation-type-select'),
            systemPromptInput: document.getElementById('system-prompt-input'),
            systemPromptLabel: document.getElementById('system-prompt-label'),
            systemPromptHelp: document.getElementById('system-prompt-help'),
            presetSelect: document.getElementById('preset-select'),
            themeSelect: document.getElementById('theme-select'),
            verbositySelect: document.getElementById('verbosity-select'),
            researcherNameInput: document.getElementById('researcher-name-input'),
            voiceSelect: document.getElementById('voice-select'),
            voiceSelectGroup: document.getElementById('voice-select-group'),

            // StyleTTS 2 parameters
            styletts2ParamsGroup: document.getElementById('styletts2-params-group'),
            styletts2Alpha: document.getElementById('styletts2-alpha'),
            styletts2Beta: document.getElementById('styletts2-beta'),
            styletts2DiffusionSteps: document.getElementById('styletts2-diffusion-steps'),

            // GitHub settings
            githubReposContainer: document.getElementById('github-repos-container'),
            githubRateLimits: document.getElementById('github-rate-limits'),

            // Memories modal
            memoriesModal: document.getElementById('memories-modal'),

            // Memory panel (sidebar)
            memoryCount: document.getElementById('memory-count'),
            memoriesContent: document.getElementById('memories-content'),
            toggleMemoryPanelBtn: document.getElementById('toggle-memory-panel'),

            // Archive modal
            archiveModal: document.getElementById('archive-modal'),
            archiveConversationTitle: document.getElementById('archive-conversation-title'),

            // Rename modal
            renameModal: document.getElementById('rename-modal'),
            renameInput: document.getElementById('rename-input'),

            // Delete modal
            deleteModal: document.getElementById('delete-modal'),
            deleteConversationTitle: document.getElementById('delete-conversation-title'),

            // Archived modal
            archivedModal: document.getElementById('archived-modal'),
            archivedList: document.getElementById('archived-list'),

            // Multi-entity modal
            multiEntityModal: document.getElementById('multi-entity-modal'),
            multiEntityList: document.getElementById('multi-entity-list'),
            multiEntityConfirm: document.getElementById('multi-entity-confirm'),

            // Entity responder selector
            entityResponderSelector: document.getElementById('entity-responder-selector'),
            entityResponderList: document.getElementById('entity-responder-list'),
            entityResponderPrompt: document.getElementById('entity-responder-prompt'),
            cancelResponderBtn: document.getElementById('cancel-responder-btn'),

            // Voice clone modal
            voiceCloneModal: document.getElementById('voice-clone-modal'),
            voiceCloneFile: document.getElementById('voice-clone-file'),
            voiceCloneName: document.getElementById('voice-clone-name'),
            voiceCloneDescription: document.getElementById('voice-clone-description'),
            voiceCloneTemperature: document.getElementById('voice-clone-temperature'),
            voiceCloneSpeed: document.getElementById('voice-clone-speed'),
            voiceCloneLengthPenalty: document.getElementById('voice-clone-length-penalty'),
            voiceCloneRepetitionPenalty: document.getElementById('voice-clone-repetition-penalty'),
            voiceCloneStatus: document.getElementById('voice-clone-status'),
            createVoiceCloneBtn: document.getElementById('create-voice-clone-btn'),

            // Voice edit modal
            voiceEditModal: document.getElementById('voice-edit-modal'),
            voiceEditName: document.getElementById('voice-edit-name'),
            voiceEditDescription: document.getElementById('voice-edit-description'),
            voiceEditTemperature: document.getElementById('voice-edit-temperature'),
            voiceEditSpeed: document.getElementById('voice-edit-speed'),
            voiceEditLengthPenalty: document.getElementById('voice-edit-length-penalty'),
            voiceEditRepetitionPenalty: document.getElementById('voice-edit-repetition-penalty'),
            voiceEditAlpha: document.getElementById('voice-edit-alpha'),
            voiceEditBeta: document.getElementById('voice-edit-beta'),
            voiceEditDiffusionSteps: document.getElementById('voice-edit-diffusion-steps'),
            saveVoiceEditBtn: document.getElementById('save-voice-edit-btn'),
            deleteVoiceBtn: document.getElementById('delete-voice-btn'),

            // Import modal elements
            importModal: document.getElementById('import-modal'),
            importFile: document.getElementById('import-file'),
            importSource: document.getElementById('import-source'),
            importAllowReimport: document.getElementById('import-allow-reimport'),
            importPreviewBtn: document.getElementById('import-preview-btn'),
            importStatus: document.getElementById('import-status'),
            importStep1: document.getElementById('import-step-1'),
            importStep2: document.getElementById('import-step-2'),
            importPreviewInfo: document.getElementById('import-preview-info'),
            importConversationList: document.getElementById('import-conversation-list'),
            importBtn: document.getElementById('import-btn'),
            importCancelBtn: document.getElementById('import-cancel-btn'),
            importProgress: document.getElementById('import-progress'),
            importProgressBar: document.getElementById('import-progress-bar'),
            importProgressText: document.getElementById('import-progress-text'),

            // Games (OGS Integration)
            gamesBtn: document.getElementById('games-btn'),
            gamesModal: document.getElementById('games-modal'),
            gamesList: document.getElementById('games-list'),
            eventsStatusContainer: document.getElementById('events-status-container'),
            refreshGamesBtn: document.getElementById('refresh-games-btn'),
            gameBoardModal: document.getElementById('game-board-modal'),
            gameBoardContainer: document.getElementById('game-board-container'),
            gameIndicator: document.getElementById('game-indicator'),

            // Toast container
            toastContainer: document.getElementById('toast-container'),
        };
    }

    /**
     * Initialize all modules with element references and callbacks
     */
    initializeModules() {
        // Set toast container for utils
        setToastContainer(this.elements.toastContainer);

        // Initialize modal module
        setModalElements(this.elements);

        // Initialize entity module
        setEntityElements(this.elements);
        setEntityCallbacks({
            onEntityLoaded: (entities) => this.onEntitiesLoaded(entities),
            onEntityChanged: (entityId) => this.onEntityChanged(entityId),
            onMultiEntityConfirmed: () => this.onMultiEntityConfirmed(),
            onResponderSelected: () => this.onResponderSelected(),
            loadConversations: () => loadConversations(),
        });

        // Initialize conversation module
        setConversationElements(this.elements);
        setConversationCallbacks({
            onConversationLoad: (conv, messages) => this.onConversationLoaded(conv, messages),
            onConversationCreated: (conv) => this.onConversationCreated(conv),
            renderMessages: (messages, latestAssistantId) => this.renderMessages(messages, latestAssistantId),
            updateHeader: (conv) => this.updateConversationHeader(conv),
            updateMemoriesPanel: () => updateMemoriesPanel(),
            clearMessages: () => clearMessages(),
        });

        // Initialize message module
        setMessageElements(this.elements);
        setMessageCallbacks({
            onEditMessage: (el, id, content) => startEditMessage(el, id, content),
            onRegenerateMessage: (id) => regenerateMessage(id),
            onSpeakMessage: (content, btn, id) => speakMessage(content, btn, id),
            scrollToBottom: () => scrollToBottom(),
        });

        // Initialize attachment module
        setAttachmentElements(this.elements);
        setAttachmentCallbacks({
            onAttachmentsChanged: () => this.handleInputChange(),
        });

        // Initialize chat module
        setChatElements(this.elements);
        setChatCallbacks({
            onConversationUpdate: (conv) => this.onConversationUpdate(conv),
            renderConversationList: () => renderConversationList(),
            getEntityLabel: (id) => getEntityLabel(id),
            showEntityResponderSelector: (mode) => showEntityResponderSelector(mode),
            hideEntityResponderSelector: () => hideEntityResponderSelector(),
            handleInputChange: () => this.handleInputChange(),
            createNewConversation: (skip) => createNewConversation(skip),
            showMultiEntityModal: () => showMultiEntityModal(),
            copyMessage: (content, btn) => copyMessage(content, btn),
            speakMessage: (content, btn, id) => speakMessage(content, btn, id),
            getGoGameContext: async () => {
                if (!state.currentConversationId) return null;
                const boardState = await getConversationBoardState(state.currentConversationId);
                if (!boardState) return null;
                // Format board state for injection into message
                return `${boardState.board_ascii}\nYour color: ${boardState.our_color}\nYour turn: ${boardState.our_turn}`;
            },
        });

        // Initialize memory module
        setMemoryElements(this.elements);

        // Initialize voice module
        setVoiceElements(this.elements);

        // Initialize settings module
        setSettingsElements(this.elements);
        setSettingsCallbacks({
            updateModelIndicator: () => this.updateModelIndicator(),
            getMaxTemperatureForCurrentEntity: () => this.getMaxTemperatureForCurrentEntity(),
        });

        // Initialize import/export module
        setImportExportElements(this.elements);
        setImportExportCallbacks({
            loadConversations: () => loadConversations(),
        });

        // Initialize games module (OGS integration)
        setGamesElements(this.elements);
        setGamesCallbacks({
            loadConversations: () => loadConversations(),
            loadConversation: (id) => loadConversation(id),
        });
    }

    /**
     * Bind all event listeners
     */
    bindEvents() {
        // Send message
        this.elements.sendBtn?.addEventListener('click', () => sendMessage());
        this.elements.messageInput?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        this.elements.messageInput?.addEventListener('input', () => this.handleInputChange());

        // Stop generation
        this.elements.stopBtn?.addEventListener('click', () => stopGeneration());

        // Continue button (multi-entity)
        this.elements.continueBtn?.addEventListener('click', () => startContinuationMode());

        // New conversation
        this.elements.newConversationBtn?.addEventListener('click', () => createNewConversation());

        // Entity selector
        this.elements.entitySelect?.addEventListener('change', (e) => handleEntityChange(e.target.value));

        // Sidebar buttons
        this.elements.settingsBtn?.addEventListener('click', () => this.openSettings());
        this.elements.memoriesBtn?.addEventListener('click', () => showMemoriesModal());
        this.elements.exportBtn?.addEventListener('click', () => exportConversation());
        this.elements.archivedBtn?.addEventListener('click', () => showArchivedModal());

        // Attachments
        this.elements.attachBtn?.addEventListener('click', () => this.elements.fileInput?.click());
        this.elements.fileInput?.addEventListener('change', (e) => handleFileSelect(e));
        initDragAndDrop();

        // Voice dictation
        this.elements.dictationBtn?.addEventListener('click', () => toggleVoiceDictation());

        // Settings modal
        document.getElementById('close-settings')?.addEventListener('click', () => hideModal('settingsModal'));
        document.getElementById('apply-settings')?.addEventListener('click', () => applySettings());
        this.elements.temperatureInput?.addEventListener('input', (e) => {
            if (this.elements.temperatureNumber) {
                this.elements.temperatureNumber.value = e.target.value;
            }
        });
        this.elements.temperatureNumber?.addEventListener('input', (e) => {
            let value = parseFloat(e.target.value);
            const maxTemp = this.getMaxTemperatureForCurrentEntity();
            if (isNaN(value)) value = 1.0;
            if (value < 0) value = 0;
            if (value > maxTemp) value = maxTemp;
            if (this.elements.temperatureInput) {
                this.elements.temperatureInput.value = value;
            }
        });
        this.elements.modelSelect?.addEventListener('change', () => {
            updateTemperatureControlState();
            updateVerbosityControlState();
        });
        this.elements.presetSelect?.addEventListener('change', (e) => loadPreset(e.target.value));

        // Memories modal
        document.getElementById('close-memories')?.addEventListener('click', () => hideModal('memoriesModal'));
        document.getElementById('memory-search-btn')?.addEventListener('click', () => searchMemories());
        document.getElementById('check-orphans-btn')?.addEventListener('click', () => checkForOrphans());
        document.getElementById('cleanup-orphans-btn')?.addEventListener('click', () => cleanupOrphans());

        // Archive modal
        document.getElementById('close-archive')?.addEventListener('click', () => hideModal('archiveModal'));
        document.getElementById('cancel-archive')?.addEventListener('click', () => hideModal('archiveModal'));
        document.getElementById('confirm-archive')?.addEventListener('click', () => archiveConversation());

        // Rename modal
        document.getElementById('close-rename')?.addEventListener('click', () => hideModal('renameModal'));
        document.getElementById('cancel-rename')?.addEventListener('click', () => hideModal('renameModal'));
        document.getElementById('confirm-rename')?.addEventListener('click', () => renameConversation());
        this.elements.renameInput?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                renameConversation();
            }
        });

        // Archived modal
        document.getElementById('close-archived')?.addEventListener('click', () => hideModal('archivedModal'));

        // Delete modal
        document.getElementById('close-delete')?.addEventListener('click', () => hideModal('deleteModal'));
        document.getElementById('cancel-delete')?.addEventListener('click', () => hideModal('deleteModal'));
        document.getElementById('confirm-delete')?.addEventListener('click', () => deleteConversation());

        // Import modal
        this.elements.importFile?.addEventListener('change', () => handleImportFileChange());
        this.elements.importPreviewBtn?.addEventListener('click', () => previewImportFile());
        this.elements.importBtn?.addEventListener('click', () => importExternalConversations());
        this.elements.importCancelBtn?.addEventListener('click', () => cancelImport());
        document.getElementById('import-select-all-memory')?.addEventListener('change', (e) => toggleAllImportCheckboxes('memory', e.target.checked));
        document.getElementById('import-select-all-history')?.addEventListener('change', (e) => toggleAllImportCheckboxes('history', e.target.checked));
        document.getElementById('import-back-btn')?.addEventListener('click', () => resetImportModal());

        // Multi-entity modal
        document.getElementById('close-multi-entity')?.addEventListener('click', () => hideMultiEntityModal());
        document.getElementById('cancel-multi-entity')?.addEventListener('click', () => hideMultiEntityModal());
        this.elements.multiEntityConfirm?.addEventListener('click', () => confirmMultiEntitySelection());

        // Entity responder selector
        this.elements.cancelResponderBtn?.addEventListener('click', () => cancelResponderSelection());

        // Voice clone modal
        document.getElementById('close-voice-clone')?.addEventListener('click', () => hideVoiceCloneModal());
        document.getElementById('cancel-voice-clone')?.addEventListener('click', () => hideVoiceCloneModal());
        this.elements.voiceCloneFile?.addEventListener('change', () => updateVoiceCloneButton());
        this.elements.voiceCloneName?.addEventListener('input', () => updateVoiceCloneButton());
        this.elements.createVoiceCloneBtn?.addEventListener('click', () => createVoiceClone());
        document.getElementById('clone-voice-btn')?.addEventListener('click', () => showVoiceCloneModal());

        // Voice edit modal
        document.getElementById('close-voice-edit')?.addEventListener('click', () => hideVoiceEditModal());
        document.getElementById('cancel-voice-edit')?.addEventListener('click', () => hideVoiceEditModal());
        this.elements.saveVoiceEditBtn?.addEventListener('click', () => saveVoiceEdit());
        this.elements.deleteVoiceBtn?.addEventListener('click', () => deleteVoice());
        document.getElementById('edit-voice-btn')?.addEventListener('click', () => showVoiceEditModal());

        // Games modal (OGS Integration)
        this.elements.gamesBtn?.addEventListener('click', () => showGamesModal());
        document.getElementById('close-games')?.addEventListener('click', () => hideGamesModal());
        this.elements.refreshGamesBtn?.addEventListener('click', () => refreshGames());
        document.getElementById('close-game-board')?.addEventListener('click', () => hideGameBoardModal());
        document.getElementById('close-board-btn')?.addEventListener('click', () => hideGameBoardModal());

        // Global keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                closeActiveModal();
                closeAllDropdowns();
            }
        });

        // Click outside to close dropdowns
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.conversation-dropdown') && !e.target.closest('.conversation-menu-btn')) {
                closeAllDropdowns();
            }
        });
    }

    /**
     * Initialize the application
     */
    async initialize() {
        // Load theme
        loadTheme();
        if (this.elements.themeSelect) {
            this.elements.themeSelect.value = getCurrentTheme();
        }

        // Load saved state from localStorage
        loadEntitySystemPromptsFromStorage();
        loadSelectedVoiceFromStorage();
        const savedResearcherName = loadResearcherName();
        if (savedResearcherName) {
            state.settings.researcherName = savedResearcherName;
        }

        // Load entities and conversations
        await loadEntities();

        // Check TTS status
        await checkTTSStatus();

        // Check STT status
        await checkSTTStatus();

        // Check games/OGS status
        await checkGamesStatus();

        // Load GitHub repos info
        await this.loadGitHubReposInfo();
    }

    /**
     * Open settings modal
     */
    openSettings() {
        initializeSettingsUI();
        updateTemperatureRange();
        showModal('settingsModal');
    }

    /**
     * Handle input change (enable/disable send button)
     */
    handleInputChange() {
        const hasContent = this.elements.messageInput?.value.trim().length > 0;
        const hasAttachmentsFlag = hasAttachments();

        if (this.elements.sendBtn) {
            this.elements.sendBtn.disabled = state.isLoading || (!hasContent && !hasAttachmentsFlag);
        }

        // Auto-resize textarea
        if (this.elements.messageInput) {
            this.elements.messageInput.style.height = 'auto';
            this.elements.messageInput.style.height = Math.min(this.elements.messageInput.scrollHeight, 200) + 'px';
        }
    }

    /**
     * Get max temperature for current entity
     */
    getMaxTemperatureForCurrentEntity() {
        const entity = state.entities.find(e => e.index_name === state.selectedEntityId);
        // OpenAI supports temperature 0-2, Anthropic only 0-1
        if (entity && entity.llm_provider === 'openai') {
            return 2.0;
        }
        return 1.0;
    }

    /**
     * Update model indicator display
     */
    updateModelIndicator() {
        updateModelIndicator();
    }

    /**
     * Handle entities loaded callback
     */
    onEntitiesLoaded(entities) {
        // Entities loaded, load conversations for first entity
        if (entities.length > 0) {
            loadConversations();
        }
    }

    /**
     * Handle entity changed callback
     */
    onEntityChanged(entityId) {
        // Clear current conversation
        state.currentConversationId = null;
        clearMessages();
        resetMemoryState();

        // Update UI
        if (this.elements.conversationTitle) {
            this.elements.conversationTitle.textContent = 'Select a conversation';
        }
        if (this.elements.conversationMeta) {
            this.elements.conversationMeta.textContent = '';
        }

        // Load conversations for new entity
        loadConversations();
    }

    /**
     * Handle multi-entity selection confirmed
     */
    onMultiEntityConfirmed() {
        // Handle pending action after entity selection
        if (state.pendingActionAfterEntitySelection === 'sendMessage') {
            const content = state.pendingMessageForEntitySelection;
            const attachments = state.pendingAttachmentsForEntitySelection;
            state.pendingActionAfterEntitySelection = null;
            state.pendingMessageForEntitySelection = null;
            state.pendingAttachmentsForEntitySelection = null;

            if (content) {
                this.elements.messageInput.value = content;
            }
            // Trigger send with skip flag
            sendMessage(true);
        }
    }

    /**
     * Handle responder selected callback
     */
    onResponderSelected() {
        if (state.responderSelectorMode === 'regenerate') {
            regenerateMessageWithEntity();
        } else {
            sendMessageWithResponder();
        }
    }

    /**
     * Handle conversation loaded callback
     */
    onConversationLoaded(conversation, messages) {
        // Handle input change to update button states
        this.handleInputChange();

        // Update game indicator if conversation is linked to an OGS game
        updateGameIndicator(conversation);
    }

    /**
     * Handle conversation created callback
     */
    onConversationCreated(conversation) {
        this.handleInputChange();
    }

    /**
     * Handle conversation update callback
     */
    onConversationUpdate(conversation) {
        // Refresh conversation list if needed
        renderConversationList();
    }

    /**
     * Render messages from a loaded conversation
     * @param {Array} messages - Array of messages to render
     * @param {string|null} latestAssistantId - ID of latest assistant message
     */
    renderMessages(messages, latestAssistantId) {
        messages.forEach(msg => {
            if (msg.role === 'tool_use' || msg.role === 'tool_result') {
                // Skip tool messages for now (rendered inline with assistant)
                return;
            }

            const options = {
                messageId: msg.id,
                speakerEntityId: msg.speaker_entity_id,
            };

            // For multi-entity, add speaker label
            if (msg.speaker_entity_id && state.isMultiEntityMode) {
                const entity = state.currentConversationEntities.find(e => e.index_name === msg.speaker_entity_id);
                if (entity) {
                    options.speakerLabel = entity.label;
                }
            }

            // Only add actions to the latest assistant message
            if (msg.role === 'assistant' && msg.id === latestAssistantId) {
                options.isLatestAssistant = true;
            }

            addMessage(msg.role, msg.content, options);
        });

        scrollToBottom();
    }

    /**
     * Update the conversation header with conversation info
     * @param {Object} conversation - Conversation object
     */
    updateConversationHeader(conversation) {
        if (this.elements.conversationTitle) {
            this.elements.conversationTitle.textContent = conversation.title || 'Untitled Conversation';
        }

        if (this.elements.conversationMeta) {
            const date = new Date(conversation.created_at).toLocaleDateString();
            let meta = date;

            if (conversation.conversation_type === 'multi_entity' && conversation.entities) {
                const entityLabels = conversation.entities.map(e => e.label).join(' & ');
                meta = `${entityLabels} • ${date}`;
            }

            this.elements.conversationMeta.textContent = meta;
        }
    }

    /**
     * Load GitHub repos info for settings
     */
    async loadGitHubReposInfo() {
        if (!this.elements.githubReposContainer) return;

        try {
            const repos = await api.listGitHubRepos();
            if (repos && repos.length > 0) {
                this.elements.githubReposContainer.innerHTML = repos.map(repo => `
                    <div class="github-repo-item">
                        <span class="github-repo-label">${escapeHtml(repo.label)}</span>
                        <span class="github-repo-name">${escapeHtml(repo.owner)}/${escapeHtml(repo.repo)}</span>
                    </div>
                `).join('');
            } else {
                this.elements.githubReposContainer.innerHTML = '<p class="text-muted">No repositories configured</p>';
            }
        } catch (error) {
            console.error('Failed to load GitHub repos:', error);
            this.elements.githubReposContainer.innerHTML = '<p class="text-muted">Failed to load repositories</p>';
        }
    }
}

// Expose functions globally for onclick handlers
window.app = {
    removeAttachment: (type, index) => removeAttachment(type, index),
    toggleConversationDropdown: (id) => toggleConversationDropdown(id),
    showArchiveModalForConversation: (id, title) => showArchiveModalForConversation(id, title),
    showRenameModalForConversation: (id, title) => showRenameModalForConversation(id, title),
    showDeleteModal: (id, title) => showDeleteModal(id, title),
    loadConversation: (id) => loadConversation(id),
    unarchiveConversation: (id) => unarchiveConversation(id),
    selectResponder: (entityId) => selectResponder(entityId),
    // Games (OGS Integration)
    viewGame: (gameId) => viewGame(gameId),
    linkGameToConversation: (gameId, conversationId) => linkGameToConversation(gameId, conversationId),
    unlinkGame: (gameId) => unlinkGame(gameId),
    goToGameConversation: (conversationId) => goToGameConversation(conversationId),
};

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new App();
});
