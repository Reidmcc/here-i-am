/**
 * Games Module
 * Handles OGS (Online-Go Server) game integration UI
 */

import { state } from './state.js';
import { showToast, escapeHtml } from './utils.js';
import { showModal, hideModal } from './modals.js';

const api = window.api;

let elements = {};
let callbacks = {};

/**
 * Set DOM element references
 */
export function setElements(els) {
    elements = els;
}

/**
 * Set callback functions
 */
export function setCallbacks(cbs) {
    callbacks = { ...callbacks, ...cbs };
}

/**
 * Check if OGS integration is enabled and update status
 */
export async function checkGamesStatus() {
    try {
        const status = await api.getEventsStatus();
        state.eventsStatus = status;

        // Check if OGS listener is configured
        if (status.listeners && status.listeners.ogs) {
            state.gamesEnabled = true;
            const ogsStatus = status.listeners.ogs;
            console.log('[Games] OGS status:', ogsStatus);
        } else {
            state.gamesEnabled = false;
        }

        // Update UI visibility
        updateGamesButtonVisibility();

        return state.gamesEnabled;
    } catch (error) {
        console.warn('[Games] Failed to check games status:', error);
        state.gamesEnabled = false;
        updateGamesButtonVisibility();
        return false;
    }
}

/**
 * Update the visibility of the games button based on feature availability
 */
function updateGamesButtonVisibility() {
    if (elements.gamesBtn) {
        elements.gamesBtn.style.display = state.gamesEnabled ? '' : 'none';
    }
}

/**
 * Load all active games for the current entity
 */
export async function loadGames() {
    if (!state.gamesEnabled) {
        return [];
    }

    try {
        const games = await api.listGames(state.selectedEntityId);
        state.games = games;
        return games;
    } catch (error) {
        console.error('[Games] Failed to load games:', error);
        showToast('Failed to load games', 'error');
        return [];
    }
}

/**
 * Get game details including board state
 */
export async function getGameDetails(gameId) {
    try {
        return await api.getGame(gameId);
    } catch (error) {
        console.error('[Games] Failed to get game details:', error);
        showToast('Failed to load game details', 'error');
        return null;
    }
}

/**
 * Show the games modal
 */
export async function showGamesModal() {
    showModal('gamesModal');

    // Show loading state
    if (elements.gamesList) {
        elements.gamesList.innerHTML = '<div class="loading-text">Loading games...</div>';
    }

    // Load games
    const games = await loadGames();
    renderGamesList(games);

    // Load events status
    await updateEventsStatusDisplay();
}

/**
 * Hide the games modal
 */
export function hideGamesModal() {
    hideModal('gamesModal');
}

/**
 * Render the list of games in the modal
 */
function renderGamesList(games) {
    if (!elements.gamesList) return;

    if (!games || games.length === 0) {
        elements.gamesList.innerHTML = `
            <div class="games-empty">
                <p>No active games found.</p>
                <p class="text-muted">Games will appear here when you have active OGS matches.</p>
            </div>
        `;
        return;
    }

    const gamesHtml = games.map(game => {
        const turnIndicator = game.our_turn
            ? '<span class="turn-indicator your-turn">Your turn</span>'
            : '<span class="turn-indicator">Opponent\'s turn</span>';

        const linkStatus = game.conversation_id
            ? `<span class="link-status linked">Linked to conversation</span>`
            : `<span class="link-status">Not linked</span>`;

        const colorIcon = game.our_color === 'black'
            ? '<span class="stone-icon black"></span>'
            : '<span class="stone-icon white"></span>';

        return `
            <div class="game-item" data-game-id="${game.game_id}">
                <div class="game-info">
                    <div class="game-header">
                        ${colorIcon}
                        <span class="game-opponent">vs ${escapeHtml(game.opponent_username)}</span>
                        ${turnIndicator}
                    </div>
                    <div class="game-details">
                        <span class="game-board-size">${game.board_size}x${game.board_size}</span>
                        <span class="game-moves">${game.move_count} moves</span>
                        <span class="game-phase">${escapeHtml(game.phase)}</span>
                    </div>
                    <div class="game-link-status">
                        ${linkStatus}
                    </div>
                </div>
                <div class="game-actions">
                    <button class="secondary-btn small" onclick="window.app.viewGame(${game.game_id})">
                        View Board
                    </button>
                    ${game.conversation_id
                        ? `<button class="secondary-btn small" onclick="window.app.goToGameConversation('${game.conversation_id}')">
                               Go to Chat
                           </button>
                           <button class="danger-btn small" onclick="window.app.unlinkGame(${game.game_id})">
                               Unlink
                           </button>`
                        : `<button class="primary-btn small" onclick="window.app.linkGameToConversation(${game.game_id})">
                               Link to Chat
                           </button>`
                    }
                </div>
            </div>
        `;
    }).join('');

    elements.gamesList.innerHTML = gamesHtml;
}

/**
 * Update the events status display in the modal
 */
async function updateEventsStatusDisplay() {
    if (!elements.eventsStatusContainer) return;

    try {
        const status = await api.getEventsStatus();
        state.eventsStatus = status;

        if (!status.listeners || !status.listeners.ogs) {
            elements.eventsStatusContainer.innerHTML = `
                <div class="events-status disconnected">
                    <span class="status-icon">&#x2715;</span>
                    <span>OGS integration not configured</span>
                </div>
            `;
            return;
        }

        const ogsStatus = status.listeners.ogs;
        const isConnected = ogsStatus.status === 'connected';
        const statusClass = isConnected ? 'connected' : 'disconnected';
        const statusIcon = isConnected ? '&#x2713;' : '&#x2715;';

        elements.eventsStatusContainer.innerHTML = `
            <div class="events-status ${statusClass}">
                <span class="status-icon">${statusIcon}</span>
                <span>OGS: ${ogsStatus.status}</span>
                ${ogsStatus.bot_username ? `<span class="bot-name">Bot: ${escapeHtml(ogsStatus.bot_username)}</span>` : ''}
            </div>
        `;
    } catch (error) {
        console.error('[Games] Failed to update events status:', error);
        elements.eventsStatusContainer.innerHTML = `
            <div class="events-status error">
                <span class="status-icon">!</span>
                <span>Failed to load status</span>
            </div>
        `;
    }
}

