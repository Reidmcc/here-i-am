<script>
    import { onMount } from 'svelte';
    import Sidebar from './components/layout/Sidebar.svelte';
    import ChatArea from './components/layout/ChatArea.svelte';
    import ToastContainer from './components/common/Toast.svelte';
    import LoadingOverlay from './components/common/Loading.svelte';
    import SettingsModal from './components/modals/SettingsModal.svelte';
    import MemoriesModal from './components/modals/MemoriesModal.svelte';
    import DeleteModal from './components/modals/DeleteModal.svelte';
    import RenameModal from './components/modals/RenameModal.svelte';
    import ArchivedModal from './components/modals/ArchivedModal.svelte';
    import MultiEntityModal from './components/modals/MultiEntityModal.svelte';
    import ImportExportModal from './components/modals/ImportExportModal.svelte';

    import { theme, isLoading, activeModal, showToast, availableModels, githubRepos, githubRateLimits } from './lib/stores/app.js';
    import { entities, selectedEntityId, isMultiEntityMode, resetMultiEntityState, currentConversationEntities, getEntity, entitySystemPrompts, entitySessionPreferences } from './lib/stores/entities.js';
    import { conversations, currentConversationId, currentConversation, resetConversationState, getNextRequestId, isValidRequestId } from './lib/stores/conversations.js';
    import { messages, resetMessagesState } from './lib/stores/messages.js';
    import { resetMemoriesState } from './lib/stores/memories.js';
    import { settings, setPresetsFromBackend, applyBackendDefaults, updateSettingQuietly } from './lib/stores/settings.js';
    import { ttsEnabled, ttsProvider, voices, sttEnabled, sttProvider, dictationMode, selectedVoiceId } from './lib/stores/voice.js';
    import * as api from './lib/api.js';

    // Debug helper - logs to browser console
    function debug(msg) {
        console.log('[App]', msg);
    }

    // Modal context for delete/rename (using $state for Svelte 5 reactivity)
    let deleteContext = $state({ title: '', id: null, type: 'conversation' });
    let renameContext = $state({ title: '', id: null, type: 'conversation' });

    // Initialization state (using $state for Svelte 5 reactivity)
    let initializationComplete = $state(false);
    let initializationError = $state(null);

    // Initialize theme on mount
    onMount(async () => {
        // Apply saved theme
        const savedTheme = $theme;
        if (savedTheme && savedTheme !== 'system') {
            document.documentElement.classList.add(`theme-${savedTheme}`);
        }

        // Load initial data
        await loadInitialData();
    });

    async function loadInitialData() {
        debug('loadInitialData starting...');

        try {
            // Load entities first
            debug('Loading entities...');
            const entityResponse = await api.listEntities();
            // API returns { entities: [...], default_entity: "..." }
            const entityList = entityResponse.entities || [];
            entities.set(entityList);
            debug('Entities loaded: ' + entityList.length);

            // Determine which entity to use (stored or first available)
            let activeEntityId = $selectedEntityId;
            if (!activeEntityId && entityList.length > 0) {
                activeEntityId = entityList[0].index_name;
                selectedEntityId.set(activeEntityId);
            }

            // Apply entity-specific settings (model, system prompt) on initial load
            // This ensures the correct model is used when restoring from localStorage
            if (activeEntityId) {
                applyEntitySettings(activeEntityId);
            }

            // Load conversations for selected entity
            if (activeEntityId) {
                debug('Loading conversations...');
                await loadConversations();
            }

            // Load config and presets (critical for UI)
            debug('Loading config...');
            const [configData, presetsData] = await Promise.all([
                api.getChatConfig(),
                api.getPresets()
            ]);
            debug('Config loaded');

            // Apply backend defaults to settings (only if user hasn't customized)
            if (configData) {
                applyBackendDefaults(configData);
                if (configData.available_models) {
                    availableModels.set(configData.available_models);
                }
            }

            // Set presets from backend (convert array to object keyed by slug)
            if (presetsData) {
                setPresetsFromBackend(presetsData);
            }

            debug('Initialization complete');
            initializationComplete = true;

            // Load TTS/STT status in background (non-blocking)
            // These are optional features and shouldn't delay the main UI
            loadTTSStatus().catch(() => {});
            loadSTTStatus().catch(() => {});
        } catch (error) {
            debug('Initialization error: ' + error.message);
            initializationError = error.message;
        }
    }

    async function loadConversations() {
        const requestId = getNextRequestId();
        try {
            const entityId = $isMultiEntityMode ? 'multi-entity' : $selectedEntityId;
            const data = await api.listConversations(50, 0, entityId);

            // Only update if this is still the current request
            if (isValidRequestId(requestId)) {
                conversations.set(data);
            }
        } catch (error) {
            const message = error?.message || String(error);
            showToast(`Failed to load conversations: ${message}`, 'error');
        }
    }

    async function loadTTSStatus() {
        try {
            const status = await api.getTTSStatus();
            ttsEnabled.set(status.enabled);
            ttsProvider.set(status.provider);

            if (status.enabled) {
                const voiceList = await api.listTTSVoices();
                voices.set(voiceList);
            }
        } catch (e) {
            // TTS not available
        }
    }

    async function loadSTTStatus() {
        try {
            const status = await api.getSTTStatus();
            sttEnabled.set(status.enabled);
            sttProvider.set(status.provider);
            dictationMode.set(status.mode || 'auto');
        } catch (e) {
            // STT not available
        }
    }

    // Handle entity change
    async function handleEntityChange(event) {
        const entityId = event.detail;

        if (entityId === 'multi-entity') {
            // Show multi-entity modal
            activeModal.set('multi-entity');
        } else {
            resetMultiEntityState();
            selectedEntityId.set(entityId);

            // Apply entity-specific model and system prompt
            applyEntitySettings(entityId);

            resetConversationState();
            resetMessagesState();
            resetMemoriesState();
            await loadConversations();
        }
    }

    // Apply entity-specific settings (model, temperature, maxTokens, voice, system prompt)
    function applyEntitySettings(entityId) {
        const entity = getEntity(entityId);
        if (!entity) return;

        // Get user's session preferences for this entity
        const prefs = entitySessionPreferences.getForEntity(entityId);

        // Model: user preference > entity default from .env
        if (prefs.model) {
            updateSettingQuietly('model', prefs.model);
            debug(`Applied user model preference: ${prefs.model}`);
        } else if (entity.default_model) {
            updateSettingQuietly('model', entity.default_model);
            debug(`Applied entity default model: ${entity.default_model}`);
        }

        // Temperature: user preference > keep current (from .env defaults)
        if (prefs.temperature !== null && prefs.temperature !== undefined) {
            updateSettingQuietly('temperature', prefs.temperature);
            debug(`Applied user temperature preference: ${prefs.temperature}`);
        }

        // Max tokens: user preference > keep current (from .env defaults)
        if (prefs.maxTokens !== null && prefs.maxTokens !== undefined) {
            updateSettingQuietly('maxTokens', prefs.maxTokens);
            debug(`Applied user maxTokens preference: ${prefs.maxTokens}`);
        }

        // Voice: user preference > keep current
        if (prefs.voiceId !== null && prefs.voiceId !== undefined) {
            selectedVoiceId.set(prefs.voiceId);
            debug(`Applied user voice preference: ${prefs.voiceId}`);
        }

        // Restore entity-specific system prompt if stored (persisted to localStorage)
        const storedPrompt = entitySystemPrompts.getForEntity(entityId);
        if (storedPrompt !== undefined && storedPrompt !== '') {
            updateSettingQuietly('systemPrompt', storedPrompt);
        } else {
            // Clear system prompt when switching to entity without stored prompt
            updateSettingQuietly('systemPrompt', '');
        }
    }

    // Handle conversation selection
    async function handleSelectConversation(event) {
        const conversationId = event.detail;
        await loadConversation(conversationId);
    }

    async function loadConversation(conversationId) {
        if (!conversationId) {
            resetConversationState();
            resetMessagesState();
            resetMemoriesState();
            return;
        }

        isLoading.set(true);
        try {
            const [conv, msgs] = await Promise.all([
                api.getConversation(conversationId),
                api.getConversationMessages(conversationId)
            ]);

            currentConversationId.set(conversationId);
            currentConversation.set(conv);
            messages.set(msgs);
            resetMemoriesState();

            // Handle multi-entity conversation
            if (conv.conversation_type === 'multi_entity' && conv.entities) {
                isMultiEntityMode.set(true);
            }
        } catch (error) {
            const message = error?.message || String(error);
            showToast(`Failed to load conversation: ${message}`, 'error');
        } finally {
            isLoading.set(false);
        }
    }

    // Handle new conversation
    async function handleNewConversation() {
        resetConversationState();
        resetMessagesState();
        resetMemoriesState();
    }

    // Reload conversations when needed
    function handleConversationsUpdated() {
        loadConversations();
    }

    // Modal helpers
    function closeModal() {
        activeModal.set(null);
    }

    function openSettings() {
        activeModal.set('settings');
    }

    function openMemories() {
        activeModal.set('memories');
    }

    function openArchived() {
        activeModal.set('archived');
    }

    function openImportExport() {
        activeModal.set('import-export');
    }

    // Delete conversation
    function handleDeleteRequest(event) {
        const { id, title } = event.detail;
        deleteContext = { id, title, type: 'conversation' };
        activeModal.set('delete');
    }

    async function handleDeleteConfirm() {
        if (!deleteContext.id) return;
        try {
            await api.deleteConversation(deleteContext.id);
            if ($currentConversationId === deleteContext.id) {
                resetConversationState();
                resetMessagesState();
                resetMemoriesState();
            }
            await loadConversations();
            showToast('Conversation deleted', 'success');
        } catch (error) {
            const message = error?.message || String(error);
            showToast(`Failed to delete: ${message}`, 'error');
        }
        closeModal();
    }

    // Rename conversation
    function handleRenameRequest(event) {
        const { id, title } = event.detail;
        renameContext = { id, title, type: 'conversation' };
        activeModal.set('rename');
    }

    async function handleRenameConfirm(event) {
        const { title } = event.detail;
        if (!renameContext.id) return;
        try {
            await api.updateConversation(renameContext.id, { title });
            await loadConversations();
            if ($currentConversationId === renameContext.id) {
                currentConversation.update(c => c ? { ...c, title } : c);
            }
            showToast('Conversation renamed', 'success');
        } catch (error) {
            const message = error?.message || String(error);
            showToast(`Failed to rename: ${message}`, 'error');
        }
        closeModal();
    }

    // Multi-entity conversation creation
    async function handleMultiEntityCreate(event) {
        const { entityIds } = event.detail;
        try {
            isMultiEntityMode.set(true);
            currentConversationEntities.set(entityIds);
            selectedEntityId.set('multi-entity');
            resetConversationState();
            resetMessagesState();
            resetMemoriesState();
            await loadConversations();
            showToast('Multi-entity mode enabled', 'success');
        } catch (error) {
            const message = error?.message || String(error);
            showToast(`Failed to create: ${message}`, 'error');
        }
        closeModal();
    }

    // Archived unarchive handler
    function handleUnarchive() {
        loadConversations();
    }
