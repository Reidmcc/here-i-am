<script>
    import { createEventDispatcher, onMount } from 'svelte';
    import AttachmentPreview from './AttachmentPreview.svelte';

    import { isLoading } from '../../lib/stores/app.js';
    import { isStreaming } from '../../lib/stores/messages.js';
    import { currentConversationId, isMultiEntityConversation } from '../../lib/stores/conversations.js';
    import { pendingAttachments, isDragging, hasAttachments, validateFile, processFile } from '../../lib/stores/attachments.js';
    import { sttEnabled, isRecording, recordingDuration } from '../../lib/stores/voice.js';
    import { showToast } from '../../lib/stores/app.js';
    import * as api from '../../lib/api.js';

    const dispatch = createEventDispatcher();

    let messageInput = '';
    let textareaEl;
    let fileInputEl;
    let mediaRecorder = null;
    let audioChunks = [];
    let recordingInterval = null;

    // Auto-resize textarea
    function autoResize() {
        if (textareaEl) {
            textareaEl.style.height = 'auto';
            textareaEl.style.height = Math.min(textareaEl.scrollHeight, 200) + 'px';
        }
    }

    function handleKeydown(event) {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            handleSend();
        }
    }

    function handleSend() {
        const content = messageInput.trim();
        if (!content && !$hasAttachments) return;
        if ($isLoading || $isStreaming) return;

        dispatch('send', {
            content: content || null,
            attachments: {
                images: [...$pendingAttachments.images],
                files: [...$pendingAttachments.files]
            }
        });

        messageInput = '';
        if (textareaEl) {
            textareaEl.style.height = 'auto';
        }
    }

    function handleStop() {
        dispatch('stop');
    }

    function handleContinue() {
        dispatch('continue');
    }

    // File handling
    function handleAttachClick() {
        fileInputEl?.click();
    }

    async function handleFileSelect(event) {
        const files = event.target.files;
        if (!files || files.length === 0) return;

        for (const file of files) {
            try {
                const result = await processFile(file);
                if (result.type === 'image') {
                    pendingAttachments.addImage(result.file, result.previewUrl, result.base64);
                } else {
                    pendingAttachments.addFile(result.file, result.content);
                }
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        // Clear input for re-selecting same file
        event.target.value = '';
    }

    // Drag and drop
    function handleDragEnter(event) {
        event.preventDefault();
        isDragging.set(true);
    }

    function handleDragLeave(event) {
        event.preventDefault();
        // Only set to false if leaving the container entirely
        if (!event.currentTarget.contains(event.relatedTarget)) {
            isDragging.set(false);
        }
    }

    function handleDragOver(event) {
        event.preventDefault();
    }

    async function handleDrop(event) {
        event.preventDefault();
        isDragging.set(false);

        const files = event.dataTransfer?.files;
        if (!files || files.length === 0) return;

        for (const file of files) {
            try {
                const result = await processFile(file);
                if (result.type === 'image') {
                    pendingAttachments.addImage(result.file, result.previewUrl, result.base64);
                } else {
                    pendingAttachments.addFile(result.file, result.content);
                }
            } catch (error) {
                showToast(error.message, 'error');
            }
        }
    }

    // Voice recording
    async function handleVoiceClick() {
        if ($isRecording) {
            stopRecording();
        } else {
            await startRecording();
        }
    }

    async function startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];

            mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    audioChunks.push(event.data);
                }
            };

            mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                stream.getTracks().forEach(track => track.stop());
                await transcribeAudio(audioBlob);
            };

            mediaRecorder.start();
            isRecording.set(true);
            recordingDuration.set(0);

            recordingInterval = setInterval(() => {
                recordingDuration.update(d => d + 1);
            }, 1000);

        } catch (error) {
            showToast(`Could not start recording: ${error.message}`, 'error');
        }
    }

    function stopRecording() {
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            mediaRecorder.stop();
        }
        isRecording.set(false);
        if (recordingInterval) {
            clearInterval(recordingInterval);
            recordingInterval = null;
        }
    }

    async function transcribeAudio(audioBlob) {
        try {
            showToast('Transcribing...', 'info');
            const result = await api.transcribeAudio(audioBlob);
            if (result.text) {
                messageInput = (messageInput + ' ' + result.text).trim();
                autoResize();
            }
        } catch (error) {
            showToast(`Transcription failed: ${error.message}`, 'error');
        }
    }

    function formatDuration(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }

    onMount(() => {
        return () => {
            // Cleanup on unmount
            if (recordingInterval) {
                clearInterval(recordingInterval);
            }
            if (mediaRecorder && mediaRecorder.state !== 'inactive') {
                mediaRecorder.stop();
            }
        };
    });
</script>

<div
    class="input-area"
    class:drag-over={$isDragging}
    on:dragenter={handleDragEnter}
    on:dragleave={handleDragLeave}
    on:dragover={handleDragOver}
    on:drop={handleDrop}
    role="region"
    aria-label="Message input"
