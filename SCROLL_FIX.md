# Scroll Fix Instructions

This branch tracks a fix for scroll jumping during tool use.

## The Change

In `frontend/js/app.js`, replace the `scrollToBottom()` method (around line 3006):

**Before:**
```javascript
scrollToBottom() {
    this.elements.messagesContainer.scrollTop = this.elements.messagesContainer.scrollHeight;
}
```

**After:**
```javascript
/**
 * Scroll to the bottom of the messages container.
 * @param {boolean} force - If true, always scroll. If false, only scroll if user is already near bottom.
 */
scrollToBottom(force = false) {
    const container = this.elements.messagesContainer;
    // Only auto-scroll if user is already near the bottom (within 150px) or if forced
    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 150;
    if (force || isNearBottom) {
        container.scrollTop = container.scrollHeight;
    }
}
```

## Why

Tool messages call `scrollToBottom()` on start and result, which yanks the view away if you've scrolled up to read. This makes it smart—only scrolls if you're already at the bottom.

Delete this file after applying the fix.
