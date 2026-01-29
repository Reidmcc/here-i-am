/**
 * Entity Management Module
 * Handles entity loading, selection, and multi-entity conversations
 */

import { state, saveEntitySystemPromptsToStorage } from './state.js';
import { showToast, escapeHtml } from './utils.js';
import { showModal, hideModal, closeAllDropdowns } from './modals.js';

// Reference to global API client
const api = window.api;

// Element references
let elements = {};

// Callbacks for entity-related actions
let callbacks = {
    onEntityLoaded: null,
    onEntityChanged: null,
    onMultiEntityConfirmed: null,
    onResponderSelected: null,
    loadConversations: null,
};

/**
 * Set element references
 * @param {Object} els - Element references
 */
export function setElements(els) {
    elements = els;
}

/**
 * Set callback functions
 * @param {Object} cbs - Callback functions
 */
export function setCallbacks(cbs) {
    callbacks = { ...callbacks, ...cbs };
}

/**
 * Load entities from the API
 */
export async function loadEntities() {
    console.log('[Entities] Starting loadEntities...');
    try {
        console.log('[Entities] Calling api.listEntities()...');
        const response = await api.listEntities();
        console.log('[Entities] API response:', response);
        // API returns { entities: [...], default_entity: "..." }
        const entities = response.entities || [];
        console.log('[Entities] Parsed entities:', entities);
        state.entities = entities;

        // Update entity selector
        console.log('[Entities] entitySelect element:', elements.entitySelect);
        if (elements.entitySelect) {
            elements.entitySelect.innerHTML = '';

            // If we have 2+ entities, add multi-entity option
            if (entities.length >= 2) {
                const multiOption = document.createElement('option');
                multiOption.value = 'multi-entity';
                multiOption.textContent = 'Multi-Entity Conversation';
                elements.entitySelect.appendChild(multiOption);
            }

            // Add individual entities
            entities.forEach(entity => {
                const option = document.createElement('option');
                option.value = entity.index_name;
                option.textContent = entity.label;
                if (entity.is_default) {
                    option.selected = true;
                }
                elements.entitySelect.appendChild(option);
            });

            // Select default entity if available (and not already set)
            if (!state.selectedEntityId && response.default_entity) {
                state.selectedEntityId = response.default_entity;
            } else if (!state.selectedEntityId && entities.length > 0) {
                state.selectedEntityId = entities[0].index_name;
            }

            // Apply entity's default system prompt if any
            if (state.selectedEntityId) {
                const defaultEntity = entities.find(e => e.index_name === state.selectedEntityId);
                if (defaultEntity && defaultEntity.default_system_prompt) {
                    state.settings.systemPrompt = defaultEntity.default_system_prompt;
                }
            }
        }

        // Restore entity selection in the dropdown
        if (state.selectedEntityId && elements.entitySelect) {
            elements.entitySelect.value = state.selectedEntityId;
        }

        updateEntityDescription();

        // Trigger callback
        console.log('[Entities] Triggering onEntityLoaded callback...');
        if (callbacks.onEntityLoaded) {
            callbacks.onEntityLoaded(entities);
        }
        console.log('[Entities] loadEntities completed successfully');

    } catch (error) {
        console.error('[Entities] Failed to load entities:', error);
        console.error('[Entities] Error details:', error.message, error.stack);
    }
}

/**
 * Handle entity selection change
 * @param {string} entityId - Selected entity ID
 */
