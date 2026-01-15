/**
 * Memories Module
 * Handles memory panel display and memory browser modal
 */

import { state } from './state.js';
import { escapeHtml, truncateText, showToast } from './utils.js';
import { showModal } from './modals.js';

// Reference to global API client
const api = window.api;

// Element references
let elements = {};

/**
 * Set element references
 * @param {Object} els - Element references
 */
export function setElements(els) {
    elements = els;
}

/**
 * Update the memories panel in the sidebar
 */
export function updateMemoriesPanel() {
    // Check if we have multi-entity memories
    const hasMultiEntityMemories = Object.keys(state.retrievedMemoriesByEntity).length > 0;

    if (hasMultiEntityMemories) {
        // Multi-entity mode: display memories grouped by entity
        let totalCount = 0;
        Object.values(state.retrievedMemoriesByEntity).forEach(e => {
            totalCount += e.memories.length;
        });

        if (elements.memoryCount) {
            elements.memoryCount.textContent = totalCount;
        }

        if (totalCount === 0) {
            if (elements.memoriesContent) {
                elements.memoriesContent.innerHTML = `
                    <div style="color: var(--text-muted); font-size: 0.85rem;">
                        No memories retrieved in this session
                    </div>
                `;
            }
            return;
        }

        // Build HTML with entity sections
        let html = '';
        for (const [entityId, entityData] of Object.entries(state.retrievedMemoriesByEntity)) {
            if (entityData.memories.length === 0) continue;

            html += `
                <div class="memory-entity-section">
                    <div class="memory-entity-header">${escapeHtml(entityData.label)} (${entityData.memories.length})</div>
                    ${entityData.memories.map(mem => renderMemoryItem(mem)).join('')}
                </div>
            `;
        }

        if (elements.memoriesContent) {
            elements.memoriesContent.innerHTML = html;
        }
    } else {
        // Single-entity mode: use flat array
        if (elements.memoryCount) {
            elements.memoryCount.textContent = state.retrievedMemories.length;
        }

        if (state.retrievedMemories.length === 0) {
            if (elements.memoriesContent) {
                elements.memoriesContent.innerHTML = `
                    <div style="color: var(--text-muted); font-size: 0.85rem;">
                        No memories retrieved in this session
                    </div>
                `;
            }
            return;
        }

        if (elements.memoriesContent) {
            elements.memoriesContent.innerHTML = state.retrievedMemories.map(
                mem => renderMemoryItem(mem)
            ).join('');
        }
    }

    // Add click handlers for expanding/collapsing
    if (elements.memoriesContent) {
        elements.memoriesContent.querySelectorAll('.memory-item').forEach(item => {
            item.addEventListener('click', () => {
                const memoryId = item.dataset.memoryId;
                if (state.expandedMemoryIds.has(memoryId)) {
                    state.expandedMemoryIds.delete(memoryId);
                } else {
                    state.expandedMemoryIds.add(memoryId);
                }
                updateMemoriesPanel();
            });
        });
    }
}

/**
 * Render a single memory item HTML
 * @param {Object} mem - Memory object
 * @returns {string} - HTML string
 */
function renderMemoryItem(mem) {
    const isExpanded = state.expandedMemoryIds.has(mem.id);
    const fullContent = mem.content || mem.content_preview || '';
    const truncatedContent = truncateText(fullContent, 100);
    const expandedContent = truncateText(fullContent, 3000);
    const displayContent = isExpanded ? expandedContent : truncatedContent;
    const canExpand = fullContent.length > 100;
    const expandHint = canExpand && !isExpanded ? '<span class="memory-item-expand-hint">(click to expand)</span>' : '';

    return `
        <div class="memory-item${isExpanded ? ' expanded' : ''}" data-memory-id="${mem.id}">
            <div class="memory-item-header">
                <span>${mem.role}${expandHint}</span>
                <span>Retrieved ${mem.times_retrieved}× &middot; Score: ${(mem.score || 0).toFixed(2)}</span>
            </div>
            <div class="memory-item-content">${escapeHtml(displayContent)}</div>
        </div>
    `;
}

/**
 * Handle incoming memory data from streaming events
 * @param {Object} data - Memory data from stream
 */
