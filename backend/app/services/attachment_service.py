"""
Attachment Service

Handles processing of image and file attachments for multimodal conversations.
- Validates attachment types and sizes
- Extracts text from PDF and DOCX files
- Formats attachments for LLM APIs
"""

import base64
import io
import logging
from typing import List, Dict, Any, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)


def extract_text_from_pdf(pdf_data: bytes) -> str:
    """
    Extract text content from a PDF file.

    Requires PyPDF2 to be installed.
    Returns extracted text or error message.
    """
    if not settings.attachment_pdf_enabled:
        return "[PDF extraction is disabled]"

    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(io.BytesIO(pdf_data))
        text_parts = []

        for page_num, page in enumerate(reader.pages, 1):
            page_text = page.extract_text()
            if page_text:
                text_parts.append(f"--- Page {page_num} ---\n{page_text}")

        if not text_parts:
            return "[PDF contains no extractable text]"

        return "\n\n".join(text_parts)

    except ImportError:
        logger.warning("PyPDF2 not installed - PDF extraction unavailable")
        return "[PDF extraction unavailable - PyPDF2 not installed]"
    except Exception as e:
        logger.error(f"Error extracting text from PDF: {e}")
        return f"[Error extracting PDF text: {str(e)}]"


def extract_text_from_docx(docx_data: bytes) -> str:
    """
    Extract text content from a DOCX file.

    Requires python-docx to be installed.
    Returns extracted text or error message.
    """
    if not settings.attachment_docx_enabled:
        return "[DOCX extraction is disabled]"

    try:
        from docx import Document

        doc = Document(io.BytesIO(docx_data))
        text_parts = []

        for para in doc.paragraphs:
            if para.text.strip():
                text_parts.append(para.text)

        if not text_parts:
            return "[DOCX contains no extractable text]"

        return "\n\n".join(text_parts)

    except ImportError:
        logger.warning("python-docx not installed - DOCX extraction unavailable")
        return "[DOCX extraction unavailable - python-docx not installed]"
    except Exception as e:
        logger.error(f"Error extracting text from DOCX: {e}")
        return f"[Error extracting DOCX text: {str(e)}]"


def process_file_attachment(filename: str, content: str, content_type: str) -> Tuple[str, str]:
    """
    Process a file attachment and extract its text content.

    Args:
        filename: Name of the file
        content: Either raw text or base64-encoded binary data
        content_type: "text" or "base64"

    Returns:
        Tuple of (extracted_text, file_type_label)
    """
    filename_lower = filename.lower()

    # Already-extracted text
    if content_type == "text":
        # Determine label from extension
        if filename_lower.endswith('.md'):
            return content, "Markdown"
        elif filename_lower.endswith('.py'):
            return content, "Python"
        elif filename_lower.endswith('.js'):
            return content, "JavaScript"
        elif filename_lower.endswith('.ts'):
            return content, "TypeScript"
        elif filename_lower.endswith('.json'):
            return content, "JSON"
        elif filename_lower.endswith(('.yaml', '.yml')):
            return content, "YAML"
        elif filename_lower.endswith('.html'):
            return content, "HTML"
        elif filename_lower.endswith('.css'):
            return content, "CSS"
        elif filename_lower.endswith('.xml'):
            return content, "XML"
        elif filename_lower.endswith('.csv'):
            return content, "CSV"
        elif filename_lower.endswith('.log'):
            return content, "Log"
        else:
            return content, "Text"

    # Base64-encoded binary data
    try:
        binary_data = base64.b64decode(content)
    except Exception as e:
        logger.error(f"Failed to decode base64 content for {filename}: {e}")
        return f"[Error decoding file: {str(e)}]", "Error"

    # Extract text based on file extension
    if filename_lower.endswith('.pdf'):
        return extract_text_from_pdf(binary_data), "PDF"
    elif filename_lower.endswith('.docx'):
        return extract_text_from_docx(binary_data), "DOCX"
    else:
        # Try to decode as text
        try:
            return binary_data.decode('utf-8'), "Text"
        except UnicodeDecodeError:
            return "[Binary file - cannot extract text]", "Binary"


