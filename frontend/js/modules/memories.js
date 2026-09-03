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
        for (const entityData of Object.values(state.retrievedMemoriesByEntity)) {
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

// Full memory text cache shared by all browser lists (memory id -> content).
// Pre-filled from list payloads; misses fall back to GET /api/memories/{id}.
const memoryFullText = new Map();

const EXPAND_HINT = '<span class="memory-list-item-expand-hint">(click for full text)</span>';

/**
 * Compute the preview text for a memory and whether there is more to show
 * @param {Object} mem - Memory object from the API
 * @returns {{preview: string, canExpand: boolean}}
 */
function memoryPreview(mem) {
    const full = mem.content || '';
    const preview = mem.content_preview || truncateText(full, 200) || '';
    return {
        preview,
        // Without the full content we can't compare lengths; a preview at the
        // backend's 200-char truncation point means there may be more to fetch
        canExpand: full ? full.length > preview.length : preview.length >= 200,
    };
}

/**
 * Make truncated memory list items clickable to toggle their full text.
 * @param {HTMLElement} listEl - Container holding .memory-list-item nodes
 * @param {Array} memories - Memory objects used to pre-fill the text cache
 */
function bindFullTextToggles(listEl, memories) {
    memories.forEach(mem => {
        if (mem.content) {
            memoryFullText.set(mem.id, mem.content);
        }
    });

    listEl.querySelectorAll('.memory-list-item.expandable').forEach(item => {
        const contentEl = item.querySelector('.memory-list-item-content');
        if (!contentEl) return;
        const previewText = contentEl.textContent;

        item.addEventListener('click', async () => {
            if (item.classList.contains('expanded')) {
                item.classList.remove('expanded');
                contentEl.textContent = previewText;
                return;
            }

            const memoryId = item.dataset.memoryId;
            let fullText = memoryFullText.get(memoryId);
            if (fullText === undefined) {
                try {
                    const mem = await api.getMemory(memoryId);
                    fullText = mem.content || '';
                    memoryFullText.set(memoryId, fullText);
                } catch (error) {
                    showToast('Failed to load full memory text', 'error');
                    console.error('Failed to load full memory text:', error);
                    return;
                }
            }
            item.classList.add('expanded');
            contentEl.textContent = fullText;
        });
    });
}

/**
 * Show the memories browser modal
 */
export async function showMemoriesModal() {
    showModal('memoriesModal');
    await loadMemoryStats();
    await loadMemoryList();
    await loadReflections();
    await loadMemoryOverrides();
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

        listEl.innerHTML = memories.map(mem => {
            const { preview, canExpand } = memoryPreview(mem);
            return `
            <div class="memory-list-item${canExpand ? ' expandable' : ''}" data-memory-id="${mem.id}">
                <div class="memory-list-item-header">
                    <span>
                        <span class="memory-list-item-role">${mem.role}</span>
                        ${canExpand ? EXPAND_HINT : ''}
                    </span>
                    <span class="memory-list-item-stats">
                        Retrieved ${mem.times_retrieved}× &middot; Significance: ${mem.significance.toFixed(2)}
                    </span>
                </div>
                <div class="memory-list-item-content">${escapeHtml(preview)}</div>
            </div>
        `;
        }).join('');

        bindFullTextToggles(listEl, memories);
    } catch (error) {
        console.error('Failed to load memories:', error);
    }
}

/**
 * Search memories semantically (same retrieval as the memory_query tool).
 * An empty query restores the default significance-sorted list.
 */