export function handleMemoryUpdate(data) {
    let hasChanges = false;
    const entityId = data.entity_id;
    const entityLabel = data.entity_label;

    if (entityId) {
        // Multi-entity mode: store memories by entity
        if (!state.retrievedMemoriesByEntity[entityId]) {
            state.retrievedMemoriesByEntity[entityId] = {
                label: entityLabel || entityId,
                memories: []
            };
        }

        const entityMemories = state.retrievedMemoriesByEntity[entityId].memories;

        if (data.trimmed_memory_ids && data.trimmed_memory_ids.length > 0) {
            const trimmedSet = new Set(data.trimmed_memory_ids);
            state.retrievedMemoriesByEntity[entityId].memories = entityMemories.filter(
                mem => !trimmedSet.has(mem.id)
            );
            hasChanges = true;
        }

        if (data.new_memories && data.new_memories.length > 0) {
            const existingIds = new Set(entityMemories.map(m => m.id));
            data.new_memories.forEach(mem => {
                if (!existingIds.has(mem.id)) {
                    state.retrievedMemoriesByEntity[entityId].memories.push(mem);
                }
            });
            hasChanges = true;
        }
    } else {
        // Single-entity mode: use flat array
        if (data.trimmed_memory_ids && data.trimmed_memory_ids.length > 0) {
            const trimmedSet = new Set(data.trimmed_memory_ids);
            state.retrievedMemories = state.retrievedMemories.filter(
                mem => !trimmedSet.has(mem.id)
            );
            hasChanges = true;
        }

        if (data.new_memories && data.new_memories.length > 0) {
            const existingIds = new Set(state.retrievedMemories.map(m => m.id));
            data.new_memories.forEach(mem => {
                if (!existingIds.has(mem.id)) {
                    state.retrievedMemories.push(mem);
                }
            });
            hasChanges = true;
        }
    }

    if (hasChanges) {
        updateMemoriesPanel();
    }
}

// =========================================================================
// Memory Browser Modal
// =========================================================================

/**
 * Show the memories browser modal
 */
export async function showMemoriesModal() {
    showModal('memoriesModal');
    await loadMemoryStats();
    await loadMemoryList();
}

/**
 * Load memory statistics
 */
export async function loadMemoryStats() {
    try {
        const stats = await api.getMemoryStats(state.selectedEntityId);
        const statsEl = document.getElementById('memory-stats');
        if (statsEl) {
            statsEl.innerHTML = `
                <div class="stat-card">
                    <div class="stat-value">${stats.total_count}</div>
                    <div class="stat-label">Total Memories</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${stats.human_count}</div>
                    <div class="stat-label">Human</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${stats.assistant_count}</div>
                    <div class="stat-label">Assistant</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${stats.avg_times_retrieved}</div>
                    <div class="stat-label">Avg Retrievals</div>
                </div>
            `;
        }
    } catch (error) {
        console.error('Failed to load memory stats:', error);
    }
}

/**
 * Load memory list
 */