def build_file_context_block(files: List[Dict[str, Any]]) -> str:
    """
    Build a text block containing extracted file contents.

    Files are labeled with their filename and type to distinguish
    from the primary user message.
    """
    if not files:
        return ""

    blocks = []
    for file_info in files:
        filename = file_info.get("filename", "unknown")
        content = file_info.get("content", "")
        content_type = file_info.get("content_type", "text")

        extracted_text, file_type = process_file_attachment(filename, content, content_type)

        blocks.append(
            f"[ATTACHED FILE: {filename} ({file_type})]\n"
            f"```\n{extracted_text}\n```\n"
            f"[/ATTACHED FILE]"
        )

    return "\n\n".join(blocks)


def format_image_for_anthropic(image: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format an image attachment for Anthropic's API.

    Anthropic format:
    {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": "..."
        }
    }
    """
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": image["media_type"],
            "data": image["data"],
        }
    }


def format_image_for_openai(image: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format an image attachment for OpenAI's API.

    OpenAI format:
    {
        "type": "image_url",
        "image_url": {
            "url": "data:image/jpeg;base64,..."
        }
    }
    """
    data_uri = f"data:{image['media_type']};base64,{image['data']}"
    return {
        "type": "image_url",
        "image_url": {
            "url": data_uri
        }
    }


