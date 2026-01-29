/**
 * Entities Store - Entity management state
 */
import { writable, derived, get } from 'svelte/store';

// List of all entities
export const entities = writable([]);

// Currently selected entity ID
function createSelectedEntityStore() {
    const stored = typeof localStorage !== 'undefined'
        ? localStorage.getItem('selectedEntityId')
        : null;

    const { subscribe, set } = writable(stored);

    return {
        subscribe,
        set: (value) => {
            if (typeof localStorage !== 'undefined') {
                if (value) {
                    localStorage.setItem('selectedEntityId', value);
                } else {
                    localStorage.removeItem('selectedEntityId');
                }
            }
            set(value);
        }
    };
}

export const selectedEntityId = createSelectedEntityStore();

// Multi-entity mode flag
export const isMultiEntityMode = writable(false);

// Entities participating in current multi-entity conversation
export const currentConversationEntities = writable([]);

// Pending responder ID for multi-entity conversations
export const pendingResponderId = writable(null);

// Per-entity system prompts stored in localStorage
function createEntitySystemPromptsStore() {
    let stored = {};
    if (typeof localStorage !== 'undefined') {
        try {
            stored = JSON.parse(localStorage.getItem('entitySystemPrompts') || '{}');
        } catch (e) {
            stored = {};
        }
    }

    const { subscribe, set, update } = writable(stored);

    return {
        subscribe,
        set: (value) => {
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem('entitySystemPrompts', JSON.stringify(value));
            }
            set(value);
        },
        setForEntity: (entityId, prompt) => {
            update(prompts => {
                const newPrompts = { ...prompts, [entityId]: prompt };
                if (typeof localStorage !== 'undefined') {
                    localStorage.setItem('entitySystemPrompts', JSON.stringify(newPrompts));
                }
                return newPrompts;
            });
        },
        getForEntity: (entityId) => {
            return get({ subscribe })[entityId] || '';
        }
    };
}

export const entitySystemPrompts = createEntitySystemPromptsStore();

// Per-entity model preferences for the current session
// NOT persisted to localStorage - resets on page refresh to pick up .env changes
function createEntityModelPreferencesStore() {
    const { subscribe, set, update } = writable({});

    return {
        subscribe,
        set,
        setForEntity: (entityId, model) => {
            update(prefs => ({ ...prefs, [entityId]: model }));
        },
        getForEntity: (entityId) => {
            return get({ subscribe })[entityId] || null;
        },
        clearForEntity: (entityId) => {
            update(prefs => {
                const newPrefs = { ...prefs };
                delete newPrefs[entityId];
                return newPrefs;
            });
        }
    };
}

export const entityModelPreferences = createEntityModelPreferencesStore();

// Derived store: currently selected entity object
export const selectedEntity = derived(
    [entities, selectedEntityId],
    ([$entities, $selectedEntityId]) => {
        if (!$selectedEntityId || !Array.isArray($entities)) return null;
        return $entities.find(e => e.index_name === $selectedEntityId) || null;
    }
);

// Derived store: entities map for quick lookup
export const entitiesMap = derived(entities, ($entities) => {
    const map = new Map();
    if (Array.isArray($entities)) {
        for (const entity of $entities) {
            map.set(entity.index_name, entity);
        }
    }
    return map;
});

// Helper function to get entity label
export function getEntityLabel(entityId) {
    const map = get(entitiesMap);
    const entity = map.get(entityId);
    return entity?.label || entityId;
}

// Helper function to get entity by ID
export function getEntity(entityId) {
    const map = get(entitiesMap);
    return map.get(entityId) || null;
}

// Reset multi-entity state
export function resetMultiEntityState() {
    isMultiEntityMode.set(false);
    currentConversationEntities.set([]);
    pendingResponderId.set(null);
}
