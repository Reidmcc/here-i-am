/**
 * Go Game Module
 * Integrates the GoGameController with the modular application structure.
 */

import { state } from './state.js';
import { showToast } from './utils.js';
import { showModal, hideModal } from './modals.js';

// Reference to global API client and GoGameController
const api = window.api;
const GoGameController = window.GoGameController;

// Module state
let elements = {};
let callbacks = {};
let goGameController = null;

/**
 * Set DOM element references for Go game
 * @param {Object} els - Element references
 */
export function setElements(els) {
    elements = els;
}

/**
 * Set callback functions for Go game
 * @param {Object} cbs - Callback functions
 */
export function setCallbacks(cbs) {
    callbacks = cbs;
}

/**
 * Initialize the Go game controller
 */
export function initGoGameController() {
    if (!GoGameController) {
        console.warn('GoGameController not available - Go game features disabled');
        return;
    }

    goGameController = new GoGameController({
        onGameUpdate: (game) => updateGoGameUI(game),
        onError: (msg) => showToast(msg, 'error'),
        getConversationId: () => state.currentConversationId,
    });

    // Initialize board container if available
    if (elements.goBoardContainer) {
        goGameController.initBoard(elements.goBoardContainer);
    }
}

/**
 * Load Go game for a conversation
 * @param {string} conversationId - Conversation ID
 */
export async function loadGoGameForConversation(conversationId) {
    if (!goGameController) return null;
    return await goGameController.loadGameForConversation(conversationId);
}

/**
 * Build Go game context block for message injection
 * @returns {string|null} Context block or null if no active game
 */
export function buildGoGameContext() {
    if (!goGameController) return null;
    return goGameController.buildContextBlock();
}

/**
 * Parse AI response for Go moves
 * @param {string} response - AI response text
 * @returns {Object|null} Move info
 */
export function parseGoMoveFromResponse(response) {
    if (!goGameController) return null;
    return goGameController.parseMoveFromResponse(response);
}

/**
 * Execute a Go move from AI response
 * @param {Object} moveInfo - Move info from parseGoMoveFromResponse
 */
export async function executeGoAIMove(moveInfo) {
    if (!goGameController) return null;
    return await goGameController.executeAIMove(moveInfo);
}

/**
 * Check if there's an active Go game
 * @returns {boolean}
 */
export function hasActiveGoGame() {
    return goGameController && goGameController.hasActiveGame();
}

/**
 * Check if it's the AI's turn in Go
 * @returns {boolean}
 */
export function isGoEntityTurn() {
    return goGameController && goGameController.isEntityTurn();
}

/**
 * Pass the turn in Go
 */
export async function passGoTurn() {
    if (!goGameController) return;
    await goGameController.pass();
}

/**
 * Resign from Go game
 */
export async function resignGoGame() {
    if (!goGameController) return;
    await goGameController.resign();
}

/**
 * Score the Go game
 */
export async function scoreGoGame() {
    if (!goGameController) return;
    await goGameController.score();
}

/**
 * Show the new Go game modal
 */
export function showGoNewGameModal() {
    if (!state.currentConversationId) {
        showToast('Please create or select a conversation first', 'error');
        return;
    }
    if (elements.goNewGameModal) {
        elements.goNewGameModal.classList.add('active');
    }
}

/**
 * Hide the new Go game modal
 */
export function hideGoNewGameModal() {
    if (elements.goNewGameModal) {
        elements.goNewGameModal.classList.remove('active');
    }
}

/**
 * Create a new Go game from the modal
 */
export async function createGoGame() {
    if (!goGameController) return;

    const boardSize = parseInt(elements.goBoardSize?.value || '19', 10);
    const playerColor = elements.goPlayerColor?.value || 'black';
    const komi = parseFloat(elements.goKomi?.value || '6.5');

    // AI plays the opposite color
    const entityColor = playerColor === 'black' ? 'white' : 'black';

    hideGoNewGameModal();

    const game = await goGameController.createGame({
        boardSize: boardSize,
        komi: komi,
        entityColor: entityColor,
    });

    return game;
}

/**
 * Toggle Go panel visibility
 */
export function toggleGoPanel() {
    if (!elements.goPanel) return;

    const isHidden = elements.goPanel.classList.contains('collapsed');
    if (isHidden) {
        elements.goPanel.classList.remove('collapsed');
        if (elements.goToggleBtn) {
            elements.goToggleBtn.textContent = 'Hide';
        }
    } else {
        elements.goPanel.classList.add('collapsed');
        if (elements.goToggleBtn) {
            elements.goToggleBtn.textContent = 'Show';
        }
    }
}

/**
 * Update the Go game UI based on game state
 * @param {Object|null} game - Game state
 */
export function updateGoGameUI(game) {
    if (!elements.goPanel) return;

    if (!game) {
        // No active game - show empty state
        if (elements.goTurnIndicator) {
            elements.goTurnIndicator.textContent = 'No active game';
        }
        if (elements.goEmptyState) {
            elements.goEmptyState.style.display = 'flex';
        }
        if (elements.goGameInfo) {
            elements.goGameInfo.style.display = 'none';
        }
        if (elements.goControls) {
            elements.goControls.style.display = 'none';
        }
        if (elements.goScoreBtn) {
            elements.goScoreBtn.style.display = 'none';
        }
        return;
    }

    // Hide empty state, show game info and controls
    if (elements.goEmptyState) {
        elements.goEmptyState.style.display = 'none';
    }
    if (elements.goGameInfo) {
        elements.goGameInfo.style.display = 'block';
    }
    if (elements.goControls) {
        elements.goControls.style.display = 'flex';
    }

    // Update turn indicator
    if (elements.goTurnIndicator) {
        if (game.status === 'active') {
            if (game.is_entity_turn) {
                elements.goTurnIndicator.textContent = "AI's turn";
                elements.goTurnIndicator.className = 'go-turn-indicator ai-turn';
            } else {
                elements.goTurnIndicator.textContent = 'Your turn';
                elements.goTurnIndicator.className = 'go-turn-indicator your-turn';
            }
        } else if (game.status === 'finished') {
            const winner = game.winner === 'black' ? 'Black' : 'White';
            elements.goTurnIndicator.textContent = `Game over - ${winner} wins`;
            elements.goTurnIndicator.className = 'go-turn-indicator game-over';
        } else if (game.status === 'scoring') {
            elements.goTurnIndicator.textContent = 'Scoring...';
            elements.goTurnIndicator.className = 'go-turn-indicator scoring';
        }
    }

    // Update game info
    if (elements.goMoveCount) {
        elements.goMoveCount.textContent = game.move_count || 0;
    }
    if (elements.goBlackCaptures) {
        elements.goBlackCaptures.textContent = game.black_captures || 0;
    }
    if (elements.goWhiteCaptures) {
        elements.goWhiteCaptures.textContent = game.white_captures || 0;
    }

    // Show score button only when game is in scoring state or after two passes
    if (elements.goScoreBtn) {
        if (game.status === 'scoring' || game.consecutive_passes >= 2) {
            elements.goScoreBtn.style.display = 'inline-block';
        } else {
            elements.goScoreBtn.style.display = 'none';
        }
    }

    // Disable controls if game is not active
    const isActive = game.status === 'active' && !game.is_entity_turn;
    if (elements.goPassBtn) {
        elements.goPassBtn.disabled = !isActive;
    }
    if (elements.goResignBtn) {
        elements.goResignBtn.disabled = game.status !== 'active';
    }
}

/**
 * Clear Go game state (e.g., when switching conversations)
 */
export function clearGoGame() {
    if (goGameController) {
        goGameController.clearGame();
    }
}
