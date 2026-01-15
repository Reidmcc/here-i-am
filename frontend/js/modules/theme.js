/**
 * Theme Management Module
 * Handles theme switching and persistence
 */

const THEME_KEY = 'here-i-am-theme';

/**
 * Load and apply saved theme on startup
 */
export function loadTheme() {
    const savedTheme = localStorage.getItem(THEME_KEY);
    if (savedTheme && savedTheme !== 'system') {
        document.documentElement.classList.remove('theme-light', 'theme-dark');
        document.documentElement.classList.add(`theme-${savedTheme}`);
    }
    // If no saved theme or 'system', let the CSS @media query handle it
}

/**
 * Get the current theme setting
 * @returns {string} - 'dark', 'light', or 'system'
 */
export function getCurrentTheme() {
    const savedTheme = localStorage.getItem(THEME_KEY);
    return savedTheme || 'system';
}

/**
 * Set and apply a theme
 * @param {string} theme - 'dark', 'light', or 'system'
 */
export function setTheme(theme) {
    const root = document.documentElement;
    root.classList.remove('theme-light', 'theme-dark');

    if (theme === 'dark') {
        root.classList.add('theme-dark');
        localStorage.setItem(THEME_KEY, 'dark');
    } else if (theme === 'light') {
        root.classList.add('theme-light');
        localStorage.setItem(THEME_KEY, 'light');
    } else {
        // 'system' - remove manual override, use CSS @media query
        localStorage.setItem(THEME_KEY, 'system');
    }
}
