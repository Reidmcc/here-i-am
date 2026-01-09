"""
Unit tests for AttachmentService and attachment processing functions.

These tests are self-contained and import the attachment_service module
directly to avoid database initialization issues.
"""
import pytest
import base64
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

# Set up path for direct module import
backend_path = Path(__file__).parent.parent
sys.path.insert(0, str(backend_path))

# Create mock settings before importing
mock_settings = MagicMock()
mock_settings.attachments_enabled = True
mock_settings.attachment_max_size_bytes = 5 * 1024 * 1024
mock_settings.attachment_pdf_enabled = True
mock_settings.attachment_docx_enabled = True
mock_settings.attachment_allowed_image_types = "image/jpeg,image/png,image/gif,image/webp"
mock_settings.attachment_allowed_text_extensions = ".txt,.md,.py,.js,.ts,.json,.yaml,.yml,.html,.css,.xml,.csv,.log"
mock_settings.get_allowed_image_types = MagicMock(return_value=["image/jpeg", "image/png", "image/gif", "image/webp"])
mock_settings.is_allowed_image_type = MagicMock(return_value=True)
mock_settings.is_allowed_text_file = MagicMock(return_value=True)

# Patch settings before importing the module
sys.modules['app.config'] = MagicMock()
sys.modules['app.config'].settings = mock_settings

# Now we can import functions directly from the attachment_service module
import importlib.util
spec = importlib.util.spec_from_file_location(
    "attachment_service",
    backend_path / "app" / "services" / "attachment_service.py"
)
attachment_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attachment_module)

# Extract functions we need to test
build_file_context_block = attachment_module.build_file_context_block
build_persistable_content = attachment_module.build_persistable_content
process_file_attachment = attachment_module.process_file_attachment
has_attachments = attachment_module.has_attachments
get_attachment_summary = attachment_module.get_attachment_summary
format_image_for_anthropic = attachment_module.format_image_for_anthropic
format_image_for_openai = attachment_module.format_image_for_openai
build_multimodal_content_anthropic = attachment_module.build_multimodal_content_anthropic
build_multimodal_content_openai = attachment_module.build_multimodal_content_openai
extract_text_from_pdf = attachment_module.extract_text_from_pdf
extract_text_from_docx = attachment_module.extract_text_from_docx
AttachmentService = attachment_module.AttachmentService
attachment_service = attachment_module.attachment_service


