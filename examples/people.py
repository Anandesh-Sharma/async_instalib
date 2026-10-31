"""Read a person through an existing session; additional reads require explicit flags.

Run: uv run python examples/people.py --session /private/account.json --username example
This example does not change relationships, play stories, or fetch another page.
"""

import argparse
import asyncio
from pathlib import Path

from async_instalib import ContentOptions, Instagram, InstagramError, SearchOptions


async def run(args: argparse.Namespace) -> None:
    """Reuse the client's saved identity and stop on the first provider failure."""
    async with Instagram.from_session_file(args.session) as instagram:
        if args.search is not None:
            page = await instagram.people.search(args.search, SearchOptions(limit=args.limit))
            for person in page.items:
                print("Search:", person.id, person.username)

        if args.suggest is not None:
            suggestions = await instagram.people.suggest(args.suggest)
            for person in suggestions.items:
                print("Suggestion:", person.id, person.username)

        profile = await instagram.people.by_username(args.username)
        # With a known ID, use: await instagram.people.profiles.by_id(user_id)
        print("Profile:", profile.id, profile.username)
        print("Followers:", profile.follower_count, "Following:", profile.following_count)

        if args.content:
            posts = await instagram.people.content.posts(
                profile.id, ContentOptions(limit=args.limit)
            )
            for media in posts.items:
                print("Post:", media.id, media.media_type)
            print("Posts returned:", len(posts.items))
            if posts.has_more:
                print(
                    "More posts available; pass this page's continuation to an explicit next call."
                )
            stories = await instagram.people.content.stories(profile.id)
            highlights = await instagram.people.content.highlights(profile.id)
            print("Story metadata received; reel present:", stories.get("reel") is not None)
            print("Highlight metadata status:", highlights.get("status"))

        if args.relationship:
            relationship = await instagram.people.relationships.get(profile.id)
            print("Following:", relationship.following)
            print("Follow request pending:", relationship.outgoing_request)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--search", help="Also read one Accounts search page for this query.")
    parser.add_argument("--suggest", help="Also read person typeahead suggestions for this query.")
    parser.add_argument(
        "--limit", type=int, default=9, help="Requested search/grid size (default 9)."
    )
    parser.add_argument(
        "--content", action="store_true", help="Read posts, stories, and highlights."
    )
    parser.add_argument("--relationship", action="store_true", help="Read relationship state.")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    if not args.session.expanduser().is_file():
        parser.error("--session must point to an existing saved session")
    try:
        asyncio.run(run(args))
    except InstagramError as error:
        print(type(error).__name__, str(error))
        print("Stopped without retrying. Resolve the reported condition before another run.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