export async function searchMemories() {
    const input = document.getElementById('memory-search-input');
    const query = input?.value.trim();
    if (!query) {
        await loadMemoryList();
        return;
    }

    if (state.selectedEntityId === 'multi-entity') {
        showToast('Select a specific entity to search memories', 'warning');
        return;
    }

    try {
        const results = await api.searchMemories(query, 10, true, state.selectedEntityId);
        const listEl = document.getElementById('memory-list');

        if (!listEl) return;

        if (results.length === 0) {
            listEl.innerHTML = '<div style="color: var(--text-muted);">No matching memories found</div>';
            return;
        }

        listEl.innerHTML = results.map(mem => {
            const { preview, canExpand } = memoryPreview(mem);
            return `
            <div class="memory-list-item${canExpand ? ' expandable' : ''}" data-memory-id="${mem.id}">
                <div class="memory-list-item-header">
                    <span>
                        <span class="memory-list-item-role">${mem.role}</span>
                        ${canExpand ? EXPAND_HINT : ''}
                    </span>
                    <span class="memory-list-item-stats">
                        Similarity: ${(mem.score || 0).toFixed(2)} &middot; Retrieved ${mem.times_retrieved}×
                    </span>
                </div>
                <div class="memory-list-item-content">${escapeHtml(preview)}</div>
            </div>
        `;
        }).join('');

        bindFullTextToggles(listEl, results);
    } catch (error) {
        showToast(error.message || 'Memory search not available', 'warning');
        console.error('Failed to search memories:', error);
    }
}

// =========================================================================
// Reflections (self-authored memories saved via memory_save)
// =========================================================================

/**
 * Load reflection memories — those the entity saved deliberately via memory_save
 */
export async function loadReflections() {
    const listEl = document.getElementById('memory-reflections-list');
    if (!listEl) return;

    try {
        const reflections = await api.listMemories({
            limit: 50,
            role: 'reflection',
            sortBy: 'created_at',
            entityId: state.selectedEntityId
        });

        if (reflections.length === 0) {
            listEl.innerHTML = '<div style="color: var(--text-muted);">No reflections saved yet</div>';
            return;
        }

        listEl.innerHTML = reflections.map(mem => {
            const { preview, canExpand } = memoryPreview(mem);
            return `
            <div class="memory-list-item${canExpand ? ' expandable' : ''}" data-memory-id="${mem.id}">
                <div class="memory-list-item-header">
                    <span>
                        <span class="memory-list-item-role">
                            reflection
                            ${mem.memory_status ? `<span class="memory-status-badge ${mem.memory_status}">${mem.memory_status}</span>` : ''}
                        </span>
                        ${canExpand ? EXPAND_HINT : ''}
                    </span>
                    <span class="memory-list-item-stats">
                        ${new Date(mem.created_at).toLocaleDateString()} &middot;
                        Retrieved ${mem.times_retrieved}&times; &middot;
                        Significance: ${mem.significance.toFixed(2)}
                    </span>
                </div>
                <div class="memory-list-item-content">${escapeHtml(preview)}</div>
            </div>
        `;
        }).join('');

        bindFullTextToggles(listEl, reflections);
    } catch (error) {
        listEl.innerHTML = '<div style="color: var(--text-muted);">Failed to load reflections</div>';
        console.error('Failed to load reflections:', error);
    }
}

// =========================================================================
// Pinned & Released Memories (entity-set status overrides)
// =========================================================================

/**
 * Who set a memory's status and when, for the overrides list.
 * Statuses written before provenance was recorded carry neither.
 */
export function statusProvenance(mem) {
    if (!mem.status_set_by) {
        return `${mem.memory_status} before provenance was recorded`;
    }
    const who = mem.status_set_by === 'researcher' ? 'you (researcher)' : 'the entity';
    const when = mem.status_set_at ? ` on ${new Date(mem.status_set_at).toLocaleString()}` : '';
    return `${mem.memory_status} by ${who}${when}`;
}

/**
 * Load memories with an entity-set status (pinned or released)
 */