class TestProcessFileAttachment:
    """Tests for process_file_attachment function."""

    def test_process_text_file(self):
        """Test processing a plain text file."""
        content, file_type = process_file_attachment(
            "notes.txt",
            "This is some text content.",
            "text"
        )
        assert content == "This is some text content."
        assert file_type == "Text"

    def test_process_markdown_file(self):
        """Test processing a markdown file."""
        content, file_type = process_file_attachment(
            "readme.md",
            "# Heading\n\nParagraph text.",
            "text"
        )
        assert content == "# Heading\n\nParagraph text."
        assert file_type == "Markdown"

    def test_process_python_file(self):
        """Test processing a Python file."""
        content, file_type = process_file_attachment(
            "script.py",
            "print('hello')",
            "text"
        )
        assert content == "print('hello')"
        assert file_type == "Python"

    def test_process_javascript_file(self):
        """Test processing a JavaScript file."""
        content, file_type = process_file_attachment(
            "app.js",
            "console.log('hello');",
            "text"
        )
        assert content == "console.log('hello');"
        assert file_type == "JavaScript"

    def test_process_typescript_file(self):
        """Test processing a TypeScript file."""
        content, file_type = process_file_attachment(
            "app.ts",
            "const x: number = 1;",
            "text"
        )
        assert content == "const x: number = 1;"
        assert file_type == "TypeScript"

    def test_process_json_file(self):
        """Test processing a JSON file."""
        content, file_type = process_file_attachment(
            "config.json",
            '{"key": "value"}',
            "text"
        )
        assert content == '{"key": "value"}'
        assert file_type == "JSON"

    def test_process_yaml_file(self):
        """Test processing a YAML file."""
        content, file_type = process_file_attachment(
            "config.yaml",
            "key: value",
            "text"
        )
        assert content == "key: value"
        assert file_type == "YAML"

    def test_process_yml_file(self):
        """Test processing a .yml file."""
        content, file_type = process_file_attachment(
            "config.yml",
            "key: value",
            "text"
        )
        assert content == "key: value"
        assert file_type == "YAML"

    def test_process_html_file(self):
        """Test processing an HTML file."""
        content, file_type = process_file_attachment(
            "page.html",
            "<html><body>Hello</body></html>",
            "text"
        )
        assert content == "<html><body>Hello</body></html>"
        assert file_type == "HTML"

    def test_process_css_file(self):
        """Test processing a CSS file."""
        content, file_type = process_file_attachment(
            "styles.css",
            "body { color: red; }",
            "text"
        )
        assert content == "body { color: red; }"
        assert file_type == "CSS"

    def test_process_xml_file(self):
        """Test processing an XML file."""
        content, file_type = process_file_attachment(
            "data.xml",
            "<root><item>value</item></root>",
            "text"
        )
        assert content == "<root><item>value</item></root>"
        assert file_type == "XML"

    def test_process_csv_file(self):
        """Test processing a CSV file."""
        content, file_type = process_file_attachment(
            "data.csv",
            "col1,col2\nval1,val2",
            "text"
        )
        assert content == "col1,col2\nval1,val2"
        assert file_type == "CSV"

    def test_process_log_file(self):
        """Test processing a log file."""
        content, file_type = process_file_attachment(
            "app.log",
            "2024-01-01 INFO: Started",
            "text"
        )
        assert content == "2024-01-01 INFO: Started"
        assert file_type == "Log"

    def test_process_base64_text_file(self):
        """Test processing a base64-encoded text file."""
        text_content = "Hello from base64!"
        base64_content = base64.b64encode(text_content.encode()).decode()

        content, file_type = process_file_attachment(
            "file.txt",
            base64_content,
            "base64"
        )
        assert content == "Hello from base64!"
        assert file_type == "Text"

    def test_process_invalid_base64(self):
        """Test processing invalid base64 content."""
        content, file_type = process_file_attachment(
            "file.txt",
            "not-valid-base64!!!",
            "base64"
        )
        assert "Error decoding file" in content
        assert file_type == "Error"

    def test_process_binary_file_fails_gracefully(self):
        """Test processing a binary file that can't be decoded as text."""
        # Create binary data that's not valid UTF-8
        binary_data = bytes([0x80, 0x81, 0x82, 0xFF, 0xFE])
        base64_content = base64.b64encode(binary_data).decode()

        content, file_type = process_file_attachment(
            "binary.bin",
            base64_content,
            "base64"
        )
        assert "Binary file" in content
        assert file_type == "Binary"


class TestBuildFileContextBlock:
    """Tests for build_file_context_block function."""

    def test_empty_files_list(self):
        """Test with empty files list."""
        result = build_file_context_block([])
        assert result == ""

    def test_single_text_file(self):
        """Test with a single text file."""
        files = [
            {"filename": "notes.txt", "content": "Some notes", "content_type": "text"}
        ]
        result = build_file_context_block(files)

        assert "[ATTACHED FILE: notes.txt (Text)]" in result
        assert "Some notes" in result
        assert "[/ATTACHED FILE]" in result

    def test_multiple_files(self):
        """Test with multiple files."""
        files = [
            {"filename": "readme.md", "content": "# README", "content_type": "text"},
            {"filename": "config.json", "content": '{"key": "value"}', "content_type": "text"},
        ]
        result = build_file_context_block(files)

        assert "[ATTACHED FILE: readme.md (Markdown)]" in result
        assert "# README" in result
        assert "[ATTACHED FILE: config.json (JSON)]" in result
        assert '{"key": "value"}' in result

    def test_file_context_format(self):
        """Test that file context uses proper code block format."""
        files = [
            {"filename": "code.py", "content": "print('hello')", "content_type": "text"}
        ]
        result = build_file_context_block(files)

        # Should have code block markers
        assert "```" in result
        assert "print('hello')" in result

    def test_preserves_file_content_exactly(self):
        """Test that file content is preserved exactly."""
        content = "Line 1\n  Line 2 with indent\n\nLine 4 after blank"
        files = [
            {"filename": "file.txt", "content": content, "content_type": "text"}
        ]
        result = build_file_context_block(files)

        assert content in result


