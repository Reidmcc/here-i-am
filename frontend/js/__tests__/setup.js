/**
 * Test Setup File
 * Configures jsdom environment and mocks for frontend tests
 */

import { vi, beforeEach, afterEach } from 'vitest';

// Mock localStorage
const localStorageMock = (() => {
    let store = {};
    return {
        getItem: vi.fn((key) => store[key] || null),
        setItem: vi.fn((key, value) => {
            store[key] = String(value);
        }),
        removeItem: vi.fn((key) => {
            delete store[key];
        }),
        clear: vi.fn(() => {
            store = {};
        }),
        get length() {
            return Object.keys(store).length;
        },
        key: vi.fn((index) => {
            return Object.keys(store)[index] || null;
        }),
        _getStore: () => store,
    };
})();

Object.defineProperty(global, 'localStorage', {
    value: localStorageMock,
    writable: true,
});

// Mock URL.createObjectURL and URL.revokeObjectURL
const mockObjectURLs = new Map();
let objectURLCounter = 0;

global.URL.createObjectURL = vi.fn((blob) => {
    const url = `blob:mock-url-${++objectURLCounter}`;
    mockObjectURLs.set(url, blob);
    return url;
});

global.URL.revokeObjectURL = vi.fn((url) => {
    mockObjectURLs.delete(url);
});

// Helper to get mock object URLs (for test assertions)
global._getMockObjectURLs = () => mockObjectURLs;

// Mock navigator.clipboard
Object.defineProperty(global.navigator, 'clipboard', {
    value: {
        writeText: vi.fn(() => Promise.resolve()),
        readText: vi.fn(() => Promise.resolve('')),
    },
    writable: true,
});

// Mock FileReader
class MockFileReader {
    constructor() {
        this.result = null;
        this.error = null;
        this.onload = null;
        this.onerror = null;
    }

    readAsText(file) {
        setTimeout(() => {
            if (file._mockError) {
                this.error = new Error('Mock read error');
                if (this.onerror) this.onerror();
            } else {
                this.result = file._mockContent || 'mock file content';
                if (this.onload) this.onload();
            }
        }, 0);
    }

    readAsDataURL(file) {
        setTimeout(() => {
            if (file._mockError) {
                this.error = new Error('Mock read error');
                if (this.onerror) this.onerror();
            } else {
                const base64Content = file._mockBase64 || 'bW9jayBiYXNlNjQgY29udGVudA==';
                const mediaType = file.type || 'application/octet-stream';
                this.result = `data:${mediaType};base64,${base64Content}`;
                if (this.onload) this.onload();
            }
        }, 0);
    }
}

global.FileReader = MockFileReader;

// Mock File constructor
class MockFile {
    constructor(parts, name, options = {}) {
        this.name = name;
        this.type = options.type || '';
        this.size = parts.reduce((acc, part) => {
            if (typeof part === 'string') return acc + part.length;
            if (part instanceof ArrayBuffer) return acc + part.byteLength;
            return acc;
        }, 0);
        this._mockContent = parts.join('');
        this._mockBase64 = null;
        this._mockError = false;
    }
}

global.File = MockFile;

// Mock Blob
class MockBlob {
    constructor(parts = [], options = {}) {
        this.type = options.type || '';
        this.size = parts.reduce((acc, part) => {
            if (typeof part === 'string') return acc + part.length;
            if (part instanceof ArrayBuffer) return acc + part.byteLength;
            return acc;
        }, 0);
        this._parts = parts;
    }

    text() {
        return Promise.resolve(this._parts.join(''));
    }

    arrayBuffer() {
        const text = this._parts.join('');
        const buffer = new ArrayBuffer(text.length);
        const view = new Uint8Array(buffer);
        for (let i = 0; i < text.length; i++) {
            view[i] = text.charCodeAt(i);
        }
        return Promise.resolve(buffer);
    }
}

global.Blob = MockBlob;

// Mock window.api (global API singleton used by modules)
global.window = global.window || {};
global.window.api = {
    getEntities: vi.fn(() => Promise.resolve([])),
    getConversations: vi.fn(() => Promise.resolve([])),
    getConversation: vi.fn(() => Promise.resolve(null)),
    createConversation: vi.fn(() => Promise.resolve({ id: 'mock-conv-id' })),
    updateConversation: vi.fn(() => Promise.resolve({})),
    deleteConversation: vi.fn(() => Promise.resolve()),
    getMessages: vi.fn(() => Promise.resolve([])),
    sendMessage: vi.fn(() => Promise.resolve({})),
    sendMessageStream: vi.fn(() => Promise.resolve(new ReadableStream())),
    searchMemories: vi.fn(() => Promise.resolve([])),
    getMemoryStats: vi.fn(() => Promise.resolve({})),
    getTTSStatus: vi.fn(() => Promise.resolve({ enabled: false })),
    speak: vi.fn(() => Promise.resolve(new Blob())),
    transcribe: vi.fn(() => Promise.resolve({ text: '' })),
    getPresets: vi.fn(() => Promise.resolve({})),
    archiveConversation: vi.fn(() => Promise.resolve()),
    unarchiveConversation: vi.fn(() => Promise.resolve()),
    getArchivedConversations: vi.fn(() => Promise.resolve([])),
};

// Reset mocks before each test
beforeEach(() => {
    // Clear localStorage
    localStorageMock.clear();
    vi.clearAllMocks();

    // Reset object URL tracking
    mockObjectURLs.clear();
    objectURLCounter = 0;

    // Clear document body
    document.body.innerHTML = '';
});

// Cleanup after each test
afterEach(() => {
    vi.restoreAllMocks();
});

// Export utilities for tests
export {
    localStorageMock,
    mockObjectURLs,
};