export async function loadMemoryList() {
    try {
        const memories = await api.listMemories({
            limit: 50,
            sortBy: 'significance',
            entityId: state.selectedEntityId
        });
        const listEl = document.getElementById('memory-list');

        if (!listEl) return;

        if (memories.length === 0) {
            listEl.innerHTML = '<div style="color: var(--text-muted);">No memories stored yet</div>';
            return;
        }

        listEl.innerHTML = memories.map(mem => `
            <div class="memory-list-item">
                <div class="memory-list-item-header">
                    <span class="memory-list-item-role">${mem.role}</span>
                    <span class="memory-list-item-stats">
                        Retrieved ${mem.times_retrieved}× &middot; Significance: ${mem.significance.toFixed(2)}
                    </span>
                </div>
                <div class="memory-list-item-content">${escapeHtml(mem.content_preview)}</div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Failed to load memories:', error);
    }
}

/**
 * Search memories
 */
export async function searchMemories() {
    const input = document.getElementById('memory-search-input');
    const query = input?.value.trim();
    if (!query) return;

    try {
        const results = await api.searchMemories(query, 10, true, state.selectedEntityId);
        const listEl = document.getElementById('memory-list');

        if (!listEl) return;

        if (results.length === 0) {
            listEl.innerHTML = '<div style="color: var(--text-muted);">No matching memories found</div>';
            return;
        }

        listEl.innerHTML = results.map(mem => `
            <div class="memory-list-item">
                <div class="memory-list-item-header">
                    <span class="memory-list-item-role">${mem.role}</span>
                    <span class="memory-list-item-stats">
                        Score: ${(mem.score || 0).toFixed(2)} &middot; Retrieved ${mem.times_retrieved}×
                    </span>
                </div>
                <div class="memory-list-item-content">${escapeHtml(mem.content || mem.content_preview)}</div>
            </div>
        `).join('');
    } catch (error) {
        showToast('Memory search not available', 'warning');
        console.error('Failed to search memories:', error);
    }
}

// =========================================================================
// Orphan Maintenance
// =========================================================================

/**
 * Check for orphaned records
 */
export async function checkForOrphans() {
    const statusEl = document.getElementById('orphan-status');
    const detailsEl = document.getElementById('orphan-details');
    const cleanupBtn = document.getElementById('cleanup-orphans-btn');
    const checkBtn = document.getElementById('check-orphans-btn');

    try {
        if (checkBtn) {
            checkBtn.disabled = true;
            checkBtn.textContent = 'Scanning...';
        }
        if (statusEl) {
            statusEl.innerHTML = '<span class="orphan-count">Scanning for orphaned records...</span>';
        }
        if (detailsEl) {
            detailsEl.style.display = 'none';
        }

        const result = await api.listOrphanedRecords(state.selectedEntityId);

        if (result.orphans_found === 0) {
            if (statusEl) {
                statusEl.innerHTML = '<span class="orphan-count orphan-ok">No orphaned records found</span>';
            }
            if (cleanupBtn) {
                cleanupBtn.disabled = true;
            }
            state._orphanData = null;
        } else {
            if (statusEl) {
                statusEl.innerHTML = `<span class="orphan-count orphan-warning">${result.orphans_found} orphaned record(s) found</span>`;
            }
            if (cleanupBtn) {
                cleanupBtn.disabled = false;
            }
            state._orphanData = result;

            // Show details
            if (detailsEl) {
                detailsEl.style.display = 'block';
                detailsEl.innerHTML = `
                    <div class="orphan-details-header">Orphaned Records:</div>
                    <div class="orphan-list">
                        ${result.orphans.slice(0, 10).map(orphan => `
                            <div class="orphan-item">
                                <span class="orphan-id">${orphan.id.substring(0, 8)}...</span>
                                ${orphan.metadata ? `
                                    <span class="orphan-meta">
                                        ${orphan.metadata.role || 'unknown'} &middot;
                                        ${orphan.metadata.created_at ? new Date(orphan.metadata.created_at).toLocaleDateString() : 'unknown date'}
                                    </span>
                                    <span class="orphan-preview">${escapeHtml(orphan.metadata.content_preview || '')}</span>
                                ` : '<span class="orphan-meta">No metadata available</span>'}
                            </div>
                        `).join('')}
                        ${result.orphans_found > 10 ? `<div class="orphan-more">... and ${result.orphans_found - 10} more</div>` : ''}
                    </div>
                `;
            }
        }
    } catch (error) {
        if (statusEl) {
            statusEl.innerHTML = '<span class="orphan-count orphan-error">Error scanning for orphans</span>';
        }
        showToast('Failed to check for orphaned records', 'error');
        console.error('Failed to check for orphans:', error);
    } finally {
        if (checkBtn) {
            checkBtn.disabled = false;
            checkBtn.textContent = 'Check for Orphans';
        }
    }
}

/**
 * Clean up orphaned records
 */
export async function cleanupOrphans() {
    if (!state._orphanData || state._orphanData.orphans_found === 0) {
        showToast('No orphans to clean up', 'info');
        return;
    }

    const count = state._orphanData.orphans_found;
    if (!confirm(`Are you sure you want to delete ${count} orphaned record(s) from Pinecone?\n\nThis action cannot be undone.`)) {
        return;
    }

    const statusEl = document.getElementById('orphan-status');
    const cleanupBtn = document.getElementById('cleanup-orphans-btn');
    const checkBtn = document.getElementById('check-orphans-btn');

    try {
        if (cleanupBtn) cleanupBtn.disabled = true;
        if (checkBtn) checkBtn.disabled = true;
        if (cleanupBtn) cleanupBtn.textContent = 'Cleaning up...';
        if (statusEl) statusEl.innerHTML = '<span class="orphan-count">Deleting orphaned records...</span>';

        const result = await api.cleanupOrphanedRecords(state.selectedEntityId, false);

        if (result.errors && result.errors.length > 0) {
            if (statusEl) {
                statusEl.innerHTML = `<span class="orphan-count orphan-warning">Cleaned ${result.orphans_deleted} records with errors</span>`;
            }
            showToast(`Cleanup completed with errors: ${result.errors.join(', ')}`, 'warning');
        } else {
            if (statusEl) {
                statusEl.innerHTML = `<span class="orphan-count orphan-ok">Successfully deleted ${result.orphans_deleted} orphaned record(s)</span>`;
            }
            showToast(`Cleaned up ${result.orphans_deleted} orphaned records`, 'success');
        }

        // Hide details and reset
        const detailsEl = document.getElementById('orphan-details');
        if (detailsEl) {
            detailsEl.style.display = 'none';
        }
        state._orphanData = null;
        if (cleanupBtn) cleanupBtn.disabled = true;
    } catch (error) {
        if (statusEl) {
            statusEl.innerHTML = '<span class="orphan-count orphan-error">Error during cleanup</span>';
        }
        showToast('Failed to clean up orphaned records', 'error');
        console.error('Failed to cleanup orphans:', error);
    } finally {
        if (checkBtn) checkBtn.disabled = false;
        if (cleanupBtn) cleanupBtn.textContent = 'Clean Up Orphans';
    }
}
