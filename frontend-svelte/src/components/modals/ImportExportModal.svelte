<script>
    import { createEventDispatcher } from 'svelte';
    import Modal from '../common/Modal.svelte';
    import { selectedEntityId, selectedEntity } from '../../lib/stores/entities.js';
    import { showToast, createAbortController, abortStream } from '../../lib/stores/app.js';
    import { downloadFile } from '../../lib/utils.js';
    import * as api from '../../lib/api.js';

    const dispatch = createEventDispatcher();

    let activeTab = 'export';
    let importFile = null;
    let importPreview = null;
    let isPreviewLoading = false;
    let isImporting = false;
    let importProgress = { current: 0, total: 0 };
    let fileInputEl;

    function close() {
        if (isImporting) {
            abortStream();
        }
        dispatch('close');
    }

    async function handleFileSelect(event) {
        const file = event.target.files?.[0];
        if (!file) return;

        importFile = file;
        importPreview = null;
        isPreviewLoading = true;

        try {
            const result = await api.previewExternalImport(file);
            importPreview = result;
        } catch (error) {
            showToast(`Failed to preview: ${error.message}`, 'error');
            importFile = null;
        } finally {
            isPreviewLoading = false;
        }
    }

    async function handleImport() {
        if (!importFile || !$selectedEntityId) return;

        isImporting = true;
        importProgress = { current: 0, total: importPreview?.total_messages || 0 };

        const controller = createAbortController();

        try {
            await api.importExternalConversationsStream(
                { file: importFile, entity_id: $selectedEntityId },
                {
                    onProgress: (data) => {
                        importProgress = {
                            current: data.current || 0,
                            total: data.total || importProgress.total
                        };
                    },
                    onComplete: (data) => {
                        showToast(`Imported ${data.imported_count || 0} conversations`, 'success');
                        dispatch('import');
                        close();
                    },
                    onError: (error) => {
                        showToast(`Import failed: ${error.message}`, 'error');
                    }
                },
                controller.signal
            );
        } catch (error) {
            if (error.name !== 'AbortError') {
                showToast(`Import failed: ${error.message}`, 'error');
            }
        } finally {
            isImporting = false;
        }
    }

    async function handleExportAll() {
        if (!$selectedEntityId) {
            showToast('Please select an entity first', 'error');
            return;
        }

        try {
            const response = await api.getConversations($selectedEntityId);
            const conversations = response.conversations || [];

            if (conversations.length === 0) {
                showToast('No conversations to export', 'info');
                return;
            }

            // Fetch full details for each conversation
            const fullConversations = await Promise.all(
                conversations.map(c => api.getConversation(c.id))
            );

            const exportData = {
                exported_at: new Date().toISOString(),
                entity_id: $selectedEntityId,
                conversation_count: fullConversations.length,
                conversations: fullConversations
            };

            const filename = `conversations-${$selectedEntityId}-${new Date().toISOString().split('T')[0]}.json`;
            downloadFile(JSON.stringify(exportData, null, 2), filename, 'application/json');
            showToast('Export completed', 'success');
        } catch (error) {
            showToast(`Export failed: ${error.message}`, 'error');
        }
    }

    function clearImport() {
        importFile = null;
        importPreview = null;
        if (fileInputEl) fileInputEl.value = '';
    }

    $: currentEntity = $selectedEntity;
</script>

