"""
Data models for the Codebase Navigator.

These models define the structure of requests, responses, and internal data
used by the navigator components.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime


class QueryType(str, Enum):
    """Types of queries the navigator can handle."""
    RELEVANCE = "relevance"       # Find files relevant to a task
    STRUCTURE = "structure"       # Understand architecture/organization
    DEPENDENCIES = "dependencies" # Trace imports and dependencies
    ENTRY_POINTS = "entry_points" # Find where to start modifications
    IMPACT = "impact"            # Assess what might be affected by changes


@dataclass
class FileInfo:
    """Metadata about a file in the codebase."""
    path: str
    relative_path: str
    size_bytes: int
    token_count: int
    language: Optional[str] = None
    last_modified: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "token_count": self.token_count,
            "language": self.language,
            "last_modified": self.last_modified.isoformat() if self.last_modified else None,
        }


@dataclass
class FileContent:
    """A file's content with metadata for inclusion in a chunk."""
    path: str
    relative_path: str
    content: str
    line_count: int
    token_count: int
    language: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "relative_path": self.relative_path,
            "line_count": self.line_count,
            "token_count": self.token_count,
            "language": self.language,
        }


@dataclass
class CodebaseChunk:
    """A chunk of a codebase that fits within a single context window."""
    chunk_id: int
    total_chunks: int
    files: List[FileContent]
    manifest: str  # Brief description of files in other chunks
    token_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "total_chunks": self.total_chunks,
            "files": [f.to_dict() for f in self.files],
            "manifest": self.manifest,
            "token_count": self.token_count,
        }


@dataclass
class CodebaseIndex:
    """Index of a codebase for navigation queries."""
    root_path: str
    total_files: int
    total_tokens: int
    files: List[FileInfo]
    chunks: List[int]  # List of chunk IDs
    total_chunks: int
    indexed_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_path": self.root_path,
            "total_files": self.total_files,
            "total_tokens": self.total_tokens,
            "files": [f.to_dict() for f in self.files],
            "chunks": self.chunks,
            "total_chunks": self.total_chunks,
            "indexed_at": self.indexed_at.isoformat(),
        }


@dataclass
class CodeSection:
    """A specific section of code within a file."""
    start_line: int
    end_line: int
    name: Optional[str] = None  # Function/class name if applicable
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "name": self.name,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CodeSection":
        return cls(
            start_line=data.get("start_line", 0),
            end_line=data.get("end_line", 0),
            name=data.get("name"),
            description=data.get("description", ""),
        )


@dataclass
class RelevantFile:
    """A file identified as relevant to a task."""
    path: str
    relevance: Literal["high", "medium", "low"]
    reason: str
    specific_sections: Optional[List[CodeSection]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "relevance": self.relevance,
            "reason": self.reason,
            "specific_sections": [s.to_dict() for s in self.specific_sections] if self.specific_sections else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RelevantFile":
        sections = data.get("specific_sections")
        return cls(
            path=data.get("path", ""),
            relevance=data.get("relevance", "low"),
            reason=data.get("reason", ""),
            specific_sections=[CodeSection.from_dict(s) for s in sections] if sections else None,
        )


@dataclass
class NavigatorResponse:
    """Structured output from navigator queries."""
    relevant_files: List[RelevantFile]
    architecture_notes: Optional[str] = None
    suggested_approach: Optional[str] = None
    dependencies_to_consider: Optional[List[str]] = None
    confidence: float = 0.0
    tokens_used: int = 0
    chunks_queried: int = 0
    query_type: QueryType = QueryType.RELEVANCE
    cached: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relevant_files": [f.to_dict() for f in self.relevant_files],
            "architecture_notes": self.architecture_notes,
            "suggested_approach": self.suggested_approach,
            "dependencies_to_consider": self.dependencies_to_consider,
            "confidence": self.confidence,
            "tokens_used": self.tokens_used,
            "chunks_queried": self.chunks_queried,
            "query_type": self.query_type.value,
            "cached": self.cached,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NavigatorResponse":
        return cls(
            relevant_files=[RelevantFile.from_dict(f) for f in data.get("relevant_files", [])],
            architecture_notes=data.get("architecture_notes"),
            suggested_approach=data.get("suggested_approach"),
            dependencies_to_consider=data.get("dependencies_to_consider"),
            confidence=data.get("confidence", 0.0),
            tokens_used=data.get("tokens_used", 0),
            chunks_queried=data.get("chunks_queried", 0),
            query_type=QueryType(data.get("query_type", "relevance")),
            cached=data.get("cached", False),
        )

    def format_for_tool(self) -> str:
        """Format the response as a string suitable for tool output."""
        lines = []

        # Header
        lines.append(f"=== Codebase Navigator Results ===")
        lines.append(f"Query type: {self.query_type.value}")
        lines.append(f"Confidence: {self.confidence:.0%}")
        if self.cached:
            lines.append("(Cached result)")
        lines.append("")

        # Architecture notes
        if self.architecture_notes:
            lines.append("## Architecture Overview")
            lines.append(self.architecture_notes)
            lines.append("")

        # Relevant files
        if self.relevant_files:
            lines.append("## Relevant Files")
            lines.append("")

            # Group by relevance
            high = [f for f in self.relevant_files if f.relevance == "high"]
            medium = [f for f in self.relevant_files if f.relevance == "medium"]
            low = [f for f in self.relevant_files if f.relevance == "low"]

            for relevance_group, label in [(high, "HIGH"), (medium, "MEDIUM"), (low, "LOW")]:
                if relevance_group:
                    lines.append(f"### {label} Relevance")
                    for file in relevance_group:
                        lines.append(f"- **{file.path}**")
                        lines.append(f"  Reason: {file.reason}")
                        if file.specific_sections:
                            for section in file.specific_sections:
                                section_name = f" ({section.name})" if section.name else ""
                                lines.append(f"  - Lines {section.start_line}-{section.end_line}{section_name}: {section.description}")
                    lines.append("")

        # Suggested approach
        if self.suggested_approach:
            lines.append("## Suggested Approach")
            lines.append(self.suggested_approach)
            lines.append("")

        # Dependencies
        if self.dependencies_to_consider:
            lines.append("## Dependencies to Consider")
            for dep in self.dependencies_to_consider:
                lines.append(f"- {dep}")
            lines.append("")

        # Stats
        lines.append(f"---")
        lines.append(f"Tokens used: {self.tokens_used:,}")
        lines.append(f"Chunks queried: {self.chunks_queried}")

        return "\n".join(lines)


@dataclass
class NavigatorQuery:
    """A query to send to the navigator."""
    task: str
    query_type: QueryType = QueryType.RELEVANCE
    additional_context: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "query_type": self.query_type.value,
            "additional_context": self.additional_context,
        }
