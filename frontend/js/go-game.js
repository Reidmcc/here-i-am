/**
 * Go Game Controller
 * 
 * Manages Go game state and coordinates with the conversation system.
 * Handles the dual-channel design:
 * - Channel 1 (Game): Board state, moves, captures
 * - Channel 2 (Conversation): Discussion linked to the game
 */

class GoGameController {
    /**
     * Create a Go game controller.
     * @param {Object} options - Configuration
     * @param {Function} options.onGameUpdate - Called when game state changes
     * @param {Function} options.onError - Called on errors
     * @param {Function} options.getConversationId - Returns current conversation ID
     * @param {Function} options.injectGameContext - Injects game context into message
     */
    constructor(options = {}) {
        this.onGameUpdate = options.onGameUpdate || (() => {});
        this.onError = options.onError || console.error;
        this.getConversationId = options.getConversationId || (() => null);
        this.injectGameContext = options.injectGameContext || null;
        
        this.currentGame = null;
        this.board = null;
        this.boardContainer = null;
    }
    
    /**
     * Initialize the board UI in a container.
     * @param {HTMLElement} container - Container for the board
     */
    initBoard(container) {
        this.boardContainer = container;
        
        if (this.currentGame) {
            this._createBoard();
        }
    }
    
    /**
     * Create or recreate the board component.
     */
    _createBoard() {
        if (!this.boardContainer || !this.currentGame) return;
        
        this.board = new GoBoard(this.boardContainer, {
            size: this.currentGame.board_size,
            onIntersectionClick: (coord, row, col) => this._handleBoardClick(coord, row, col),
        });
        
        this.board.updateState(this.currentGame);
    }
    
    /**
     * Load the active game for a conversation, if any.
     * @param {string} conversationId - Conversation ID
     */
    async loadGameForConversation(conversationId) {
        try {
            const game = await api.getActiveGoGame(conversationId);
            this.currentGame = game;
            
            if (game && this.boardContainer) {
                this._createBoard();
            }
            
            this.onGameUpdate(this.currentGame);
            return this.currentGame;
        } catch (error) {
            // No active game is not an error
            if (error.message.includes('404') || error.message.includes('not found')) {
                this.currentGame = null;
                this.onGameUpdate(null);
                return null;
            }
            this.onError('Failed to load game: ' + error.message);
            return null;
        }
    }
    
    /**
     * Create a new game for the current conversation.
     * @param {Object} options - Game options
     * @param {number} options.boardSize - Board size (9, 13, 19)
     * @param {number} options.komi - Komi value
     * @param {string} options.entityColor - AI plays 'black' or 'white'
     */
    async createGame(options = {}) {
        const conversationId = this.getConversationId();
        if (!conversationId) {
            this.onError('No active conversation');
            return null;
        }
        
        try {
            const game = await api.createGoGame(conversationId, options);
            this.currentGame = game;
            
            if (this.boardContainer) {
                this._createBoard();
            }
            
            this.onGameUpdate(this.currentGame);
            return this.currentGame;
        } catch (error) {
            this.onError('Failed to create game: ' + error.message);
            return null;
        }
    }
    
    /**
     * Handle click on the board.
     */
    async _handleBoardClick(coord, row, col) {
        if (!this.currentGame) return;
        if (this.currentGame.status !== 'active') return;
        
        // Check if it's the human's turn (not entity's turn)
        if (this.currentGame.is_entity_turn) {
            this.onError("It's the AI's turn to play");
            return;
        }
        
        await this.makeMove(coord);
    }
    
    /**
     * Make a move in the current game.
     * @param {string} coordinate - Move coordinate (e.g., 'D4')
     */
    async makeMove(coordinate) {
        if (!this.currentGame) {
            this.onError('No active game');
            return null;
        }
        
        try {
            const result = await api.makeGoMove(this.currentGame.id, coordinate);
            
            if (result.success) {
                this.currentGame = result.game;
                if (this.board) {
                    this.board.updateState(this.currentGame);
                }
                this.onGameUpdate(this.currentGame);
                return result;
            } else {
                this.onError(result.error || 'Invalid move');
                return null;
            }
        } catch (error) {
            this.onError('Failed to make move: ' + error.message);
            return null;
        }
    }
    
    /**
     * Pass the turn.
     */
    async pass() {
        if (!this.currentGame) {
            this.onError('No active game');
            return null;
        }
        
        try {
            const result = await api.passGoTurn(this.currentGame.id);
            this.currentGame = result.game;
            if (this.board) {
                this.board.updateState(this.currentGame);
            }
            this.onGameUpdate(this.currentGame);
            return result;
        } catch (error) {
            this.onError('Failed to pass: ' + error.message);
            return null;
        }
    }
    
