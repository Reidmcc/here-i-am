/**
 * Attachments Store - File attachment state management
 */
import { writable, derived } from 'svelte/store';

// Allowed file types
export const ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
export const ALLOWED_TEXT_EXTENSIONS = ['.txt', '.md', '.py', '.js', '.ts', '.json', '.yaml', '.yml', '.html', '.css', '.xml', '.csv', '.log'];
export const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB

// Pending attachments for the current message
function createAttachmentsStore() {
    const initial = { images: [], files: [] };
    const { subscribe, set, update } = writable(initial);

    return {
        subscribe,
        addImage: (file, previewUrl, base64) => {
            update(attachments => ({
                ...attachments,
                images: [...attachments.images, { file, previewUrl, base64 }]
            }));
        },
        addFile: (file, content) => {
            update(attachments => ({
                ...attachments,
                files: [...attachments.files, { file, content }]
            }));
        },
        removeImage: (index) => {
            update(attachments => {
                const newImages = [...attachments.images];
                const removed = newImages.splice(index, 1)[0];
                // Revoke blob URL
                if (removed?.previewUrl) {
                    URL.revokeObjectURL(removed.previewUrl);
                }
                return { ...attachments, images: newImages };
            });
        },
        removeFile: (index) => {
            update(attachments => {
                const newFiles = [...attachments.files];
                newFiles.splice(index, 1);
                return { ...attachments, files: newFiles };
            });
        },
        reset: () => {
            update(attachments => {
                // Revoke all blob URLs
                for (const img of attachments.images) {
                    if (img.previewUrl) {
                        URL.revokeObjectURL(img.previewUrl);
                    }
                }
                return { images: [], files: [] };
            });
        },
        set
    };
}

export const pendingAttachments = createAttachmentsStore();

// Drag state for drop zone
export const isDragging = writable(false);

// Derived store: has attachments
export const hasAttachments = derived(
    pendingAttachments,
    ($attachments) => $attachments.images.length > 0 || $attachments.files.length > 0
);

// Derived store: attachment count
export const attachmentCount = derived(
    pendingAttachments,
    ($attachments) => $attachments.images.length + $attachments.files.length
);

/**
 * Validate file type and size
 */
export function validateFile(file) {
    if (file.size > MAX_FILE_SIZE) {
        return { valid: false, error: `File "${file.name}" exceeds maximum size of 5MB` };
    }

    // Check if it's an image
    if (ALLOWED_IMAGE_TYPES.includes(file.type)) {
        return { valid: true, type: 'image' };
    }

    // Check if it's an allowed text file
    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
    if (ALLOWED_TEXT_EXTENSIONS.includes(ext)) {
        return { valid: true, type: 'text' };
    }

    // PDF and DOCX are handled server-side
    if (file.name.toLowerCase().endsWith('.pdf')) {
        return { valid: true, type: 'pdf' };
    }
    if (file.name.toLowerCase().endsWith('.docx')) {
        return { valid: true, type: 'docx' };
    }

    return { valid: false, error: `File type not supported: ${file.name}` };
}

/**
 * Read file as base64
 */
export function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

/**
 * Read file as text
 */
export function readFileAsText(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsText(file);
    });
}

/**
 * Process a file for attachment
 */
export async function processFile(file) {
    const validation = validateFile(file);
    if (!validation.valid) {
        throw new Error(validation.error);
    }

    if (validation.type === 'image') {
        const base64 = await readFileAsBase64(file);
        const previewUrl = URL.createObjectURL(file);
        return { type: 'image', file, base64, previewUrl };
    } else {
        // Text file, PDF, DOCX - read as text (PDF/DOCX handled server-side)
        const content = await readFileAsText(file);
        return { type: 'file', file, content };
    }
}
