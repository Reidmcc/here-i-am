import js from '@eslint/js';
import globals from 'globals';

export default [
  {
    // No build step, so nothing here is generated; only deps are excluded.
    ignores: ['node_modules/**', 'coverage/**'],
  },
  js.configs.recommended,
  {
    files: ['js/**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        ...globals.browser,
      },
    },
    rules: {
      // An `_` prefix marks a binding that is deliberately unused -- a
      // callback parameter kept to document the signature, or a positional
      // placeholder ahead of one that is used.
      'no-unused-vars': ['error', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],
    },
  },
  {
    // Vitest + jsdom: test globals are injected, and the suite reaches for
    // `global` when stubbing browser APIs.
    files: ['js/__tests__/**/*.js'],
    languageOptions: {
      globals: {
        ...globals.node,
        vi: 'readonly',
        describe: 'readonly',
        it: 'readonly',
        test: 'readonly',
        expect: 'readonly',
        beforeEach: 'readonly',
        afterEach: 'readonly',
        beforeAll: 'readonly',
        afterAll: 'readonly',
      },
    },
  },
];
