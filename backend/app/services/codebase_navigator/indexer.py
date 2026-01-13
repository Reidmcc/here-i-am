"""
CodebaseIndexer for the Codebase Navigator.

Responsible for preparing a codebase for navigator queries by:
- Traversing directory structure respecting .gitignore
- Filtering out non-code files
- Calculating token counts for intelligent chunking
- Generating file manifests with metadata
- Chunking large codebases into navigator-sized pieces
"""

import logging
import os
import fnmatch
import hashlib
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Set, Dict, Tuple

import tiktoken

from .models import (
    FileInfo,
    FileContent,
    CodebaseChunk,
    CodebaseIndex,
)
from .exceptions import IndexingError

logger = logging.getLogger(__name__)

# Language detection based on file extension
LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".sql": "sql",
    ".graphql": "graphql",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".xml": "xml",
    ".sh": "shell",
    ".bash": "shell",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
}


def detect_language(path: str) -> Optional[str]:
    """Detect programming language from file extension."""
    ext = Path(path).suffix.lower()
    return LANGUAGE_MAP.get(ext)


def is_binary_file(path: Path) -> bool:
    """Check if a file is binary by reading its first bytes."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
            # Check for null bytes (common in binary files)
            if b"\x00" in chunk:
                return True
            # Check if it's mostly printable text
            text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)))
            non_text = len(chunk.translate(None, text_chars))
            if len(chunk) > 0 and non_text / len(chunk) > 0.30:
                return True
            return False
    except Exception:
        return True  # Assume binary if we can't read it


class GitIgnoreParser:
    """Parse and match against .gitignore patterns."""

    def __init__(self, root_path: Path):
        self.root_path = root_path
        self.patterns: List[Tuple[str, bool]] = []  # (pattern, is_negation)
        self._load_gitignore()

    def _load_gitignore(self) -> None:
        """Load patterns from .gitignore file."""
        gitignore_path = self.root_path / ".gitignore"
        if gitignore_path.exists():
            try:
                with open(gitignore_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        is_negation = line.startswith("!")
                        if is_negation:
                            line = line[1:]
                        self.patterns.append((line, is_negation))
            except Exception as e:
                logger.warning(f"Error reading .gitignore: {e}")

    def should_ignore(self, path: Path) -> bool:
        """Check if a path should be ignored based on .gitignore patterns."""
        try:
            rel_path = path.relative_to(self.root_path)
            rel_str = str(rel_path).replace("\\", "/")

            # Also check against basename for patterns without /
            basename = path.name

            ignored = False
            for pattern, is_negation in self.patterns:
                # Handle directory patterns (ending with /)
                if pattern.endswith("/"):
                    dir_pattern = pattern[:-1]
                    if path.is_dir():
                        if fnmatch.fnmatch(rel_str, dir_pattern) or fnmatch.fnmatch(basename, dir_pattern):
                            ignored = not is_negation
                        # Also check if any parent matches
                        for parent in rel_path.parts[:-1]:
                            if fnmatch.fnmatch(parent, dir_pattern):
                                ignored = not is_negation
                                break
                else:
                    # Regular pattern
                    if "/" in pattern:
                        # Pattern with path separator - match against full path
                        if fnmatch.fnmatch(rel_str, pattern):
                            ignored = not is_negation
                    else:
                        # Pattern without path separator - match against any path component
                        if fnmatch.fnmatch(basename, pattern):
                            ignored = not is_negation
                        # Also check against full path
                        if fnmatch.fnmatch(rel_str, pattern) or fnmatch.fnmatch(rel_str, f"**/{pattern}"):
                            ignored = not is_negation

            return ignored
        except ValueError:
            return False


class CodebaseIndexer:
    """
    Indexes a codebase for efficient navigation queries.

    Responsibilities:
    - Traverse directory structure respecting .gitignore
    - Filter out non-code files (binaries, assets, etc.)
    - Calculate token counts for intelligent chunking
    - Generate file manifests with metadata
    - Chunk large codebases into navigator-sized pieces
    """

    def __init__(
        self,
        root_path: Path,
        max_tokens_per_chunk: int = 200_000,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ):
        """
        Initialize the indexer.

        Args:
            root_path: Root directory of the codebase
            max_tokens_per_chunk: Maximum tokens per chunk (default 200k for Devstral's 256k context)
            include_patterns: Glob patterns for files to include (e.g., ["*.py", "*.js"])
            exclude_patterns: Glob patterns for files/directories to exclude
        """
        self.root_path = Path(root_path).resolve()
        self.max_tokens_per_chunk = max_tokens_per_chunk
        self.include_patterns = include_patterns or [
            "*.py", "*.js", "*.ts", "*.jsx", "*.tsx",
            "*.java", "*.go", "*.rs", "*.c", "*.cpp", "*.h",
            "*.json", "*.yaml", "*.yml", "*.toml", "*.md",
            "*.sql", "*.graphql", "*.html", "*.css", "*.scss",
        ]
        self.exclude_patterns = exclude_patterns or [
            "node_modules/", "venv/", ".venv/", "__pycache__/", ".git/",
            "dist/", "build/", ".next/", "*.min.js", "*.map", "*.lock",
            "*.bundle.js", ".tox/", ".mypy_cache/", ".pytest_cache/",
            "*.egg-info/", ".eggs/", "htmlcov/", ".coverage",
        ]

        # Initialize tokenizer (GPT-4 encoding is a good approximation)
        try:
            self._tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._tokenizer = None
            logger.warning("tiktoken not available, using character-based estimation")

        # Initialize gitignore parser
        self._gitignore = GitIgnoreParser(self.root_path)

        # Cached index data
        self._files: List[FileInfo] = []
        self._file_contents: Dict[str, FileContent] = {}
        self._chunks: List[CodebaseChunk] = []
        self._indexed = False

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        if self._tokenizer:
            return len(self._tokenizer.encode(text, disallowed_special=()))
        # Fallback: approximate 4 characters per token
        return len(text) // 4

    def _should_include(self, path: Path) -> bool:
        """Check if a file should be included based on patterns."""
        rel_path = str(path.relative_to(self.root_path)).replace("\\", "/")
        basename = path.name

        # Check exclude patterns first
        for pattern in self.exclude_patterns:
            if pattern.endswith("/"):
                # Directory pattern - check if any part of the path matches
                dir_pattern = pattern[:-1]
                for part in rel_path.split("/"):
                    if fnmatch.fnmatch(part, dir_pattern):
                        return False
            else:
                if fnmatch.fnmatch(basename, pattern) or fnmatch.fnmatch(rel_path, pattern):
                    return False

        # Check include patterns
        for pattern in self.include_patterns:
            if fnmatch.fnmatch(basename, pattern):
                return True

        return False

    def _scan_files(self) -> List[FileInfo]:
        """Scan the codebase and collect file information."""
        files = []

        for dirpath, dirnames, filenames in os.walk(self.root_path):
            current_dir = Path(dirpath)

            # Filter out excluded directories
            dirnames[:] = [
                d for d in dirnames
                if not self._gitignore.should_ignore(current_dir / d)
                and not any(fnmatch.fnmatch(d, p.rstrip("/")) for p in self.exclude_patterns if p.endswith("/"))
            ]

            for filename in filenames:
                file_path = current_dir / filename

                # Skip if gitignored
                if self._gitignore.should_ignore(file_path):
                    continue

                # Skip if doesn't match include patterns
                if not self._should_include(file_path):
                    continue

                # Skip binary files
                if is_binary_file(file_path):
                    continue

                try:
                    stat = file_path.stat()

                    # Skip very large files (>1MB)
                    if stat.st_size > 1024 * 1024:
                        logger.debug(f"Skipping large file: {file_path}")
                        continue

                    # Read and count tokens
                    try:
                        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                    except Exception as e:
                        logger.debug(f"Could not read file {file_path}: {e}")
                        continue

                    token_count = self._count_tokens(content)

                    files.append(FileInfo(
                        path=str(file_path),
                        relative_path=str(file_path.relative_to(self.root_path)),
                        size_bytes=stat.st_size,
                        token_count=token_count,
                        language=detect_language(str(file_path)),
                        last_modified=datetime.fromtimestamp(stat.st_mtime),
                    ))

                    # Store content for later chunking
                    self._file_contents[str(file_path)] = FileContent(
                        path=str(file_path),
                        relative_path=str(file_path.relative_to(self.root_path)),
                        content=content,
                        line_count=content.count("\n") + 1,
                        token_count=token_count,
                        language=detect_language(str(file_path)),
                    )

                except Exception as e:
                    logger.warning(f"Error processing file {file_path}: {e}")
                    continue

        return files

    def _create_chunks(self, files: List[FileInfo]) -> List[CodebaseChunk]:
        """Create chunks from the file list."""
        if not files:
            return []

        # Sort files by importance (core code first, tests/docs later)
        def file_priority(f: FileInfo) -> int:
            rel = f.relative_path.lower()
            # Highest priority: main application code
            if any(x in rel for x in ["src/", "app/", "lib/"]):
                return 0
            # Medium priority: root level files
            if "/" not in rel:
                return 1
            # Lower priority: tests, docs, examples
            if any(x in rel for x in ["test", "spec", "doc", "example"]):
                return 3
            return 2

        sorted_files = sorted(files, key=file_priority)

        chunks = []
        current_chunk_files: List[FileContent] = []
        current_chunk_tokens = 0
        chunk_id = 0

        # Reserve tokens for:
        # - Manifest (~2000 tokens)
        # - System prompt (~1000 tokens)
        # - Query prompt wrapper (~500 tokens)
        # - Safety margin
        overhead_reserve = 5000

        for file_info in sorted_files:
            content = self._file_contents.get(file_info.path)
            if not content:
                continue

            # Calculate FORMATTED token count, not raw tokens
            # format_chunk_for_query adds "[line N] " prefix to every line (~4 tokens per line)
            # Plus file header (~20 tokens) and footer (~10 tokens)
            line_count = content.line_count
            line_number_overhead = line_count * 4  # ~4 tokens for "[line N] " per line
            file_header_overhead = 30  # "=== FILE: path ===" + "=== END FILE ==="
            file_formatted_tokens = content.token_count + line_number_overhead + file_header_overhead

            if current_chunk_tokens + file_formatted_tokens > self.max_tokens_per_chunk - overhead_reserve:
                # Finish current chunk
                if current_chunk_files:
                    chunks.append(CodebaseChunk(
                        chunk_id=chunk_id,
                        total_chunks=0,  # Will be updated later
                        files=current_chunk_files,
                        manifest="",  # Will be updated later
                        token_count=current_chunk_tokens,
                    ))
                    chunk_id += 1
                    current_chunk_files = []
                    current_chunk_tokens = 0

            # Add file to current chunk
            current_chunk_files.append(content)
            current_chunk_tokens += file_formatted_tokens

        # Don't forget the last chunk
        if current_chunk_files:
            chunks.append(CodebaseChunk(
                chunk_id=chunk_id,
                total_chunks=0,
                files=current_chunk_files,
                manifest="",
                token_count=current_chunk_tokens,
            ))

        # Update total_chunks and manifests
        total_chunks = len(chunks)
        for i, chunk in enumerate(chunks):
            chunk.total_chunks = total_chunks

            # Create manifest describing other chunks
            if total_chunks > 1:
                other_chunks_info = []
                for j, other_chunk in enumerate(chunks):
                    if i != j:
                        chunk_files = [f.relative_path for f in other_chunk.files[:5]]
                        if len(other_chunk.files) > 5:
                            chunk_files.append(f"...and {len(other_chunk.files) - 5} more files")
                        other_chunks_info.append(f"Chunk {j}: {', '.join(chunk_files)}")
                chunk.manifest = "Other chunks contain:\n" + "\n".join(other_chunks_info)

        self._chunks = chunks
        return chunks

    def index(self) -> CodebaseIndex:
        """
        Scan the codebase and build an index.

        Returns a CodebaseIndex containing:
        - File tree structure
        - Token counts per file
        - Language detection per file
        - Chunking strategy if codebase exceeds single-context size
        """
        if not self.root_path.exists():
            raise IndexingError(f"Path does not exist: {self.root_path}")

        if not self.root_path.is_dir():
            raise IndexingError(f"Path is not a directory: {self.root_path}")

        logger.info(f"Indexing codebase at {self.root_path}")

        # Scan files
        self._files = self._scan_files()
        total_tokens = sum(f.token_count for f in self._files)

        logger.info(f"Found {len(self._files)} files, {total_tokens:,} total tokens")

        # Create chunks
        chunks = self._create_chunks(self._files)

        logger.info(f"Created {len(chunks)} chunks")

        self._indexed = True

        return CodebaseIndex(
            root_path=str(self.root_path),
            total_files=len(self._files),
            total_tokens=total_tokens,
            files=self._files,
            chunks=list(range(len(chunks))),
            total_chunks=len(chunks),
        )

    def get_chunk(self, chunk_id: int) -> CodebaseChunk:
        """
        Retrieve a specific chunk of the codebase for navigator query.

        Each chunk contains:
        - Concatenated file contents with clear delimiters
        - File path markers for each file
        - Metadata about what's included vs excluded
        """
        if not self._indexed:
            raise IndexingError("Codebase has not been indexed yet. Call index() first.")

        if chunk_id < 0 or chunk_id >= len(self._chunks):
            raise IndexingError(f"Invalid chunk_id {chunk_id}. Valid range: 0-{len(self._chunks) - 1}")

        return self._chunks[chunk_id]

    def get_all_chunks(self) -> List[CodebaseChunk]:
        """Get all chunks (for small codebases or multi-chunk queries)."""
        if not self._indexed:
            raise IndexingError("Codebase has not been indexed yet. Call index() first.")
        return self._chunks

    def format_chunk_for_query(self, chunk: CodebaseChunk) -> str:
        """Format a chunk's content for sending to the navigator."""
        lines = []

        # Add manifest for multi-chunk context
        if chunk.total_chunks > 1:
            lines.append(f"[CHUNK {chunk.chunk_id + 1} OF {chunk.total_chunks}]")
            lines.append("")
            lines.append(chunk.manifest)
            lines.append("")
            lines.append("=" * 60)
            lines.append("")

        # Add each file's content
        for file_content in chunk.files:
            lines.append(f"=== FILE: {file_content.relative_path} ===")

            # Add line numbers to each line
            content_lines = file_content.content.split("\n")
            for i, line in enumerate(content_lines, 1):
                lines.append(f"[line {i}] {line}")

            lines.append("=== END FILE ===")
            lines.append("")

        return "\n".join(lines)

    def get_codebase_hash(self) -> str:
        """Generate a hash of the codebase for cache invalidation."""
        if not self._indexed:
            raise IndexingError("Codebase has not been indexed yet. Call index() first.")

        # Hash based on file paths and modification times
        hash_input = ""
        for f in sorted(self._files, key=lambda x: x.path):
            hash_input += f"{f.relative_path}:{f.size_bytes}:{f.last_modified.isoformat() if f.last_modified else ''}\n"

        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    def get_single_chunk_if_small(self) -> Optional[CodebaseChunk]:
        """Return the single chunk if codebase fits in one, otherwise None."""
        if not self._indexed:
            raise IndexingError("Codebase has not been indexed yet. Call index() first.")

        if len(self._chunks) == 1:
            return self._chunks[0]
        return None
