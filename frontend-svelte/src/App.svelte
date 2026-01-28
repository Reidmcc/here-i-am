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
    import { entities, selectedEntityId, isMultiEntityMode, resetMultiEntityState, currentConversationEntities } from './lib/stores/entities.js';
    import { conversations, currentConversationId, currentConversation, resetConversationState, getNextRequestId, isValidRequestId } from './lib/stores/conversations.js';
    import { messages, resetMessagesState } from './lib/stores/messages.js';
    import { resetMemoriesState } from './lib/stores/memories.js';
    import { settings, presets } from './lib/stores/settings.js';
    import { ttsEnabled, ttsProvider, voices, sttEnabled, sttProvider, dictationMode } from './lib/stores/voice.js';
    import * as api from './lib/api.js';

    // Debug helper - writes to visible debug div
    function debug(msg) {
        const el = document.getElementById('debug-log');
        if (el) el.innerHTML += '[App] ' + msg + '<br>';
    }

    // Modal context for delete/rename
    let deleteContext = { title: '', id: null, type: 'conversation' };
    let renameContext = { title: '', id: null, type: 'conversation' };

    // Initialization state
    let initializationComplete = false;
    let initializationError = null;

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
            // Load entities first (required for app to function)
            debug('Calling api.listEntities()...');
            const response = await api.listEntities();
            // API returns { entities: [...], default_entity: "..." }
            const entitiesList = response.entities || [];
            debug('Entities loaded: ' + entitiesList.length + ' entities');
            entities.set(entitiesList);

            // Select first entity if none selected, preferring the default entity
            if (!$selectedEntityId && entitiesList.length > 0) {
                const defaultEntityId = response.default_entity || entitiesList[0].index_name;
                selectedEntityId.set(defaultEntityId);
            }

            // Mark initialization complete - UI can now render
            initializationComplete = true;

            // Load remaining data in parallel (non-blocking)
            const loadTasks = [];

            // Load conversations for selected entity
            if ($selectedEntityId) {
                loadTasks.push(loadConversations().catch(e => console.warn('Failed to load conversations:', e)));
            }

            // Load presets
            loadTasks.push(
                api.getPresets()
                    .then(presetsData => presets.set(presetsData))
                    .catch(e => console.warn('Failed to load presets:', e))
            );

            // Load chat config (available models)
            loadTasks.push(
                api.getChatConfig()
                    .then(config => {
                        if (config?.available_models) {
                            availableModels.set(config.available_models);
                        }
                    })
                    .catch(e => console.warn('Failed to load chat config:', e))
            );

            // Load TTS/STT status
            loadTasks.push(loadTTSStatus().catch(e => console.warn('Failed to load TTS status:', e)));
            loadTasks.push(loadSTTStatus().catch(e => console.warn('Failed to load STT status:', e)));

            // Load GitHub repos if available
            loadTasks.push(
                api.listGitHubRepos()
                    .then(repos => githubRepos.set(repos))
                    .catch(() => { /* GitHub not configured, ignore */ })
            );

            // Wait for all background tasks
            await Promise.all(loadTasks);
        } catch (error) {
            console.error('[App] loadInitialData error:', error);
            initializationError = error.message;
            initializationComplete = true; // Allow UI to render error state
            showToast(`Failed to load initial data: ${error.message}`, 'error');
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
            showToast(`Failed to load conversations: ${error.message}`, 'error');
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
            resetConversationState();
            resetMessagesState();
            resetMemoriesState();
            await loadConversations();
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
            showToast(`Failed to load conversation: ${error.message}`, 'error');
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
            showToast(`Failed to delete: ${error.message}`, 'error');
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
            showToast(`Failed to rename: ${error.message}`, 'error');
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
            showToast(`Failed to create: ${error.message}`, 'error');
        }
        closeModal();
    }

    // Archived unarchive handler
    function handleUnarchive() {
        loadConversations();
    }
</script>

{#if !initializationComplete}
    <div class="init-loading">
        <div class="init-spinner"></div>
        <p>Connecting to backend...</p>
    </div>
{:else if initializationError}
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

    .init-loading,
    .init-error {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        height: 100vh;
        color: var(--text-primary, #e0e0e0);
        background: var(--bg-primary, #1a1a1a);
    }

    .init-loading p,
    .init-error p {
        margin: 1rem 0 0.5rem;
        font-size: 1rem;
    }

    .init-spinner {
        width: 40px;
        height: 40px;
        border: 3px solid var(--bg-tertiary, #3d3d3d);
        border-top-color: var(--accent, #4a9eff);
        border-radius: 50%;
        animation: spin 1s linear infinite;
    }

    @keyframes spin {
        to { transform: rotate(360deg); }
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
