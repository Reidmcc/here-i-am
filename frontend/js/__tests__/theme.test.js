/**
 * Unit Tests for Theme Module
 * Tests theme switching and persistence
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
    loadTheme,
    getCurrentTheme,
    setTheme,
} from '../modules/theme.js';

describe('Theme Module', () => {
    const THEME_KEY = 'here-i-am-theme';

    beforeEach(() => {
        // Clear theme classes from document root
        document.documentElement.classList.remove('theme-light', 'theme-dark');
    });

    describe('getCurrentTheme', () => {
        it('should return saved theme from localStorage', () => {
            localStorage.setItem(THEME_KEY, 'dark');
            expect(getCurrentTheme()).toBe('dark');
        });

        it('should return "system" when no theme is saved', () => {
            expect(getCurrentTheme()).toBe('system');
        });

        it('should return light theme when saved', () => {
            localStorage.setItem(THEME_KEY, 'light');
            expect(getCurrentTheme()).toBe('light');
        });
    });

    describe('loadTheme', () => {
        it('should apply dark theme class when dark theme is saved', () => {
            localStorage.setItem(THEME_KEY, 'dark');

            loadTheme();

            expect(document.documentElement.classList.contains('theme-dark')).toBe(true);
            expect(document.documentElement.classList.contains('theme-light')).toBe(false);
        });

        it('should apply light theme class when light theme is saved', () => {
            localStorage.setItem(THEME_KEY, 'light');

            loadTheme();

            expect(document.documentElement.classList.contains('theme-light')).toBe(true);
            expect(document.documentElement.classList.contains('theme-dark')).toBe(false);
        });

        it('should not apply any theme class when system theme is saved', () => {
            localStorage.setItem(THEME_KEY, 'system');

            loadTheme();

            expect(document.documentElement.classList.contains('theme-light')).toBe(false);
            expect(document.documentElement.classList.contains('theme-dark')).toBe(false);
        });

        it('should not apply any theme class when no theme is saved', () => {
            loadTheme();

            expect(document.documentElement.classList.contains('theme-light')).toBe(false);
            expect(document.documentElement.classList.contains('theme-dark')).toBe(false);
        });

        it('should remove existing theme classes before applying new one', () => {
            document.documentElement.classList.add('theme-light');
            localStorage.setItem(THEME_KEY, 'dark');

            loadTheme();

            expect(document.documentElement.classList.contains('theme-dark')).toBe(true);
            expect(document.documentElement.classList.contains('theme-light')).toBe(false);
        });
    });

    describe('setTheme', () => {
        describe('dark theme', () => {
            it('should add theme-dark class', () => {
                setTheme('dark');
                expect(document.documentElement.classList.contains('theme-dark')).toBe(true);
            });

            it('should remove theme-light class', () => {
                document.documentElement.classList.add('theme-light');
                setTheme('dark');
                expect(document.documentElement.classList.contains('theme-light')).toBe(false);
            });

            it('should save to localStorage', () => {
                setTheme('dark');
                expect(localStorage.getItem(THEME_KEY)).toBe('dark');
            });
        });

        describe('light theme', () => {
            it('should add theme-light class', () => {
                setTheme('light');
                expect(document.documentElement.classList.contains('theme-light')).toBe(true);
            });

            it('should remove theme-dark class', () => {
                document.documentElement.classList.add('theme-dark');
                setTheme('light');
                expect(document.documentElement.classList.contains('theme-dark')).toBe(false);
            });

            it('should save to localStorage', () => {
                setTheme('light');
                expect(localStorage.getItem(THEME_KEY)).toBe('light');
            });
        });

        describe('system theme', () => {
            it('should remove both theme classes', () => {
                document.documentElement.classList.add('theme-dark');
                setTheme('system');
                expect(document.documentElement.classList.contains('theme-dark')).toBe(false);
                expect(document.documentElement.classList.contains('theme-light')).toBe(false);
            });

            it('should save to localStorage', () => {
                setTheme('system');
                expect(localStorage.getItem(THEME_KEY)).toBe('system');
            });
        });

        it('should handle unknown theme values as system theme', () => {
            setTheme('unknown');
            expect(document.documentElement.classList.contains('theme-dark')).toBe(false);
            expect(document.documentElement.classList.contains('theme-light')).toBe(false);
            expect(localStorage.getItem(THEME_KEY)).toBe('system');
        });

        it('should handle theme switching correctly', () => {
            setTheme('dark');
            expect(document.documentElement.classList.contains('theme-dark')).toBe(true);

            setTheme('light');
            expect(document.documentElement.classList.contains('theme-light')).toBe(true);
            expect(document.documentElement.classList.contains('theme-dark')).toBe(false);

            setTheme('system');
            expect(document.documentElement.classList.contains('theme-light')).toBe(false);
            expect(document.documentElement.classList.contains('theme-dark')).toBe(false);
        });
    });
});