    /**
     * Resign from the game.
     */
    async resign() {
        if (!this.currentGame) {
            this.onError('No active game');
            return null;
        }
        
        if (!confirm('Are you sure you want to resign?')) {
            return null;
        }
        
        try {
            const result = await api.resignGoGame(this.currentGame.id);
            this.currentGame = result.game;
            if (this.board) {
                this.board.updateState(this.currentGame);
            }
            this.onGameUpdate(this.currentGame);
            return result;
        } catch (error) {
            this.onError('Failed to resign: ' + error.message);
            return null;
        }
    }
    
    /**
     * Score the game.
     */
    async score(finalize = true) {
        if (!this.currentGame) {
            this.onError('No active game');
            return null;
        }
        
        try {
            const result = await api.scoreGoGame(this.currentGame.id, finalize);
            if (finalize && result.game) {
                this.currentGame = result.game;
                if (this.board) {
                    this.board.updateState(this.currentGame);
                }
            }
            this.onGameUpdate(this.currentGame);
            return result;
        } catch (error) {
            this.onError('Failed to score: ' + error.message);
            return null;
        }
    }
    
    /**
     * Build the game context block to inject into a message.
     * Returns null if no active game.
     */
    buildContextBlock() {
        if (!this.currentGame) return null;
        if (this.currentGame.status !== 'active') return null;
        
        return this.currentGame.board_ascii + '\n\n' +
            (this.currentGame.is_entity_turn 
                ? 'It is your turn. Include MOVE: <coordinate> in your response (e.g., MOVE: Q4), or MOVE: pass, or MOVE: resign.'
                : 'Waiting for human to play.');
    }
    
    /**
     * Parse AI response for move commands.
     * @param {string} response - AI response text
     * @returns {Object|null} Move info {type: 'move'|'pass'|'resign', coordinate?}
     */
    parseMoveFromResponse(response) {
        const match = response.match(/MOVE:\s*(\S+)/i);
        if (!match) return null;
        
        const moveText = match[1].toLowerCase();
        
        if (moveText === 'pass') {
            return { type: 'pass' };
        } else if (moveText === 'resign') {
            return { type: 'resign' };
        } else {
            return { type: 'move', coordinate: moveText.toUpperCase() };
        }
    }
    
    /**
     * Execute a move parsed from AI response.
     * @param {Object} moveInfo - Move info from parseMoveFromResponse
     */
    async executeAIMove(moveInfo) {
        if (!moveInfo) return null;
        if (!this.currentGame) return null;
        
        try {
            let result;
            
            if (moveInfo.type === 'pass') {
                result = await api.passGoTurn(this.currentGame.id);
            } else if (moveInfo.type === 'resign') {
                result = await api.resignGoGame(this.currentGame.id);
            } else if (moveInfo.type === 'move') {
                result = await api.makeGoMove(this.currentGame.id, moveInfo.coordinate);
            }
            
            if (result && result.game) {
                this.currentGame = result.game;
                if (this.board) {
                    this.board.updateState(this.currentGame);
                }
                this.onGameUpdate(this.currentGame);
            }
            
            return result;
        } catch (error) {
            this.onError('Failed to execute AI move: ' + error.message);
            return null;
        }
    }
    
    /**
     * Check if there's an active game.
     */
    hasActiveGame() {
        return this.currentGame && this.currentGame.status === 'active';
    }
    
    /**
     * Check if it's the AI's turn.
     */
    isEntityTurn() {
        return this.currentGame && this.currentGame.is_entity_turn;
    }
    
    /**
     * Get current game info for display.
     */
    getGameInfo() {
        if (!this.currentGame) return null;
        
        const g = this.currentGame;
        return {
            id: g.id,
            boardSize: g.board_size,
            currentPlayer: g.current_player,
            moveCount: g.move_count,
            blackCaptures: g.black_captures,
            whiteCaptures: g.white_captures,
            status: g.status,
            isEntityTurn: g.is_entity_turn,
            winner: g.winner,
            blackScore: g.black_score,
            whiteScore: g.white_score,
        };
    }
    
    /**
     * Clear the current game state.
     */
    clearGame() {
        this.currentGame = null;
        if (this.boardContainer) {
            this.boardContainer.innerHTML = '';
        }
        this.board = null;
        this.onGameUpdate(null);
    }
}
