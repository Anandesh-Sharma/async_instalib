"""Recorded presentation context for the About this account Bloks screen."""

from dataclasses import dataclass
from typing import Any

# Context keys the app sends as null on every recorded Bloks screen request.
_NULL_CONTEXT = (
    "use_cached_templates",
    "ttrc_marker_id",
    "pixel_ratio",
    "bloks_version",
    "perf_debug_overlay_enabled",
    "debug_tooling_metadata_token",
    "is_push_on",
    "is_e2e_test",
    "in_free_mode",
    "surface",
    "ios_gpu_family",
    "extra_data",
    "using_white_navbar",
    "cached_templates_version",
    "ttrc_join_id",
    "debug_overlay_enabled",
    "is_data_savings_mode_active",
    "styles_id",
)


@dataclass(frozen=True, slots=True)
class AboutOptions:
    """Customize entry and rendering metadata without replacing target or identity."""

    referer_type: str = "ProfileMore"
    theme: str = "three_neutral_gray"
    design_system: str = "XMDS"
    android_os_api_level: int = 33
    android_device_performance_class: int = 0
    gpu_memory_mb: int = 32768

    def __post_init__(self) -> None:
        for value in (self.referer_type, self.theme, self.design_system):
            if (
                not isinstance(value, str)
                or not value
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(
                    "About context strings must be nonempty without control characters."
                )
        for count in (self.android_os_api_level, self.gpu_memory_mb):
            if type(count) is not int or count < 1:
                raise ValueError("Android API level and GPU memory must be positive integers.")
        if (
            type(self.android_device_performance_class) is not int
            or self.android_device_performance_class < 0
        ):
            raise ValueError("Android device performance class must be a nonnegative integer.")

    def bloks_context(self) -> dict[str, Any]:
        """The recorded ``bk_context``; comment settings screens send the same values."""
        return {
            **dict.fromkeys(_NULL_CONTEXT),
            "theme_params": [
                {
                    "user_selected_theme": None,
                    "value": [self.theme],
                    "design_system_name": self.design_system,
                }
            ],
            "js_cache_contents": [],
            "is_flipper_enabled": False,
            "fblite_unity_properties": [],
            "blacklisted_styles": [],
            "gpu_memory_mb": self.gpu_memory_mb,
            "styles": [],
            "android_device_performance_class": self.android_device_performance_class,
            "android_os_api_level": self.android_os_api_level,
        }