class TestBuildPersistableContent:
    """Tests for build_persistable_content function."""

    def test_no_attachments(self):
        """Test with no attachments returns original message."""
        result = build_persistable_content("Hello world", None)
        assert result == "Hello world"

    def test_empty_attachments(self):
        """Test with empty attachments dict returns original message."""
        result = build_persistable_content("Hello world", {"images": [], "files": []})
        assert result == "Hello world"

    def test_images_only_not_persisted(self):
        """Test that images only (no files) returns original message."""
        attachments = {
            "images": [{"data": "base64data", "media_type": "image/jpeg"}],
            "files": []
        }
        result = build_persistable_content("Describe this image", attachments)
        assert result == "Describe this image"

    def test_single_text_file(self):
        """Test with a single text file attachment."""
        attachments = {
            "images": [],
            "files": [
                {"filename": "notes.txt", "content": "File content here", "content_type": "text"}
            ]
        }
        result = build_persistable_content("Please review", attachments)

        assert "[ATTACHED FILE: notes.txt (Text)]" in result
        assert "File content here" in result
        assert "Please review" in result
        # File context should come before user message
        file_pos = result.find("[ATTACHED FILE:")
        msg_pos = result.find("Please review")
        assert file_pos < msg_pos

    def test_multiple_files(self):
        """Test with multiple file attachments."""
        attachments = {
            "images": [],
            "files": [
                {"filename": "readme.md", "content": "# README", "content_type": "text"},
                {"filename": "code.py", "content": "print(1)", "content_type": "text"},
            ]
        }
        result = build_persistable_content("Review these files", attachments)

        assert "[ATTACHED FILE: readme.md (Markdown)]" in result
        assert "[ATTACHED FILE: code.py (Python)]" in result
        assert "Review these files" in result

    def test_files_and_images_only_files_persisted(self):
        """Test that files are persisted but images are not included."""
        attachments = {
            "images": [{"data": "imagedata", "media_type": "image/png"}],
            "files": [
                {"filename": "notes.txt", "content": "Notes content", "content_type": "text"}
            ]
        }
        result = build_persistable_content("See attached", attachments)

        # Files should be in result
        assert "[ATTACHED FILE: notes.txt (Text)]" in result
        assert "Notes content" in result
        # Image data should NOT be in result
        assert "imagedata" not in result

    def test_empty_message_with_file(self):
        """Test with file attachment but no user message."""
        attachments = {
            "images": [],
            "files": [
                {"filename": "data.json", "content": '{"key": 1}', "content_type": "text"}
            ]
        }
        result = build_persistable_content(None, attachments)

        assert "[ATTACHED FILE: data.json (JSON)]" in result
        assert '{"key": 1}' in result

    def test_empty_message_string_with_file(self):
        """Test with file attachment but empty string message."""
        attachments = {
            "images": [],
            "files": [
                {"filename": "file.txt", "content": "Content", "content_type": "text"}
            ]
        }
        result = build_persistable_content("", attachments)

        assert "[ATTACHED FILE: file.txt (Text)]" in result

    def test_handles_pydantic_models(self):
        """Test that Pydantic models are properly converted to dicts."""
        # Create mock Pydantic-like object
        class MockPydantic:
            def model_dump(self):
                return {"filename": "mock.txt", "content": "Mock content", "content_type": "text"}

        attachments = {
            "images": [],
            "files": [MockPydantic()]
        }
        result = build_persistable_content("Test", attachments)

        assert "[ATTACHED FILE: mock.txt (Text)]" in result


class TestHasAttachments:
    """Tests for has_attachments function."""

    def test_none_attachments(self):
        """Test with None attachments."""
        assert has_attachments(None) is False

    def test_empty_attachments(self):
        """Test with empty attachments dict."""
        assert has_attachments({"images": [], "files": []}) is False

    def test_images_only(self):
        """Test with images only."""
        attachments = {
            "images": [{"data": "x", "media_type": "image/jpeg"}],
            "files": []
        }
        assert has_attachments(attachments) is True

    def test_files_only(self):
        """Test with files only."""
        attachments = {
            "images": [],
            "files": [{"filename": "x.txt", "content": "x", "content_type": "text"}]
        }
        assert has_attachments(attachments) is True

    def test_both_images_and_files(self):
        """Test with both images and files."""
        attachments = {
            "images": [{"data": "x", "media_type": "image/jpeg"}],
            "files": [{"filename": "x.txt", "content": "x", "content_type": "text"}]
        }
        assert has_attachments(attachments) is True


