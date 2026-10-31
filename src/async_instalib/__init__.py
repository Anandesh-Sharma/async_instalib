"""Async access to Instagram's mobile API."""

from .auth import TwoFactorMethod  # Select the pending login's verification method.
from .client import Instagram  # Own one account's async resources and connection pool.
from .config import (
    AuthOptions,  # Customize login metadata and supported payload parameters.
    RequestPolicy,  # Bound account work and control response-driven pauses.
)
from .config.about import AboutOptions  # Configure the captured account-information screen request.
from .config.comments import CommentListOptions  # Choose comment sort order and continue pages.
from .config.content import (
    ContentOptions,  # Configure one profile content page.
    MetadataOptions,  # Override story/highlight query parameters.
    ProfileOptions,  # Configure captured profile entry context.
    ReelsOptions,  # Configure one recorded reels-grid page.
    RelatedOptions,  # Configure related-account suggestions.
    RepostsOptions,  # Configure repost metadata pages without playback hydration.
    TaggedOptions,  # Configure tagged-media pages and explicit continuations.
)
from .config.devices import (
    GALAXY_S24,  # Samsung Galaxy S24 (SM-S921U), Android 14.
    GALAXY_S25,  # Samsung Galaxy S25 (SM-S931B), Android 16; the default.
    GALAXY_S25_ULTRA,  # Samsung Galaxy S25 Ultra (SM-S938B), Android 16.
    INFINIX_X6880,  # Infinix X6880, Android 15.
    PIXEL_9_PRO,  # Google Pixel 9 Pro, Android 17.
    AndroidDevice,  # A real phone's fields in the Instagram user agent.
)
from .config.direct import InboxOptions  # Choose how many threads and messages the inbox returns.
from .config.metadata import (
    AboutDecodeOptions,  # Bound inert account-information bundle decoding.
    HighlightItemsOptions,  # Configure one selected highlight item read.
)
from .config.muted import MutedDecodeOptions  # Bound inert muted-account screen decoding.
from .config.people import (
    MutualFollowersOptions,  # Configure the captured mutual-followers preview.
    PeopleListOptions,  # Configure bounded relationship lists.
    SearchOptions,  # Configure one account-search page.
)
from .config.posts import (
    CarouselItem,  # Attach tags and alt text to one carousel photo.
    EditOptions,  # Replace a post's caption and set alt text, location, or added tags.
    HighlightChanges,  # Rename a highlight or add and remove stories.
    PostOptions,  # Configure a feed photo or carousel.
    ReelOptions,  # Configure a reel, including whether it appears in the main grid.
    StoryOptions,  # Configure a story's caption and Close Friends audience.
    UserTag,  # Tag one person at a position on a photo.
)
from .config.relationship_lists import (
    CloseFriendsListOptions,  # Configure the captured member page.
    FollowRequestsListOptions,  # Configure the incoming-request list.
    MutedListOptions,  # Configure the muted-accounts screen envelope.
)
from .config.relationships import (
    ActionOptions,  # Customize explicit relationship request metadata.
    BlockOptions,  # Select block scope and explicit provider metadata.
    CloseFriendsOptions,  # Configure one-member Close Friends edits.
    FavoritesOptions,  # Configure one-member feed Favorites edits.
    MuteOptions,  # Choose one captured content category to mute.
    NotificationOptions,  # Choose content and notification preference.
)
from .exceptions import (
    AccountPaused,  # Require explicit review before further account requests.
    AccountRestricted,  # Surface restrictions without automatic retries.
    APIError,  # Report otherwise unclassified provider failures.
    BadCredentials,  # Report a rejected username or password.
    ChallengeRequired,  # Surface account-owner verification requirements.
    InstagramError,  # Catch all expected SDK failures.
    LoginFlowUnsupported,  # Identify a provider flow not yet established.
    LoginRequired,  # Identify an absent or rejected session.
    NetworkError,  # Report uncertain network outcomes without replaying writes.
    PersonNotFound,  # Identify an unavailable username from the direct profile lookup.
    ProtocolError,  # Identify invalid or unsupported provider responses.
    RateLimited,  # Expose the provider's wait hint.
    RequestBudgetExceeded,  # Report local request or login budget exhaustion.
    SessionInUse,  # Prevent two owners of the same session file.
    TwoFactorRequired,  # Continue a password login with a verification code.
)
from .models import (
    ClientOptions,  # Configure bounded connections, timeouts, and an explicit proxy.
    ClientProfile,  # Configure public mobile app metadata.
    User,  # Read stable account fields and explicit raw provider data.
)
from .models.about import (
    AboutAccount,  # Read named account-information state without executing UI code.
    AboutState,  # Preserve a named state and its initial provider value.
)
from .models.activity import (
    Notification,
)  # Read one activity-feed row: comment, mention, like, follow.
from .models.comments import Comment  # Read one comment or reply with its author and counts.
from .models.content import Media  # Read media identity, caption, and provider metadata.
from .models.direct import (
    Message,  # Read one direct message; pass it to reply, react, edit, or unsend.
    Thread,  # Read one conversation with its newest messages.
)
from .models.muted import MutedPerson  # Read verified account IDs and nullable mute categories.
from .models.pagination import (
    Continuation,  # Bind opaque cursors to their originating request.
    Page,  # Return one explicit result page.
    PageContext,  # Identify the account, operation, target, and query.
)
from .models.people import (
    PersonProfile,  # Read profile details without inventing missing values.
    PersonSummary,  # Read partial person identity and relationship fields.
    Relationship,  # Represent account-relative relationship flags.
)
from .models.posts import (
    Place,  # Tag a location found with posts.places.search().
    Publication,  # Read a publish acknowledgement and its processing status.
)
from .models.stories import (
    Highlight,  # Read one highlight identity and metadata.
    HighlightCover,  # Preserve cover media metadata.
    HighlightImage,  # Read a highlight cover image variant.
    HighlightItems,  # Read one selected highlight's typed media items.
    HighlightTray,  # Read typed highlight tray entries.
    StoryAuthor,  # Preserve partial story author identity without inventing a username.
    StoryCollection,  # Read a typed story response with raw metadata.
    StoryItem,  # Read typed story media and optional timestamps.
    StoryReel,  # Group accessible stories under their owner.
)
from .models.uploads import (
    Audio,  # Send an MP4/M4A recording as a voice message.
    Photo,  # Publish a JPEG unchanged, with its dimensions read from the file.
    Video,  # Publish an MP4 unchanged, with an optional first-frame cover.
)
from .resources.relationships import RelationshipActionResult  # Read status-only acknowledgements.
from .session import (
    Device,  # Persist device identity across client restarts.
    Session,  # Import, save, and restore private session state.
)

