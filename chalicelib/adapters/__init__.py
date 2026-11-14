from chalicelib.adapters.base import SubscriptionPlatform, UnsupportedPayload
from chalicelib.adapters.recharge import RechargePlatform

SUBSCRIPTION_PLATFORMS: dict[str, SubscriptionPlatform] = {"recharge": RechargePlatform()}

__all__ = ["SUBSCRIPTION_PLATFORMS", "SubscriptionPlatform", "UnsupportedPayload"]