export function handleEntityChange(entityId) {
    const previousEntityId = state.selectedEntityId;

    // Detect if switching to multi-entity mode
    if (entityId === 'multi-entity') {
        // Don't trigger multi-entity modal on initial load
        const timeSinceConstruction = Date.now() - state.constructedAt;
        if (timeSinceConstruction < 500) {
            // Initial load, revert to first entity
            if (state.entities.length > 0) {
                state.selectedEntityId = state.entities[0].index_name;
                if (elements.entitySelect) {
                    elements.entitySelect.value = state.selectedEntityId;
                }
            }
            return;
        }

        state.isMultiEntityMode = true;
        state.selectedEntityId = entityId;
        state.currentConversationEntities = [];

        // Show the multi-entity selection modal
        showMultiEntityModal();
        updateEntityDescription();

        // Clear current conversation when switching to multi-entity
        state.currentConversationId = null;
        state.retrievedMemories = [];
        state.retrievedMemoriesByEntity = {};

        // Hide continue button
        if (elements.continueBtn) {
            elements.continueBtn.style.display = 'none';
        }

        // Load conversations if callback provided
        if (callbacks.loadConversations) {
            callbacks.loadConversations();
        }
        return;
    }

    // Switching to single-entity mode
    state.isMultiEntityMode = false;
    state.currentConversationEntities = [];
    state.selectedEntityId = entityId;

    // Hide continue button
    if (elements.continueBtn) {
        elements.continueBtn.style.display = 'none';
    }

    // Update model to entity's saved or default model
    const entity = state.entities.find(e => e.index_name === entityId);
    if (entity) {
        // Update model selector based on provider first (to populate valid options)
        const provider = entity.llm_provider || 'anthropic';
        if (provider) {
            updateModelSelectorForProvider(provider);
        }

        // Restore entity-specific model (saved user preference takes priority)
        if (state.entityModels[entityId] !== undefined) {
            // Verify the saved model is valid for this provider
            const savedModel = state.entityModels[entityId];
            const isValidModel = state.availableModels.some(
                m => m.id === savedModel && m.provider === provider
            );
            if (isValidModel) {
                state.settings.model = savedModel;
            } else if (entity.default_model) {
                // Saved model invalid for this provider, use default
                state.settings.model = entity.default_model;
            }
        } else if (entity.default_model) {
            state.settings.model = entity.default_model;
        }

        // Update the dropdown to reflect the selected model
        if (elements.modelSelect) {
            elements.modelSelect.value = state.settings.model;
        }

        // Restore entity-specific system prompt
        if (state.entitySystemPrompts[entityId] !== undefined) {
            state.settings.systemPrompt = state.entitySystemPrompts[entityId];
        } else if (entity.default_system_prompt) {
            state.settings.systemPrompt = entity.default_system_prompt;
        } else {
            state.settings.systemPrompt = null;
        }
    }

    updateEntityDescription();

    // Reload conversations for new entity
    if (callbacks.loadConversations) {
        callbacks.loadConversations();
    }

    // Trigger callback
    if (callbacks.onEntityChanged) {
        callbacks.onEntityChanged(entityId, previousEntityId);
    }
}

/**
 * Update model selector based on entity's LLM provider
 * @param {string} provider - LLM provider name
 */
export function updateModelSelectorForProvider(provider) {
    if (!elements.modelSelect || !state.availableModels) return;

    // Filter models by provider from flat array
    const models = state.availableModels.filter(m => m.provider === provider);
    elements.modelSelect.innerHTML = '';

    models.forEach(model => {
        const option = document.createElement('option');
        option.value = model.id;
        option.textContent = model.name;
        elements.modelSelect.appendChild(option);
    });

    // Try to keep current model if valid for this provider
    const currentModelValid = models.some(m => m.id === state.settings.model);
    if (currentModelValid) {
        elements.modelSelect.value = state.settings.model;
    } else if (models.length > 0) {
        state.settings.model = models[0].id;
        elements.modelSelect.value = models[0].id;
    }
}

/**
 * Update entity description display
 */
export function updateEntityDescription() {
    if (!elements.entityDescription) return;

    // Multi-entity mode
    if (state.isMultiEntityMode && state.currentConversationEntities.length > 0) {
        const labels = state.currentConversationEntities.map(e => e.label).join(' & ');
        elements.entityDescription.textContent = `Multi-entity: ${labels}`;
        elements.entityDescription.style.display = 'block';
        return;
    }

    // Single entity
    const entity = state.entities.find(e => e.index_name === state.selectedEntityId);
    if (entity) {
        // Build description from provider and model
        const provider = entity.llm_provider || 'anthropic';
        const model = entity.default_model || 'default';
        let description = `${provider}: ${model}`;

        if (entity.description) {
            description = entity.description;
        }

        elements.entityDescription.textContent = description;
        elements.entityDescription.style.display = 'block';
    } else {
        elements.entityDescription.style.display = 'none';
    }
}

/**
 * Get entity label by ID
 * @param {string} entityId - Entity ID
 * @returns {string} - Entity label
 */
export function getEntityLabel(entityId) {
    if (entityId === 'multi-entity') {
        return 'Multi-Entity';
    }
    const entity = state.entities.find(e => e.index_name === entityId);
    return entity ? entity.label : entityId;
}

// =========================================================================
// Multi-Entity Modal Functions
// =========================================================================

/**
 * Show the multi-entity selection modal
 */