__all__ = [
    "Audio",
    "AndroidDevice",
    "GALAXY_S24",
    "GALAXY_S25",
    "GALAXY_S25_ULTRA",
    "INFINIX_X6880",
    "PIXEL_9_PRO",
    "InboxOptions",
    "Message",
    "Thread",
    "Notification",
    "Comment",
    "CommentListOptions",
    "CarouselItem",
    "EditOptions",
    "HighlightChanges",
    "Photo",
    "Place",
    "PostOptions",
    "Publication",
    "ReelOptions",
    "StoryOptions",
    "UserTag",
    "Video",
    "MutedDecodeOptions",
    "MutedPerson",
    "HighlightItemsOptions",
    "HighlightItems",
    "StoryAuthor",
    "MutualFollowersOptions",
    "FollowRequestsListOptions",
    "FavoritesOptions",
    "MutedListOptions",
    "CloseFriendsOptions",
    "CloseFriendsListOptions",
    "AboutDecodeOptions",
    "AboutAccount",
    "AboutState",
    "StoryItem",
    "StoryReel",
    "StoryCollection",
    "HighlightImage",
    "HighlightCover",
    "Highlight",
    "HighlightTray",
    "AboutOptions",
    "TaggedOptions",
    "RepostsOptions",
    "ReelsOptions",
    "BlockOptions",
    "RelatedOptions",
    "ActionOptions",
    "MuteOptions",
    "NotificationOptions",
    "RelationshipActionResult",
    "PersonNotFound",
    "ProfileOptions",
    "ContentOptions",
    "MetadataOptions",
    "Media",
    "SearchOptions",
    "PeopleListOptions",
    "PersonSummary",
    "PersonProfile",
    "Relationship",
    "Page",
    "Continuation",
    "PageContext",
    "AuthOptions",
    "RequestPolicy",
    "AccountPaused",
    "RequestBudgetExceeded",
    "SessionInUse",
    "Instagram",
    "Session",
    "Device",
    "ClientOptions",
    "ClientProfile",
    "User",
    "TwoFactorMethod",
    "InstagramError",
    "APIError",
    "AccountRestricted",
    "BadCredentials",
    "ChallengeRequired",
    "LoginFlowUnsupported",
    "LoginRequired",
    "NetworkError",
    "ProtocolError",
    "RateLimited",
    "TwoFactorRequired",
]