>
    {#if $hasAttachments}
        <AttachmentPreview />
    {/if}

    <div class="input-container">
        <button
            class="attach-btn"
            on:click={handleAttachClick}
            title="Attach file"
            disabled={$isLoading || $isStreaming}
        >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
            </svg>
        </button>

        <input
            type="file"
            bind:this={fileInputEl}
            on:change={handleFileSelect}
            accept="image/jpeg,image/png,image/gif,image/webp,.txt,.md,.py,.js,.ts,.json,.yaml,.yml,.html,.css,.xml,.csv,.log,.pdf,.docx"
            multiple
            hidden
        />

        {#if $sttEnabled}
            <button
                class="voice-btn"
                class:recording={$isRecording}
                on:click={handleVoiceClick}
                title={$isRecording ? `Recording ${formatDuration($recordingDuration)}` : 'Voice input'}
                disabled={$isLoading || $isStreaming}
            >
                {#if $isRecording}
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                        <rect x="6" y="6" width="12" height="12" rx="2"></rect>
                    </svg>
                {:else}
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
                        <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                        <line x1="12" y1="19" x2="12" y2="23"></line>
                        <line x1="8" y1="23" x2="16" y2="23"></line>
                    </svg>
                {/if}
            </button>
        {/if}

        <textarea
            bind:this={textareaEl}
            bind:value={messageInput}
            on:input={autoResize}
            on:keydown={handleKeydown}
            placeholder="Type a message..."
            rows="1"
            disabled={$isLoading || $isStreaming}
        ></textarea>

        {#if $isStreaming}
            <button class="stop-btn" on:click={handleStop}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                    <rect x="6" y="6" width="12" height="12" rx="2"></rect>
                </svg>
                Stop
            </button>
        {:else}
            <button
                class="send-btn"
                on:click={handleSend}
                disabled={$isLoading || (!messageInput.trim() && !$hasAttachments)}
            >
                Send
            </button>
        {/if}

        {#if $currentConversationId && $isMultiEntityConversation && !$isStreaming && !$isLoading}
            <button class="continue-btn" on:click={handleContinue}>
                Continue
            </button>
        {/if}
    </div>

    <div class="input-meta">
        <span></span>
        <span>Press Enter to send, Shift+Enter for new line</span>
    </div>
</div>

<style>
    .input-area {
        padding: 16px 24px;
        background-color: var(--bg-secondary);
        border-top: 1px solid var(--border-color);
    }

    .input-area.drag-over {
        background-color: var(--bg-tertiary);
        border-color: var(--accent);
        box-shadow: inset 0 0 0 2px var(--accent);
    }

    .input-container {
        display: flex;
        gap: 12px;
        max-width: 800px;
        margin: 0 auto;
    }

    textarea {
        flex: 1;
        padding: 12px 16px;
        background-color: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 8px;
        color: var(--text-primary);
        font-family: var(--font-sans);
        font-size: 0.95rem;
        resize: none;
        min-height: 44px;
        max-height: 200px;
        overflow-y: auto;
    }

    textarea:focus {
        outline: none;
        border-color: var(--accent);
        box-shadow: 0 0 0 2px var(--accent-subtle);
    }

    textarea::placeholder {
        color: var(--text-muted);
    }

    textarea:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .send-btn {
        padding: 12px 24px;
        background-color: var(--accent);
        color: white;
        border: none;
        border-radius: 8px;
        cursor: pointer;
        font-size: 0.95rem;
        font-weight: 500;
        transition: background-color 0.2s;
    }

    .send-btn:hover:not(:disabled) {
        background-color: var(--accent-hover);
    }

    .send-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .stop-btn {
        padding: 12px 24px;
        background-color: var(--danger);
        color: white;
        border: none;
        border-radius: 8px;
        cursor: pointer;
        font-size: 0.95rem;
        font-weight: 500;
        transition: background-color 0.2s;
        display: flex;
        align-items: center;
        gap: 6px;
    }

    .stop-btn:hover {
        background-color: var(--danger-hover);
    }

    .continue-btn {
        padding: 12px 24px;
        background-color: var(--accent-subtle);
        color: var(--accent);
        border: 1px solid var(--accent);
        border-radius: 8px;
        cursor: pointer;
        font-size: 0.95rem;
        font-weight: 500;
        transition: background-color 0.2s, color 0.2s;
    }

    .continue-btn:hover {
        background-color: var(--accent);
        color: white;
    }

    .attach-btn {
        padding: 10px 14px;
        background-color: var(--bg-tertiary);
        color: var(--text-secondary);
        border: 1px solid var(--border-color);
        border-radius: 8px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.2s;
    }

    .attach-btn:hover:not(:disabled) {
        background-color: var(--bg-primary);
        color: var(--text-primary);
        border-color: var(--accent);
    }

    .attach-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .voice-btn {
        padding: 10px 14px;
        background-color: var(--bg-tertiary);
        color: var(--text-secondary);
        border: 1px solid var(--border-color);
        border-radius: 8px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.2s;
    }

    .voice-btn:hover:not(:disabled) {
        background-color: var(--bg-primary);
        color: var(--text-primary);
        border-color: var(--accent);
    }

    .voice-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .voice-btn.recording {
        background-color: var(--danger);
        border-color: var(--danger);
        color: white;
        animation: pulse-recording 1.5s ease-in-out infinite;
    }

    .voice-btn.recording:hover {
        background-color: var(--danger-hover);
        border-color: var(--danger-hover);
    }

    .input-meta {
        max-width: 800px;
        margin: 8px auto 0;
        display: flex;
        justify-content: space-between;
        font-size: 0.75rem;
        color: var(--text-muted);
    }
</style>