/**
 * View a game's board state
 */
export async function viewGame(gameId) {
    state.selectedGameId = gameId;

    // Show board modal
    showModal('gameBoardModal');

    if (elements.gameBoardContainer) {
        elements.gameBoardContainer.innerHTML = '<div class="loading-text">Loading board...</div>';
    }

    const gameDetails = await getGameDetails(gameId);

    if (!gameDetails) {
        if (elements.gameBoardContainer) {
            elements.gameBoardContainer.innerHTML = '<div class="error-text">Failed to load game board</div>';
        }
        return;
    }

    renderGameBoard(gameDetails);
}

/**
 * Render the game board display
 */
function renderGameBoard(game) {
    if (!elements.gameBoardContainer) return;

    const turnText = game.our_turn
        ? 'Your turn to play'
        : `Waiting for ${game.opponent_username}`;

    const colorIcon = game.our_color === 'black'
        ? '<span class="stone-icon black"></span>'
        : '<span class="stone-icon white"></span>';

    elements.gameBoardContainer.innerHTML = `
        <div class="game-board-header">
            <div class="game-board-title">
                ${colorIcon}
                <span>vs ${escapeHtml(game.opponent_username)}</span>
            </div>
            <div class="game-board-status">
                <span class="turn-status">${turnText}</span>
                <span class="move-count">Move ${game.move_count}</span>
            </div>
        </div>
        <div class="game-board-captures">
            <span>Captures - Black: ${game.captures.black || 0}, White: ${game.captures.white || 0}</span>
        </div>
        <pre class="game-board-ascii">${escapeHtml(game.board_ascii)}</pre>
        <div class="game-board-actions">
            <a href="https://online-go.com/game/${game.game_id}" target="_blank" class="secondary-btn">
                Open on OGS
            </a>
        </div>
    `;
}

/**
 * Hide the game board modal
 */
export function hideGameBoardModal() {
    hideModal('gameBoardModal');
    state.selectedGameId = null;
}

/**
 * Link a game to a conversation
 */
export async function linkGameToConversation(gameId, conversationId = null) {
    try {
        const result = await api.linkGame(gameId, conversationId);

        if (result.created_new_conversation) {
            showToast('Game linked to new conversation', 'success');
        } else {
            showToast('Game linked to conversation', 'success');
        }

        // Reload games list
        const games = await loadGames();
        renderGamesList(games);

        // Reload conversations if callback is available
        if (callbacks.loadConversations) {
            callbacks.loadConversations();
        }

        // Optionally navigate to the conversation
        if (result.conversation_id && callbacks.loadConversation) {
            callbacks.loadConversation(result.conversation_id);
            hideGamesModal();
        }

        return result;
    } catch (error) {
        console.error('[Games] Failed to link game:', error);
        showToast('Failed to link game: ' + error.message, 'error');
        return null;
    }
}

/**
 * Unlink a game from its conversation
 */
export async function unlinkGame(gameId) {
    try {
        await api.unlinkGame(gameId);
        showToast('Game unlinked from conversation', 'success');

        // Reload games list
        const games = await loadGames();
        renderGamesList(games);

        // Reload conversations
        if (callbacks.loadConversations) {
            callbacks.loadConversations();
        }
    } catch (error) {
        console.error('[Games] Failed to unlink game:', error);
        showToast('Failed to unlink game: ' + error.message, 'error');
    }
}

/**
 * Navigate to a game's linked conversation
 */
export function goToGameConversation(conversationId) {
    if (callbacks.loadConversation) {
        callbacks.loadConversation(conversationId);
        hideGamesModal();
    }
}

/**
 * Get board state for the current conversation (if linked to a game)
 */
export async function getConversationBoardState(conversationId) {
    try {
        return await api.getConversationBoardState(conversationId);
    } catch (error) {
        // Not an error if conversation isn't linked to a game
        if (error.message && error.message.includes('not linked')) {
            return null;
        }
        console.warn('[Games] Failed to get conversation board state:', error);
        return null;
    }
}

/**
 * Update the game indicator in the conversation header
 */
export function updateGameIndicator(conversation) {
    if (!elements.gameIndicator) return;

    if (conversation.external_link_type === 'ogs_game') {
        const gameId = conversation.external_link_id;
        const metadata = conversation.external_link_metadata || {};

        elements.gameIndicator.style.display = 'flex';
        elements.gameIndicator.innerHTML = `
            <span class="game-indicator-icon">&#9679;</span>
            <span class="game-indicator-text">
                Go Game vs ${escapeHtml(metadata.opponent || 'Unknown')}
            </span>
            <button class="game-indicator-btn" onclick="window.app.viewGame(${gameId})" title="View board">
                View Board
            </button>
        `;
    } else {
        elements.gameIndicator.style.display = 'none';
        elements.gameIndicator.innerHTML = '';
    }
}

/**
 * Refresh games list
 */
export async function refreshGames() {
    const games = await loadGames();
    renderGamesList(games);
    await updateEventsStatusDisplay();
    showToast('Games refreshed', 'info');
}
