# async_instalib: usage and reference

[Back to the project](https://github.com/Anandesh-Sharma/async_instalib#readme)

An async Python client for Instagram's private Android API. It covers password
login, persistent sessions, account reads, the person operations below,
publishing and managing your own posts, reels, stories, and highlights,
comments, and triggers that deliver account events to handlers and webhooks. It is
independent of Meta's supported public API.

The client is built from requests recorded in the local Android lab, including
Instagram 452.1.0.53.64's CAA/Bloks login sequence. It obtains fresh server context
and form identifiers for each login; it does not replay recorded credentials.

## At a glance

| Area | What you can do |
| --- | --- |
| Sessions | Password login, persistent device identity, saved sessions and account reads |
| People | Search, profiles, followers, following, stories, highlights and relationship controls |
| Publishing | Posts, carousels, reels, stories, highlights and comments |
| Direct messages | Read inboxes and threads; explicit send and thread actions |
| Events | Poll account events with handlers and optional webhook delivery |

[Sign in](#sign-in) · [People](#find-people-and-read-profiles) ·
[Publishing](#publish-and-manage-your-posts) · [Messages](#direct-messages) ·
[Events](#triggers-react-to-account-events) · [Configuration](#configuration-reference) ·
[Development](#development)

One account, one async client, one shared connection pool. Pagination and writes
are explicit; session files preserve account state between runs.

This is an independent, unofficial client for Instagram's private API. It is not
affiliated with or endorsed by Instagram. Private endpoints can change without notice.
Saved-session ownership uses POSIX file locks; use macOS or Linux.

## Install

Requires Python 3.11 or newer. Install from PyPI:

```sh
pip install async_instalib
```

For development and the `uv run` examples below:

```sh
git clone https://github.com/Anandesh-Sharma/async_instalib.git
cd async_instalib
uv sync --locked
```

For reproducible deployments, pin a PyPI version or install a reviewed Git commit: `git+https://github.com/Anandesh-Sharma/async_instalib.git@<commit-sha>`.

## Sign in

Use the local CLI to enter credentials without putting the password in source,
command arguments, environment files, or shell history:

```sh
uv run async-instalib login \
  --session ~/.local/share/async_instalib/account.session.json
```

After login, the CLI verifies the session with `account.me()` and saves it.
Running `login` again reuses an existing session without prompting for credentials.
An expired session raises an error rather than silently attempting another login. A saved
session can be checked without another password attempt:

```sh
uv run async-instalib me \
  --session ~/.local/share/async_instalib/account.session.json
```

For Python applications:

```python
import asyncio
from pathlib import Path

from async_instalib import Instagram

session_file = Path.home() / ".local/share/async_instalib/account.session.json"


async def main():
    async with Instagram.from_session_file(session_file) as instagram:
        me = await instagram.account.me()
        print(me.username)


asyncio.run(main())
```

With credentials already obtained through your application's secret input:

```python
async with Instagram.from_session_file(session_file) as instagram:
    if not instagram.session.authenticated:
        await instagram.auth.login(username, password)
    me = await instagram.account.me()
```

`Session.from_headers(headers)` can import your own captured Android request.
It copies recognized app, device, and authentication fields. Keep the header
mapping and session file private. `Session.authenticated` indicates local
credential presence; `await instagram.account.me()` checks server acceptance.

## Find people and read profiles

Person operations share the existing client's session, connection pool, request
budgets, and saved account state. IDs are strings; use the ID returned by search
or a profile instead of constructing an ID from a username.

```python
from async_instalib import ContentOptions, Instagram, SearchOptions

async with Instagram.from_session_file(session_file) as instagram:
    results = await instagram.people.search("example", SearchOptions(limit=10))
    for person in results.items:
        print(person.id, person.username, person.is_private)

    suggestions = await instagram.people.suggest("example")
    profile = await instagram.people.by_username("example_username")

    # When the numeric ID is already known, skip username resolution:
    profile = await instagram.people.profiles.by_id(profile.id)

    posts = await instagram.people.content.posts(profile.id, ContentOptions(limit=9))
    stories = await instagram.people.content.story_collection(profile.id)
    highlights = await instagram.people.content.highlight_tray(profile.id)
    about = await instagram.people.profiles.about_details(profile.id)
    relationship = await instagram.people.relationships.get(profile.id)
```

[`examples/people.py`](https://github.com/Anandesh-Sharma/async_instalib/blob/main/examples/people.py) is a runnable read-only example. Its
default operation resolves a username and reads that profile. Additional reads
are explicit flags:

```sh
uv run python examples/people.py \
  --session ~/.local/share/async_instalib/account.session.json \
  --username example_username --search example --suggest example --content
```

| Operation under `instagram.people` | Result and behavior |
| --- | --- |
| `search(query, SearchOptions(limit=20))` | `Page[PersonSummary]` from the Accounts search screen. No automatic paging. |
| `suggest(query)` | Person-only `Page[PersonSummary]` from typeahead; keyword and hashtag rows are excluded. Instagram chooses the result count. |
| `by_username(username, options=None)` | `PersonProfile` from one direct username stream; accepts an optional leading `@` and checks returned identity. Explicit `ProfileOptions` add a profile-by-ID read to preserve those options. |
| `profiles.by_username(username)` | Same direct username lookup without a search dependency. |
| `profiles.by_id(user_id, ProfileOptions())` | `PersonProfile` merged from progressive profile response documents. |
| `profiles.related(user_id, RelatedOptions())` | One `Page[PersonSummary]` of profile recommendations; does not follow accounts. |
| `profiles.about(user_id, AboutOptions())` | Provider-native GraphQL envelope for the About this account screen; preserved for callers that need the original bundle string. |
| `profiles.about_details(user_id, options=None, decode_options=None)` | `AboutAccount` with named country state and a bounded, decoded JSON bundle. No UI code is executed. |
| `lists.followers(user_id, PeopleListOptions())` | One `Page[PersonSummary]` of visible followers, optionally filtered by `query` or continued with the returned cursor. Instagram chooses the count and can limit visibility; provider flags remain in `.raw`. |
| `lists.following(user_id, PeopleListOptions())` | One `Page[PersonSummary]` of followed accounts; pass a returned continuation explicitly for another page. |
| `lists.mutual_followers(user_id, MutualFollowersOptions())` | Bounded `Page[PersonSummary]` of mutual followers, excluding suggested accounts; no total or continuation is implied. |
| `content.posts(user_id, ContentOptions(limit=9))` | One `Page[Media]` from the profile grid, preserving provider order. |
| `content.reels(user_id, ReelsOptions())` | One streamed `Page[Media]` from the reels tab, without playing media. |
| `content.tagged(user_id, TaggedOptions())` | One `Page[Media]` of visible posts tagging the person. |
| `content.reposts(user_id, RepostsOptions())` | One `Page[Media]` from the reposts tab; Instagram chooses the count. No repost action occurs. |
| `content.story_collection(user_id, MetadataOptions())` | `StoryCollection` with a nullable `StoryReel` and typed `StoryItem` media. No seen acknowledgement or playback. |
| `content.highlight_tray(user_id, MetadataOptions())` | `HighlightTray` with typed titles, covers, owners, and counts; no implicit item fetch. |
| `content.highlight_items(highlight_id, HighlightItemsOptions())` | `HighlightItems` for one `highlight:<id>` from the tray. Fetches its story metadata once, without playback, seen acknowledgement, or neighboring prefetch. |
| `content.stories(user_id, MetadataOptions())` | Original provider-native dictionary; `reel` may be null. No seen acknowledgement. |
| `content.highlights(user_id, MetadataOptions())` | Provider-native highlight metadata dictionary. Does not fetch every highlight's items. |
| `relationships.get(user_id)` | Account-relative `Relationship` state. |
| `relationships.lists.blocked()` | `Page[PersonSummary]` of the signed-in account's blocked accounts. |
| `relationships.lists.restricted()` | `Page[PersonSummary]` of restricted accounts. |
| `relationships.lists.close_friends(CloseFriendsListOptions())` | Current Close Friends members, excluding suggestions. |
| `relationships.lists.favorites()` | Feed Favorites members, separate from notification preferences. |
| `relationships.lists.muted(MutedListOptions())` | Original muted-screen envelope plus bounded, inert `decoded_payload`. |
| `relationships.lists.muted_accounts(options=None, decode_options=None)` | `Page[MutedPerson]` with verified numeric IDs and nullable category flags. No implicit profile lookup. |
| `relationships.lists.incoming_requests(FollowRequestsListOptions())` | Fully loaded incoming request `Page[PersonSummary]` for the signed-in account; no approval or dismissal. |

Missing fields remain `None`, including relationship flags; unknown does not mean
false. Typed person, media, and page objects retain provider fields in `.raw`,
which is excluded from their repr. Treat this data as private. Search visibility
depends on the signed-in account. `PersonNotFound` means the direct username endpoint reported that the profile was
unavailable; it does not prove that the account does not exist for other viewers.

`Page.items` is a tuple. Reading a page never fetches another page. Accounts
search supports an explicit `SearchOptions(cursor=page.continuation)` for the same
query and account. Its continuation preserves the ranking token, page index, and
both search-session IDs. A requested `limit` is not a guaranteed result count;
a live check requesting 30 returned two pages of 20 without overlap. Typeahead
has no recorded continuation input.
Profile grid continuation uses `ContentOptions(cursor=page.continuation)` and
validates the originating account, operation, target, and feature variables. It
preserves the three continuation fields observed in an actual app next-page
request: the media cursor, grid cursor, and pinned-item context. This request shape
has app and synthetic coverage and passed a live SDK check with two populated
pages of nine items each, without overlap. Supply the previous page's continuation
unchanged instead of building a cursor manually. Fetch each additional page
explicitly:

```python
if posts.continuation is not None:
    next_posts = await instagram.people.content.posts(
        profile.id, ContentOptions(limit=9, cursor=posts.continuation)
    )
```

`ProfileOptions` exposes `module`, `entry_point`, and `push_disabled` profile
context. `ContentOptions` exposes `limit`, `cursor`, and a `variables` mapping for
recorded GraphQL feature flags; target/count/cursor fields remain owned by their
typed inputs. `MetadataOptions(parameters={...})` supplies explicit string query
parameters for stories and highlights. These overrides do not establish support
for an undocumented operation or a server-side feature.

Typed story metadata preserves the provider's expiry, owner, and interaction
flags. `StoryCollection.reel` is `None` when no accessible reel is returned;
`StoryReel.items` contains `StoryItem` objects, which extend `Media`. Item-level
`StoryAuthor` identities may omit usernames; those fields remain `None` rather
than being fabricated from surrounding profile metadata.
`HighlightTray.items` contains `Highlight` entries with optional `HighlightCover`
and `HighlightImage` descriptors. Reading image metadata does not download an
image. A null `HighlightTray.has_fetched_all` means completeness is unknown.
The original `stories()` and `highlights()` dictionary methods remain available.
`highlight_items(highlights.items[0].id)` explicitly loads one selected highlight
and returns its metadata plus a tuple of `StoryItem` objects. This read merges the
recorded progressive reel-map response and never requests neighboring highlights
or sends Instagram's separate media-seen operation. `HighlightItemsOptions`
controls request context, exclusions, and optional capability metadata; the
response remains subject to the client's byte and stream limits.

`AboutAccount.country_display` and `country_visible` come from named Bloks state
keys. The country is a provider display string, not an ISO code. `states` retains
named `AboutState` entries, `bundle` holds decoded inert JSON, and `.raw` preserves
the original envelope. The captured screen provides its joined date only through
localized UI text and exposes no machine-keyed username history. The library does
not turn those labels into structured history fields. Use `AboutOptions` for
request context and `AboutDecodeOptions` for bundle size, node-count, and depth
limits; parsing never executes Bloks programs.

Account-owned blocked, restricted, Close Friends, and Favorites lists are separate
from a person's followers/following. They return one recorded response only.
An advertised continuation or an incomplete counted list raises `ProtocolError`
until its next-page contract is established. `muted_accounts()` binds each account navigation ID to the same ID in its mute
controls, then reads the machine switch states. It supports both observed embedded
layout formats, never executes their actions, and fails on ambiguous bindings.
`MutedPerson` exposes `posts`, `stories`, `notes`, `activity`, `map_notes`,
`map_location`, and `quick_snap`; missing categories and usernames remain `None`.
Use `muted()` for the original screen metadata. Incoming requests accept
only the captured fully loaded category with a null cursor; a screen's “see all”
flag does not establish a pagination contract.

Follower/following pages return every row supplied by Instagram. `PeopleListOptions`
has `query` and `cursor` only; it does not advertise a count parameter absent from
the recorded request. Instagram may return a limited follower list even for a
public account. This is a provider visibility limit, not a claim that the page
contains every follower. No list operation fetches additional pages automatically.
Followers and following support a name query and explicit continuation, with
cursors bound to the account, operation, target, and query. Follower pages can
overlap: the recorded app pages overlapped too. The library preserves every row
and does not silently deduplicate or imply stable ranking. The filtered follower
form was checked live on the signed-in account's own list; arbitrary-target filtered
results also passed a populated live check on a public profile.
`.raw` preserves `should_limit_list_of_followers` and
`should_limit_list_of_followings` when supplied. `.has_more` reports a continuation,
not complete visibility into an account's relationships. Mutual followers use the
captured social-context preview; suggested users stay in `.raw` and a “see all”
button is not interpreted as an available continuation.

## Explicit relationship changes

Relationship methods change the signed-in account's state. Call only the change
you intend; the read-only example never invokes them. There are no mutation
retries, automatic follow/unfollow cycles, or bulk actions.

```python
from async_instalib import MuteOptions, NotificationOptions

relationships = instagram.people.relationships
state = await relationships.follow(target_user_id)
# A private account may return outgoing_request=True instead of following=True.

state = await relationships.mute(target_user_id, MuteOptions(content="posts"))
state = await relationships.unmute(target_user_id, MuteOptions(content="posts"))
acknowledgement = await relationships.notifications(
    target_user_id, NotificationOptions(content="posts", mode="relevant")
)
```

| Method under `instagram.people.relationships` | Result and scope |
| --- | --- |
| `get(user_id)` | `Relationship` read with nullable flags. |
| `follow(user_id, ActionOptions())` | `Relationship`; a follow request can remain pending. |
| `unfollow(user_id, options=None)` | `Relationship`; the default context is `following_sheet`. |
| `cancel_request(user_id, options=None)` | `Relationship`; cancels an outgoing request with default context `friending_center`. |
| `mute(user_id, MuteOptions())`, `unmute(...)` | `Relationship`; one category per call: `posts`, `stories`, `notes`, `activity`, `map_notes`, or `map_location`. |
| `mute_quick_snap(user_id)`, `unmute_quick_snap(user_id)` | `RelationshipActionResult`; acknowledgement without inventing a relationship flag. |
| `notifications(user_id, NotificationOptions(content, mode))` | `RelationshipActionResult`; content is `posts`, `stories`, `reels`, or `live`, and mode is `all`, `relevant`, or `off`. |
| `restrict(user_id, ActionOptions())`, `unrestrict(...)` | `Relationship` from the server's matching target entry. |
| `block(user_id, BlockOptions())`, `unblock(user_id, options=None)` | `Relationship`; unblock defaults to the recorded `blocked_list` context. |
| `add_close_friend(user_id, CloseFriendsOptions())`, `remove_close_friend(...)` | `RelationshipActionResult`; changes one member without replacing the list. |
| `add_favorite(user_id, FavoritesOptions())`, `remove_favorite(...)` | `Relationship`; changes one feed favorite, separately from notification settings. |
| `approve_request(user_id, options=None)` | `Relationship`; approves one incoming request. Default context is `newsfeed_you`. |
| `decline_request(user_id, options=None)` | `Relationship`; declines one incoming request. Default context is `friending_center`. |
| `remove_follower(user_id, options=None)` | `Relationship`; removes one follower. Default context is `self_followers`. |

`ActionOptions` controls `container_module`, `radio_type`, and permitted extra
string form parameters. `MuteOptions` selects the content category and entry
context. `NotificationOptions` exposes the content/mode selection and extra
parameters. Extra parameters are copied and cannot replace target, account/device
identity, credentials, or owned fields. Default request context depends on the
operation; passing an options object explicitly supplies that object's values.

`BlockOptions(auto_block=True)` preserves the captured app setting for blocking
associated accounts. Successful block and unblock app flows were recorded with
an independently controlled target. Live SDK block/unblock checks also passed
with `auto_block=False`; the server accepted the requested narrower scope.
Linked-account restrictions can still make a particular target ineligible. Both
restrict and unrestrict have positive app captures and successful live SDK checks.
All provider failures remain typed errors, with no automatic mutation retries.

Map-location muting requires the target's distinct `fbid_v2` from `PersonProfile`,
passed as `MuteOptions(content="map_location", fbid_v2=profile.fbid_v2)`. It is not
interchangeable with the Instagram person ID. Other mute categories use that
person ID. Close Friends and Favorites changes add or remove one chosen member;
they never replace the whole list. Request approval may notify the requester and
only applies when an incoming request exists. Approval, decline, and follower
removal target one person; unrelated pending requests are left unchanged.

`cancel_request()` uses Instagram's shared cancellation/unfollow operation. If the
recipient has already accepted the request, it can unfollow them instead. It makes
one mutation without a hidden preflight read or retry, and returns the actual
`following` and `outgoing_request` flags. Use `get()` to inspect current state;
that read cannot make a later cancellation atomic with acceptance.

`RelationshipActionResult` acknowledges a successful status response without
inventing relationship flags. A network failure can leave a mutation's outcome
unknown; read the relationship before deciding what to do next. Reels notification
requests omit the app's recorded hardware telemetry rather than reporting a
captured emulator's hardware. That omission has synthetic coverage but has not
been checked with a live SDK reels-notification request.

## Person feature evidence

App-observed means a request/response was recorded while navigating the Android
app. Synthetic tests validate local serialization, parsing, and failure behavior.
Live SDK checks mean this Python client received a successful response for the
tested account; they do not establish behavior for every account or response shape.

| Feature | Evidence and remaining limit |
| --- | --- |
| Accounts search and typeahead | App recordings, synthetic tests, real loopback HTTP, and successful live SDK reads. Accounts pagination passed two pages of 20 with no overlap; typeahead has no continuation input. |
| Profile by ID | App-recorded progressive stream, synthetic tests, and a successful live SDK read. |
| Username resolution | Direct streamed app captures, synthetic identity/error tests, and a successful live SDK lookup of the controlled private profile. Default lookup makes one request; explicit legacy profile options preserve the additional ID request. |
| Profile grid | App-recorded first and next pages, including three-field continuation context, synthetic tests, and two successful populated live SDK pages with nine items each and no overlap. |
| Related profiles | App capture, synthetic parsing tests, and a live SDK response with 12 recommendations. |
| About this account | App capture, synthetic tests, and live SDK raw and typed reads. Only country display/visibility have captured machine keys; joined date and username history remain unnormalized. |
| Followers/following | App captures and successful live SDK reads: two follower pages of 25 with four overlapping IDs, an earlier limited 50-row follower result, two following pages of 25 with no overlap, and a filtered following page with four matches. The own-account follower filter also passed live with the expected empty result after fixture removal. Arbitrary-target filtered follower results also passed a populated live check on a public profile. Optional fields that produced app-side GraphQL execution errors are excluded instead of suppressing errors. |
| Mutual followers | Populated app capture, synthetic tests, and a populated live SDK read. Bounded preview only, without inferred total or continuation. |
| Profile reels | App-recorded first and next pages, synthetic tests, and two successful live SDK pages of 12 items each without overlap. No playback requests. |
| Tagged media | App-recorded first and continuation requests, synthetic tests, and two live SDK pages of 12 items each without overlap. |
| Reposted media | Three recorded pages validated offline and two live SDK pages of 12 items each without overlap. No separate media-detail request or repost mutation is made. |
| Story/highlight metadata | App recordings, synthetic tests, and live SDK raw and typed reads: one story item and seven highlights. Highlight REST stream documents are merged. No playback or seen acknowledgement. |
| Highlight items | App captures, synthetic tests, and a successful live SDK read of 51 items with matching highlight ID and default options. The optional capability field was omitted successfully. The SDK does not perform app-style neighboring prefetch or media-seen requests. |
| Follow/unfollow, relationship read, posts mute/unmute | App captures, synthetic tests, and a successful controlled live SDK cycle. Follow, block, restrict, and mute state were checked afterward and the initial state was restored. |
| Story, Notes, activity, map Notes/location, and Quick Snap muting | App capture pairs, synthetic tests, and controlled live SDK pairs passed. Activity uses captured operation-scoped GraphQL headers; independent readback confirmed restoration for activity and Quick Snap. |
| Close Friends and feed Favorites edits | Successful app capture pairs and synthetic tests. Controlled live SDK add/list/remove cycles also passed. Each call changes one member. |
| Incoming-request listing | Populated app capture, synthetic tests, and a live SDK page containing one request. Only the fully loaded category is supported. |
| Incoming-request approval/decline and follower removal | Successful app captures and synthetic tests. Controlled live SDK approval/removal and a separate decline fixture passed; follow-up reads confirmed restoration. Unrelated requests were preserved. |
| Outgoing follow requests and cancellation | Private-target app captures and a controlled live SDK cycle passed: `follow()` returned `following=False, outgoing_request=True`; `cancel_request()` returned both false. A subsequent read confirmed restoration while the target remained private. Synthetic tests verify captured fields, nullable state, overrides, and no retry after an uncertain network outcome. |
| Account-owned relationship lists | Blocked/restricted/Close Friends/Favorites reads passed live SDK checks after restoration, with empty results. Typed muted-screen decoding passed a live Posts+Notes fixture and empty restoration; raw metadata remains available. Close Friends and Favorites also passed populated live reads. List continuation remains unsupported. |
| Person notifications | All three modes for posts/stories/reels/live have app captures and synthetic tests. Live SDK all/off/default cycles passed for all four categories, with profile readback after each change and original preferences restored. |
| Block/unblock and restrict/unrestrict | Positive app captures and synthetic tests. Controlled SDK block/unblock and restrict/unrestrict cycles passed. Target-specific rejections remain possible. |
| Pagination | Accounts search, profile-grid, reels, tagged, reposts, and follower/following-list continuation passed populated live SDK checks. Follower pages may overlap. Typeahead and account-owned relationship-list continuation remain unsupported. |

GraphQL typeahead and grid responses can contain multiple JSON documents and
deferred patches. The transport merges supported dictionary/list patches and
preserves stream metadata. Progressive REST profile documents are checked before
the profile resource merges them. Both paths enforce response-size, document-count,
and nesting bounds. Malformed successful streams fail explicitly rather than
returning incomplete results. Errors on HTTP 200 still raise typed SDK errors;
challenge/restriction pauses, rate-limit deadlines, and session rejection apply
before another queued request is admitted with the default single connection.

## Publish and manage your posts

`instagram.posts` publishes to the signed-in account. Each call uploads the media
unchanged, then sends one configure request, exactly as recorded from the Android
app. Publishing is visible to your audience; nothing is retried.

```python
import asyncio

from async_instalib import (
    CarouselItem,
    Instagram,
    Photo,
    PostOptions,
    ReelOptions,
    StoryOptions,
    UserTag,
    Video,
)

async with Instagram.from_session_file(session_file) as instagram:
    me = await instagram.account.me()
    place = (await instagram.posts.places.search("Mumbai")).items[0]

    photo = Photo.from_file("lake.jpg")
    post = await instagram.posts.publish_photo(
        photo,
        PostOptions(
            caption="Lake day",
            location=place,
            user_tags=(UserTag(me.id, 0.4, 0.6),),
            alt_text="A blue lake between cliffs",
            hide_like_count=True,
        ),
    )

    clip = Video.from_file("clip.mp4", cover=Photo.from_file("clip-first-frame.jpg"))
    album = await instagram.posts.publish_carousel(
        [CarouselItem(photo, alt_text="The lake"), clip], PostOptions(caption="Trip")
    )
    reel = await instagram.posts.publish_reel(
        Video.from_file("reel.mp4"), ReelOptions(caption="Hi")
    )
    story = await instagram.posts.publish_story(photo, StoryOptions(close_friends=True))

    while not album.completed:  # videos can stay PENDING while Instagram processes them
        await asyncio.sleep(5)
        album = await instagram.posts.status(album.upload_id)
```

Every publish returns a `Publication`: the post `id`, `upload_id`, `status`
(`COMPLETED` or `PENDING`), the published `media` (a `Media`), and a carousel's
`children`. [`examples/posts.py`](https://github.com/Anandesh-Sharma/async_instalib/blob/main/examples/posts.py) publishes one photo, reel, or
story from the command line and can delete it again with `--delete`.

| Operation under `instagram.posts` | Request and behavior |
| --- | --- |
| `publish_photo(photo, PostOptions())` | `rupload_igphoto` then `media/configure/`. |
| `publish_carousel(items, PostOptions())` | 2 to 10 photos and videos in order: one upload per item (plus a cover per video), then `media/configure_sidecar/`. Every item is validated before the first upload. |
| `publish_reel(video, ReelOptions())` | `rupload_igvideo` then `media/configure_to_clips/`; Instagram takes the cover from the video. |
| `publish_story(photo_or_video, StoryOptions())` | Upload then `media/configure_to_story/`; expires after 24 hours. |
| `status(upload_id)` | `media/get_upload_status_REST/`: the current `Publication`. |
| `places.search(query)` | One `Page[Place]` from the app's place picker query. |
| `media.edit(media_id, EditOptions(caption))` | Replace the caption; optionally set alt text, a location, or added people tags. Returns the edited `Media`. |
| `media.delete(media)` | Delete a post, carousel, reel, or story. Takes the `Media`, because the request differs by kind. |
| `media.archive(media_id)`, `media.unarchive(media_id)` | Hide a post from your profile, or show it again. |
| `media.archived()` | `Page[Media]` of archived posts. |
| `media.pin(media_id)`, `media.unpin(media_id)` | Pin a post to the top of your grid, or unpin it. |
| `media.add_to_grid(media_id)`, `media.remove_from_grid(media_id)` | Show a reel in the main grid, or keep it in the Reels tab only. |
| `media.enable_comments(media_id)`, `media.disable_comments(media_id)` | Turn commenting on or off for a post or story. |
| `media.set_counts(media_id, hide_likes, hide_shares)` | Hide or show the like and share counts. |
| `highlights.create(title, story_ids)` | A highlight from your stories; the first story is the cover. |
| `highlights.edit(highlight, HighlightChanges(...))` | Rename it or add and remove stories, keeping its cover. Pass the `Highlight` from `create` or `people.content.highlight_tray`. |
| `highlights.delete(highlight_id)` | Delete a highlight; its stories are not deleted. |

`media_id` is the `<pk>_<user id>` value from `Publication.media.id` or any `Media`;
a bare `<pk>` also works. These actions accept only the signed-in account's media.

### Media files

`Photo` takes a JPEG and `Video` an MP4; both read their size (and duration) from
the file and refuse anything they cannot read. The library uploads your bytes
unchanged; Instagram transcodes them. It does not resize, crop, or convert, so
prepare files in the size you want, for example a 1080×1350 photo or a 1080×1920
reel. A carousel video needs `cover=`, a JPEG of its first frame, because the app
uploads one for each:

```sh
ffmpeg -i clip.mp4 -frames:v 1 -q:v 3 clip-first-frame.jpg
```

Large videos take longer than the default 20-second `ClientOptions.timeout`; raise
`write_timeout` for big uploads. Each upload counts against the request budget.

### Options

| Object | Fields | Notes |
| --- | --- | --- |
| `PostOptions` | `caption`, `location`, `user_tags`, `alt_text`, `comments_disabled`, `hide_like_count`, `hide_share_count` | Feed photo or carousel. In a carousel, set tags and alt text per photo with `CarouselItem`. Switches are sent only when on, as the app does. |
| `CarouselItem` | `media`, `user_tags`, `alt_text` | Tags and alt text were recorded for carousel photos only. |
| `ReelOptions` | `caption`, `location`, `tagged_user_ids`, `comments_disabled`, `hide_like_count`, `hide_share_count`, `share_to_feed` | Reels tag people without a position. `share_to_feed=False` keeps the reel out of the main grid. |
| `StoryOptions` | `caption`, `close_friends` | `close_friends=True` limits the story to your Close Friends list. |
| `EditOptions` | `caption`, `alt_text`, `location`, `user_tags` | `caption` always replaces the current caption; unset fields stay unchanged. |
| `HighlightChanges` | `title`, `add`, `remove` | Story media IDs to add or remove. |
| `UserTag` | `user_id`, `x`, `y` | Position as fractions of the photo's width and height. |
| `Place` | `id`, `name`, `address`, `latitude`, `longitude` | From `places.search()`; pass it as `location`. |

There are no free-form provider parameters: every field maps to a value recorded
from the app, so an unrecorded field cannot publish something unintended.

### Posts evidence and limits

Every request was recorded while doing the same action in the Android app on a test
account, then repeated live through this library on that account. Each test item
was deleted afterwards, and a final read confirmed no posts, stories, highlights, or
Close Friends remained.

| Feature | Evidence |
| --- | --- |
| Photo post with caption, location, people tag, alt text, comments off, hidden counts | App capture and a live SDK publish. The returned media confirmed the location, alt text, comments off, and hidden like count; Instagram does not echo tags in this response. |
| Carousel of a photo and a video, with per-photo tag and alt text, location, comments off, hidden like count | Two app captures and a live SDK publish that went from `PENDING` to `COMPLETED` through `status()`. |
| Reel with location, tag, and hidden counts; grid remove and add | App captures and a live SDK publish and grid round trip. |
| Photo story with caption; Close Friends video story | App captures and live SDK publishes; the returned audience was `besties`. |
| Edit, delete (every kind), archive, archive list, unarchive, pin, unpin, comments on/off, count visibility | App captures of each menu action and live SDK calls. Deletion also passed for media read from the profile grid. |
| Highlight create, rename with an added story, delete | App captures and a live SDK round trip. Adding stories was recorded with empty lists and verified live. |
| Place search | App capture and a live SDK search. |

Limits:

- Uploads use the app's single-request form. The app's resumable upload, codec
  negotiation (`upload_settings`), image recompression, and the post-publish PDQ
  image hash are not sent. Publishing worked without them in the live checks.
- The app's provenance metadata, navigation history, and device telemetry are not
  sent. The `device` field repeats the values already in the user agent.
- `media.edit` can add people tags; removing existing tags was not recorded.
- `media.archived()` returns one page; a longer archive raises `ProtocolError`
  until its continuation is recorded. Story archive is not covered.
- Reading one media item by ID was not recorded; read your posts with
  `people.content.posts(your_id)` or keep the `Publication.media`.
- Not covered: drafts, scheduling, music, stickers, collaborators, AI labels,
  boosting, and comments or likes on posts.

## Comments

`instagram.comments` reads, writes, and moderates comments, using the requests
recorded from the app's comment sheet. Each write is sent once with a fresh
idempotence token; nothing is retried.

```python
from async_instalib import CommentListOptions, Photo

comments = instagram.comments
page = await comments.list(post.media.id, CommentListOptions(sort="recent"))
if page.continuation is not None:
    older = await comments.list(
        post.media.id, CommentListOptions(sort="recent", cursor=page.continuation)
    )

mine = await comments.post(post.media.id, "Nice light @friend #lake")
answer = await comments.reply(post.media.id, mine.id, "Thanks!")
await comments.like(mine.id)
await comments.edit(mine.id, "Nice light, edited")
await comments.pin(post.media.id, mine.id)
await comments.delete(post.media.id, answer.id)

await comments.settings.allow_comments_from("followers")
await comments.settings.block_commenter(person_id)
```

`media_id` is the post's `<pk>_<owner id>` (`Media.id`); a bare `<pk>` means one of
your own posts. Comment IDs are the numeric `Comment.id`.

| Operation under `instagram.comments` | Request and behavior |
| --- | --- |
| `list(media_id, CommentListOptions(sort, cursor))` | One `Page[Comment]` of top-level comments from `stream_comments`. `sort` is `default` (the app's "For you"), `recent`, or `meta_verified`. The page merges the streamed documents in order; `continuation` carries `next_min_id` and is bound to the post and sort. |
| `replies(media_id, comment_id)` | `Page[Comment]` of one comment's replies. A thread that advertises more replies raises `ProtocolError`; reply paging was not recorded. |
| `post(media_id, text)`, `reply(media_id, comment_id, text)` | A text comment or reply. Mentions and hashtags are plain text. |
| `post_gif(media_id, gif_id)` | A GIF comment by GIPHY media ID. |
| `post_photo(media_id, photo)` | Uploads a JPEG, then `media/configure_to_comment/`. |
| `edit(comment_id, text)` | Your own comment, through the recorded `CommentEditMutation`. |
| `delete(media_id, comment_id)` | `comment/bulk_delete/`: your comment, or anyone's comment on your post. |
| `like(comment_id)`, `unlike(comment_id)`, `likers(comment_id)` | Comment likes and one `Page[PersonSummary]` of likers. |
| `pin(media_id, comment_id)`, `unpin(media_id, comment_id)` | Pin a comment on your own post. |

`comments.settings` changes the whole account, through the recorded Bloks settings
handlers:

| Operation | Values |
| --- | --- |
| `allow_comments_from(audience)` | Posts and reels: `followers`, `mutual_followers` (followers you follow back), or `off`. |
| `allow_story_comments_from(audience)` | Stories: the same three values. |
| `allow_gif_comments(enabled)`, `allow_photo_comments(enabled)` | Allow or refuse GIF and photo comments. |
| `block_commenter(user_id)`, `unblock_commenter(user_id)` | Hide a person's new comments from everyone but them. |

To turn comments off for one post, use `posts.media.disable_comments()`. To restrict
or block a commenter's account, use `people.relationships.restrict()` or `block()`.

### Comments evidence and limits

Every operation was recorded in the app on the test account, including moderation
of a comment written by a second test account, and then repeated live through this
library. The live check also read two pages of 15 comments each, with no overlap, from
a popular public post. All test comments were deleted and every setting was restored.

- Comments are sent without the app's `user_breadcrumb` (a typing-time signal) and its
  feed-ranking context. The live checks succeeded without them.
- The recorded audiences are those offered to a private account; "everyone" was not
  offered, so it is not supported.
- Not covered: hidden words (the app sent no request the lab could see), reply paging,
  deleting several comments in one call, reading the current settings, and reporting
  comments, which files a real report.

## Direct messages

`instagram.direct` reads the inbox and message requests, sends every recorded message
kind, and changes threads and messages. Each send is one request with a fresh offline
threading ID; nothing is retried.

```python
from async_instalib import Audio, InboxOptions, Photo

direct = instagram.direct
inbox = await direct.inbox(InboxOptions(thread_limit=15, message_limit=5))
thread = inbox.items[0]
newest = thread.messages[0]

sent = await direct.send.text(thread.id, "On my way", reply_to=newest)
await direct.send.photo(thread.id, Photo.from_file("lake.jpg"))
await direct.send.voice(thread.id, Audio.from_file("note.m4a"))
await direct.messages.react(newest, "😂")
await direct.messages.edit(sent, "On my way, 5 min")
await direct.messages.unsend(sent)

for request in (await direct.requests()).items:
    await direct.approve(request.id)  # or direct.hide(request.id) to decline
```

`thread` is the numeric `Thread.id`. Message actions take the `Message` itself, because
Instagram needs its item ID, client context, sender, and type together; sends return
the sent `Message` for later edits, reactions, or unsends.

| Reads | Request and behavior |
| --- | --- |
| `inbox(InboxOptions(thread_limit, message_limit))` | `direct_v2/inbox/`: one `Page[Thread]`, newest first, each with its newest messages. Pinned threads merge into the list. |
| `requests(include_older=False)` | Message requests (`IGDirectGetPendingInboxQuery`). `include_older` adds the older requests the app shows behind "See older". |
| `presence(user_ids)` | `{user_id: Presence(is_active, last_active_at)}` for the people you talk to. |

| Sends under `direct.send` | Request |
| --- | --- |
| `text(thread, text, reply_to=None)` | `broadcast/text/`. `reply_to` quotes a text message; replies to other kinds were not recorded. |
| `link(thread, text, urls)` | `broadcast/link/`; `urls` are the links in `text`, shown as previews. |
| `photo(thread, Photo)`, `video(thread, Video)` | Uploads to `rupload.facebook.com/messenger_image` or `messenger_video` as a file attachment, then `broadcast/photo_attachment/` or `video_attachment/`. |
| `voice(thread, Audio)` | Uploads the MP4/M4A to `messenger_audio`, then `broadcast/voice_attachment/` with its waveform. |
| `like(thread)`, `gif(thread, giphy_id)` | The heart sticker, or a GIPHY GIF (`animated_media`). |
| `media(thread, Media, text=None)` | Shares a reel (`IGDirectClipShareMutationWithResponse`, optional text; returns the item ID) or a photo post (`media_share`, no text). |
| `profile(thread, user_id)` | Shares a profile (`IGDirectBroadcastProfile`). |
| `forward(thread, Message)` | Forwards a message from another thread. |

| Changes | Request |
| --- | --- |
| `messages.react(message, emoji="❤")`, `unreact(message)` | `broadcast/reaction/`; a new emoji replaces your old one. |
| `messages.edit(message, text)` | Your own text message (`IGDirectEditMessageMutation`). |
| `messages.unsend(message)` | `threads/{id}/items/{item}/delete/`: removes your message for everyone. |
| `messages.mark_seen(message)` | `IGDirectItemSeenMutation`: read receipts up to that message. |
| `messages.pin(message)`, `unpin(message)` | Pin a message at the top of the thread. |
| `approve(thread)`, `hide(thread)` | Accept a request; decline it or delete a chat from your inbox (future requests are not sent to spam). |
| `pin(thread)`, `unpin(thread)` | Pin the thread in your inbox (`IGPinThreadMutation`). |
| `mute(thread, seconds=3600)`, `unmute(thread)` | Mute messages for 1 hour, 8 hours, or 24 hours (the app's choices). |
| `mute_calls(thread)`, `unmute_calls(thread)` | Mute or unmute calls until you change it. |

### Direct messages evidence and limits

Every operation above was recorded in the app on the test account while a second test
account wrote back, then repeated live through this library: each send kind,
reply, edit, reaction, unsend, both pins, seen, presence, mute, call mute, and accepting
a request. All test messages were unsent afterwards.

- Group chats are not supported: creating, renaming, adding or removing people, and
  leaving need a third test account, which the lab did not have.
- Starting a thread with someone you have never messaged, and reading older messages
  of a thread (history paging), were not recorded. `inbox()` reads the first page only.
- Disappearing messages, themes, nicknames, and typing indicators travel over the
  app's realtime (MQTT) connection, not HTTP; they are not supported. So are video
  and voice calls.
- Voice messages show a flat waveform unless you pass `Audio(waveform=...)` levels;
  the library does not decode audio.
- Shares cover reels and photo posts; carousels, videos in the feed, and stories were
  not recorded.

## Triggers: react to account events

`async_instalib.triggers` watches the signed-in account and delivers each new event,
such as a tag, mention, comment, or direct message, to your Python handlers and to your
webhooks. Every source produces the same `Event`, so handlers and webhooks stay the same
when a new source is added.

```python
from pathlib import Path

from async_instalib import Instagram
from async_instalib.triggers import TriggerOptions, Triggers, Webhook

state = Path.home() / ".local/share/async_instalib/triggers.json"
async with Instagram.from_session_file(session_file) as instagram:
    options = TriggerOptions(interval=60, state_file=state)
    async with Triggers(instagram, options) as triggers:
        triggers.add_webhook(Webhook("https://example.com/instagram", secret=secret))

        @triggers.on("tag", "mention")
        async def noticed(event):
            print(event.type, event.actor.username, event.media_id, event.text)

        await triggers.run()  # polls until the task is cancelled
```

Or run it as a service from the command line, keeping the secret out of the arguments:

```sh
export HOOK_SECRET=...
uv run async-instalib watch --session ~/.local/share/async_instalib/account.session.json \
  --webhook https://example.com/instagram --secret-env HOOK_SECRET --events tag,mention,comment
```

| Event type | Source and meaning |
| --- | --- |
| `tag` | `tagged`: someone tagged you in a post or reel (the "Photos of you" feed). |
| `mention` | `activity`: someone mentioned you in a comment or a caption. |
| `comment` | `activity`: someone commented on your post. |
| `like` | `activity`: someone liked your post. |
| `follow` | `activity`: someone started following you. |
| `notification` | `activity`: any other row; Instagram's own type name is in `data["name"]`. |
| `message` | `direct`: someone sent you a message (not your own, not thread notices). `data` has `thread_id`, `message_type`, and the raw `message`. |
| `message_reaction` | `direct`: someone reacted to a message; `text` is the emoji. Changing the emoji is a new event. |
| `message_request` | `direct`: a message request; `text` is the sender's newest message. |

An `Event` has `id` (unique and stable: `<source>:<Instagram id>`), `type`, `source`,
`account_id`, `occurred_at`, `actor` (`id`, `username`), `media_id`, `comment_id`,
`text`, and `data` with Instagram's original fields. `instagram.activity.notifications()`
reads the activity feed directly as `Notification` rows.

### Webhooks

Each event is one JSON POST of `Event.to_dict()` with these headers:

| Header | Value |
| --- | --- |
| `X-Instalib-Event` | The event type. |
| `X-Instalib-Delivery` | The event `id`; drop repeats with it. |
| `X-Instalib-Timestamp` | Unix seconds when it was sent. |
| `X-Instalib-Signature` | With a secret: `sha256=` + hex HMAC-SHA256 of `"<timestamp>.<body>"`. |

```python
import hashlib, hmac


def verified(secret: bytes, headers, body: bytes) -> bool:
    signed = headers["X-Instalib-Timestamp"].encode() + b"." + body
    expected = "sha256=" + hmac.new(secret, signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, headers.get("X-Instalib-Signature", ""))
```

`Webhook(url, secret=None, events=frozenset(), timeout=10, attempts=3)` filters by event
type (all when empty). Network errors, 429, and 5xx responses are retried after 1 and
2 seconds; other 4xx responses are not. Webhooks use their own HTTP client and never
receive Instagram credentials. A failing handler or webhook is logged and passed to
`@triggers.on_error` handlers without stopping other deliveries.

### How it runs

- Polling, not push: polls are at least 30 seconds apart (60 by default). The default
  sources make four requests per poll (activity 1, tagged 1, direct 2: inbox and
  requests), counted against the account's request policy. A rate limit or
  local budget waits for its delay; a challenge or rejected session stops `run()`.
- The first poll of a source records what already exists and delivers nothing;
  `TriggerOptions(deliver_existing=True)` delivers it too.
- Delivered event IDs are saved to `state_file` (mode `0600`), so a restart does not
  repeat old events. Delivery is at least once: a crash between delivering and
  saving can repeat an event.
- Reading the activity feed does not mark it seen in the app (`mark_as_seen=false`).
- A new source is any object with a `name` and `async poll(instagram) -> list[Event]`;
  pass it in `TriggerOptions(sources=(...))`.

Evidence: the activity feed, tagged feed, and direct inbox were recorded in the app
with real comments, mentions, likes, follows, tags, messages, and reactions from a
second test account. In live runs, `async-instalib watch` recorded a baseline, then
delivered a new comment, a caption mention, a new tag, a message, and a reaction to a
local webhook, each with a valid signature. `message_request` is covered by offline
tests only: no fresh request could be reproduced between the two test accounts.

Limits: only the newest page of the activity feed is read, so poll often enough; a tag
that waits for your manual approval may appear only once approved. `DirectSource`
sees the newest 15 threads with 10 messages each per poll; messages beyond that
between two polls are missed, so pass `DirectSource(InboxOptions(...))` with higher
limits for a busy inbox.

## Public operations and guarantees

| Operation | Contract |
| --- | --- |
| `Instagram(session=None, options=None)` | Owns one account's session and pooled HTTP client. |
| `Instagram.from_session_file(path, options=None)` | Loads or creates private state and saves on close, including after an operation raises. No implicit login. |
| `auth.login(username, password)` | Runs CAA setup and submits the encrypted password once; returns `User`. |
| `account.me()` | Reads the authenticated account and checks its ID against the session. |
| `Session.save(path)` / `Session.load(path)` | Preserve identity, bearer credentials, scoped cookies, and routing state. |
| `Session.from_headers(headers)` | Imports recognized state from your own captured request. |
| `aclose()` / async context manager | Close connections without logging out of Instagram. |
| `auth.login_legacy(username, password)` | Explicit compatibility endpoint; the tested account returns `needs_upgrade`. |
| `auth.verify_two_factor(code, method=TwoFactorMethod.TOTP)` | Continues a **legacy** two-factor response; supports TOTP and SMS. |

Use one active client per `Session`. For multiple accounts, create separate
sessions and files. Requests share a bounded connection pool; the default
concurrency limit is one. Do not share one client between event loops.

`ClientOptions` controls timeouts, concurrency, and an optional explicit proxy
and CA bundle. TLS verification is enabled, environment proxies are ignored, and
redirects are rejected. The API origin is restricted to Instagram or a loopback
test server; direct-message attachments upload to `rupload.facebook.com`, the host the
app uses. There are no automatic password retries or hidden account writes.

Sessions are written atomically with mode `0600`; passwords are never saved.
Session files still contain reusable login credentials and are not encrypted at
rest. `from_session_file` saves admission state before dispatch, response state after
exchanges, and final state on close. With plain `Instagram(session)`,
call `session.save()` explicitly. A forced process termination can still lose a response received immediately
before termination. The last persisted admission still counts against its budget. Use one active client
per file. Invalid files raise instead of silently discarding an existing identity.

## Configure headers without rotating identity

`ClientProfile` owns public app metadata. `Device` owns persistent device IDs.
Both are saved in the session. Configure them when creating an account's session
and reuse that session across runs.

```python
from dataclasses import replace
from async_instalib import ClientOptions, ClientProfile, Instagram, Session

profile = ClientProfile().with_locale("en_IN", timezone_offset=19800)
profile = replace(profile, country_code=91)
options = ClientOptions(
    max_connections=1,
    extra_headers={"X-IG-Connection-Type": "WIFI"},
)

async with Instagram(Session(profile=profile), options) as instagram:
    await instagram.auth.login(username, password)
    instagram.session.save(session_file)
```

The default user agent describes a real phone: a Samsung Galaxy S25 (`SM-S931B`) on
Android 16. To use another phone, pick a preset when you create the session:

```python
from async_instalib import PIXEL_9_PRO, ClientProfile, Session

session = Session(profile=ClientProfile.for_device(PIXEL_9_PRO).with_locale("en_GB", 0))
```

| Preset | Phone | Android |
| --- | --- | --- |
| `GALAXY_S25` (default) | Samsung Galaxy S25, `SM-S931B` | 16 |
| `GALAXY_S25_ULTRA` | Samsung Galaxy S25 Ultra, `SM-S938B` | 16 |
| `GALAXY_S24` | Samsung Galaxy S24, `SM-S921U` | 14 |
| `PIXEL_9_PRO` | Google Pixel 9 Pro | 17 |
| `INFINIX_X6880` | Infinix X6880 | 15 |

Each preset copies the device fields (Android version, dpi, screen, maker, model,
code name, chip) of an Instagram app user agent published on whatmyuseragent.com
from builds 447 to 451. The app version `452.1.0.53.64` and build number `1100805085`
always come from the recorded APK, because the GraphQL IDs belong to that build.
`AndroidDevice(...)` describes another phone the same way. A session saves its profile,
so the device stays the same across runs; do not change the device of a signed-in
session, which Instagram sees as a new phone. Sessions saved before this default
changed keep the emulator user agent they were created with.

Use configuration matching your environment. `with_locale()` updates both the
locale headers and the locale inside the Android user agent. A complete custom
`user_agent`, `app_id`, `bloks_version`, and `capabilities` can be supplied through
`ClientProfile`; keep app-version fields consistent with your captured profile.
Custom combinations are not verified merely because they pass syntax validation.

`extra_headers` is copied and frozen for each client. It accepts additional
metadata, but rejects client-owned authentication, device identity, routing,
attestation, and HTTP framing headers. Configure owned public fields through
`ClientProfile` and `Device`. Tokens and signed routing state come from Instagram.
GraphQL operation identity and legacy-root headers are owned per request; global
extra headers cannot replace them. Activity muting supplies its captured Pando
header bundle automatically without leaking it onto other requests.
Proxy credentials and extra-header values are omitted from configuration reprs.

Header customization does **not** provide anonymity or guarantee against bans.
The bearer token identifies the account; HTTP headers do not hide the network
source or change the client's TLS implementation. Meta describes rate limits,
data limits, and activity-pattern checks in its
[explanation of automated-access defenses](https://about.fb.com/news/2021/04/how-we-combat-scraping/).
No request frequency or header preset in this library is certified as safe.

## Configuration reference

All supported operating parameters are constructor arguments on typed dataclasses.
Use `dataclasses.replace(settings, field=value)` to derive another configuration.
See [the runnable example](https://github.com/Anandesh-Sharma/async_instalib/blob/main/examples/configured_client.py) for a complete client setup.

| Object | Parameters | Defaults / meaning |
| --- | --- | --- |
| `ClientOptions` | `base_url` | `https://i.instagram.com/api/v1/`; only this HTTPS host or loopback test origins. |
| | `timeout`, `connect_timeout` | 20 and 5 seconds. |
| | `read_timeout`, `write_timeout`, `pool_timeout` | `None`: inherit `timeout`; otherwise positive seconds. |
| | `max_connections`, `max_keepalive_connections` | 1; keepalive defaults to the connection limit. Zero keepalive disables reuse. |
| | `keepalive_expiry`, `http2` | 5 seconds, `True`. |
| | `max_response_bytes` | 16 MiB of decompressed response data. |
| | `proxy`, `ca_bundle`, `extra_headers` | Explicit proxy, additional CA file, and extra metadata headers; unset by default. |
| | `policy`, `auth` | `RequestPolicy()` and `AuthOptions()` below. |
| `RequestPolicy` | `min_interval` | 1 second between request admissions; zero disables local spacing. |
| | `max_requests`, `window_seconds` | 60 admitted requests within a rolling 60-second window. |
| | `max_login_attempts`, `login_window_seconds` | 3 explicit login attempts within 900 seconds. Includes failed preflight attempts. |
| | `fallback_cooldown` | 60 seconds when HTTP 429 has no valid `Retry-After`. |
| | `pause_on_challenge`, `pause_on_restriction`, `pause_on_unsupported_login` | All `True`; stop subsequent work when these conditions occur. |
| `AuthOptions` | `offline_experiment_group` | Captured `caa_iteration_v3_perf_ig_4` setup group. |
| | `styles_id`, `theme`, `design_system` | `instagram`, `three_neutral_gray`, `XMDS`. |
| | `latency_marker_id`, `trust_this_device` | 36707139; legacy two-factor trust flag defaults to `False`. |
| | `prepare_parameters`, `client_parameters`, `server_parameters` | JSON dictionaries merged over the corresponding CAA metadata maps; empty by default. |
| | `network_info` | Override reported network metadata; default reports no Android SIM subscriptions. |
| `ClientProfile` | `user_agent`, `app_id`, `bloks_version`, `capabilities` | Matched captured app profile; see `config/profile.py` for exact public defaults. |
| | `locale`, `timezone_offset`, `country_code` | `en_US`, 0 seconds from UTC, calling code 1. |
| `Device` | `id`, `android_id`, `family_id`, `phone_id`, `advertising_id` | Generated once per new session; all explicitly constructible and persisted. |
| `SearchOptions` | `limit`, `cursor` | Requested count defaults to 20; explicit continuation binds the account and exact query while preserving search session/ranking state. |
| `PeopleListOptions` | `query`, `cursor` | Optional name filter and scoped continuation; Instagram chooses the page size. Only recorded filter/pagination forms are supported. |
| `MutualFollowersOptions` | `limit`, `module` | Requested preview count 12 and `profile_social_context`; no automatic continuation. |
| `ProfileOptions` | `module`, `entry_point`, `push_disabled` | Captured profile entry context; `serp_users`, `search_navigate_to_user`, and `True`. |
| `ContentOptions` | `limit`, `cursor`, `variables` | Requested grid count 9, no cursor, and no feature overrides. Pagination preserves account/target/feature context. |
| `ReelsOptions` | `limit`, `cursor`, `sort_by_views`, `include_feed_video`, `chunk_size`, `variables`, `parameters` | Provider-default count (`None`), no cursor, `False`, `True`, chunk size 6, empty override mappings. `variables` controls top-level GraphQL flags; `parameters` controls permitted nested data fields. |
| `TaggedOptions` | `limit`, `cursor`, `parameters` | Requested count 12, no cursor, and empty query overrides. Count/cursor remain owned by typed inputs. |
| `RepostsOptions` | `cursor`, `prepend_author_ids`, `variables` | No cursor; omitted authors use the profile's target ID; empty feature overrides. The recorded operation exposes no count parameter. |
| `MetadataOptions` | `parameters` | Explicit story/highlight query overrides; empty by default. |
| `HighlightItemsOptions` | `reason`, `source`, `batch_size`, `exclude_media_ids`, `supported_capabilities_new` | `on_tap`, `profile`, 1, empty exclusions, and omitted optional capability metadata. Always requests the single supplied highlight ID. |
| `RelatedOptions` | `username`, `parameters` | Optional target username and profile recommendation context overrides. |
| `AboutDecodeOptions` | `max_bundle_bytes`, `max_nodes`, `max_depth` | 16 MiB, 100,000 nodes, 64 levels; inert embedded JSON decoding only. |
| `AboutOptions` | `referer_type`, `theme`, `design_system`, `android_os_api_level`, `android_device_performance_class`, `gpu_memory_mb` | Recorded presentation defaults: `ProfileMore`, `three_neutral_gray`, `XMDS`, 33, 0, and 32768. Target and session identity remain client-owned. |
| `ActionOptions` | `container_module`, `radio_type`, `parameters` | `profile`, `wifi-none`, empty overrides. Unfollow, unblock, request approval/decline, and follower removal use recorded operation-specific defaults when options are omitted. |
| `MuteOptions` | `content`, `container_module`, `parameters`, `fbid_v2`, `map_entry_point` | `posts`, `media_mute_sheet`, empty overrides, `None`, `PROFILE`. Other categories: `stories`, `notes`, `activity`, `map_notes`, `map_location`; location requires the target's `fbid_v2`. |
| `CloseFriendsOptions` | `module`, `source`, `parameters` | `audience_selection`, `settings`, empty overrides; changes one member. |
| `FavoritesOptions` | `module`, `block_on_empty_thread_creation`, `parameters` | `following_sheet`, `False`, empty overrides. Omitted options on removal use `favorites_management`. |
| `CloseFriendsListOptions` | `page_size` | Requested count 40; no established continuation input. |
| `FollowRequestsListOptions` | `entry_point`, `expand_top_category` | `self_follower_follow_request_row`, `False`; current-account incoming requests only. |
| `MutedListOptions` | `styles_id`, `theme`, `design_system` | `instagram`, `three_neutral_gray`, `XMDS`; muted-screen rendering metadata. |
| `MutedDecodeOptions` | `max_decoded_bytes`, `max_nodes`, `max_depth`, `max_embedded_payloads` | 16 MiB, 100,000, 64, 64; cumulative limits for inert typed muted-account decoding. |
| `NotificationOptions` | `content`, `mode`, `parameters` | Required content and mode; empty overrides. See the relationship table above. |
| `BlockOptions` | `auto_block`, `surface`, `container_module`, `parameters` | `True`, `profile`, `profile`, empty overrides. App capture uses associated-account blocking; live SDK checks also accepted `False`. |
| Posts | `PostOptions`, `CarouselItem`, `ReelOptions`, `StoryOptions`, `EditOptions`, `HighlightChanges` | See [Publish and manage your posts](#publish-and-manage-your-posts). |
| Comments | `CommentListOptions` | `sort` and `cursor`; see [Comments](#comments). |
| Operation | `account.me(edit=True)` | Controls the account endpoint's `edit` query parameter. |
| Injection | `Instagram(..., transport=...)` | Supply an HTTPX async transport for tests or a custom transport implementation. |

Authentication metadata dictionaries are deep-copied at client construction;
changing the original dictionaries afterwards does not change an active client.
Overrides are top-level replacements, including nested objects. Credentials,
account access context, device identity, and fresh server input IDs cannot be
replaced through these dictionaries. Pass credentials to `auth.login`, configure
identity through `Session`, and let the server issue fresh context. Protocol
endpoints, encryption rules, TLS verification, and redirect rejection are not
arbitrary configuration switches.

## Account request policy

These defaults are local operating limits, **not Instagram-approved quotas**.
They can reduce accidental bursts and repeated login attempts, but do not make
private API use safe from restrictions.

Every admission counts, including a dispatched request that later fails. Budget
exhaustion raises `RequestBudgetExceeded` before network I/O; its `.budget` and
`.retry_after` identify what needs to wait. Login budgets count whole explicit
login attempts, while the request budget also counts their individual HTTP calls.
Changing budget windows does not reconstruct history already discarded under a
shorter window; keep a consistent policy for each account.

With a file-backed client, budgets are checkpointed before dispatch. Challenges
and restrictions are checkpointed when observed. The first failing operation
raises its specific error; subsequent operations raise `AccountPaused`. This
pause persists across process restarts. Resolve verification in Instagram, then
explicitly call `await instagram.resume_after_review()`. That clears only the
local pause, preserving rate limits and budgets. It does not clear a server ban.

One file-backed client owns one account session at a time. A second owner raises
`SessionInUse`. The OS releases the lock after a process exits; leave the empty
`.lock` sidecar in place. This ownership mechanism requires POSIX file locking
(macOS/Linux). It does not coordinate multiple hosts or copied session files.
Share a single active client among tasks for one account, and use separate clients
for separate accounts. In-flight requests may already have reached Instagram
when another request triggers a pause; the default single connection minimizes
that overlap. The plain in-memory constructor leaves ownership and persistence
to its caller.

## Respect rate limits

An HTTP 429 raises `RateLimited` immediately. The client stores the server's
`Retry-After` deadline and refuses subsequent requests until it expires, without
sending them or retrying the original operation. The deadline survives saving
and loading a session. If the server omits a usable hint, the local fallback is
the configured `fallback_cooldown` (60 seconds by default); that is a local
policy, not an Instagram allowance.

```python
from async_instalib import RateLimited

try:
    me = await instagram.account.me()
except RateLimited as error:
    # Schedule a later attempt explicitly; do not loop or repeat password login.
    print("Wait before retrying:", error.retry_after)
```

For non-HTTP application errors, the server may omit a wait hint, so
`retry_after` can be `None`. In-flight concurrent requests cannot be recalled
when a rate limit arrives; `max_connections=1` serializes requests. Session-file locking prevents concurrent owners of one file on macOS/Linux.
Separate files or hosts do not share budgets, even if they contain the same account.

## Headers and authentication evidence

| Category | Handling | Evidence |
| --- | --- | --- |
| App identity | Versioned `ClientProfile`: user agent, app ID, capabilities, Bloks version, locale. | Recorded Android requests; device presets from published Instagram app user agents. |
| Device identity | Persistent UUID, Android ID, family ID; preserved on session load. | Recorded header roles; fresh client-generated values for a new session. |
| Authentication | Bearer token, account ID, scoped cookies. | Recorded successful login and authenticated requests. |
| Routing state | `x-mid`, `ig-u-rur`, claim and recognized server updates. | Recorded requests and response headers. |
| Per-request metadata | Fresh client timestamps and client session ID. | Recorded field roles; newly generated values. |
| CAA state | Fresh account-access context and literal server parameters from the login screen. | Recorded app sequence and live unauthenticated Python preflight. |
| Attestation | Server nonce plus the observed unavailable-keystore response (`-1013`). | Recorded ARM64 emulator login; no hardware signature is fabricated. |

The SDK does not replay app connection telemetry, cached account tokens, vetted
device nonces, or captured device signatures. It reports no Android SIM
subscriptions. Whether these omitted signals matter for other accounts remains
unverified.

CAA responses contain data embedded in Bloks expressions. The parser reads the
needed literal maps and JSON strings; it does not execute expressions. Embedded
numeric HTTP header values are normalized to strings. Unknown layouts fail
explicitly instead of guessing a login continuation.

Password envelopes use fresh AES-GCM encryption with the server's RSA public key.
The `qe/sync/` key bootstrap was observed returning HTTP 405 with valid key
headers; that exception is confined to this endpoint. The initial legacy protocol
was cross-checked against
[instagrapi's authentication implementation](https://github.com/subzeroid/instagrapi/blob/master/instagrapi/mixins/auth.py)
and [password envelope implementation](https://github.com/subzeroid/instagrapi/blob/master/instagrapi/mixins/password.py).
The current CAA implementation uses the subsequently recorded app login as its
primary evidence, including server-issued form IDs.

## Failures and current limits

All expected SDK failures derive from `InstagramError`, with safe diagnostic
context in `.info`. HTTP and JSON failures, expired sessions, rejected legacy
credentials, account challenges, restrictions, and rate limits have distinct
errors. `RateLimited.retry_after` preserves the server's wait hint. Network
failures are not retried because the server may already have received the request.

CAA verification branches, including CAA two-factor, approval notifications, and
recovery screens, are not implemented. They currently raise
`LoginFlowUnsupported`; complete account verification in the official app.
`verify_two_factor()` applies only to an explicit legacy login continuation.

Person, post, comment, direct-message, and trigger coverage is limited to the
operations and evidence listed above.
Global outgoing-request enumeration remains unimplemented. Settings searches and
the Following and invites screen did not expose it, and bounded APK analysis found
no established list request. With a pending request present, the inspected own
following list returned no rows and did not expose that pending request. Per-person pending state, request creation, and
cancellation are supported. This is a limit of the observed contracts, not proof
that Instagram never exposes an outgoing list elsewhere.
Account-owned list continuation requires a populated continuation fixture; the
controlled lists returned complete small responses. Full mutual-follower
enumeration also remains unestablished beyond the captured preview. Neither is
implemented by guessing a cursor or treating a navigation button as pagination.
The About screen does not
provide captured machine fields for joined dates or username history.
Capturing an endpoint does not establish its full contract. Instagram's private
API and versioned app metadata can change.

GraphQL requests send the persisted query IDs (`client_doc_id`) that Instagram
452.1.0.53.64 ships in its APK. They are the same for every account: an ID is the
operation name's hash followed by its query text's hash, so Meta replaces it only when
it edits that query. An ID can stop working once Instagram retires old app versions;
that request then raises `ProtocolError`. When the library moves to a newer app
version, the lab's `check_doc_ids.py` lists the IDs missing from the new APK.

## Structure

```text
src/async_instalib/
  client.py                 public client, file ownership, lifecycle
  cli.py                    interactive credential input, commands, and `watch`
  config/                   profile, networking, auth, request-policy settings
  auth/                     login orchestration, CAA, Bloks parsing, encryption
  transport/                HTTP exchange, response translation, admission guard
  session/                  identity/state, persistence, process-level file lease
  resources/                account, people, lists, profiles, content, relationships,
                            posts, media uploads and actions, highlights, places,
                            comments and comment settings, direct messages
  models/                   typed responses and compatibility exports
  triggers/                 event sources, delivery state, webhooks, the polling runner
  exceptions.py             public failures
examples/configured_client.py
examples/people.py           explicit person reads using an existing session
examples/posts.py            publish one photo, reel, or story, optionally deleting it
tests/                      protocol, configuration, policy, and lifecycle tests
```

Root imports such as `from async_instalib import Instagram, Session` remain stable.
Older session files load with defaults for newly added policy state. Private
underscore modules have moved into their owning packages. Configuration objects
are separate from saved credentials: supply the same options when reopening a
session; profile/device values and budget history are persisted with the session.

HTTPX provides async HTTP/2 and decompression; `cryptography` provides encryption.
There is no dependency on the emulator, Frida, or instagrapi at runtime.

## Verification

Run from this directory so the standalone tool configuration is selected:

```sh
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy --config-file pyproject.toml src
uv build
```

Tests use synthetic credentials and local HTTP responses. They cover password
round-trip decryption, fresh CAA context, numeric embedded headers, session
restoration, legacy two-factor continuation, typed failures, redirect rejection,
credential redaction, connection limits, and a real loopback HTTP/cookie exchange.
Person tests cover query/form serialization, typed partial fields, streamed
GraphQL patches, progressive REST failures, and cursor scope validation. Post tests
use synthetic JPEG and MP4 files to check media parsing, upload headers, every
configure and management form, validation before any upload, and typed failures.
Direct tests check every recorded send, upload host and attachment header, message
and thread action, and how `DirectSource` turns messages, reactions, and requests
into events with header-safe IDs. Real
loopback HTTP tests also verify chunked person responses, cookie/auth rotation
between API paths, and stopping queued work after a streamed account error.
No test contacts Instagram.

Live observations on this Mac:

- Importing the recorded Android session and reading `account.me()` succeeded.
- The app's fresh CAA login returned an embedded logged-in user and bearer token.
- Python's CAA nonce, screen, and OAuth preflight requests returned HTTP 200.
- Legacy password login returned HTTP 400 `needs_upgrade`.
- Python CAA password login and the subsequent account verification succeeded
  after fixing numeric embedded-header parsing.
- The CLI reused that saved session without a password prompt and verified the
  account again through the new session-file interface.

Person read and mutation evidence is maintained in the feature table above.
The final controlled-target relationship check matched its initial baseline:
follow/request, block/restrict, mute, Close Friends, and Favorites flags returned
false. Additional map toggles were restored through their recorded UI inverses.
Restoration does not undo notifications already delivered by Instagram.

Postman's installed CLI (1.45.0) lacks the simulation commands required by the
available plugin workflow. Verification therefore uses local protocol tests,
a loopback HTTP server, and the live checks above. Raw HAR files, passwords,
account data, and session tokens remain outside this repository.

## Development

The normal suite uses synthetic data and local HTTP servers. It does not need a
Instagram account. Run these checks before opening a pull request:

```sh
uv sync --locked
uv run pytest -q
uv run ruff check src tests examples
uv run ruff format --check src tests examples
uv run mypy src
uv build
```

CI runs these checks on Linux and macOS with Python 3.11 and 3.13. Historical live
verification is documented above; CI does not verify current private API behavior.

Keep changes focused, include a synthetic regression test for behavior changes,
and update this README when a public method changes. Open a
[bug report or feature request](https://github.com/Anandesh-Sharma/async_instalib/issues/new/choose) with a minimal example,
Python/package versions, and a redacted error. Keep cookies, passwords, tokens,
session files, raw captures and real account data out of issues and pull requests.

## Related library

[async_linkedinlib](https://github.com/Anandesh-Sharma/async_linkedinlib) is the companion async Python client for LinkedIn. Each package is independent.

## License

[MIT](https://github.com/Anandesh-Sharma/async_instalib/blob/main/LICENSE) © 2026 Anandesh Sharma.

## Releases

Publishing a GitHub release runs the complete CI matrix, builds a wheel and source
archive, validates package metadata, and uploads both to PyPI with
[trusted publishing](https://docs.pypi.org/trusted-publishers/). No stored PyPI token
is needed. The publisher is bound to `Anandesh-Sharma/async_instalib`, `release.yml`, and
the GitHub environment `pypi`.

1. Update `project.version` in `pyproject.toml`, run `uv lock`, and commit to `main`.
2. Publish a GitHub release with the matching tag `v<version>` from that commit.
3. Check the [publishing workflow](https://github.com/Anandesh-Sharma/async_instalib/actions/workflows/release.yml) and
   [PyPI project](https://pypi.org/project/async_instalib/).

Tags must match the package version exactly and point to a commit on `main`.
A draft release does not publish. A published prerelease does; use a matching
PEP 440 prerelease version such as `0.3.0rc1`. Re-running a release skips files
already present only after checking their SHA-256 hashes match the build.