class TestGetAttachmentSummary:
    """Tests for get_attachment_summary function."""

    def test_none_attachments(self):
        """Test with None attachments."""
        assert get_attachment_summary(None) == "none"

    def test_empty_attachments(self):
        """Test with empty attachments."""
        assert get_attachment_summary({"images": [], "files": []}) == "none"

    def test_images_only(self):
        """Test with images only."""
        attachments = {
            "images": [{"data": "x", "media_type": "image/jpeg"}],
            "files": []
        }
        assert get_attachment_summary(attachments) == "1 image(s)"

    def test_multiple_images(self):
        """Test with multiple images."""
        attachments = {
            "images": [
                {"data": "x", "media_type": "image/jpeg"},
                {"data": "y", "media_type": "image/png"},
            ],
            "files": []
        }
        assert get_attachment_summary(attachments) == "2 image(s)"

    def test_files_only(self):
        """Test with files only."""
        attachments = {
            "images": [],
            "files": [{"filename": "x.txt", "content": "x", "content_type": "text"}]
        }
        assert get_attachment_summary(attachments) == "1 file(s)"

    def test_multiple_files(self):
        """Test with multiple files."""
        attachments = {
            "images": [],
            "files": [
                {"filename": "a.txt", "content": "a", "content_type": "text"},
                {"filename": "b.txt", "content": "b", "content_type": "text"},
                {"filename": "c.txt", "content": "c", "content_type": "text"},
            ]
        }
        assert get_attachment_summary(attachments) == "3 file(s)"

    def test_both_images_and_files(self):
        """Test with both images and files."""
        attachments = {
            "images": [{"data": "x", "media_type": "image/jpeg"}],
            "files": [
                {"filename": "a.txt", "content": "a", "content_type": "text"},
                {"filename": "b.txt", "content": "b", "content_type": "text"},
            ]
        }
        assert get_attachment_summary(attachments) == "1 image(s), 2 file(s)"


class TestFormatImageForAnthropic:
    """Tests for format_image_for_anthropic function."""

    def test_formats_correctly(self):
        """Test image formatting for Anthropic API."""
        image = {
            "data": "base64imagedata",
            "media_type": "image/jpeg"
        }
        result = format_image_for_anthropic(image)

        assert result["type"] == "image"
        assert result["source"]["type"] == "base64"
        assert result["source"]["media_type"] == "image/jpeg"
        assert result["source"]["data"] == "base64imagedata"


class TestFormatImageForOpenAI:
    """Tests for format_image_for_openai function."""

    def test_formats_correctly(self):
        """Test image formatting for OpenAI API."""
        image = {
            "data": "base64imagedata",
            "media_type": "image/png"
        }
        result = format_image_for_openai(image)

        assert result["type"] == "image_url"
        assert result["image_url"]["url"] == "data:image/png;base64,base64imagedata"


class TestBuildMultimodalContentAnthropic:
    """Tests for build_multimodal_content_anthropic function."""

    def test_text_only(self):
        """Test with text only, no images or files."""
        result = build_multimodal_content_anthropic("Hello", [], [])

        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "Hello"

    def test_with_images(self):
        """Test with images."""
        images = [
            {"data": "img1", "media_type": "image/jpeg"},
            {"data": "img2", "media_type": "image/png"},
        ]
        result = build_multimodal_content_anthropic("Describe", images, [])

        # Images should come first, then text
        assert result[0]["type"] == "image"
        assert result[1]["type"] == "image"
        assert result[2]["type"] == "text"
        assert result[2]["text"] == "Describe"

    def test_with_files(self):
        """Test with file attachments."""
        files = [
            {"filename": "code.py", "content": "print(1)", "content_type": "text"}
        ]
        result = build_multimodal_content_anthropic("Review", [], files)

        assert len(result) == 1
        assert result[0]["type"] == "text"
        # File context should be prepended to message
        assert "[ATTACHED FILE: code.py (Python)]" in result[0]["text"]
        assert "Review" in result[0]["text"]

    def test_with_images_and_files(self):
        """Test with both images and files."""
        images = [{"data": "img", "media_type": "image/jpeg"}]
        files = [{"filename": "f.txt", "content": "text", "content_type": "text"}]

        result = build_multimodal_content_anthropic("Message", images, files)

        # Image first, then text with file context
        assert result[0]["type"] == "image"
        assert result[1]["type"] == "text"
        assert "[ATTACHED FILE: f.txt (Text)]" in result[1]["text"]
        assert "Message" in result[1]["text"]


