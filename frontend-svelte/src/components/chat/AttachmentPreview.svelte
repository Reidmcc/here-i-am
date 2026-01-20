<script>
    import { pendingAttachments } from '../../lib/stores/attachments.js';

    function removeImage(index) {
        pendingAttachments.removeImage(index);
    }

    function removeFile(index) {
        pendingAttachments.removeFile(index);
    }

    function formatFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }
</script>

<div class="attachment-preview">
    {#if $pendingAttachments.images.length > 0}
        <div class="preview-section images">
            {#each $pendingAttachments.images as image, index}
                <div class="preview-item image-preview">
                    <img src={image.previewUrl} alt={image.file.name} />
                    <div class="preview-info">
                        <span class="preview-name" title={image.file.name}>
                            {image.file.name}
                        </span>
                        <span class="preview-size">
                            {formatFileSize(image.file.size)}
                        </span>
                    </div>
                    <button
                        class="remove-btn"
                        on:click={() => removeImage(index)}
                        title="Remove image"
                    >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="18" y1="6" x2="6" y2="18"></line>
                            <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                    </button>
                </div>
            {/each}
        </div>
    {/if}

    {#if $pendingAttachments.files.length > 0}
        <div class="preview-section files">
            {#each $pendingAttachments.files as file, index}
                <div class="preview-item file-preview">
                    <div class="file-icon">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                            <polyline points="14 2 14 8 20 8"></polyline>
                            <line x1="16" y1="13" x2="8" y2="13"></line>
                            <line x1="16" y1="17" x2="8" y2="17"></line>
                            <polyline points="10 9 9 9 8 9"></polyline>
                        </svg>
                    </div>
                    <div class="preview-info">
                        <span class="preview-name" title={file.file.name}>
                            {file.file.name}
                        </span>
                        <span class="preview-size">
                            {formatFileSize(file.file.size)}
                        </span>
                    </div>
                    <button
                        class="remove-btn"
                        on:click={() => removeFile(index)}
                        title="Remove file"
                    >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="18" y1="6" x2="6" y2="18"></line>
                            <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                    </button>
                </div>
            {/each}
        </div>
    {/if}
</div>

<style>
    .attachment-preview {
        display: flex;
        flex-direction: column;
        gap: 8px;
        padding: 12px;
        background-color: var(--bg-tertiary);
        border-radius: 8px;
        margin-bottom: 12px;
        max-width: 800px;
        margin-left: auto;
        margin-right: auto;
    }

    .preview-section {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
    }

    .preview-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px;
        background-color: var(--bg-secondary);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        position: relative;
    }

    .image-preview {
        flex-direction: column;
        width: 120px;
    }

    .image-preview img {
        width: 100%;
        height: 80px;
        object-fit: cover;
        border-radius: 4px;
    }

    .file-preview {
        max-width: 200px;
    }

    .file-icon {
        color: var(--text-secondary);
        flex-shrink: 0;
    }

    .preview-info {
        display: flex;
        flex-direction: column;
        gap: 2px;
        min-width: 0;
        flex: 1;
    }

    .preview-name {
        font-size: 0.8rem;
        color: var(--text-primary);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .preview-size {
        font-size: 0.7rem;
        color: var(--text-muted);
    }

    .remove-btn {
        position: absolute;
        top: 4px;
        right: 4px;
        padding: 4px;
        background-color: var(--bg-primary);
        border: 1px solid var(--border-color);
        border-radius: 50%;
        cursor: pointer;
        color: var(--text-secondary);
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.2s;
    }

    .remove-btn:hover {
        background-color: var(--danger);
        border-color: var(--danger);
        color: white;
    }
</style>
