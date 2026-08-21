/**
 * State Management Module
 * Centralized state store for the application
 */

/**
 * Application state store
 * This is a simple state object that modules can read from and write to
 */
export const state = {
    // Current conversation
    currentConversationId: null,
    conversations: [],

    // Entity state
    selectedEntityId: null,
    entities: [],
    entitySystemPrompts: {},
    // Per-entity thinking effort, mirrored from the entities API response.
    // null for an entity means "use the backend default" (thinkingEffortDefault).
    entityThinkingEfforts: {},
    // The level the backend applies when an entity has none of its own
    thinkingEffortDefault: 'high',
    // Levels the backend accepts, ascending (overwritten from the API response)
    thinkingEffortLevels: ['low', 'medium', 'high', 'xhigh', 'max'],
    entityModels: {},  // Per-entity model selection persistence
    // Configured default_model that was in effect when each entityModels entry
    // was saved. Used to detect when an entity's .env default_model has changed
    // so a now-stale saved selection can be dropped (letting the new .env
    // default take effect) instead of silently shadowing it.
    entityModelDefaults: {},

    // Multi-entity state
    isMultiEntityMode: false,
    currentConversationEntities: [],
    pendingResponderId: null,
    pendingRegenerateMessageId: null,
    pendingMultiEntityAction: null,
    responderSelectorMode: null,
    pendingMessageContent: null,
    pendingMessageAttachments: null,
    pendingUserMessageEl: null,
    pendingActionAfterEntitySelection: null,
    pendingMessageForEntitySelection: null,
    pendingAttachmentsForEntitySelection: null,

    // UI state
    isLoading: false,
    streamAbortController: null,
    importAbortController: null,

    // Message-entry height chosen by dragging the divider above the input area.
    // null = default auto-resize behavior; a number is a user-fixed pixel height
    // for the message textarea that persists across messages and restarts.
    messageInputHeight: null,

    // Settings
    settings: {
        model: 'claude-sonnet-4-5-20250929',
        temperature: 1.0,
        maxTokens: 4096,
        systemPrompt: null,
        conversationType: 'normal',
        verbosity: 'medium',
        // null = follow the backend default for this entity
        thinkingEffort: null,
        researcherName: '',
        enterInsertsNewline: false,
    },

    // Memory state
    retrievedMemories: [],
    retrievedMemoriesByEntity: {},
    expandedMemoryIds: new Set(),

    // TTS state
    ttsEnabled: false,
    ttsProvider: null,
    ttsVoices: [],
    selectedVoiceId: null,
    localTtsServerHealthy: false,
    audioCache: new Map(),
    currentAudio: null,
    currentSpeakingBtn: null,

    // STT state
    dictationMode: 'none',
    isRecording: false,
    speechRecognition: null,
    mediaRecorder: null,
    audioChunks: [],

    // Attachments
    pendingAttachments: {
        images: [],
        files: []
    },

    // Go game reference
    goGame: null,

    // Pending actions for modals
    pendingArchiveId: null,
    pendingDeleteId: null,
    pendingRenameId: null,

    // Import state
    importFileContent: null,
    importPreviewData: null,

    // Orphan maintenance
    _orphanData: null,

    // Conversation loading tracking
    loadConversationsRequestId: 0,
    lastCreatedConversation: null,

    // App construction time
    constructedAt: Date.now(),

    // Config
    availableModels: [],
    providers: [],  // Provider info incl. per-provider default_model (from /chat/config)
    presets: [],    // System-prompt presets (from /config/presets) — backend is the source of truth
    attachmentConfig: null,  // Attachment limits/flags from /chat/config (null until loaded)
};

/**
 * Reset memory state (called when switching conversations)
 */
export function resetMemoryState() {
    state.retrievedMemories = [];
    state.retrievedMemoriesByEntity = {};
    state.expandedMemoryIds.clear();
}

/**
 * Reset attachment state
 */
export function resetAttachments() {
    // Revoke blob URLs for images
    for (const img of state.pendingAttachments.images) {
        if (img.previewUrl) {
            URL.revokeObjectURL(img.previewUrl);
        }
    }
    state.pendingAttachments = { images: [], files: [] };
}

/**
 * Clear audio cache
 */
export function clearAudioCache() {
    for (const cached of state.audioCache.values()) {
        if (cached.url) {
            URL.revokeObjectURL(cached.url);
        }
    }
    state.audioCache.clear();
}

// NOTE: Per-entity system prompts and thinking effort are NOT stored
// client-side. The backend
// (entity_settings table, /api/entities/{id}/system-prompt and
// /api/entities/{id}/thinking-effort) is the source of truth; the caches in
// state.entitySystemPrompts and state.entityThinkingEfforts are populated from
// the entities API response on load. See modules/entities.js and
// modules/settings.js.
//
// LEGACY_ENTITY_PROMPTS_KEY is only read once at startup to migrate any
// prompts a previous version saved in localStorage up to the backend.
export const LEGACY_ENTITY_PROMPTS_KEY = 'entity_system_prompts';

/**
 * Load entity models from localStorage
 */
export function loadEntityModelsFromStorage() {
    try {
        const saved = localStorage.getItem('entity_models');
        if (saved) {
            state.entityModels = JSON.parse(saved);
        }
        const savedDefaults = localStorage.getItem('entity_model_defaults');
        if (savedDefaults) {
            state.entityModelDefaults = JSON.parse(savedDefaults);
        }
    } catch (e) {
        console.warn('Failed to load entity models:', e);
    }
}

/**
 * Save entity models to localStorage
 */
