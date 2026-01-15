/**
 * Go Game UI Module
 * Handles Go game panel UI updates and modal interactions
 */

import { state } from './state.js';
import { showToast } from './utils.js';
import { showModal, hideModal } from './modals.js';

// Element references
let elements = {};

// Go game controller reference
let goGame = null;

/**
 * Set element references
 * @param {Object} els - Element references
 */
export function setElements(els) {
    elements = els;
}

/**
 * Set the Go game controller instance
 * @param {Object} controller - GoGameController instance
 */
export function setGoGameController(controller) {
    goGame = controller;
}

/**
 * Update the Go game UI based on game state
 * @param {Object} game - Game state object
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
    if (!goGame) {
        showToast('Go game controller not initialized', 'error');
        return;
    }

    const boardSize = parseInt(elements.goBoardSize?.value, 10) || 19;
    const playerColor = elements.goPlayerColor?.value || 'black';
    const komi = parseFloat(elements.goKomi?.value) || 6.5;

    // AI plays the opposite color
    const entityColor = playerColor === 'black' ? 'white' : 'black';

    hideGoNewGameModal();

    const game = await goGame.createGame({
        boardSize: boardSize,
        komi: komi,
        entityColor: entityColor,
    });

    if (game) {
        showToast('Game started!', 'success');
    }
}

/**
 * Handle Go pass action
 */
export function handleGoPass() {
    if (goGame) {
        goGame.pass();
    }
}

/**
 * Handle Go resign action
 */
export function handleGoResign() {
    if (goGame) {
        goGame.resign();
    }
}

/**
 * Handle Go score action
 */
export function handleGoScore() {
    if (goGame) {
        goGame.score();
    }
}

/**
 * Get Go game context for message injection
 * @returns {string|null} - Game context block or null
 */
export function getGoGameContext() {
    if (goGame) {
        return goGame.buildContextBlock();
    }
    return null;
}

/**
 * Parse and execute AI move from response
 * @param {string} responseContent - AI response content
 */
export async function executeGoMoveFromResponse(responseContent) {
    if (!goGame) return;

    const moveInfo = goGame.parseMoveFromResponse(responseContent);
    if (moveInfo) {
        await goGame.executeAIMove(moveInfo);
    }
}

/**
 * Load game for conversation
 * @param {string} conversationId - Conversation ID
 */
export async function loadGameForConversation(conversationId) {
    if (goGame) {
        await goGame.loadGameForConversation(conversationId);
    }
}

/**
 * Initialize Go board
 * @param {HTMLElement} container - Board container element
 */
export function initBoard(container) {
    if (goGame) {
        goGame.initBoard(container);
    }
}
