import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        // Use jsdom environment for DOM testing
        environment: 'jsdom',

        // Enable global test APIs (describe, it, expect, etc.)
        globals: true,

        // Setup file for mocks and globals
        setupFiles: ['./js/__tests__/setup.js'],

        // Test file patterns
        include: ['./js/__tests__/**/*.test.js'],

        // Coverage configuration
        coverage: {
            provider: 'v8',
            reporter: ['text', 'html', 'json'],
            include: ['js/modules/**/*.js'],
            exclude: ['js/__tests__/**', 'js/app.js'],
        },

        // Timeout for tests (in ms)
        testTimeout: 10000,

        // Report slow tests
        slowTestThreshold: 300,
    },
});
