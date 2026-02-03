/**
 * Agents Module
 * Handles subagent management, display, and interaction
 */

import { state } from './state.js';
import { showToast, escapeHtml, formatTimestamp } from './utils.js';
import { showModal, hideModal } from './modals.js';

// Element references
let elements = {};

// Callbacks
let callbacks = {};

// API reference
const api = window.api;

// Polling interval for agent status
let agentPollingInterval = null;
const POLLING_INTERVAL_MS = 5000;

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
 * Initialize agents module
 */
export async function initAgents() {
    // Check if subagents are enabled
    try {
        const status = await api.getSubagentsStatus();
        state.subagentsEnabled = status.enabled;
        state.subagentMaxConcurrent = status.max_concurrent || 5;

        if (status.enabled) {
            // Load agent types
            await loadAgentTypes();
        }
    } catch (error) {
        console.error('Failed to check subagent status:', error);
        state.subagentsEnabled = false;
    }

    updateAgentsTabVisibility();
}

/**
 * Load available agent types
 */
export async function loadAgentTypes() {
    if (!state.subagentsEnabled) return;

    try {
        const types = await api.listAgentTypes();
        state.agentTypes = types;
    } catch (error) {
        console.error('Failed to load agent types:', error);
        state.agentTypes = [];
    }
}

/**
 * Update the agents tab visibility based on whether subagents are enabled
 */
function updateAgentsTabVisibility() {
    if (elements.agentsTab) {
        elements.agentsTab.style.display = state.subagentsEnabled ? 'block' : 'none';
    }
}

/**
 * Load agents for the current conversation
 */
export async function loadConversationAgents() {
    if (!state.subagentsEnabled || !state.currentConversationId) {
        state.conversationAgents = [];
        renderAgentsList();
        return;
    }

    try {
        const agents = await api.listConversationAgents(state.currentConversationId);
        state.conversationAgents = agents;
        renderAgentsList();

        // Start polling if there are active agents
        const hasActiveAgents = agents.some(a => a.is_active);
        if (hasActiveAgents) {
            startAgentPolling();
        } else {
            stopAgentPolling();
        }
    } catch (error) {
        console.error('Failed to load conversation agents:', error);
        state.conversationAgents = [];
        renderAgentsList();
    }
}

/**
 * Start polling for agent status updates
 */
function startAgentPolling() {
    if (agentPollingInterval) return;

    agentPollingInterval = setInterval(async () => {
        if (!state.currentConversationId) {
            stopAgentPolling();
            return;
        }

        try {
            const agents = await api.listConversationAgents(state.currentConversationId);
            state.conversationAgents = agents;
            renderAgentsList();

            // Stop polling if no more active agents
            const hasActiveAgents = agents.some(a => a.is_active);
            if (!hasActiveAgents) {
                stopAgentPolling();
            }
        } catch (error) {
            console.error('Failed to poll agent status:', error);
        }
    }, POLLING_INTERVAL_MS);
}

/**
 * Stop polling for agent status updates
 */
function stopAgentPolling() {
    if (agentPollingInterval) {
        clearInterval(agentPollingInterval);
        agentPollingInterval = null;
    }
}

/**
 * Render the agents list in the UI
 */
function renderAgentsList() {
    if (!elements.agentsList) return;

    const agents = state.conversationAgents || [];

    // Update count
    if (elements.agentsCount) {
        elements.agentsCount.textContent = agents.length;
    }

    if (agents.length === 0) {
        elements.agentsList.innerHTML = `
            <div class="agents-empty">
                <p>No agents in this conversation.</p>
                <p class="agents-hint">The AI can spawn agents using the <code>create_subagent</code> tool.</p>
            </div>
        `;
        return;
    }

    const html = agents.map(agent => renderAgentItem(agent)).join('');
    elements.agentsList.innerHTML = html;
}

/**
 * Render a single agent item for the sidebar list
 */
function renderAgentItem(agent) {
    const truncatedInstruction = agent.instructions.length > 60
        ? agent.instructions.substring(0, 60) + '...'
        : agent.instructions;

    let actionsHtml = '';
    if (agent.is_active) {
        actionsHtml = `<button class="agent-stop-btn" onclick="window.app.stopAgent('${agent.id}')" title="Stop agent">Stop</button>`;
    }

    return `
        <div class="agent-item" onclick="window.app.showAgentDetails('${agent.id}')">
            <div class="agent-item-header">
                <span class="agent-item-type">${escapeHtml(agent.agent_type)}</span>
                <span class="agent-item-status ${agent.status}">${agent.status}</span>
            </div>
            <div class="agent-item-instruction">${escapeHtml(truncatedInstruction)}</div>
            <div class="agent-item-meta">${formatTimestamp(agent.created_at)}</div>
            ${actionsHtml ? `<div class="agent-item-actions" onclick="event.stopPropagation()">${actionsHtml}</div>` : ''}
        </div>
    `;
}

