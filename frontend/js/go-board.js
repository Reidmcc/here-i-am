/**
 * Go Board Component
 * 
 * Renders an interactive Go board using SVG.
 * Handles stone placement, highlighting, and board state visualization.
 */

class GoBoard {
    /**
     * Create a Go board component.
     * @param {HTMLElement} container - Container element for the board
     * @param {Object} options - Configuration options
     * @param {number} options.size - Board size (9, 13, or 19)
     * @param {Function} options.onIntersectionClick - Callback when intersection is clicked
     */
    constructor(container, options = {}) {
        this.container = container;
        this.size = options.size || 19;
        this.onIntersectionClick = options.onIntersectionClick || null;
        
        // Board state
        this.boardState = null;  // 2D array: 0=empty, 1=black, 2=white
        this.lastMove = null;    // {row, col} or null
        this.koPoint = null;     // {row, col} or null
        
        // Visual settings
        this.cellSize = 30;
        this.margin = 25;
        this.stoneRadius = 13;
        
        // Column labels (A-T, skipping I)
        this.colLabels = 'ABCDEFGHJKLMNOPQRST'.slice(0, this.size);
        
        // Star points (hoshi) for different board sizes
        this.starPoints = this._getStarPoints();
        
        // Create SVG
        this._createBoard();
    }
    
    /**
     * Get star point positions for the board size.
     */
    _getStarPoints() {
        if (this.size === 9) {
            return [[2, 2], [2, 6], [4, 4], [6, 2], [6, 6]];
        } else if (this.size === 13) {
            return [[3, 3], [3, 9], [6, 6], [9, 3], [9, 9]];
        } else {  // 19x19
            return [
                [3, 3], [3, 9], [3, 15],
                [9, 3], [9, 9], [9, 15],
                [15, 3], [15, 9], [15, 15]
            ];
        }
    }
    
    /**
     * Create the SVG board structure.
     */
    _createBoard() {
        const totalSize = this.margin * 2 + this.cellSize * (this.size - 1);
        
        // Create SVG element
        this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        this.svg.setAttribute('viewBox', `0 0 ${totalSize} ${totalSize}`);
        this.svg.setAttribute('class', 'go-board-svg');
        
        // Background
        const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        bg.setAttribute('width', totalSize);
        bg.setAttribute('height', totalSize);
        bg.setAttribute('fill', '#DEB887');  // Burlywood - traditional Go board color
        bg.setAttribute('class', 'go-board-background');
        this.svg.appendChild(bg);
        
        // Create groups for layering
        this.gridGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        this.starPointGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        this.labelGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        this.stoneGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        this.markerGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        this.hoverGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        
        this.svg.appendChild(this.gridGroup);
        this.svg.appendChild(this.starPointGroup);
        this.svg.appendChild(this.labelGroup);
        this.svg.appendChild(this.stoneGroup);
        this.svg.appendChild(this.markerGroup);
        this.svg.appendChild(this.hoverGroup);
        
        // Draw grid lines
        this._drawGrid();
        
        // Draw star points
        this._drawStarPoints();
        
        // Draw coordinate labels
        this._drawLabels();
        
        // Add click handler
        this.svg.addEventListener('click', (e) => this._handleClick(e));
        
        // Add hover effect
        this.svg.addEventListener('mousemove', (e) => this._handleMouseMove(e));
        this.svg.addEventListener('mouseleave', () => this._clearHover());
        
        // Add to container
        this.container.innerHTML = '';
        this.container.appendChild(this.svg);
    }
    
    /**
     * Draw the grid lines.
     */
    _drawGrid() {
        for (let i = 0; i < this.size; i++) {
            const pos = this.margin + i * this.cellSize;
            const start = this.margin;
            const end = this.margin + (this.size - 1) * this.cellSize;
            
            // Horizontal line
            const hLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            hLine.setAttribute('x1', start);
            hLine.setAttribute('y1', pos);
            hLine.setAttribute('x2', end);
            hLine.setAttribute('y2', pos);
            hLine.setAttribute('stroke', '#000');
            hLine.setAttribute('stroke-width', i === 0 || i === this.size - 1 ? 1.5 : 0.5);
            this.gridGroup.appendChild(hLine);
            
            // Vertical line
            const vLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            vLine.setAttribute('x1', pos);
            vLine.setAttribute('y1', start);
            vLine.setAttribute('x2', pos);
            vLine.setAttribute('y2', end);
            vLine.setAttribute('stroke', '#000');
            vLine.setAttribute('stroke-width', i === 0 || i === this.size - 1 ? 1.5 : 0.5);
            this.gridGroup.appendChild(vLine);
        }
    }
    
