/**
 * Tests for attachments store
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import {
  ALLOWED_IMAGE_TYPES,
  ALLOWED_TEXT_EXTENSIONS,
  MAX_FILE_SIZE,
  pendingAttachments,
  isDragging,
  hasAttachments,
  attachmentCount,
  validateFile,
  readFileAsBase64,
  readFileAsText,
  processFile,
} from './attachments.js';

describe('attachment constants', () => {
  it('should define allowed image types', () => {
    expect(ALLOWED_IMAGE_TYPES).toContain('image/jpeg');
    expect(ALLOWED_IMAGE_TYPES).toContain('image/png');
    expect(ALLOWED_IMAGE_TYPES).toContain('image/gif');
    expect(ALLOWED_IMAGE_TYPES).toContain('image/webp');
  });

  it('should define allowed text extensions', () => {
    expect(ALLOWED_TEXT_EXTENSIONS).toContain('.txt');
    expect(ALLOWED_TEXT_EXTENSIONS).toContain('.md');
    expect(ALLOWED_TEXT_EXTENSIONS).toContain('.js');
    expect(ALLOWED_TEXT_EXTENSIONS).toContain('.py');
    expect(ALLOWED_TEXT_EXTENSIONS).toContain('.json');
  });

  it('should set max file size to 5MB', () => {
    expect(MAX_FILE_SIZE).toBe(5 * 1024 * 1024);
  });
});

describe('pendingAttachments store', () => {
  beforeEach(() => {
    pendingAttachments.reset();
  });

  it('should start empty', () => {
    const attachments = get(pendingAttachments);
    expect(attachments.images).toEqual([]);
    expect(attachments.files).toEqual([]);
  });

  it('should add an image', () => {
    const mockFile = { name: 'test.jpg', type: 'image/jpeg' };
    pendingAttachments.addImage(mockFile, 'blob:preview', 'base64data');

    const attachments = get(pendingAttachments);
    expect(attachments.images).toHaveLength(1);
    expect(attachments.images[0]).toEqual({
      file: mockFile,
      previewUrl: 'blob:preview',
      base64: 'base64data',
    });
  });

  it('should add a file', () => {
    const mockFile = { name: 'test.txt', type: 'text/plain' };
    pendingAttachments.addFile(mockFile, 'file content');

    const attachments = get(pendingAttachments);
    expect(attachments.files).toHaveLength(1);
    expect(attachments.files[0]).toEqual({
      file: mockFile,
      content: 'file content',
    });
  });

  it('should remove an image and revoke URL', () => {
    const mockFile = { name: 'test.jpg', type: 'image/jpeg' };
    pendingAttachments.addImage(mockFile, 'blob:preview', 'base64data');
    expect(get(pendingAttachments).images).toHaveLength(1);

    pendingAttachments.removeImage(0);
    expect(get(pendingAttachments).images).toHaveLength(0);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview');
  });

  it('should remove a file', () => {
    const mockFile = { name: 'test.txt', type: 'text/plain' };
    pendingAttachments.addFile(mockFile, 'content');
    expect(get(pendingAttachments).files).toHaveLength(1);

    pendingAttachments.removeFile(0);
    expect(get(pendingAttachments).files).toHaveLength(0);
  });

  it('should reset and revoke all URLs', () => {
    pendingAttachments.addImage({ name: 'test1.jpg' }, 'blob:1', 'b64-1');
    pendingAttachments.addImage({ name: 'test2.jpg' }, 'blob:2', 'b64-2');
    pendingAttachments.addFile({ name: 'test.txt' }, 'content');

    pendingAttachments.reset();

    const attachments = get(pendingAttachments);
    expect(attachments.images).toEqual([]);
    expect(attachments.files).toEqual([]);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:1');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:2');
  });
});

describe('isDragging store', () => {
  it('should default to false', () => {
    expect(get(isDragging)).toBe(false);
  });

  it('should be settable', () => {
    isDragging.set(true);
    expect(get(isDragging)).toBe(true);
    isDragging.set(false);
    expect(get(isDragging)).toBe(false);
  });
});

describe('hasAttachments derived store', () => {
  beforeEach(() => {
    pendingAttachments.reset();
  });

  it('should return false when empty', () => {
    expect(get(hasAttachments)).toBe(false);
  });

  it('should return true when has images', () => {
    pendingAttachments.addImage({ name: 'test.jpg' }, 'blob:url', 'b64');
    expect(get(hasAttachments)).toBe(true);
  });

  it('should return true when has files', () => {
    pendingAttachments.addFile({ name: 'test.txt' }, 'content');
    expect(get(hasAttachments)).toBe(true);
  });
});

describe('attachmentCount derived store', () => {
  beforeEach(() => {
    pendingAttachments.reset();
  });

  it('should return 0 when empty', () => {
    expect(get(attachmentCount)).toBe(0);
  });

  it('should count images and files', () => {
    pendingAttachments.addImage({ name: 'test.jpg' }, 'blob:url', 'b64');
    pendingAttachments.addFile({ name: 'test.txt' }, 'content');
    pendingAttachments.addFile({ name: 'test2.txt' }, 'content2');
    expect(get(attachmentCount)).toBe(3);
  });
});

describe('validateFile', () => {
  it('should reject files over max size', () => {
    const file = { name: 'large.txt', size: 10 * 1024 * 1024 };
    const result = validateFile(file);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('exceeds maximum size');
  });

  it('should accept valid image types', () => {
    for (const type of ALLOWED_IMAGE_TYPES) {
      const file = { name: 'test.jpg', size: 1000, type };
      const result = validateFile(file);
      expect(result.valid).toBe(true);
      expect(result.type).toBe('image');
    }
  });

  it('should accept valid text extensions', () => {
    const extensions = ['.txt', '.md', '.js', '.py', '.json'];
    for (const ext of extensions) {
      const file = { name: `test${ext}`, size: 1000, type: 'text/plain' };
      const result = validateFile(file);
      expect(result.valid).toBe(true);
      expect(result.type).toBe('text');
    }
  });

  it('should accept PDF files', () => {
    const file = { name: 'document.pdf', size: 1000, type: 'application/pdf' };
    const result = validateFile(file);
    expect(result.valid).toBe(true);
    expect(result.type).toBe('pdf');
  });

  it('should accept DOCX files', () => {
    const file = { name: 'document.docx', size: 1000, type: 'application/vnd.openxmlformats' };
    const result = validateFile(file);
    expect(result.valid).toBe(true);
    expect(result.type).toBe('docx');
  });

  it('should reject unsupported file types', () => {
    const file = { name: 'test.exe', size: 1000, type: 'application/x-msdownload' };
    const result = validateFile(file);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('not supported');
  });
});

describe('readFileAsBase64', () => {
  it('should read file as base64 data URL', async () => {
    const mockFile = new Blob(['test content'], { type: 'text/plain' });

    // Mock FileReader
    const mockReader = {
      result: 'data:text/plain;base64,dGVzdCBjb250ZW50',
      onload: null,
      onerror: null,
      readAsDataURL: vi.fn(function () {
        setTimeout(() => this.onload?.(), 0);
      }),
    };
    vi.spyOn(global, 'FileReader').mockImplementation(() => mockReader);

    const promise = readFileAsBase64(mockFile);
    await vi.waitFor(() => expect(mockReader.readAsDataURL).toHaveBeenCalled());

    const result = await promise;
    expect(result).toBe(mockReader.result);
  });
});

describe('readFileAsText', () => {
  it('should read file as text', async () => {
    const mockFile = new Blob(['test content'], { type: 'text/plain' });

    // Mock FileReader
    const mockReader = {
      result: 'test content',
      onload: null,
      onerror: null,
      readAsText: vi.fn(function () {
        setTimeout(() => this.onload?.(), 0);
      }),
    };
    vi.spyOn(global, 'FileReader').mockImplementation(() => mockReader);

    const promise = readFileAsText(mockFile);
    await vi.waitFor(() => expect(mockReader.readAsText).toHaveBeenCalled());

    const result = await promise;
    expect(result).toBe('test content');
  });
});

describe('processFile', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should throw for invalid files', async () => {
    const file = { name: 'test.exe', size: 1000, type: 'application/x-msdownload' };
    await expect(processFile(file)).rejects.toThrow('not supported');
  });

  it('should throw for oversized files', async () => {
    const file = { name: 'test.txt', size: 10 * 1024 * 1024, type: 'text/plain' };
    await expect(processFile(file)).rejects.toThrow('exceeds maximum size');
  });

  it('should process image files', async () => {
    const mockReader = {
      result: 'data:image/jpeg;base64,abc123',
      onload: null,
      readAsDataURL: vi.fn(function () {
        setTimeout(() => this.onload?.(), 0);
      }),
    };
    vi.spyOn(global, 'FileReader').mockImplementation(() => mockReader);

    const file = { name: 'test.jpg', size: 1000, type: 'image/jpeg' };
    const result = await processFile(file);

    expect(result.type).toBe('image');
    expect(result.file).toBe(file);
    expect(result.base64).toBe('data:image/jpeg;base64,abc123');
    expect(result.previewUrl).toBe('blob:mock-url');
  });

  it('should process text files', async () => {
    const mockReader = {
      result: 'file content',
      onload: null,
      readAsText: vi.fn(function () {
        setTimeout(() => this.onload?.(), 0);
      }),
    };
    vi.spyOn(global, 'FileReader').mockImplementation(() => mockReader);

    const file = { name: 'test.txt', size: 1000, type: 'text/plain' };
    const result = await processFile(file);

    expect(result.type).toBe('file');
    expect(result.file).toBe(file);
    expect(result.content).toBe('file content');
  });
});