def build_multimodal_content_anthropic(
    text_content: str,
    images: List[Dict[str, Any]],
    files: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Build multimodal content array for Anthropic's API.

    Structure:
    1. Image blocks (if any)
    2. File context block (if any files)
    3. User's text message
    """
    content_blocks = []

    # Add images first
    for image in images:
        content_blocks.append(format_image_for_anthropic(image))

    # Build text content with file context
    file_context = build_file_context_block(files)
    full_text = ""

    if file_context:
        full_text = f"{file_context}\n\n"

    if text_content:
        full_text += text_content

    if full_text:
        content_blocks.append({
            "type": "text",
            "text": full_text,
        })

    return content_blocks


def build_multimodal_content_openai(
    text_content: str,
    images: List[Dict[str, Any]],
    files: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Build multimodal content array for OpenAI's API.

    Structure:
    1. Text block (including file context)
    2. Image blocks (if any)
    """
    content_blocks = []

    # Build text content with file context first
    file_context = build_file_context_block(files)
    full_text = ""

    if file_context:
        full_text = f"{file_context}\n\n"

    if text_content:
        full_text += text_content

    if full_text:
        content_blocks.append({
            "type": "text",
            "text": full_text,
        })

    # Add images after text
    for image in images:
        content_blocks.append(format_image_for_openai(image))

    return content_blocks


def has_attachments(attachments: Optional[Dict[str, Any]]) -> bool:
    """Check if there are any attachments to process."""
    if not attachments:
        return False

    images = attachments.get("images", [])
    files = attachments.get("files", [])

    return bool(images or files)


def get_attachment_summary(attachments: Optional[Dict[str, Any]]) -> str:
    """Get a human-readable summary of attachments for logging."""
    if not attachments:
        return "none"

    images = attachments.get("images", [])
    files = attachments.get("files", [])

    parts = []
    if images:
        parts.append(f"{len(images)} image(s)")
    if files:
        parts.append(f"{len(files)} file(s)")

    return ", ".join(parts) if parts else "none"


def build_persistable_content(
    user_message: Optional[str],
    attachments: Optional[Dict[str, Any]],
) -> str:
    """
    Build the persistable content combining user message with extracted file content.

    This is used to store the human message in the database and memory system.
    Text file contents are extracted and included, while images are NOT persisted
    (they remain ephemeral as base64 data is too large to store).

    Args:
        user_message: The user's text message (can be None/empty)
        attachments: Optional attachments dict with "images" and "files" lists

    Returns:
        Combined content string with file context prepended to user message
    """
    if not attachments:
        return user_message or ""

    files = attachments.get("files", [])

    # Convert Pydantic models to dicts if needed
    files_list = [
        f.model_dump() if hasattr(f, 'model_dump') else f
        for f in files
    ]

    if not files_list:
        return user_message or ""

    # Build file context block
    file_context = build_file_context_block(files_list)

    if not file_context:
        return user_message or ""

    # Combine file context with user message
    if user_message:
        return f"{file_context}\n\n{user_message}"
    else:
        return file_context


# Singleton-like module functions
class AttachmentService:
    """Service for processing message attachments."""

    def is_enabled(self) -> bool:
        """Check if attachments are enabled."""
        return settings.attachments_enabled

    def validate_image(self, image: Dict[str, Any]) -> None:
        """
        Validate an image attachment.

        Raises ValueError if validation fails.
        """
        media_type = image.get("media_type", "")
        data = image.get("data", "")

        # Check media type
        if not settings.is_allowed_image_type(media_type):
            allowed = settings.get_allowed_image_types()
            raise ValueError(f"Unsupported image type '{media_type}'. Allowed: {', '.join(allowed)}")

        # Check size (approximate from base64 length)
        # Base64 encoding increases size by ~33%
        estimated_size = len(data) * 3 // 4
        max_size = settings.attachment_max_size_bytes

        if estimated_size > max_size:
            raise ValueError(
                f"Image size ({estimated_size / (1024*1024):.1f}MB) exceeds "
                f"maximum ({max_size / (1024*1024):.1f}MB)"
            )

    def validate_file(self, file: Dict[str, Any]) -> None:
        """
        Validate a file attachment.

        Raises ValueError if validation fails.
        """
        filename = file.get("filename", "")
        content = file.get("content", "")
        content_type = file.get("content_type", "text")

        filename_lower = filename.lower()

        # Check if it's an allowed text file or supported binary format
        is_text = settings.is_allowed_text_file(filename)
        is_pdf = filename_lower.endswith('.pdf') and settings.attachment_pdf_enabled
        is_docx = filename_lower.endswith('.docx') and settings.attachment_docx_enabled

        if not (is_text or is_pdf or is_docx):
            raise ValueError(f"Unsupported file type: {filename}")

        # Check size
        if content_type == "text":
            estimated_size = len(content.encode('utf-8'))
        else:
            # Base64
            estimated_size = len(content) * 3 // 4

        max_size = settings.attachment_max_size_bytes
        if estimated_size > max_size:
            raise ValueError(
                f"File size ({estimated_size / (1024*1024):.1f}MB) exceeds "
                f"maximum ({max_size / (1024*1024):.1f}MB)"
            )

    def validate_attachments(self, attachments: Optional[Dict[str, Any]]) -> None:
        """
        Validate all attachments.

        Raises ValueError if any validation fails.
        """
        if not attachments:
            return

        if not self.is_enabled():
            if has_attachments(attachments):
                raise ValueError("Attachments are disabled")
            return

        for image in attachments.get("images", []):
            self.validate_image(image)

        for file in attachments.get("files", []):
            self.validate_file(file)

    def process_attachments_for_provider(
        self,
        text_content: str,
        attachments: Optional[Dict[str, Any]],
        provider: str,
    ) -> Any:
        """
        Process attachments and return content formatted for the specified provider.

        Args:
            text_content: The user's text message
            attachments: Optional attachments dict with "images" and "files" lists
            provider: "anthropic", "openai", or "google"

        Returns:
            For providers with attachment support: List of content blocks
            For providers without support: String with file content appended
        """
        if not attachments or not has_attachments(attachments):
            return text_content

        images = attachments.get("images", [])
        files = attachments.get("files", [])

        # Convert Pydantic models to dicts if needed
        images_list = [
            img.model_dump() if hasattr(img, 'model_dump') else img
            for img in images
        ]
        files_list = [
            f.model_dump() if hasattr(f, 'model_dump') else f
            for f in files
        ]

        logger.info(f"[ATTACHMENTS] Processing {len(images_list)} images, {len(files_list)} files for {provider}")

        if provider == "anthropic":
            return build_multimodal_content_anthropic(text_content, images_list, files_list)
        elif provider == "openai":
            return build_multimodal_content_openai(text_content, images_list, files_list)
        else:
            # Google/other providers - append file context to text, skip images
            if images_list:
                logger.warning(f"[ATTACHMENTS] {provider} does not support images - skipping {len(images_list)} images")

            file_context = build_file_context_block(files_list)
            if file_context:
                return f"{file_context}\n\n{text_content}"
            return text_content


# Singleton instance
attachment_service = AttachmentService()