class TestBuildMultimodalContentOpenAI:
    """Tests for build_multimodal_content_openai function."""

    def test_text_only(self):
        """Test with text only."""
        result = build_multimodal_content_openai("Hello", [], [])

        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "Hello"

    def test_with_images(self):
        """Test with images (text comes first in OpenAI format)."""
        images = [
            {"data": "img1", "media_type": "image/jpeg"},
        ]
        result = build_multimodal_content_openai("Describe", images, [])

        # Text first, then images for OpenAI
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "Describe"
        assert result[1]["type"] == "image_url"

    def test_with_files(self):
        """Test with file attachments."""
        files = [
            {"filename": "data.json", "content": '{"x": 1}', "content_type": "text"}
        ]
        result = build_multimodal_content_openai("Review", [], files)

        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert "[ATTACHED FILE: data.json (JSON)]" in result[0]["text"]


class TestAttachmentServiceValidation:
    """Tests for AttachmentService validation methods."""

    def test_is_enabled(self):
        """Test is_enabled checks settings."""
        # The mock_settings already has attachments_enabled = True
        assert attachment_service.is_enabled() is True

    def test_validate_image_valid(self):
        """Test validation of valid image."""
        image = {
            "data": "a" * 1000,  # Small image
            "media_type": "image/jpeg"
        }
        # Should not raise (mock_settings has is_allowed_image_type returning True)
        attachment_service.validate_image(image)

    def test_validate_image_invalid_type(self):
        """Test validation rejects invalid image type."""
        # Temporarily change mock behavior
        original = mock_settings.is_allowed_image_type.return_value
        mock_settings.is_allowed_image_type.return_value = False

        image = {
            "data": "x",
            "media_type": "image/bmp"
        }
        with pytest.raises(ValueError, match="Unsupported image type"):
            attachment_service.validate_image(image)

        # Restore
        mock_settings.is_allowed_image_type.return_value = original

    def test_validate_image_too_large(self):
        """Test validation rejects oversized image."""
        # Set a small limit
        original = mock_settings.attachment_max_size_bytes
        mock_settings.attachment_max_size_bytes = 1000  # 1KB limit

        # Create large base64 data (larger than 1KB when decoded)
        image = {
            "data": "a" * 2000,
            "media_type": "image/jpeg"
        }
        with pytest.raises(ValueError, match="exceeds maximum"):
            attachment_service.validate_image(image)

        # Restore
        mock_settings.attachment_max_size_bytes = original

    def test_validate_file_valid_text(self):
        """Test validation of valid text file."""
        file = {
            "filename": "notes.txt",
            "content": "Some text content",
            "content_type": "text"
        }
        # Should not raise
        attachment_service.validate_file(file)

    def test_validate_file_invalid_extension(self):
        """Test validation rejects invalid file extension."""
        # Temporarily change mock behavior
        original = mock_settings.is_allowed_text_file.return_value
        mock_settings.is_allowed_text_file.return_value = False
        original_pdf = mock_settings.attachment_pdf_enabled
        original_docx = mock_settings.attachment_docx_enabled
        mock_settings.attachment_pdf_enabled = False
        mock_settings.attachment_docx_enabled = False

        file = {
            "filename": "script.exe",
            "content": "binary",
            "content_type": "text"
        }
        with pytest.raises(ValueError, match="Unsupported file type"):
            attachment_service.validate_file(file)

        # Restore
        mock_settings.is_allowed_text_file.return_value = original
        mock_settings.attachment_pdf_enabled = original_pdf
        mock_settings.attachment_docx_enabled = original_docx

    def test_validate_file_too_large(self):
        """Test validation rejects oversized file."""
        original = mock_settings.attachment_max_size_bytes
        mock_settings.attachment_max_size_bytes = 100  # 100 byte limit

        file = {
            "filename": "large.txt",
            "content": "x" * 200,  # Exceeds limit
            "content_type": "text"
        }
        with pytest.raises(ValueError, match="exceeds maximum"):
            attachment_service.validate_file(file)

        # Restore
        mock_settings.attachment_max_size_bytes = original

    def test_validate_attachments_disabled(self):
        """Test validation fails when attachments are disabled."""
        original = mock_settings.attachments_enabled
        mock_settings.attachments_enabled = False

        attachments = {
            "images": [{"data": "x", "media_type": "image/jpeg"}],
            "files": []
        }
        with pytest.raises(ValueError, match="Attachments are disabled"):
            attachment_service.validate_attachments(attachments)

        # Restore
        mock_settings.attachments_enabled = original

    def test_validate_attachments_none_passes(self):
        """Test validation passes for None attachments."""
        # Should not raise
        attachment_service.validate_attachments(None)

    def test_validate_attachments_empty_passes(self):
        """Test validation passes for empty attachments."""
        # Should not raise
        attachment_service.validate_attachments({"images": [], "files": []})