<Modal title="Import / Export" size="large" on:close={close}>
    <div class="import-export-layout">
        <div class="tabs">
            <button
                class="tab-btn"
                class:active={activeTab === 'export'}
                on:click={() => activeTab = 'export'}
            >
                Export
            </button>
            <button
                class="tab-btn"
                class:active={activeTab === 'import'}
                on:click={() => activeTab = 'import'}
            >
                Import
            </button>
        </div>

        <div class="tab-content">
            {#if activeTab === 'export'}
                <div class="export-section">
                    <h3>Export Conversations</h3>
                    <p class="description">
                        Export all conversations for the current entity ({currentEntity?.label || 'None selected'}) to a JSON file.
                    </p>

                    <button
                        class="btn btn-primary"
                        on:click={handleExportAll}
                        disabled={!$selectedEntityId}
                    >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                            <polyline points="7 10 12 15 17 10"></polyline>
                            <line x1="12" y1="15" x2="12" y2="3"></line>
                        </svg>
                        Export All Conversations
                    </button>
                </div>
            {/if}

            {#if activeTab === 'import'}
                <div class="import-section">
                    <h3>Import External Conversations</h3>
                    <p class="description">
                        Import conversations from OpenAI or Anthropic exports. Messages will be stored
                        to the current entity's memory ({currentEntity?.label || 'None selected'}).
                    </p>

                    <div class="file-upload-area">
                        <input
                            bind:this={fileInputEl}
                            type="file"
                            accept=".json"
                            on:change={handleFileSelect}
                            hidden
                        />
                        <button
                            class="upload-btn"
                            on:click={() => fileInputEl?.click()}
                            disabled={isImporting}
                        >
                            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                <polyline points="17 8 12 3 7 8"></polyline>
                                <line x1="12" y1="3" x2="12" y2="15"></line>
                            </svg>
                            {importFile ? 'Change File' : 'Select JSON File'}
                        </button>
                    </div>

                    {#if isPreviewLoading}
                        <div class="preview-loading">
                            Analyzing file...
                        </div>
                    {/if}

                    {#if importPreview}
                        <div class="preview-card">
                            <div class="preview-header">
                                <span class="preview-title">{importFile?.name}</span>
                                <button class="clear-btn" on:click={clearImport}>Clear</button>
                            </div>
                            <div class="preview-stats">
                                <div class="stat">
                                    <span class="stat-value">{importPreview.conversation_count || 0}</span>
                                    <span class="stat-label">Conversations</span>
                                </div>
                                <div class="stat">
                                    <span class="stat-value">{importPreview.total_messages || 0}</span>
                                    <span class="stat-label">Messages</span>
                                </div>
                                <div class="stat">
                                    <span class="stat-value">{importPreview.format || 'Unknown'}</span>
                                    <span class="stat-label">Format</span>
                                </div>
                            </div>
                        </div>
                    {/if}

                    {#if isImporting}
                        <div class="import-progress">
                            <div class="progress-bar">
                                <div
                                    class="progress-fill"
                                    style="width: {importProgress.total ? (importProgress.current / importProgress.total * 100) : 0}%"
                                ></div>
                            </div>
                            <span class="progress-text">
                                Importing... {importProgress.current} / {importProgress.total}
                            </span>
                        </div>
                    {/if}

                    {#if importPreview && !isImporting}
                        <button
                            class="btn btn-primary"
                            on:click={handleImport}
                            disabled={!$selectedEntityId}
                        >
                            Import to {currentEntity?.label || 'Entity'}
                        </button>
                    {/if}
                </div>
            {/if}
        </div>
    </div>

    <svelte:fragment slot="footer">
        <button class="btn btn-secondary" on:click={close}>
            {isImporting ? 'Cancel' : 'Close'}
        </button>
    </svelte:fragment>
</Modal>

<style>
    .import-export-layout {
        min-height: 300px;
    }

    .tabs {
        display: flex;
        gap: 4px;
        margin-bottom: 24px;
        border-bottom: 1px solid var(--border-color);
        padding-bottom: 12px;
    }

    .tab-btn {
        padding: 10px 20px;
        background: transparent;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        font-size: 0.95rem;
        color: var(--text-secondary);
        transition: all 0.2s;
    }

    .tab-btn:hover {
        background-color: var(--bg-tertiary);
        color: var(--text-primary);
    }

    .tab-btn.active {
        background-color: var(--accent-subtle);
        color: var(--accent);
        font-weight: 500;
    }

    .export-section,
    .import-section {
        display: flex;
        flex-direction: column;
        gap: 16px;
    }

    h3 {
        margin: 0;
        font-size: 1.1rem;
        color: var(--text-primary);
    }

    .description {
        font-size: 0.9rem;
        color: var(--text-muted);
        line-height: 1.5;
    }

    .file-upload-area {
        padding: 32px;
        background-color: var(--bg-tertiary);
        border: 2px dashed var(--border-color);
        border-radius: 12px;
        text-align: center;
    }

    .upload-btn {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 12px 24px;
        background-color: var(--bg-secondary);
        border: 1px solid var(--border-color);
        border-radius: 8px;
        cursor: pointer;
        color: var(--text-primary);
        font-size: 0.95rem;
        transition: all 0.2s;
    }

    .upload-btn:hover:not(:disabled) {
        background-color: var(--bg-primary);
        border-color: var(--accent);
    }

    .upload-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .preview-loading {
        padding: 20px;
        text-align: center;
        color: var(--text-muted);
    }

    .preview-card {
        padding: 16px;
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 8px;
    }

    .preview-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 16px;
    }

    .preview-title {
        font-weight: 500;
        color: var(--text-primary);
    }

    .clear-btn {
        padding: 4px 12px;
        background: transparent;
        border: 1px solid var(--border-color);
        border-radius: 4px;
        cursor: pointer;
        font-size: 0.8rem;
        color: var(--text-secondary);
    }

    .clear-btn:hover {
        background-color: var(--bg-secondary);
    }

    .preview-stats {
        display: flex;
        gap: 24px;
    }

    .stat {
        display: flex;
        flex-direction: column;
        gap: 4px;
    }

    .stat-value {
        font-size: 1.5rem;
        font-weight: 700;
        color: var(--accent);
    }

    .stat-label {
        font-size: 0.8rem;
        color: var(--text-muted);
    }

    .import-progress {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }

    .progress-bar {
        height: 8px;
        background-color: var(--bg-tertiary);
        border-radius: 4px;
        overflow: hidden;
    }

    .progress-fill {
        height: 100%;
        background-color: var(--accent);
        transition: width 0.3s;
    }

    .progress-text {
        font-size: 0.85rem;
        color: var(--text-muted);
        text-align: center;
    }

    .btn {
        padding: 12px 24px;
        border-radius: 8px;
        font-size: 0.95rem;
        cursor: pointer;
        transition: all 0.2s;
        display: inline-flex;
        align-items: center;
        gap: 8px;
    }

    .btn-primary {
        background-color: var(--accent);
        color: white;
        border: none;
    }

    .btn-primary:hover:not(:disabled) {
        background-color: var(--accent-hover);
    }

    .btn-primary:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .btn-secondary {
        background-color: var(--bg-tertiary);
        color: var(--text-primary);
        border: 1px solid var(--border-color);
    }

    .btn-secondary:hover {
        background-color: var(--bg-primary);
    }
</style>
