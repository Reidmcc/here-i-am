/**
 * Unit Tests for Attachments Module
 * Tests file/image attachment handling
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { state } from '../modules/state.js';
import {
    setElements,
    setCallbacks,
    handleFileSelect,
    processFiles,
    updateAttachmentPreview,
    removeAttachment,
    clearAttachments,
    hasAttachments,
    getAttachmentsForRequest,
    buildDisplayContentWithAttachments,
} from '../modules/attachments.js';

describe('Attachments Module', () => {
    let mockElements;
    let mockCallbacks;

    beforeEach(() => {
        // Reset state attachments
        state.pendingAttachments = { images: [], files: [] };

        // Create mock elements
        mockElements = {
            attachmentPreview: document.createElement('div'),
            attachmentList: document.createElement('div'),
        };

        // Create mock callbacks
        mockCallbacks = {
            onAttachmentsChanged: vi.fn(),
        };

        setElements(mockElements);
        setCallbacks(mockCallbacks);
    });

    describe('hasAttachments', () => {
        it('should return false when no attachments', () => {
            expect(hasAttachments()).toBe(false);
        });

        it('should return true when images are pending', () => {
            state.pendingAttachments.images.push({ name: 'test.png' });
            expect(hasAttachments()).toBe(true);
        });

        it('should return true when files are pending', () => {
            state.pendingAttachments.files.push({ name: 'test.txt' });
            expect(hasAttachments()).toBe(true);
        });

        it('should return true when both images and files are pending', () => {
            state.pendingAttachments.images.push({ name: 'test.png' });
            state.pendingAttachments.files.push({ name: 'test.txt' });
            expect(hasAttachments()).toBe(true);
        });
    });

    describe('processFiles', () => {
        it('should reject files larger than 5MB', async () => {
            const largeFile = new File(['x'.repeat(6 * 1024 * 1024)], 'large.txt', { type: 'text/plain' });
            largeFile.size = 6 * 1024 * 1024; // Override size

            await processFiles([largeFile]);

            expect(state.pendingAttachments.files.length).toBe(0);
        });

        it('should process valid image files', async () => {
            const imageFile = new File(['fake image data'], 'test.png', { type: 'image/png' });
            imageFile._mockBase64 = 'dGVzdA==';

            await processFiles([imageFile]);

            expect(state.pendingAttachments.images.length).toBe(1);
            expect(state.pendingAttachments.images[0].name).toBe('test.png');
            expect(state.pendingAttachments.images[0].type).toBe('image/png');
            expect(mockCallbacks.onAttachmentsChanged).toHaveBeenCalled();
        });

        it('should process valid text files', async () => {
            const textFile = new File(['hello world'], 'test.txt', { type: 'text/plain' });
            textFile._mockContent = 'hello world';

            await processFiles([textFile]);

            expect(state.pendingAttachments.files.length).toBe(1);
            expect(state.pendingAttachments.files[0].name).toBe('test.txt');
            expect(state.pendingAttachments.files[0].content).toBe('hello world');
            expect(mockCallbacks.onAttachmentsChanged).toHaveBeenCalled();
        });

        it('should handle PDF files as base64 for server-side processing', async () => {
            const pdfFile = new File(['pdf content'], 'document.pdf', { type: 'application/pdf' });

            await processFiles([pdfFile]);

            expect(state.pendingAttachments.files.length).toBe(1);
            expect(state.pendingAttachments.files[0].name).toBe('document.pdf');
            expect(state.pendingAttachments.files[0].type).toBe('application/pdf');
            expect(state.pendingAttachments.files[0].contentType).toBe('base64');
            expect(typeof state.pendingAttachments.files[0].content).toBe('string');
        });

        it('should handle DOCX files as base64 for server-side processing', async () => {
            const docxFile = new File(['docx content'], 'document.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });

            await processFiles([docxFile]);

            expect(state.pendingAttachments.files.length).toBe(1);
            expect(state.pendingAttachments.files[0].name).toBe('document.docx');
            expect(state.pendingAttachments.files[0].contentType).toBe('base64');
            expect(typeof state.pendingAttachments.files[0].content).toBe('string');
        });

        it('should reject unsupported file types', async () => {
            const unsupportedFile = new File(['data'], 'virus.exe', { type: 'application/octet-stream' });

            await processFiles([unsupportedFile]);

            expect(state.pendingAttachments.files.length).toBe(0);
            expect(state.pendingAttachments.images.length).toBe(0);
        });

        it('should process multiple files', async () => {
            const imageFile = new File(['image'], 'test.png', { type: 'image/png' });
            imageFile._mockBase64 = 'dGVzdA==';

            const textFile = new File(['text'], 'test.txt', { type: 'text/plain' });
            textFile._mockContent = 'text content';

            await processFiles([imageFile, textFile]);

            expect(state.pendingAttachments.images.length).toBe(1);
            expect(state.pendingAttachments.files.length).toBe(1);
        });

        it('should handle supported image types', async () => {
            const imageTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];

            for (const type of imageTypes) {
                state.pendingAttachments = { images: [], files: [] };

                const ext = type.split('/')[1];
                const file = new File(['data'], `test.${ext}`, { type });
                file._mockBase64 = 'dGVzdA==';

                await processFiles([file]);

                expect(state.pendingAttachments.images.length).toBe(1);
            }
        });

        it('should handle supported text extensions', async () => {
            const extensions = ['.txt', '.md', '.py', '.js', '.ts', '.json', '.yaml', '.yml', '.html', '.css', '.xml', '.csv', '.log'];

            for (const ext of extensions) {
                state.pendingAttachments = { images: [], files: [] };

                const file = new File(['content'], `test${ext}`, { type: 'text/plain' });
                file._mockContent = 'file content';

                await processFiles([file]);

                expect(state.pendingAttachments.files.length).toBe(1);
            }
        });
    });

    describe('handleFileSelect', () => {
        it('should process files from file input event', async () => {
            const mockFile = new File(['test'], 'test.txt', { type: 'text/plain' });
            mockFile._mockContent = 'test content';

            const mockEvent = {
                target: {
                    files: [mockFile],
                    value: 'C:\\fakepath\\test.txt',
                },
            };

            // handleFileSelect doesn't await processFiles, so we need to wait for it
            handleFileSelect(mockEvent);

            // Wait for async file reading to complete
            await new Promise(resolve => setTimeout(resolve, 10));

            expect(state.pendingAttachments.files.length).toBe(1);
            expect(mockEvent.target.value).toBe('');
        });

        it('should reset input value after processing', () => {
            const mockEvent = {
                target: {
                    files: [],
                    value: 'something',
                },
            };

            handleFileSelect(mockEvent);

            expect(mockEvent.target.value).toBe('');
        });
    });

    describe('updateAttachmentPreview', () => {
        it('should hide preview when no attachments', () => {
            mockElements.attachmentPreview.style.display = 'block';

            updateAttachmentPreview();

            expect(mockElements.attachmentPreview.style.display).toBe('none');
        });

        it('should show preview when attachments exist', () => {
            state.pendingAttachments.images.push({
                name: 'test.png',
                previewUrl: 'blob:url',
            });

            updateAttachmentPreview();

            expect(mockElements.attachmentPreview.style.display).toBe('block');
        });

        it('should render image previews', () => {
            state.pendingAttachments.images.push({
                name: 'image.png',
                previewUrl: 'blob:image-url',
            });

            updateAttachmentPreview();

            const html = mockElements.attachmentList.innerHTML;
            expect(html).toContain('attachment-item image');
            expect(html).toContain('blob:image-url');
            expect(html).toContain('image.png');
        });

        it('should render file previews', () => {
            state.pendingAttachments.files.push({
                name: 'document.txt',
                type: 'text/plain',
            });

            updateAttachmentPreview();

            const html = mockElements.attachmentList.innerHTML;
            expect(html).toContain('attachment-item file');
            expect(html).toContain('TXT');
            expect(html).toContain('document.txt');
        });

        it('should handle XSS in filenames', () => {
            state.pendingAttachments.files.push({
                name: '<script>alert("xss")</script>.txt',
                type: 'text/plain',
            });

            updateAttachmentPreview();

            const html = mockElements.attachmentList.innerHTML;
            expect(html).not.toContain('<script>');
            expect(html).toContain('&lt;script&gt;');
        });
    });

    describe('removeAttachment', () => {
        beforeEach(() => {
            state.pendingAttachments = {
                images: [
                    { name: 'img1.png', previewUrl: 'blob:url-1' },
                    { name: 'img2.png', previewUrl: 'blob:url-2' },
                ],
                files: [
                    { name: 'doc1.txt' },
                    { name: 'doc2.txt' },
                ],
            };
        });

        it('should remove image at specified index', () => {
            removeAttachment('image', 0);

            expect(state.pendingAttachments.images.length).toBe(1);
            expect(state.pendingAttachments.images[0].name).toBe('img2.png');
        });

        it('should revoke blob URL when removing image', () => {
            removeAttachment('image', 0);

            expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:url-1');
        });

        it('should remove file at specified index', () => {
            removeAttachment('file', 1);

            expect(state.pendingAttachments.files.length).toBe(1);
            expect(state.pendingAttachments.files[0].name).toBe('doc1.txt');
        });

        it('should call onAttachmentsChanged callback', () => {
            removeAttachment('image', 0);

            expect(mockCallbacks.onAttachmentsChanged).toHaveBeenCalled();
        });

        it('should update preview after removal', () => {
            mockElements.attachmentPreview.style.display = 'block';

            // Remove all attachments
            removeAttachment('image', 0);
            removeAttachment('image', 0);
            removeAttachment('file', 0);
            removeAttachment('file', 0);

            expect(mockElements.attachmentPreview.style.display).toBe('none');
        });

        it('should handle image without previewUrl', () => {
            state.pendingAttachments.images.push({ name: 'no-url.png' });

            expect(() => removeAttachment('image', 2)).not.toThrow();
        });
    });

    describe('clearAttachments', () => {
        beforeEach(() => {
            state.pendingAttachments = {
                images: [
                    { name: 'img1.png', previewUrl: 'blob:url-1' },
                    { name: 'img2.png', previewUrl: 'blob:url-2' },
                ],
                files: [
                    { name: 'doc.txt' },
                ],
            };
        });

        it('should clear all attachments', () => {
            clearAttachments();

            expect(state.pendingAttachments.images.length).toBe(0);
            expect(state.pendingAttachments.files.length).toBe(0);
        });

        it('should revoke all blob URLs', () => {
            clearAttachments();

            expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
            expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:url-1');
            expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:url-2');
        });

        it('should call onAttachmentsChanged callback', () => {
            clearAttachments();

            expect(mockCallbacks.onAttachmentsChanged).toHaveBeenCalled();
        });

        it('should update preview to hidden', () => {
            mockElements.attachmentPreview.style.display = 'block';

            clearAttachments();

            expect(mockElements.attachmentPreview.style.display).toBe('none');
        });
    });

    describe('getAttachmentsForRequest', () => {
        it('should format images for API request', () => {
            state.pendingAttachments.images.push({
                name: 'test.png',
                type: 'image/png',
                base64: 'base64data',
            });

            const result = getAttachmentsForRequest();

            expect(result.images.length).toBe(1);
            expect(result.images[0]).toEqual({
                name: 'test.png',
                media_type: 'image/png',
                data: 'base64data',
            });
        });

        it('should format files for API request', () => {
            state.pendingAttachments.files.push({
                name: 'document.txt',
                type: 'text/plain',
                content: 'file content',
                contentType: 'text',
            });

            const result = getAttachmentsForRequest();

            expect(result.files.length).toBe(1);
            expect(result.files[0]).toEqual({
                filename: 'document.txt',
                media_type: 'text/plain',
                content: 'file content',
                content_type: 'text',
            });
        });

        it('should return empty arrays when no attachments', () => {
            const result = getAttachmentsForRequest();

            expect(result.images).toEqual([]);
            expect(result.files).toEqual([]);
        });
    });

    describe('buildDisplayContentWithAttachments', () => {
        it('should return text content when no attachments', () => {
            const result = buildDisplayContentWithAttachments('Hello world', { images: [], files: [] });
            expect(result).toBe('Hello world');
        });

        it('should prepend image count indicator', () => {
            const result = buildDisplayContentWithAttachments('Hello', { images: [{}], files: [] });
            expect(result).toBe('[1 image attached]\n\nHello');
        });

        it('should use plural for multiple images', () => {
            const result = buildDisplayContentWithAttachments('Hello', { images: [{}, {}], files: [] });
            expect(result).toBe('[2 images attached]\n\nHello');
        });

        it('should prepend file count indicator', () => {
            const result = buildDisplayContentWithAttachments('Hello', { images: [], files: [{}] });
            expect(result).toBe('[1 file attached]\n\nHello');
        });

        it('should use plural for multiple files', () => {
            const result = buildDisplayContentWithAttachments('Hello', { images: [], files: [{}, {}, {}] });
            expect(result).toBe('[3 files attached]\n\nHello');
        });

        it('should combine image and file indicators', () => {
            const result = buildDisplayContentWithAttachments('Hello', { images: [{}], files: [{}, {}] });
            expect(result).toBe('[1 image attached] [2 files attached]\n\nHello');
        });

        it('should return placeholder for attachments only (no text)', () => {
            const result = buildDisplayContentWithAttachments('', { images: [{}], files: [] });
            expect(result).toBe('[1 image attached]\n\n');
        });

        it('should return placeholder for null/undefined text with attachments', () => {
            const result = buildDisplayContentWithAttachments(null, { images: [{}], files: [] });
            expect(result).toBe('[1 image attached]\n\n');
        });

        it('should handle missing attachment arrays', () => {
            const result = buildDisplayContentWithAttachments('Hello', {});
            expect(result).toBe('Hello');
        });
    });
});