</script>

{#if initializationError}
    <div class="init-error">
        <h2>Failed to Connect</h2>
        <p>{initializationError}</p>
        <p class="hint">Make sure the backend server is running on port 8000.</p>
        <button onclick={() => location.reload()}>Retry</button>
    </div>
{:else}
    <div class="app-container">
        <Sidebar
            on:entityChange={handleEntityChange}
            on:selectConversation={handleSelectConversation}
            on:newConversation={handleNewConversation}
            on:conversationsUpdated={handleConversationsUpdated}
            on:openSettings={openSettings}
            on:openMemories={openMemories}
            on:openArchived={openArchived}
            on:openImportExport={openImportExport}
            on:deleteConversation={handleDeleteRequest}
            on:renameConversation={handleRenameRequest}
        />
        <ChatArea
            on:conversationCreated={handleConversationsUpdated}
            on:loadConversation={(e) => loadConversation(e.detail)}
        />
    </div>
{/if}

<ToastContainer />
<LoadingOverlay />

<!-- Modals - conditionally rendered -->
{#if $activeModal === 'settings'}
    <SettingsModal on:close={closeModal} />
{/if}

{#if $activeModal === 'memories'}
    <MemoriesModal on:close={closeModal} />
{/if}

{#if $activeModal === 'delete'}
    <DeleteModal
        title={deleteContext.title}
        itemType={deleteContext.type}
        on:close={closeModal}
        on:confirm={handleDeleteConfirm}
    />
{/if}

{#if $activeModal === 'rename'}
    <RenameModal
        currentTitle={renameContext.title}
        itemType={renameContext.type}
        on:close={closeModal}
        on:confirm={handleRenameConfirm}
    />
{/if}

{#if $activeModal === 'archived'}
    <ArchivedModal
        on:close={closeModal}
        on:unarchive={handleUnarchive}
    />
{/if}

{#if $activeModal === 'multi-entity'}
    <MultiEntityModal
        on:close={closeModal}
        on:create={handleMultiEntityCreate}
    />
{/if}

{#if $activeModal === 'import-export'}
    <ImportExportModal
        on:close={closeModal}
        on:import={handleConversationsUpdated}
    />
{/if}

<style>
    .app-container {
        display: flex;
        height: 100vh;
        overflow: hidden;
    }

    .init-error {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        height: 100vh;
        color: var(--text-primary, #e0e0e0);
        background: var(--bg-primary, #1a1a1a);
    }

    .init-error p {
        margin: 1rem 0 0.5rem;
        font-size: 1rem;
    }

    .init-error h2 {
        margin: 0;
        color: var(--error, #ff6b6b);
    }

    .init-error .hint {
        color: var(--text-secondary, #888);
        font-size: 0.875rem;
    }

    .init-error button {
        margin-top: 1rem;
        padding: 0.5rem 1.5rem;
        background: var(--accent, #4a9eff);
        color: white;
        border: none;
        border-radius: 4px;
        cursor: pointer;
        font-size: 1rem;
    }

    .init-error button:hover {
        opacity: 0.9;
    }
</style>
