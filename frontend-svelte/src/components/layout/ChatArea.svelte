<script>
    import { createEventDispatcher, onMount, afterUpdate } from 'svelte';
    import { get } from 'svelte/store';
    import MessageList from '../chat/MessageList.svelte';
    import InputArea from '../chat/InputArea.svelte';
    import MemoriesPanel from '../chat/MemoriesPanel.svelte';
    import EntityResponderSelector from '../chat/EntityResponderSelector.svelte';

    import { currentConversation, currentConversationId, addConversationToList, updateConversationInList } from '../../lib/stores/conversations.js';
    import { messages, streamingContent, streamingMessage, streamingTools, startStreaming, stopStreaming, addMessage, appendStreamingContent, addStreamingTool, updateStreamingToolResult, resetPendingMessage, pendingMessageContent, pendingMessageAttachments, responderSelectorMode } from '../../lib/stores/messages.js';
    import { retrievedMemories, addMemories, resetMemoriesState } from '../../lib/stores/memories.js';
    import { selectedEntityId, isMultiEntityMode, currentConversationEntities, pendingResponderId, getEntityLabel, entitySystemPrompts } from '../../lib/stores/entities.js';
    import { settings, researcherName } from '../../lib/stores/settings.js';
    import { isLoading, showToast, createAbortController, abortStream, streamAbortController } from '../../lib/stores/app.js';
    import { pendingAttachments } from '../../lib/stores/attachments.js';
    import * as api from '../../lib/api.js';

    const dispatch = createEventDispatcher();

    let messagesContainer;

    // Auto-scroll to bottom when messages change
    // Using afterUpdate instead of reactive $: with tick() to avoid hang
    afterUpdate(() => {
        if (messagesContainer) {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
    });

    async function handleSendMessage(event) {
        const { content, attachments } = event.detail;

        if ($isMultiEntityMode && $currentConversationEntities.length > 1) {
            // Multi-entity mode: show responder selector
            pendingMessageContent.set(content);
            pendingMessageAttachments.set(attachments);
            responderSelectorMode.set('send');
            return;
        }

        // Single entity mode: send directly
        await sendMessageToEntity(content, attachments, $selectedEntityId);
    }

    async function handleSelectResponder(event) {
        const entityId = event.detail;
        pendingResponderId.set(entityId);

        const content = $pendingMessageContent;
        const attachments = $pendingMessageAttachments;

        resetPendingMessage();

        await sendMessageToEntity(content, attachments, entityId);
    }

    async function handleContinue() {
        if ($isMultiEntityMode && $currentConversationEntities.length > 1) {
            responderSelectorMode.set('continue');
            return;
        }
        // Single entity continue
        await sendMessageToEntity(null, { images: [], files: [] }, $selectedEntityId);
    }

    async function sendMessageToEntity(content, attachments, respondingEntityId) {
        const controller = createAbortController();
        isLoading.set(true);

        try {
            // Create conversation if one doesn't exist
            let conversationId = $currentConversationId;
            if (!conversationId) {
                const createData = {
                    entity_id: $isMultiEntityMode ? 'multi-entity' : respondingEntityId,
                };
                if ($isMultiEntityMode && $currentConversationEntities.length > 0) {
                    createData.entity_ids = $currentConversationEntities;
                    createData.conversation_type = 'multi_entity';
                }
                const newConv = await api.createConversation(createData);
                conversationId = newConv.id;
                currentConversationId.set(conversationId);
                addConversationToList(newConv);
            }

            // Get entity-specific system prompt
            const entityPrompts = entitySystemPrompts.getForEntity(respondingEntityId);
            const systemPrompt = entityPrompts || $settings.systemPrompt;

            // Prepare request data
            const requestData = {
                message: content,
                conversation_id: conversationId,
                responding_entity_id: $isMultiEntityMode ? respondingEntityId : undefined,
                model: $settings.model,
                temperature: $settings.temperature,
                max_tokens: $settings.maxTokens,
                system_prompt: systemPrompt || undefined,
                user_display_name: $researcherName || undefined,
            };

            // Add attachments if present
            if (attachments.images?.length > 0 || attachments.files?.length > 0) {
                requestData.attachments = {
                    images: attachments.images.map(img => ({
                        type: 'image',
                        media_type: img.file.type,
                        data: img.base64.split(',')[1] // Remove data URL prefix
                    })),
                    files: attachments.files.map(f => ({
                        type: 'text',
                        filename: f.file.name,
                        content: f.content
                    }))
                };
            }

            // Add human message to UI immediately (if there's content)
            if (content) {
                const humanMessage = {
                    id: `temp-${Date.now()}`,
                    role: 'human',
                    content: content,
                    created_at: new Date().toISOString(),
                    attachments: attachments
                };
                addMessage(humanMessage);
            }

            // Clear attachments
            pendingAttachments.reset();

            // Start streaming
            startStreaming({
                speaker_entity_id: respondingEntityId,
                speakerLabel: $isMultiEntityMode ? getEntityLabel(respondingEntityId) : null
            });

            // Turn off loading overlay once streaming starts - user can interact during streaming
            isLoading.set(false);

            let conversationCreated = false;

            await api.sendMessageStream(requestData, {
                onMemories: (data) => {
                    if (data.memories) {
                        addMemories(data.memories, $currentConversationId || data.conversation_id, respondingEntityId);
                    }
                },
                onStart: (data) => {
                    // Conversation may have been created
                    if (data.conversation_id && !$currentConversationId) {
                        currentConversationId.set(data.conversation_id);
                        conversationCreated = true;
                    }
                },
                onToken: (data) => {
                    if (data.content) {
                        appendStreamingContent(data.content);
                    }
                },
                onToolStart: (data) => {
                    addStreamingTool({
                        id: data.tool_use_id,
                        name: data.tool_name,
                        input: data.tool_input,
                        status: 'loading'
                    });
                },
                onToolResult: (data) => {
                    updateStreamingToolResult(data.tool_use_id, {
                        content: data.result,
                        error: data.error
                    });
                },
                onDone: (data) => {
                    // Streaming complete
                },
                onStored: (data) => {
                    // Get the accumulated streaming content before clearing
                    const currentContent = get(streamingContent);
                    const currentTools = get(streamingTools);

                    // Replace temp human message with stored one (using the ID from server)
                    if (data.human_message_id) {
                        messages.update(msgs => {
                            // Find the temp message to preserve its content
                            const tempMsg = msgs.find(m => m.id.startsWith('temp-'));
                            // Remove temp human message
                            const filtered = msgs.filter(m => !m.id.startsWith('temp-'));
                            // Add message with real ID from server
                            return [...filtered, {
                                id: data.human_message_id,
                                role: 'human',
                                content: tempMsg?.content || content,
                                created_at: tempMsg?.created_at || new Date().toISOString(),
                                attachments: tempMsg?.attachments
                            }];
                        });
                    }

                    // Create assistant message from the accumulated streaming content
                    if (data.assistant_message_id && currentContent) {
                        const assistantMsg = {
                            id: data.assistant_message_id,
                            role: 'assistant',
                            content: currentContent,
                            created_at: new Date().toISOString(),
                            speakerLabel: $isMultiEntityMode ? getEntityLabel(respondingEntityId) : null,
                            tool_use: currentTools.length > 0 ? currentTools : undefined
                        };
                        addMessage(assistantMsg);
                    }

                    // Update conversation title if auto-generated
                    if (data.title) {
                        updateConversationInList($currentConversationId, { title: data.title });
                        currentConversation.update(c => c ? { ...c, title: data.title } : c);
                    }

                    // Notify parent of conversation creation
                    if (conversationCreated) {
                        dispatch('conversationCreated');
                    }

                    stopStreaming();
                },
                onError: (data) => {
                    showToast(data.error || 'An error occurred', 'error');
                    stopStreaming();
                },
                onAborted: () => {
                    showToast('Message generation stopped', 'info');
                    stopStreaming();
                }
            }, controller.signal);

        } catch (error) {
            if (error.name !== 'AbortError') {
                const message = error?.message || String(error);
                showToast(`Error: ${message}`, 'error');
            }
            stopStreaming();
        } finally {
            isLoading.set(false);
        }
    }

    function handleStopGeneration() {
        abortStream();
    }

    async function handleRegenerate(event) {
        const { messageId } = event.detail;
        isLoading.set(true);

        try {
            startStreaming();

            // Turn off loading overlay once streaming starts - user can interact during streaming
            isLoading.set(false);

            await api.regenerateStream({
                message_id: messageId,
                responding_entity_id: $isMultiEntityMode ? $pendingResponderId : undefined
            }, {
                onMemories: (data) => {
                    if (data.memories && $currentConversationId) {
                        addMemories(data.memories, $currentConversationId);
                    }
                },
                onStart: () => {},
                onToken: (data) => {
                    if (data.content) {
                        appendStreamingContent(data.content);
                    }
                },
                onToolStart: (data) => {
                    addStreamingTool({
                        id: data.tool_use_id,
                        name: data.tool_name,
                        input: data.tool_input,
                        status: 'loading'
                    });
                },
                onToolResult: (data) => {
                    updateStreamingToolResult(data.tool_use_id, {
                        content: data.result,
                        error: data.error
                    });
                },
                onDone: () => {},
                onStored: (data) => {
                    // Reload conversation messages
                    dispatch('loadConversation', $currentConversationId);
                    stopStreaming();
                },
                onError: (data) => {
                    showToast(data.error || 'Regeneration failed', 'error');
                    stopStreaming();
                }
            });

        } catch (error) {
            const message = error?.message || String(error);
            showToast(`Error: ${message}`, 'error');
            stopStreaming();
        } finally {
            isLoading.set(false);
        }
    }

    async function handleEditMessage(event) {
        const { messageId, content } = event.detail;

        try {
            await api.updateMessage(messageId, content);
            messages.update(msgs => msgs.map(m =>
                m.id === messageId ? { ...m, content } : m
            ));
            showToast('Message updated', 'success');
        } catch (error) {
            const message = error?.message || String(error);
            showToast(`Failed to update message: ${message}`, 'error');
        }
    }

    async function handleDeleteMessage(event) {
        const { messageId } = event.detail;

        try {
            await api.deleteMessage(messageId);
            messages.update(msgs => msgs.filter(m => m.id !== messageId));
            showToast('Message deleted', 'success');
        } catch (error) {
            const message = error?.message || String(error);
            showToast(`Failed to delete message: ${message}`, 'error');
        }
    }

    function handleCancelResponder() {
        resetPendingMessage();
    }
</script>

<main class="chat-area">
    <header class="chat-header">
        <div class="conversation-info">
            <h2>{$currentConversation?.title || 'New Conversation'}</h2>
            <p class="conversation-meta">
                {#if $currentConversation}
                    {$currentConversation.llm_model_used || $settings.model}
                {:else}
                    {$settings.model}
                {/if}
            </p>
        </div>
        <div class="header-actions">
            {#if $currentConversationId}
                <button class="icon-btn" on:click={() => dispatch('loadConversation', $currentConversationId)}>
                    Refresh
                </button>
            {/if}
        </div>
    </header>

    <MemoriesPanel />

    <div class="messages-container" bind:this={messagesContainer}>
        <MessageList
            on:regenerate={handleRegenerate}
            on:editMessage={handleEditMessage}
            on:deleteMessage={handleDeleteMessage}
        />
    </div>

    <InputArea
        on:send={handleSendMessage}
        on:stop={handleStopGeneration}
        on:continue={handleContinue}
    />

    {#if $responderSelectorMode}
        <EntityResponderSelector
            mode={$responderSelectorMode}
            on:select={handleSelectResponder}
            on:cancel={handleCancelResponder}
        />
    {/if}
</main>

<style>
    .chat-area {
        flex: 1;
        display: flex;
        flex-direction: column;
        overflow: hidden;
    }

    .chat-header {
        padding: 16px 24px;
        background-color: var(--bg-secondary);
        border-bottom: 1px solid var(--border-color);
        display: flex;
        justify-content: space-between;
        align-items: center;
    }

    .conversation-info h2 {
        font-size: 1.1rem;
        font-weight: 500;
    }

    .conversation-meta {
        font-size: 0.8rem;
        color: var(--text-muted);
    }

    .header-actions {
        display: flex;
        gap: 8px;
    }

    .icon-btn {
        padding: 8px 12px;
        background-color: var(--bg-tertiary);
        color: var(--text-secondary);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        cursor: pointer;
        font-size: 0.85rem;
        transition: all 0.2s;
    }

    .icon-btn:hover {
        background-color: var(--bg-primary);
        color: var(--text-primary);
    }

    .messages-container {
        flex: 1;
        overflow-y: auto;
        padding: 24px;
    }
</style>