class TestAttachmentServiceProcessForProvider:
    """Tests for AttachmentService.process_attachments_for_provider method."""

    def test_no_attachments_returns_text(self):
        """Test with no attachments returns plain text."""
        result = attachment_service.process_attachments_for_provider(
            "Hello world",
            None,
            "anthropic"
        )
        assert result == "Hello world"

    def test_empty_attachments_returns_text(self):
        """Test with empty attachments returns plain text."""
        result = attachment_service.process_attachments_for_provider(
            "Hello world",
            {"images": [], "files": []},
            "anthropic"
        )
        assert result == "Hello world"

    def test_anthropic_provider(self):
        """Test processing for Anthropic provider."""
        attachments = {
            "images": [{"data": "imgdata", "media_type": "image/jpeg"}],
            "files": [{"filename": "f.txt", "content": "text", "content_type": "text"}]
        }
        result = attachment_service.process_attachments_for_provider(
            "Message",
            attachments,
            "anthropic"
        )

        assert isinstance(result, list)
        # Should have image block and text block
        types = [block["type"] for block in result]
        assert "image" in types
        assert "text" in types

    def test_openai_provider(self):
        """Test processing for OpenAI provider."""
        attachments = {
            "images": [{"data": "imgdata", "media_type": "image/png"}],
            "files": []
        }
        result = attachment_service.process_attachments_for_provider(
            "Describe",
            attachments,
            "openai"
        )

        assert isinstance(result, list)
        types = [block["type"] for block in result]
        assert "image_url" in types
        assert "text" in types

    def test_google_provider_files_only(self):
        """Test Google provider gets file context appended to text."""
        attachments = {
            "images": [{"data": "imgdata", "media_type": "image/jpeg"}],
            "files": [{"filename": "code.py", "content": "print(1)", "content_type": "text"}]
        }
        result = attachment_service.process_attachments_for_provider(
            "Review",
            attachments,
            "google"
        )

        # Google returns string, not list
        assert isinstance(result, str)
        assert "[ATTACHED FILE: code.py (Python)]" in result
        assert "Review" in result
        # Image data should not be in result
        assert "imgdata" not in result

    def test_google_provider_images_skipped(self):
        """Test Google provider skips images (logs warning)."""
        attachments = {
            "images": [{"data": "imgdata", "media_type": "image/jpeg"}],
            "files": []
        }
        result = attachment_service.process_attachments_for_provider(
            "Message",
            attachments,
            "google"
        )

        # Should return just the text since images are skipped
        assert result == "Message"


class TestPDFExtraction:
    """Tests for PDF text extraction."""

    def test_pdf_extraction_disabled(self):
        """Test PDF extraction returns message when disabled."""
        original = mock_settings.attachment_pdf_enabled
        mock_settings.attachment_pdf_enabled = False

        result = extract_text_from_pdf(b"fake pdf data")
        assert result == "[PDF extraction is disabled]"

        mock_settings.attachment_pdf_enabled = original


class TestDOCXExtraction:
    """Tests for DOCX text extraction."""

    def test_docx_extraction_disabled(self):
        """Test DOCX extraction returns message when disabled."""
        original = mock_settings.attachment_docx_enabled
        mock_settings.attachment_docx_enabled = False

        result = extract_text_from_docx(b"fake docx data")
        assert result == "[DOCX extraction is disabled]"

        mock_settings.attachment_docx_enabled = original
