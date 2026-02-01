"""
Moltbook tools for AI entities.

Provides social networking operations that AI entities can use during
conversations to interact with Moltbook, a social network for AI agents.

Available tools:
- moltbook_get_feed: Retrieve posts from personalized or global feed
- moltbook_get_submolt_feed: Get posts from a specific community
- moltbook_get_post: Fetch a single post with comments
- moltbook_create_post: Publish new content
- moltbook_create_comment: Post or reply to comments
- moltbook_vote: Upvote/downvote posts or comments
- moltbook_search: Semantic search across content
- moltbook_get_profile: Retrieve agent profiles
- moltbook_list_submolts: Browse available communities
- moltbook_get_submolt: View community details
- moltbook_follow: Follow/unfollow agents
- moltbook_subscribe: Subscribe/unsubscribe to communities
"""

import logging
from typing import Optional

from app.config import settings
from app.services.moltbook_service import moltbook_service
from app.services.tool_service import ToolCategory, ToolService

logger = logging.getLogger(__name__)


# =============================================================================
# Tool Executors
# =============================================================================

async def moltbook_get_feed(
    feed_type: str = "global",
    sort: str = "hot",
    limit: int = 25,
) -> str:
    """Get posts from personalized or global feed."""
    if not settings.moltbook_enabled:
        return "Error: Moltbook integration is not enabled"

    if not settings.moltbook_api_key:
        return "Error: Moltbook API key is not configured"

    if feed_type not in ("personal", "global"):
        return f"Error: Invalid feed_type '{feed_type}'. Must be 'personal' or 'global'."

    if sort not in ("hot", "new", "top", "rising"):
        return f"Error: Invalid sort '{sort}'. Must be 'hot', 'new', 'top', or 'rising'."

    success, result = await moltbook_service.get_feed(
        feed_type=feed_type,
        sort=sort,
        limit=limit,
    )

    return result


async def moltbook_get_submolt_feed(
    submolt: str,
    sort: str = "hot",
    limit: int = 25,
) -> str:
    """Get posts from a specific submolt (community)."""
    if not settings.moltbook_enabled:
        return "Error: Moltbook integration is not enabled"

    if not settings.moltbook_api_key:
        return "Error: Moltbook API key is not configured"

    if not submolt:
        return "Error: submolt parameter is required"

    if sort not in ("hot", "new", "top", "rising"):
        return f"Error: Invalid sort '{sort}'. Must be 'hot', 'new', 'top', or 'rising'."

    success, result = await moltbook_service.get_submolt_feed(
        submolt=submolt,
        sort=sort,
        limit=limit,
    )

    return result


async def moltbook_get_post(
    post_id: str,
    comment_sort: str = "top",
) -> str:
    """Get a single post with its comments."""
    if not settings.moltbook_enabled:
        return "Error: Moltbook integration is not enabled"

    if not settings.moltbook_api_key:
        return "Error: Moltbook API key is not configured"

    if not post_id:
        return "Error: post_id parameter is required"

    if comment_sort not in ("top", "new", "controversial"):
        return f"Error: Invalid comment_sort '{comment_sort}'. Must be 'top', 'new', or 'controversial'."

    success, result = await moltbook_service.get_post(
        post_id=post_id,
        comment_sort=comment_sort,
    )

    return result


async def moltbook_create_post(
    submolt: str,
    title: str,
    content: Optional[str] = None,
    url: Optional[str] = None,
) -> str:
    """Create a new post in a submolt."""
    if not settings.moltbook_enabled:
        return "Error: Moltbook integration is not enabled"

    if not settings.moltbook_api_key:
        return "Error: Moltbook API key is not configured"

    if not submolt:
        return "Error: submolt parameter is required"

    if not title:
        return "Error: title parameter is required"

    if not content and not url:
        return "Error: Either content (for text posts) or url (for link posts) is required"

    success, result = await moltbook_service.create_post(
        submolt=submolt,
        title=title,
        content=content,
        url=url,
    )

    return result


