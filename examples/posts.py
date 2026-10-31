"""Publish one photo, reel, or story from your saved session; nothing else is changed.

Run: uv run python examples/posts.py --session /private/account.json --photo lake.jpg
     uv run python examples/posts.py --session /private/account.json --reel clip.mp4
     uv run python examples/posts.py --session /private/account.json --story lake.jpg
Publishing is visible to your audience. Add --delete to remove the item right after.
"""

import argparse
import asyncio
from pathlib import Path

from async_instalib import (
    Instagram,
    InstagramError,
    Photo,
    PostOptions,
    Publication,
    ReelOptions,
    StoryOptions,
    Video,
)


async def run(args: argparse.Namespace) -> None:
    """Publish once, wait for video processing, and optionally delete the result."""
    async with Instagram.from_session_file(args.session) as instagram:
        posts = instagram.posts
        publication: Publication
        if args.photo:
            photo = Photo.from_file(args.photo)
            publication = await posts.publish_photo(photo, PostOptions(caption=args.caption))
        elif args.reel:
            reel = Video.from_file(args.reel)
            publication = await posts.publish_reel(reel, ReelOptions(caption=args.caption))
        else:
            video = args.story.suffix.lower() == ".mp4"
            story = Video.from_file(args.story) if video else Photo.from_file(args.story)
            story_options = StoryOptions(caption=args.caption or None)
            publication = await posts.publish_story(story, story_options)
        # Videos can stay PENDING while Instagram processes them; check a few times.
        for _ in range(10):
            if publication.completed:
                break
            await asyncio.sleep(5)
            publication = await posts.status(publication.upload_id)
        print("Published:", publication.id, publication.status)
        if publication.media is not None:
            print("Media:", publication.media.id, publication.media.code)
        if args.delete and publication.media is not None:
            await posts.media.delete(publication.media)
            print("Deleted:", publication.media.id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    kind = parser.add_mutually_exclusive_group(required=True)
    kind.add_argument("--photo", type=Path, help="JPEG to publish to the feed.")
    kind.add_argument("--reel", type=Path, help="MP4 to publish as a reel.")
    kind.add_argument("--story", type=Path, help="JPEG or MP4 to publish as a story.")
    parser.add_argument("--caption", default="", help="Caption text.")
    parser.add_argument("--delete", action="store_true", help="Delete it again afterwards.")
    args = parser.parse_args()
    if not args.session.expanduser().is_file():
        parser.error("--session must point to an existing saved session")
    try:
        asyncio.run(run(args))
    except InstagramError as error:
        print(type(error).__name__, str(error))
        print("Stopped without retrying. Check your profile before publishing again.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