    /**
     * Draw star points (hoshi).
     */
    _drawStarPoints() {
        for (const [row, col] of this.starPoints) {
            const x = this.margin + col * this.cellSize;
            const y = this.margin + row * this.cellSize;
            
            const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            circle.setAttribute('cx', x);
            circle.setAttribute('cy', y);
            circle.setAttribute('r', 3);
            circle.setAttribute('fill', '#000');
            this.starPointGroup.appendChild(circle);
        }
    }
    
    /**
     * Draw coordinate labels.
     */
    _drawLabels() {
        const fontSize = 10;
        const offset = 8;
        
        for (let i = 0; i < this.size; i++) {
            const pos = this.margin + i * this.cellSize;
            
            // Column labels (top and bottom)
            const colLabel = this.colLabels[i];
            
            const topLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            topLabel.setAttribute('x', pos);
            topLabel.setAttribute('y', this.margin - offset);
            topLabel.setAttribute('text-anchor', 'middle');
            topLabel.setAttribute('font-size', fontSize);
            topLabel.setAttribute('fill', '#333');
            topLabel.textContent = colLabel;
            this.labelGroup.appendChild(topLabel);
            
            const bottomLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            bottomLabel.setAttribute('x', pos);
            bottomLabel.setAttribute('y', this.margin + (this.size - 1) * this.cellSize + offset + fontSize);
            bottomLabel.setAttribute('text-anchor', 'middle');
            bottomLabel.setAttribute('font-size', fontSize);
            bottomLabel.setAttribute('fill', '#333');
            bottomLabel.textContent = colLabel;
            this.labelGroup.appendChild(bottomLabel);
            
            // Row labels (left and right) - numbered from bottom
            const rowNum = this.size - i;
            
            const leftLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            leftLabel.setAttribute('x', this.margin - offset);
            leftLabel.setAttribute('y', pos + fontSize / 3);
            leftLabel.setAttribute('text-anchor', 'end');
            leftLabel.setAttribute('font-size', fontSize);
            leftLabel.setAttribute('fill', '#333');
            leftLabel.textContent = rowNum;
            this.labelGroup.appendChild(leftLabel);
            
            const rightLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            rightLabel.setAttribute('x', this.margin + (this.size - 1) * this.cellSize + offset);
            rightLabel.setAttribute('y', pos + fontSize / 3);
            rightLabel.setAttribute('text-anchor', 'start');
            rightLabel.setAttribute('font-size', fontSize);
            rightLabel.setAttribute('fill', '#333');
            rightLabel.textContent = rowNum;
            this.labelGroup.appendChild(rightLabel);
        }
    }
    
    /**
     * Convert pixel coordinates to board position.
     */
    _pixelToBoard(x, y) {
        const col = Math.round((x - this.margin) / this.cellSize);
        const row = Math.round((y - this.margin) / this.cellSize);
        
        if (col >= 0 && col < this.size && row >= 0 && row < this.size) {
            return { row, col };
        }
        return null;
    }
    
    /**
     * Convert board position to coordinate string (e.g., 'D4').
     */
    _boardToCoord(row, col) {
        const colLetter = this.colLabels[col];
        const rowNum = this.size - row;
        return `${colLetter}${rowNum}`;
    }
    
    /**
     * Handle click on the board.
     */
    _handleClick(event) {
        if (!this.onIntersectionClick) return;
        
        const rect = this.svg.getBoundingClientRect();
        const svgWidth = rect.width;
        const viewBoxWidth = this.margin * 2 + this.cellSize * (this.size - 1);
        const scale = viewBoxWidth / svgWidth;
        
        const x = (event.clientX - rect.left) * scale;
        const y = (event.clientY - rect.top) * scale;
        
        const pos = this._pixelToBoard(x, y);
        if (pos) {
            const coord = this._boardToCoord(pos.row, pos.col);
            this.onIntersectionClick(coord, pos.row, pos.col);
        }
    }
    
    /**
     * Handle mouse move for hover effect.
     */
    _handleMouseMove(event) {
        const rect = this.svg.getBoundingClientRect();
        const svgWidth = rect.width;
        const viewBoxWidth = this.margin * 2 + this.cellSize * (this.size - 1);
        const scale = viewBoxWidth / svgWidth;
        
        const x = (event.clientX - rect.left) * scale;
        const y = (event.clientY - rect.top) * scale;
        
        const pos = this._pixelToBoard(x, y);
        this._clearHover();
        
        if (pos && this.boardState) {
            // Only show hover on empty intersections
            if (this.boardState[pos.row][pos.col] === 0) {
                this._showHover(pos.row, pos.col);
            }
        }
    }
    
    /**
     * Show hover indicator at position.
     */
    _showHover(row, col) {
        const x = this.margin + col * this.cellSize;
        const y = this.margin + row * this.cellSize;
        
        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.setAttribute('cx', x);
        circle.setAttribute('cy', y);
        circle.setAttribute('r', this.stoneRadius);
        circle.setAttribute('fill', 'rgba(0, 0, 0, 0.2)');
        circle.setAttribute('class', 'hover-stone');
        circle.style.pointerEvents = 'none';
        this.hoverGroup.appendChild(circle);
    }
    