/**
 * Stop a running agent
 */
export async function stopAgent(agentId) {
    try {
        const result = await api.stopAgent(agentId);
        if (result.success) {
            showToast('Agent stopped', 'success');
            await loadConversationAgents();
        } else {
            showToast(result.message || 'Failed to stop agent', 'error');
        }
    } catch (error) {
        console.error('Failed to stop agent:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

/**
 * Show detailed information about an agent
 */
export async function showAgentDetails(agentId) {
    try {
        const agent = await api.getAgent(agentId);

        // Build the details HTML
        let detailsHtml = `
            <div class="agent-details-content">
                <div class="agent-detail-row">
                    <span class="agent-detail-label">ID:</span>
                    <span class="agent-detail-value">${escapeHtml(agent.id)}</span>
                </div>
                <div class="agent-detail-row">
                    <span class="agent-detail-label">Type:</span>
                    <span class="agent-detail-value">${escapeHtml(agent.agent_type)}</span>
                </div>
                <div class="agent-detail-row">
                    <span class="agent-detail-label">Status:</span>
                    <span class="agent-detail-value agent-status-${agent.status}">${escapeHtml(agent.status)}</span>
                </div>
                <div class="agent-detail-row">
                    <span class="agent-detail-label">Model:</span>
                    <span class="agent-detail-value">${escapeHtml(agent.model)}</span>
                </div>
                <div class="agent-detail-row">
                    <span class="agent-detail-label">Working Directory:</span>
                    <span class="agent-detail-value">${escapeHtml(agent.working_directory)}</span>
                </div>
                <div class="agent-detail-row">
                    <span class="agent-detail-label">Created:</span>
                    <span class="agent-detail-value">${formatTimestamp(agent.created_at)}</span>
                </div>
                ${agent.started_at ? `
                <div class="agent-detail-row">
                    <span class="agent-detail-label">Started:</span>
                    <span class="agent-detail-value">${formatTimestamp(agent.started_at)}</span>
                </div>
                ` : ''}
                ${agent.completed_at ? `
                <div class="agent-detail-row">
                    <span class="agent-detail-label">Completed:</span>
                    <span class="agent-detail-value">${formatTimestamp(agent.completed_at)}</span>
                </div>
                ` : ''}

                <div class="agent-detail-section">
                    <div class="agent-detail-section-label">Instructions:</div>
                    <div class="agent-detail-section-content">${escapeHtml(agent.instructions)}</div>
                </div>

                ${agent.follow_up_instructions && agent.follow_up_instructions.length > 0 ? `
                <div class="agent-detail-section">
                    <div class="agent-detail-section-label">Follow-up Instructions (${agent.follow_up_instructions.length}):</div>
                    <div class="agent-detail-section-content">
                        ${agent.follow_up_instructions.map(f => `
                            <div class="agent-followup">
                                <span class="agent-followup-time">${formatTimestamp(f.timestamp)}</span>
                                <span class="agent-followup-text">${escapeHtml(f.instruction)}</span>
                            </div>
                        `).join('')}
                    </div>
                </div>
                ` : ''}

                ${agent.result ? `
                <div class="agent-detail-section">
                    <div class="agent-detail-section-label">Result:</div>
                    <div class="agent-detail-section-content agent-result-full">${escapeHtml(agent.result)}</div>
                </div>
                ` : ''}

                ${agent.error_message ? `
                <div class="agent-detail-section">
                    <div class="agent-detail-section-label">Error:</div>
                    <div class="agent-detail-section-content agent-error-full">${escapeHtml(agent.error_message)}</div>
                </div>
                ` : ''}
            </div>
        `;

        // Update modal content
        if (elements.agentDetailsContent) {
            elements.agentDetailsContent.innerHTML = detailsHtml;
        }

        // Show the modal
        showModal('agentDetailsModal');

    } catch (error) {
        console.error('Failed to load agent details:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

/**
 * Cleanup when leaving a conversation
 */
export function cleanupAgents() {
    stopAgentPolling();
    state.conversationAgents = [];
    if (elements.agentsList) {
        elements.agentsList.innerHTML = '';
    }
}

/**
 * Get agent types summary for display
 */
export function getAgentTypesSummary() {
    if (!state.agentTypes || state.agentTypes.length === 0) {
        return 'No agent types configured';
    }

    return state.agentTypes.map(t => `${t.label} (${t.name})`).join(', ');
}