export async function loadMemoryOverrides() {
    const listEl = document.getElementById('memory-overrides-list');
    if (!listEl) return;

    try {
        const overrides = await api.listMemoryOverrides(state.selectedEntityId);

        if (overrides.length === 0) {
            listEl.innerHTML = '<div style="color: var(--text-muted);">No pinned or released memories</div>';
            return;
        }

        listEl.innerHTML = overrides.map(mem => {
            const { preview, canExpand } = memoryPreview(mem);
            return `
            <div class="memory-list-item${canExpand ? ' expandable' : ''}" data-memory-id="${mem.id}">
                <div class="memory-list-item-header">
                    <span>
                        <span class="memory-list-item-role">
                            ${escapeHtml(mem.role)}
                            <span class="memory-status-badge ${mem.memory_status}">${mem.memory_status}</span>
                        </span>
                        ${canExpand ? EXPAND_HINT : ''}
                    </span>
                    <span class="memory-list-item-stats">
                        ${new Date(mem.created_at).toLocaleDateString()} &middot;
                        Retrieved ${mem.times_retrieved}&times; &middot;
                        ${escapeHtml(statusProvenance(mem))}
                        <button class="secondary-btn small remove-status-btn" data-memory-id="${mem.id}">
                            Remove ${mem.memory_status === 'pinned' ? 'pin' : 'release'}
                        </button>
                    </span>
                </div>
                <div class="memory-list-item-content">${escapeHtml(preview)}</div>
            </div>
        `;
        }).join('');

        bindFullTextToggles(listEl, overrides);

        listEl.querySelectorAll('.remove-status-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const memoryId = btn.dataset.memoryId;
                if (!confirm('Remove this status? This overrides the entity\'s own choice about its memory.')) {
                    return;
                }
                try {
                    await api.setMemoryStatus(memoryId, null);
                    showToast('Memory status cleared', 'success');
                    await loadMemoryOverrides();
                } catch (error) {
                    showToast('Failed to clear memory status', 'error');
                    console.error('Failed to clear memory status:', error);
                }
            });
        });
    } catch (error) {
        listEl.innerHTML = '<div style="color: var(--text-muted);">Failed to load pinned/released memories</div>';
        console.error('Failed to load memory overrides:', error);
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

// =========================================================================
// Disaster Recovery (rebuild vectors from DB / restore DB from vectors)
// =========================================================================

function rebuildScopeLabel() {
    return state.selectedEntityId ? `entity "${state.selectedEntityId}"` : 'all entities';
}

/**
 * Dry-run a vector rebuild and show what would be upserted
 */
export async function previewRebuildVectors() {
    const statusEl = document.getElementById('rebuild-vectors-status');
    const previewBtn = document.getElementById('preview-rebuild-vectors-btn');
    const runBtn = document.getElementById('run-rebuild-vectors-btn');
    const wipeFirst = document.getElementById('rebuild-wipe-first-checkbox')?.checked || false;

    try {
        if (previewBtn) {
            previewBtn.disabled = true;
            previewBtn.textContent = 'Previewing...';
        }
        if (statusEl) statusEl.innerHTML = '<span class="orphan-count">Planning vector rebuild...</span>';

        const result = await api.rebuildVectors(state.selectedEntityId, true, wipeFirst);
        state._rebuildPreview = result;

        if (result.errors && result.errors.length > 0) {
            if (statusEl) {
                statusEl.innerHTML = `<span class="orphan-count orphan-error">${escapeHtml(result.errors.join('; '))}</span>`;
            }
            if (runBtn) runBtn.disabled = true;
            return;
        }

        const perEntity = result.entities
            .map(e => `${escapeHtml(e.entity_id)}: ${e.records_planned}`)
            .join(', ');
        const skipped = Object.entries(result.skipped || {})
            .filter(([, count]) => count > 0)
            .map(([key, count]) => `${key.replace(/_/g, ' ')}: ${count}`)
            .join(', ');
        if (statusEl) {
            statusEl.innerHTML = `
                <span class="orphan-count ${result.total_records_planned > 0 ? 'orphan-warning' : 'orphan-ok'}">
                    ${result.total_records_planned} record(s) would be upserted (${perEntity || 'no entities'})
                </span>
                ${skipped ? `<span class="orphan-meta">Skipped &mdash; ${escapeHtml(skipped)}</span>` : ''}
            `;
        }
        if (runBtn) runBtn.disabled = result.total_records_planned === 0;
    } catch (error) {
        if (statusEl) statusEl.innerHTML = '<span class="orphan-count orphan-error">Error planning vector rebuild</span>';
        showToast('Failed to preview vector rebuild', 'error');
        console.error('Failed to preview vector rebuild:', error);
    } finally {
        if (previewBtn) {
            previewBtn.disabled = false;
            previewBtn.textContent = 'Preview Vector Rebuild';
        }
    }
}

/**
 * Actually rebuild Pinecone vectors from the SQL database
 */
export async function runRebuildVectors() {
    const preview = state._rebuildPreview;
    if (!preview || preview.total_records_planned === 0) {
        showToast('Run a preview first', 'info');
        return;
    }

    const wipeFirst = document.getElementById('rebuild-wipe-first-checkbox')?.checked || false;
    const wipeNote = wipeFirst
        ? '\n\nThe existing index contents will be DELETED first.'
        : '\n\nExisting records will be overwritten by message ID (no wipe).';
    if (!confirm(`Rebuild vectors for ${rebuildScopeLabel()}?\n\n${preview.total_records_planned} record(s) will be re-vectorized in Pinecone.${wipeNote}`)) {
        return;
    }

    const statusEl = document.getElementById('rebuild-vectors-status');
    const previewBtn = document.getElementById('preview-rebuild-vectors-btn');
    const runBtn = document.getElementById('run-rebuild-vectors-btn');

    try {
        if (runBtn) {
            runBtn.disabled = true;
            runBtn.textContent = 'Rebuilding...';
        }
        if (previewBtn) previewBtn.disabled = true;
        if (statusEl) statusEl.innerHTML = '<span class="orphan-count">Rebuilding vectors (this can take a while)...</span>';

        const result = await api.rebuildVectors(state.selectedEntityId, false, wipeFirst);
        state._rebuildPreview = null;

        const entityErrors = result.entities.flatMap(e => e.errors || []);
        const allErrors = [...(result.errors || []), ...entityErrors];
        if (allErrors.length > 0) {
            if (statusEl) {
                statusEl.innerHTML = `<span class="orphan-count orphan-warning">Upserted ${result.total_records_upserted} record(s) with ${allErrors.length} error(s)</span>`;
            }
            showToast(`Vector rebuild finished with errors (${allErrors.length})`, 'warning');
            console.warn('Vector rebuild errors:', allErrors);
        } else {
            if (statusEl) {
                statusEl.innerHTML = `<span class="orphan-count orphan-ok">Rebuilt ${result.total_records_upserted} vector record(s)</span>`;
            }
            showToast(`Rebuilt ${result.total_records_upserted} vector records`, 'success');
        }
    } catch (error) {
        if (statusEl) statusEl.innerHTML = '<span class="orphan-count orphan-error">Error rebuilding vectors</span>';
        showToast('Failed to rebuild vectors', 'error');
        console.error('Failed to rebuild vectors:', error);
    } finally {
        if (previewBtn) previewBtn.disabled = false;
        if (runBtn) {
            runBtn.disabled = true;
            runBtn.textContent = 'Rebuild Vectors';
        }
    }
}

/**
 * Dry-run a database restore from Pinecone and show what would be created
 */
export async function previewRestoreDatabase() {
    const statusEl = document.getElementById('restore-db-status');
    const previewBtn = document.getElementById('preview-restore-db-btn');
    const runBtn = document.getElementById('run-restore-db-btn');

    try {
        if (previewBtn) {
            previewBtn.disabled = true;
            previewBtn.textContent = 'Previewing...';
        }
        if (statusEl) statusEl.innerHTML = '<span class="orphan-count">Scanning Pinecone records...</span>';

        const result = await api.restoreFromVectors(state.selectedEntityId, true);
        state._restorePreview = result;

        if (result.errors && result.errors.length > 0) {
            if (statusEl) {
                statusEl.innerHTML = `<span class="orphan-count orphan-error">${escapeHtml(result.errors.join('; '))}</span>`;
            }
            if (runBtn) runBtn.disabled = true;
            return;
        }

        const toCreate = result.messages_created + result.conversations_created;
        if (statusEl) {
            statusEl.innerHTML = `
                <span class="orphan-count ${toCreate > 0 ? 'orphan-warning' : 'orphan-ok'}">
                    ${result.conversations_created} conversation(s) and ${result.messages_created} message(s)
                    would be created from ${result.records_scanned} Pinecone record(s)
                </span>
                <span class="orphan-meta">
                    Already in database: ${result.messages_existing} message(s)
                    ${result.messages_preview_only > 0 ? ` &middot; ${result.messages_preview_only} recoverable only as 200-char previews` : ''}
                </span>
            `;
        }
        if (runBtn) runBtn.disabled = toCreate === 0;
    } catch (error) {
        if (statusEl) statusEl.innerHTML = '<span class="orphan-count orphan-error">Error planning database restore</span>';
        showToast('Failed to preview database restore', 'error');
        console.error('Failed to preview database restore:', error);
    } finally {
        if (previewBtn) {
            previewBtn.disabled = false;
            previewBtn.textContent = 'Preview Database Restore';
        }
    }
}

/**
 * Actually restore SQL conversations/messages from Pinecone records
 */
export async function runRestoreDatabase() {
    const preview = state._restorePreview;
    if (!preview || (preview.messages_created === 0 && preview.conversations_created === 0)) {
        showToast('Run a preview first', 'info');
        return;
    }

    if (!confirm(`Restore database from vectors for ${rebuildScopeLabel()}?\n\n${preview.conversations_created} conversation(s) and ${preview.messages_created} message(s) will be created.\n\nExisting rows are never modified. Only vectorized content is recovered (no titles, tool exchanges, or attachments).`)) {
        return;
    }

    const statusEl = document.getElementById('restore-db-status');
    const previewBtn = document.getElementById('preview-restore-db-btn');
    const runBtn = document.getElementById('run-restore-db-btn');

    try {
        if (runBtn) {
            runBtn.disabled = true;
            runBtn.textContent = 'Restoring...';
        }
        if (previewBtn) previewBtn.disabled = true;
        if (statusEl) statusEl.innerHTML = '<span class="orphan-count">Restoring database from Pinecone...</span>';

        const result = await api.restoreFromVectors(state.selectedEntityId, false);
        state._restorePreview = null;

        if (result.errors && result.errors.length > 0) {
            if (statusEl) {
                statusEl.innerHTML = `<span class="orphan-count orphan-warning">Created ${result.conversations_created} conversation(s), ${result.messages_created} message(s) with errors</span>`;
            }
            showToast(`Restore finished with errors: ${result.errors.join(', ')}`, 'warning');
        } else {
            if (statusEl) {
                statusEl.innerHTML = `<span class="orphan-count orphan-ok">Created ${result.conversations_created} conversation(s) and ${result.messages_created} message(s)</span>`;
            }
            showToast(`Restored ${result.messages_created} messages from Pinecone`, 'success');
        }
    } catch (error) {
        if (statusEl) statusEl.innerHTML = '<span class="orphan-count orphan-error">Error restoring database</span>';
        showToast('Failed to restore database from vectors', 'error');
        console.error('Failed to restore database:', error);
    } finally {
        if (previewBtn) previewBtn.disabled = false;
        if (runBtn) {
            runBtn.disabled = true;
            runBtn.textContent = 'Restore Database';
        }
    }
}