    /**
     * Clear hover indicator.
     */
    _clearHover() {
        this.hoverGroup.innerHTML = '';
    }
    
    /**
     * Update the board with new state.
     * @param {Object} gameState - Game state from API
     */
    updateState(gameState) {
        this.boardState = gameState.board_state;
        this.lastMove = null;
        this.koPoint = null;
        
        // Parse last move from move history
        if (gameState.move_history) {
            // SGF format: ";B[pd];W[dd]..."
            const moves = gameState.move_history.split(';').filter(m => m);
            if (moves.length > 0) {
                const lastMoveStr = moves[moves.length - 1];
                const match = lastMoveStr.match(/[BW]\[([a-s])([a-s])\]/);
                if (match) {
                    const col = match[1].charCodeAt(0) - 'a'.charCodeAt(0);
                    const row = match[2].charCodeAt(0) - 'a'.charCodeAt(0);
                    this.lastMove = { row, col };
                }
            }
        }
        
        // Parse ko point
        if (gameState.ko_point) {
            const parts = gameState.ko_point.split(',');
            this.koPoint = { row: parseInt(parts[0]), col: parseInt(parts[1]) };
        }
        
        this._renderStones();
        this._renderMarkers();
    }
    
    /**
     * Render all stones on the board.
     */
    _renderStones() {
        this.stoneGroup.innerHTML = '';
        
        if (!this.boardState) return;
        
        for (let row = 0; row < this.size; row++) {
            for (let col = 0; col < this.size; col++) {
                const cell = this.boardState[row][col];
                if (cell !== 0) {
                    this._drawStone(row, col, cell === 1 ? 'black' : 'white');
                }
            }
        }
    }
    
    /**
     * Draw a single stone.
     */
    _drawStone(row, col, color) {
        const x = this.margin + col * this.cellSize;
        const y = this.margin + row * this.cellSize;
        
        // Stone shadow
        const shadow = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        shadow.setAttribute('cx', x + 1);
        shadow.setAttribute('cy', y + 1);
        shadow.setAttribute('r', this.stoneRadius);
        shadow.setAttribute('fill', 'rgba(0, 0, 0, 0.3)');
        this.stoneGroup.appendChild(shadow);
        
        // Stone
        const stone = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        stone.setAttribute('cx', x);
        stone.setAttribute('cy', y);
        stone.setAttribute('r', this.stoneRadius);
        
        if (color === 'black') {
            // Black stone with subtle gradient
            stone.setAttribute('fill', '#111');
        } else {
            // White stone with subtle gradient  
            stone.setAttribute('fill', '#f5f5f5');
            stone.setAttribute('stroke', '#999');
            stone.setAttribute('stroke-width', '0.5');
        }
        
        this.stoneGroup.appendChild(stone);
    }
    
    /**
     * Render markers (last move, ko point).
     */
    _renderMarkers() {
        this.markerGroup.innerHTML = '';
        
        // Last move marker
        if (this.lastMove) {
            const x = this.margin + this.lastMove.col * this.cellSize;
            const y = this.margin + this.lastMove.row * this.cellSize;
            const cell = this.boardState[this.lastMove.row][this.lastMove.col];
            
            const marker = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            marker.setAttribute('cx', x);
            marker.setAttribute('cy', y);
            marker.setAttribute('r', 5);
            marker.setAttribute('fill', 'none');
            marker.setAttribute('stroke', cell === 1 ? '#fff' : '#000');
            marker.setAttribute('stroke-width', 2);
            this.markerGroup.appendChild(marker);
        }
        
        // Ko point marker (X shape)
        if (this.koPoint) {
            const x = this.margin + this.koPoint.col * this.cellSize;
            const y = this.margin + this.koPoint.row * this.cellSize;
            const size = 6;
            
            const line1 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line1.setAttribute('x1', x - size);
            line1.setAttribute('y1', y - size);
            line1.setAttribute('x2', x + size);
            line1.setAttribute('y2', y + size);
            line1.setAttribute('stroke', '#c00');
            line1.setAttribute('stroke-width', 2);
            this.markerGroup.appendChild(line1);
            
            const line2 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line2.setAttribute('x1', x + size);
            line2.setAttribute('y1', y - size);
            line2.setAttribute('x2', x - size);
            line2.setAttribute('y2', y + size);
            line2.setAttribute('stroke', '#c00');
            line2.setAttribute('stroke-width', 2);
            this.markerGroup.appendChild(line2);
        }
    }
    
    /**
     * Resize the board for a different size.
     */
    resize(newSize) {
        if (newSize !== this.size) {
            this.size = newSize;
            this.colLabels = 'ABCDEFGHJKLMNOPQRST'.slice(0, this.size);
            this.starPoints = this._getStarPoints();
            this._createBoard();
        }
    }
}