async def moltbook_create_comment(
    post_id: str,
    content: str,
    parent_id: Optional[str] = None,
) -> str:
    """Add a comment to a post or reply to an existing comment."""
    if not settings.moltbook_enabled:
        return "Error: Moltbook integration is not enabled"

    if not settings.moltbook_api_key:
        return "Error: Moltbook API key is not configured"

    if not post_id:
        return "Error: post_id parameter is required"

    if not content:
        return "Error: content parameter is required"

    success, result = await moltbook_service.create_comment(
        post_id=post_id,
        content=content,
        parent_id=parent_id,
    )

    return result


async def moltbook_vote(
    target_type: str,
    target_id: str,
    vote: str,
) -> str:
    """Upvote or downvote a post or comment."""
    if not settings.moltbook_enabled:
        return "Error: Moltbook integration is not enabled"

    if not settings.moltbook_api_key:
        return "Error: Moltbook API key is not configured"

    if target_type not in ("post", "comment"):
        return f"Error: Invalid target_type '{target_type}'. Must be 'post' or 'comment'."

    if not target_id:
        return "Error: target_id parameter is required"

    if vote not in ("up", "down"):
        return f"Error: Invalid vote '{vote}'. Must be 'up' or 'down'."

    success, result = await moltbook_service.vote(
        target_type=target_type,
        target_id=target_id,
        vote=vote,
    )

    return result


async def moltbook_search(
    query: str,
    search_type: str = "all",
    limit: int = 20,
) -> str:
    """Search across Moltbook content."""
    if not settings.moltbook_enabled:
        return "Error: Moltbook integration is not enabled"

    if not settings.moltbook_api_key:
        return "Error: Moltbook API key is not configured"

    if not query:
        return "Error: query parameter is required"

    if len(query) > 500:
        return "Error: query must be 500 characters or less"

    if search_type not in ("posts", "comments", "all"):
        return f"Error: Invalid search_type '{search_type}'. Must be 'posts', 'comments', or 'all'."

    success, result = await moltbook_service.search(
        query=query,
        search_type=search_type,
        limit=limit,
    )

    return result


async def moltbook_get_profile(
    name: Optional[str] = None,
) -> str:
    """Get agent profile information."""
    if not settings.moltbook_enabled:
        return "Error: Moltbook integration is not enabled"

    if not settings.moltbook_api_key:
        return "Error: Moltbook API key is not configured"

    success, result = await moltbook_service.get_profile(name=name)

    return result


async def moltbook_list_submolts() -> str:
    """List all available submolts (communities)."""
    if not settings.moltbook_enabled:
        return "Error: Moltbook integration is not enabled"

    if not settings.moltbook_api_key:
        return "Error: Moltbook API key is not configured"

    success, result = await moltbook_service.list_submolts()

    return result


async def moltbook_get_submolt(
    name: str,
) -> str:
    """Get detailed information about a specific submolt."""
    if not settings.moltbook_enabled:
        return "Error: Moltbook integration is not enabled"

    if not settings.moltbook_api_key:
        return "Error: Moltbook API key is not configured"

    if not name:
        return "Error: name parameter is required"

    success, result = await moltbook_service.get_submolt(name=name)

    return result


async def moltbook_follow(
    agent_name: str,
    action: str,
) -> str:
    """Follow or unfollow another agent."""
    if not settings.moltbook_enabled:
        return "Error: Moltbook integration is not enabled"

    if not settings.moltbook_api_key:
        return "Error: Moltbook API key is not configured"

    if not agent_name:
        return "Error: agent_name parameter is required"

    if action not in ("follow", "unfollow"):
        return f"Error: Invalid action '{action}'. Must be 'follow' or 'unfollow'."

    success, result = await moltbook_service.follow(
        agent_name=agent_name,
        action=action,
    )

    return result


async def moltbook_subscribe(
    submolt: str,
    action: str,
) -> str:
    """Subscribe or unsubscribe from a submolt."""
    if not settings.moltbook_enabled:
        return "Error: Moltbook integration is not enabled"

    if not settings.moltbook_api_key:
        return "Error: Moltbook API key is not configured"

    if not submolt:
        return "Error: submolt parameter is required"

    if action not in ("subscribe", "unsubscribe"):
        return f"Error: Invalid action '{action}'. Must be 'subscribe' or 'unsubscribe'."

    success, result = await moltbook_service.subscribe(
        submolt=submolt,
        action=action,
    )

    return result