export function saveEntityModelsToStorage() {
    try {
        localStorage.setItem('entity_models', JSON.stringify(state.entityModels));
        localStorage.setItem('entity_model_defaults', JSON.stringify(state.entityModelDefaults));
    } catch (e) {
        console.warn('Failed to save entity models:', e);
    }
}

/**
 * Resolve the persisted per-entity model selection for an entity, honoring the
 * researcher's UI choice while keeping the .env config authoritative.
 *
 * The per-entity model the researcher picks in the UI is persisted to
 * localStorage so it survives restarts. But that saved value must not
 * permanently shadow the entity's configured `default_model`: when the
 * researcher edits `default_model` in `.env` (PINECONE_INDEXES), the new
 * default should take effect. We detect that by recording the configured
 * default in effect when the selection was saved (entityModelDefaults). If the
 * entity's current configured default differs, the saved selection is stale —
 * we drop it (and its baseline) and return null so callers fall through to the
 * new default.
 *
 * Entries saved before this baseline existed have no recorded default, so they
 * are treated as stale on first load after upgrade — exactly the desired
 * migration (the .env default wins, then the next deliberate pick re-saves a
 * baseline).
 *
 * @param {Object} entity - Entity object (index_name, llm_provider, default_model)
 * @returns {string|null} - A valid saved model id, or null if none/stale/invalid
 */
export function getValidSavedEntityModel(entity) {
    if (!entity) return null;
    const entityId = entity.index_name;
    const savedModel = state.entityModels[entityId];
    if (savedModel === undefined) return null;

    // Drop a selection saved against a different (now-changed) .env default.
    const currentDefault = entity.default_model ?? null;
    const savedAgainstDefault = state.entityModelDefaults[entityId] ?? null;
    if (savedAgainstDefault !== currentDefault) {
        delete state.entityModels[entityId];
        delete state.entityModelDefaults[entityId];
        saveEntityModelsToStorage();
        return null;
    }

    // Only honor the saved model if it is still valid for this provider.
    const provider = entity.llm_provider || 'anthropic';
    const isValid = state.availableModels?.some(
        m => m.id === savedModel && m.provider === provider
    );
    return isValid ? savedModel : null;
}

/**
 * Load selected voice from localStorage
 */
export function loadSelectedVoiceFromStorage() {
    try {
        const saved = localStorage.getItem('selected_voice_id');
        if (saved) {
            state.selectedVoiceId = saved;
        }
    } catch (e) {
        console.warn('Failed to load selected voice:', e);
    }
}

/**
 * Save selected voice to localStorage
 */
export function saveSelectedVoiceToStorage() {
    try {
        if (state.selectedVoiceId) {
            localStorage.setItem('selected_voice_id', state.selectedVoiceId);
        } else {
            localStorage.removeItem('selected_voice_id');
        }
    } catch (e) {
        console.warn('Failed to save selected voice:', e);
    }
}

/**
 * Load researcher name from localStorage
 * @returns {string|null} The saved researcher name or null
 */
export function loadResearcherName() {
    const savedName = localStorage.getItem('researcher_name');
    if (savedName) {
        state.settings.researcherName = savedName;
        return savedName;
    }
    return null;
}

/**
 * Save researcher name to localStorage
 * @param {string} name - Researcher name to save
 */
export function saveResearcherName(name) {
    if (name !== undefined) {
        state.settings.researcherName = name;
    }
    localStorage.setItem('researcher_name', state.settings.researcherName || '');
}

const MESSAGE_INPUT_HEIGHT_KEY = 'message_input_height';

/**
 * Load the user-chosen message-entry height from localStorage into state.
 * @returns {number|null} The saved height in pixels, or null if unset/invalid
 */
export function loadMessageInputHeight() {
    try {
        const saved = localStorage.getItem(MESSAGE_INPUT_HEIGHT_KEY);
        const height = saved !== null ? parseInt(saved, 10) : NaN;
        state.messageInputHeight = Number.isFinite(height) && height > 0 ? height : null;
    } catch (e) {
        console.warn('Failed to load message input height:', e);
        state.messageInputHeight = null;
    }
    return state.messageInputHeight;
}

/**
 * Persist the user-chosen message-entry height to localStorage.
 * Pass null to clear the saved height and restore default auto-resize.
 * @param {number|null} height - Height in pixels, or null to reset
 */
export function saveMessageInputHeight(height) {
    if (height !== undefined) {
        state.messageInputHeight = Number.isFinite(height) && height > 0 ? Math.round(height) : null;
    }
    try {
        if (state.messageInputHeight) {
            localStorage.setItem(MESSAGE_INPUT_HEIGHT_KEY, String(state.messageInputHeight));
        } else {
            localStorage.removeItem(MESSAGE_INPUT_HEIGHT_KEY);
        }
    } catch (e) {
        console.warn('Failed to save message input height:', e);
    }
}

/**
 * Load the Enter-key behavior preference from localStorage
 * @returns {boolean} True if Enter should insert a newline instead of sending
 */
export function loadEnterInsertsNewline() {
    const saved = localStorage.getItem('enter_inserts_newline');
    state.settings.enterInsertsNewline = saved === 'true';
    return state.settings.enterInsertsNewline;
}

/**
 * Save the Enter-key behavior preference to localStorage
 * @param {boolean} enabled - True if Enter should insert a newline instead of sending
 */
export function saveEnterInsertsNewline(enabled) {
    if (enabled !== undefined) {
        state.settings.enterInsertsNewline = !!enabled;
    }
    localStorage.setItem('enter_inserts_newline', state.settings.enterInsertsNewline ? 'true' : 'false');
}