export function showMultiEntityModal() {
    if (!elements.multiEntityList) return;

    // Populate entity list with checkboxes
    elements.multiEntityList.innerHTML = state.entities.map(entity => `
        <label class="multi-entity-checkbox">
            <input type="checkbox" value="${entity.index_name}" data-label="${escapeHtml(entity.label)}">
            <span class="entity-label">${escapeHtml(entity.label)}</span>
            ${entity.description ? `<span class="entity-desc">${escapeHtml(entity.description)}</span>` : ''}
        </label>
    `).join('');

    // Add change listeners
    elements.multiEntityList.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', updateMultiEntityConfirmButton);
    });

    updateMultiEntityConfirmButton();
    showModal('multiEntityModal');
}

/**
 * Hide the multi-entity selection modal
 */
export function hideMultiEntityModal() {
    hideModal('multiEntityModal');

    // If we didn't complete selection, revert to single-entity mode
    if (!state.isMultiEntityMode) {
        if (state.entities.length > 0) {
            state.selectedEntityId = state.entities[0].index_name;
            if (elements.entitySelect) {
                elements.entitySelect.value = state.selectedEntityId;
            }
        }
    }
}

/**
 * Update the confirm button state based on selection
 */
function updateMultiEntityConfirmButton() {
    if (!elements.confirmMultiEntity || !elements.multiEntityList) return;
    const selected = elements.multiEntityList.querySelectorAll('input[type="checkbox"]:checked');
    elements.confirmMultiEntity.disabled = selected.length < 2;
}

/**
 * Confirm multi-entity selection
 * @param {string} action - 'createConversation' or 'default'
 */
export function confirmMultiEntitySelection(action = 'default') {
    if (!elements.multiEntityList) return;

    const selected = elements.multiEntityList.querySelectorAll('input[type="checkbox"]:checked');
    const selectedEntityIds = Array.from(selected).map(cb => cb.value);

    if (selectedEntityIds.length < 2) {
        showToast('Please select at least 2 entities', 'error');
        return;
    }

    // Store selected entities with full info
    state.currentConversationEntities = selectedEntityIds.map(id => {
        const entity = state.entities.find(e => e.index_name === id);
        return entity || { index_name: id, label: id };
    });

    hideModal('multiEntityModal');
    updateEntityDescription();

    // Trigger callback
    if (callbacks.onMultiEntityConfirmed) {
        callbacks.onMultiEntityConfirmed();
    }

    // Load conversations filtered for multi-entity
    if (callbacks.loadConversations) {
        callbacks.loadConversations();
    }

    // Show continue button for multi-entity mode
    if (elements.continueBtn) {
        elements.continueBtn.style.display = 'inline-block';
    }
}

// =========================================================================
// Entity Responder Selector (for multi-entity conversations)
// =========================================================================

/**
 * Show the entity responder selector
 * @param {string|boolean} mode - 'respond', 'continuation', 'regenerate', or true for continuation mode
 */
export function showEntityResponderSelector(mode = 'respond') {
    if (!elements.entityResponderSelector || !elements.entityResponderList) return;

    // Handle boolean for backward compatibility
    if (mode === true) mode = 'continuation';

    // Only show for multi-entity mode with entities
    if (!state.isMultiEntityMode || state.currentConversationEntities.length === 0) {
        return;
    }

    state.responderSelectorMode = mode;

    // Update prompt text
    if (elements.entityResponderPrompt) {
        if (mode === 'continuation') {
            elements.entityResponderPrompt.textContent = 'Who should continue?';
        } else if (mode === 'regenerate') {
            elements.entityResponderPrompt.textContent = 'Who should regenerate?';
        } else {
            elements.entityResponderPrompt.textContent = 'Who should respond?';
        }
    }

    // Populate buttons
    elements.entityResponderList.innerHTML = state.currentConversationEntities.map(entity => `
        <button class="entity-responder-btn" data-entity-id="${entity.index_name}">
            ${escapeHtml(entity.label)}
        </button>
    `).join('');

    // Add click handlers
    elements.entityResponderList.querySelectorAll('.entity-responder-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            selectResponder(btn.dataset.entityId);
        });
    });

    elements.entityResponderSelector.style.display = 'flex';
}

/**
 * Select a responder entity
 * @param {string} entityId - Entity ID to select
 */
export function selectResponder(entityId) {
    state.pendingResponderId = entityId;
    hideEntityResponderSelector();

    // Trigger callback
    if (callbacks.onResponderSelected) {
        callbacks.onResponderSelected();
    }
}

/**
 * Cancel responder selection
 */
export function cancelResponderSelection() {
    state.pendingResponderId = null;
    hideEntityResponderSelector();
}

/**
 * Hide the entity responder selector
 */
export function hideEntityResponderSelector() {
    if (elements.entityResponderSelector) {
        elements.entityResponderSelector.style.display = 'none';
    }
}