# =============================================================================
# Tool Registration
# =============================================================================

def register_moltbook_tools(tool_service: ToolService) -> None:
    """Register all Moltbook tools with the tool service."""

    # Only register if Moltbook is enabled
    enabled = settings.moltbook_enabled and bool(settings.moltbook_api_key)

    # moltbook_get_feed
    tool_service.register_tool(
        name="moltbook_get_feed",
        description="Retrieve posts from the Moltbook feed. Use feed_type='personal' for personalized recommendations or 'global' for all posts. Sort by 'hot' (default), 'new', 'top', or 'rising'.",
        input_schema={
            "type": "object",
            "properties": {
                "feed_type": {
                    "type": "string",
                    "enum": ["personal", "global"],
                    "description": "Feed type: 'personal' for personalized or 'global' for all posts",
                    "default": "global",
                },
                "sort": {
                    "type": "string",
                    "enum": ["hot", "new", "top", "rising"],
                    "description": "Sort order for posts",
                    "default": "hot",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Number of posts to retrieve (1-50)",
                    "default": 25,
                },
            },
            "required": [],
        },
        executor=moltbook_get_feed,
        category=ToolCategory.MOLTBOOK,
        enabled=enabled,
    )

    # moltbook_get_submolt_feed
    tool_service.register_tool(
        name="moltbook_get_submolt_feed",
        description="Get posts from a specific submolt (community). Submolts are like subreddits - themed communities for discussion.",
        input_schema={
            "type": "object",
            "properties": {
                "submolt": {
                    "type": "string",
                    "description": "Submolt name (community name without 'm/' prefix)",
                },
                "sort": {
                    "type": "string",
                    "enum": ["hot", "new", "top", "rising"],
                    "description": "Sort order for posts",
                    "default": "hot",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Number of posts to retrieve (1-50)",
                    "default": 25,
                },
            },
            "required": ["submolt"],
        },
        executor=moltbook_get_submolt_feed,
        category=ToolCategory.MOLTBOOK,
        enabled=enabled,
    )

    # moltbook_get_post
    tool_service.register_tool(
        name="moltbook_get_post",
        description="Retrieve a single post with its comments. Use this to read full post content and see the discussion.",
        input_schema={
            "type": "object",
            "properties": {
                "post_id": {
                    "type": "string",
                    "description": "The unique identifier of the post",
                },
                "comment_sort": {
                    "type": "string",
                    "enum": ["top", "new", "controversial"],
                    "description": "How to sort comments",
                    "default": "top",
                },
            },
            "required": ["post_id"],
        },
        executor=moltbook_get_post,
        category=ToolCategory.MOLTBOOK,
        enabled=enabled,
    )

    # moltbook_create_post
    tool_service.register_tool(
        name="moltbook_create_post",
        description="Create a new post in a submolt. Provide either content (for text posts) or url (for link posts). Rate limited: 1 post per 30 minutes.",
        input_schema={
            "type": "object",
            "properties": {
                "submolt": {
                    "type": "string",
                    "description": "Target submolt name (community to post in)",
                },
                "title": {
                    "type": "string",
                    "description": "Post title",
                },
                "content": {
                    "type": "string",
                    "description": "Text content for text posts (optional if url provided)",
                },
                "url": {
                    "type": "string",
                    "description": "URL for link posts (optional if content provided)",
                },
            },
            "required": ["submolt", "title"],
        },
        executor=moltbook_create_post,
        category=ToolCategory.MOLTBOOK,
        enabled=enabled,
    )

    # moltbook_create_comment
    tool_service.register_tool(
        name="moltbook_create_comment",
        description="Add a comment to a post or reply to an existing comment. Rate limited: 1 comment per 20 seconds, 50 comments per day.",
        input_schema={
            "type": "object",
            "properties": {
                "post_id": {
                    "type": "string",
                    "description": "The post to comment on",
                },
                "content": {
                    "type": "string",
                    "description": "Comment text content",
                },
                "parent_id": {
                    "type": "string",
                    "description": "Parent comment ID for nested replies (optional)",
                },
            },
            "required": ["post_id", "content"],
        },
        executor=moltbook_create_comment,
        category=ToolCategory.MOLTBOOK,
        enabled=enabled,
    )

    # moltbook_vote
    tool_service.register_tool(
        name="moltbook_vote",
        description="Upvote or downvote a post or comment to express your opinion on the content.",
        input_schema={
            "type": "object",
            "properties": {
                "target_type": {
                    "type": "string",
                    "enum": ["post", "comment"],
                    "description": "Type of content to vote on",
                },
                "target_id": {
                    "type": "string",
                    "description": "ID of the post or comment",
                },
                "vote": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Vote direction",
                },
            },
            "required": ["target_type", "target_id", "vote"],
        },
        executor=moltbook_vote,
        category=ToolCategory.MOLTBOOK,
        enabled=enabled,
    )

    # moltbook_search
    tool_service.register_tool(
        name="moltbook_search",
        description="Search across Moltbook content using semantic search. Find posts and comments matching your query.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (max 500 characters)",
                    "maxLength": 500,
                },
                "search_type": {
                    "type": "string",
                    "enum": ["posts", "comments", "all"],
                    "description": "What to search: posts only, comments only, or all content",
                    "default": "all",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum results to return (1-50)",
                    "default": 20,
                },
            },
            "required": ["query"],
        },
        executor=moltbook_search,
        category=ToolCategory.MOLTBOOK,
        enabled=enabled,
    )

    # moltbook_get_profile
    tool_service.register_tool(
        name="moltbook_get_profile",
        description="Get an agent's profile information. Omit 'name' to get your own profile.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Agent name to look up (optional, returns your profile if omitted)",
                },
            },
            "required": [],
        },
        executor=moltbook_get_profile,
        category=ToolCategory.MOLTBOOK,
        enabled=enabled,
    )

    # moltbook_list_submolts
    tool_service.register_tool(
        name="moltbook_list_submolts",
        description="List all available submolts (communities) on Moltbook. Browse to find interesting communities to join.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        executor=moltbook_list_submolts,
        category=ToolCategory.MOLTBOOK,
        enabled=enabled,
    )

    # moltbook_get_submolt
    tool_service.register_tool(
        name="moltbook_get_submolt",
        description="Get detailed information about a specific submolt (community), including description, rules, and subscriber count.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Submolt name to look up",
                },
            },
            "required": ["name"],
        },
        executor=moltbook_get_submolt,
        category=ToolCategory.MOLTBOOK,
        enabled=enabled,
    )

    # moltbook_follow
    tool_service.register_tool(
        name="moltbook_follow",
        description="Follow or unfollow another agent on Moltbook. Following an agent shows their posts in your personal feed.",
        input_schema={
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Name of the agent to follow/unfollow",
                },
                "action": {
                    "type": "string",
                    "enum": ["follow", "unfollow"],
                    "description": "Action to perform",
                },
            },
            "required": ["agent_name", "action"],
        },
        executor=moltbook_follow,
        category=ToolCategory.MOLTBOOK,
        enabled=enabled,
    )

    # moltbook_subscribe
    tool_service.register_tool(
        name="moltbook_subscribe",
        description="Subscribe or unsubscribe from a submolt (community). Subscribed submolts appear in your personal feed.",
        input_schema={
            "type": "object",
            "properties": {
                "submolt": {
                    "type": "string",
                    "description": "Submolt name to subscribe/unsubscribe",
                },
                "action": {
                    "type": "string",
                    "enum": ["subscribe", "unsubscribe"],
                    "description": "Action to perform",
                },
            },
            "required": ["submolt", "action"],
        },
        executor=moltbook_subscribe,
        category=ToolCategory.MOLTBOOK,
        enabled=enabled,
    )

    status = "enabled" if enabled else "disabled (MOLTBOOK_ENABLED=false or no API key)"
    logger.info(f"Registered 12 Moltbook tools ({status})")
