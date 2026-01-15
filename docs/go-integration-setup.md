# Go Game Integration Setup

This document describes the changes needed to complete the Go game integration.

## Files Created

### Backend
- `backend/app/models/go_game.py` - Database model for Go games
- `backend/app/services/go_service.py` - Game logic (moves, captures, scoring)
- `backend/app/services/go_context.py` - Context injection and response parsing
- `backend/app/routes/go.py` - REST API endpoints

### Frontend
- `frontend/js/go-board.js` - SVG board component
- `frontend/js/go-game.js` - Game controller
- `frontend/js/api.js` - API client methods added
- `frontend/css/go-board.css` - Styling

## Required Changes to index.html

### 1. Add CSS Link (in `<head>`)

After line 7 (`<link rel="stylesheet" href="css/styles.css">`), add:
```html
<link rel="stylesheet" href="css/go-board.css">
```

### 2. Add Go Panel (inside `<main>`, before `</main>`)

After the input-area div (around line 119), before `</main>`, add:
```html
<!-- Go Game Panel -->
<div class="go-panel" id="go-panel">
    <div class="go-panel-header">
        <span class="go-panel-title">Go Game</span>
        <div class="go-panel-actions">
            <button class="go-panel-btn" id="go-new-game-btn">New Game</button>
            <button class="go-panel-btn" id="go-toggle-btn">Hide</button>
        </div>
    </div>
    
    <div class="go-turn-indicator" id="go-turn-indicator">
        No active game
    </div>
    
    <div class="go-board-container" id="go-board-container">
        <div class="go-empty-state" id="go-empty-state">
            <p>No active game</p>
            <button class="go-panel-btn primary" id="go-start-game-btn">Start a Game</button>
        </div>
    </div>
    
    <div class="go-game-info" id="go-game-info" style="display: none;">
        <div class="go-info-row">
            <span class="go-info-label">Move</span>
            <span class="go-info-value" id="go-move-count">0</span>
        </div>
        <div class="go-info-row">
            <span class="go-info-label">Captures</span>
            <div class="go-captures">
                <span class="go-capture-item">
                    <span class="go-stone-icon black"></span>
                    <span id="go-black-captures">0</span>
                </span>
                <span class="go-capture-item">
                    <span class="go-stone-icon white"></span>
                    <span id="go-white-captures">0</span>
                </span>
            </div>
        </div>
    </div>
    
    <div class="go-controls" id="go-controls" style="display: none;">
        <button class="go-control-btn" id="go-pass-btn">Pass</button>
        <button class="go-control-btn" id="go-resign-btn">Resign</button>
        <button class="go-control-btn" id="go-score-btn" style="display: none;">Score</button>
    </div>
</div>
```

### 3. Add New Game Modal (before `</body>`)

Add this modal before the closing `</body>` tag:
```html
<!-- New Go Game Modal -->
<div class="modal" id="go-new-game-modal">
    <div class="modal-content go-new-game-dialog">
        <h3>New Go Game</h3>
        
        <div class="go-dialog-field">
            <label for="go-board-size">Board Size</label>
            <select id="go-board-size">
                <option value="9">9×9 (Quick)</option>
                <option value="13">13×13 (Medium)</option>
                <option value="19" selected>19×19 (Full)</option>
            </select>
        </div>
        
        <div class="go-dialog-field">
            <label for="go-player-color">You Play As</label>
            <select id="go-player-color">
                <option value="white">White (AI plays Black, moves first)</option>
                <option value="black">Black (You move first)</option>
            </select>
        </div>
        
        <div class="go-dialog-field">
            <label for="go-komi">Komi (compensation for White)</label>
            <input type="number" id="go-komi" value="6.5" step="0.5" min="0" max="10">
        </div>
        
        <div class="go-dialog-buttons">
            <button class="go-panel-btn" id="go-cancel-new-game">Cancel</button>
            <button class="go-panel-btn primary" id="go-create-game">Start Game</button>
        </div>
    </div>
</div>
```

### 4. Add Script Tags (before `</body>`)

Before the existing script tags, add:
```html
<script src="js/go-board.js"></script>
<script src="js/go-game.js"></script>
```

The order should be:
1. go-board.js
2. go-game.js  
3. api.js
4. app.js

## Required Changes to app.js

The App class needs to integrate with the Go game controller. Key integration points:

### 1. Initialize Go Game Controller

In the constructor:
```javascript
this.goGame = new GoGameController({
    onGameUpdate: (game) => this.updateGoGameUI(game),
    onError: (msg) => this.showToast(msg, 'error'),
    getConversationId: () => this.currentConversationId,
});
```

### 2. Load Game When Conversation Changes

In `loadConversation()`, after loading messages:
```javascript
await this.goGame.loadGameForConversation(conversationId);
```

### 3. Inject Game Context Before Sending

In `sendMessage()`, before calling the API:
```javascript
let messageToSend = message;
const gameContext = this.goGame.buildContextBlock();
if (gameContext) {
    messageToSend = `[GO GAME STATE]\n${gameContext}\n[/GO GAME STATE]\n\n${message}`;
}
```

### 4. Parse AI Response for Moves

After receiving the AI response:
```javascript
const moveInfo = this.goGame.parseMoveFromResponse(response);
if (moveInfo) {
    await this.goGame.executeAIMove(moveInfo);
}
```

### 5. Update UI Methods

Add methods to update the Go panel based on game state:
- `updateGoGameUI(game)` - Update all Go UI elements
- `showGoPanel()` / `hideGoPanel()` - Toggle visibility
- `openNewGameModal()` / `closeNewGameModal()` - Modal handling

## Testing

1. Create a new conversation
2. Click "New Game" in the Go panel
3. Select board size and color
4. If AI plays black, it should make the first move
5. Click on the board to place stones
6. AI should respond with MOVE: commands
7. Game should track captures and ko correctly
8. Pass/resign should work
9. Two passes should trigger scoring
