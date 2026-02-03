/**
 * Utility functions module
 * Contains helper functions used across the application
 */

/**
 * Escape HTML to prevent XSS
 * @param {string} text - Text to escape
 * @returns {string} - Escaped HTML
 */
export function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Truncate text to a maximum length
 * @param {string} text - Text to truncate
 * @param {number} maxLength - Maximum length
 * @returns {string} - Truncated text
 */
export function truncateText(text, maxLength) {
    if (!text || text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
}

/**
 * Render markdown to HTML for message display.
 * Handles: bold, italic, inline code, code blocks, links, and line breaks.
 * @param {string} text - The raw text to render
 * @returns {string} - HTML string with markdown rendered
 */
export function renderMarkdown(text) {
    if (!text) return '';

    // First escape HTML to prevent XSS
    let html = escapeHtml(text);

    // Code blocks (```language\ncode\n```) - must be processed before inline code
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (match, lang, code) => {
        const langClass = lang ? ` data-language="${lang}"` : '';
        return `<pre class="md-code-block"${langClass}><code>${code.trim()}</code></pre>`;
    });

    // Inline code (`code`) - but not inside code blocks
    html = html.replace(/`([^`\n]+)`/g, '<code class="md-inline-code">$1</code>');

    // Bold (**text** or __text__) - process before italic
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');

    // Italic (*text*) - single asterisks
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

    // Italic (_text_) - underscores at word boundaries (without lookbehind for browser compatibility)
    html = html.replace(/(^|[\s\(\[])_([^_]+)_([\s\)\]\.,!?;:]|$)/g, '$1<em>$2</em>$3');

    // Links [text](url)
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" class="md-link">$1</a>');

    // Headers (## text) - only at start of line
    html = html.replace(/^### (.+)$/gm, '<h4 class="md-header">$1</h4>');
    html = html.replace(/^## (.+)$/gm, '<h3 class="md-header">$1</h3>');
    html = html.replace(/^# (.+)$/gm, '<h2 class="md-header">$1</h2>');

    // Unordered lists (- item or * item) - but not if * is for bold/italic
    html = html.replace(/^- (.+)$/gm, '<li class="md-list-item">$1</li>');
    // Wrap consecutive list items in <ul> (use .*? to allow HTML tags like <strong> inside)
    html = html.replace(/(<li class="md-list-item">.*?<\/li>\n?)+/g, '<ul class="md-list">$&</ul>');

    // Ordered lists (1. item)
    html = html.replace(/^\d+\. (.+)$/gm, '<li class="md-list-item-ordered">$1</li>');
    // Wrap consecutive ordered list items in <ol> (use .*? to allow HTML tags like <strong> inside)
    html = html.replace(/(<li class="md-list-item-ordered">.*?<\/li>\n?)+/g, '<ol class="md-list">$&</ol>');

    // Blockquotes (> text)
    html = html.replace(/^&gt; (.+)$/gm, '<blockquote class="md-blockquote">$1</blockquote>');
    // Merge consecutive blockquotes
    html = html.replace(/<\/blockquote>\n<blockquote class="md-blockquote">/g, '<br>');

    // Horizontal rules (---, ***) - must be 3+ characters, alone on a line
    html = html.replace(/^\s*([-*])\1{2,}\s*$/gm, '<hr class="md-hr">');

    return html;
}

/**
 * Strip markdown formatting from text for cleaner TTS.
 * @param {string} text - Text with markdown
 * @returns {string} - Plain text
 */
export function stripMarkdown(text) {
    if (!text) return '';
    return text
        // Remove code blocks
        .replace(/```[\s\S]*?```/g, '')
        // Remove inline code
        .replace(/`[^`]+`/g, '')
        // Remove headers
        .replace(/^#{1,6}\s+/gm, '')
        // Remove bold/italic
        .replace(/\*\*([^*]+)\*\*/g, '$1')
        .replace(/__([^_]+)__/g, '$1')
        .replace(/\*([^*]+)\*/g, '$1')
        .replace(/_([^_]+)_/g, '$1')
        // Remove links, keep text
        .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
        // Remove blockquotes
        .replace(/^>\s+/gm, '')
        // Remove list markers
        .replace(/^[-*]\s+/gm, '')
        .replace(/^\d+\.\s+/gm, '')
        // Remove horizontal rules
        .replace(/^[-*]{3,}$/gm, '')
        // Clean up extra whitespace
        .replace(/\n{3,}/g, '\n\n')
        .trim();
}

/**
 * Read a file as text
 * @param {File} file - File to read
 * @returns {Promise<string>} - File content as text
 */
export function readFileAsText(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error('Failed to read file'));
        reader.readAsText(file);
    });
}

/**
 * Read a file as base64
 * @param {File} file - File to read
 * @returns {Promise<string>} - Base64 encoded content (without data URL prefix)
 */
export function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            // Extract base64 from data URL (remove "data:image/png;base64," prefix)
            const dataUrl = reader.result;
            const base64 = dataUrl.split(',')[1];
            resolve(base64);
        };
        reader.onerror = () => reject(new Error('Failed to read file'));
        reader.readAsDataURL(file);
    });
}

// Toast container reference (set by ToastManager)
let toastContainer = null;

/**
 * Set the toast container element
 * @param {HTMLElement} container - Toast container element
 */
export function setToastContainer(container) {
    toastContainer = container;
}

/**
 * Show a toast notification
 * @param {string} message - Message to display
 * @param {string} type - Toast type: 'info', 'success', 'warning', 'error'
 */
export function showToast(message, type = 'info') {
    if (!toastContainer) {
        toastContainer = document.getElementById('toast-container');
    }
    if (!toastContainer) {
        console.warn('Toast container not found');
        return;
    }

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;

    toastContainer.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 3000);
}

/**
 * Show loading overlay
 * @param {HTMLElement} overlay - Loading overlay element
 * @param {boolean} show - Whether to show or hide
 */
export function showLoading(overlay, show) {
    if (show) {
        overlay.classList.add('active');
    } else {
        overlay.classList.remove('active');
    }
}

/**
 * Format a timestamp for display
 * @param {string} timestamp - ISO timestamp string
 * @returns {string} - Formatted timestamp
 */
export function formatTimestamp(timestamp) {
    if (!timestamp) return '';
    try {
        const date = new Date(timestamp);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / (1000 * 60));
        const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
        const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

        // Less than an hour ago
        if (diffMins < 60) {
            return diffMins <= 1 ? 'just now' : `${diffMins} minutes ago`;
        }

        // Less than a day ago
        if (diffHours < 24) {
            return `${diffHours} hour${diffHours === 1 ? '' : 's'} ago`;
        }

        // Less than a week ago
        if (diffDays < 7) {
            return `${diffDays} day${diffDays === 1 ? '' : 's'} ago`;
        }

        // Otherwise, show the date
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
        return timestamp;
    }
}
