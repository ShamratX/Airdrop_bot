import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "CatIQAirdrop_bot").strip().lstrip("@") or "CatIQAirdrop_bot"
PROJECT_NAME = os.getenv("PROJECT_NAME", "CATIQ").strip() or "CATIQ"
PROJECT_WEBSITE = os.getenv("PROJECT_WEBSITE", "https://catiq.xyz").strip() or "https://catiq.xyz"
BOT_DESCRIPTION = os.getenv(
    "BOT_DESCRIPTION",
    "Official Multi-chain Airdrop Bot for CatIQ. Join now and get free tokens",
).strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@catiqofficial").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
GROUP_USERNAME = os.getenv("GROUP_USERNAME", "@CatIQCommunity").strip()
GROUP_ID = os.getenv("GROUP_ID", "").strip()
# Prefer invite link for Join buttons (more reliable than @username on some clients)
GROUP_INVITE_LINK = os.getenv("GROUP_INVITE_LINK", "").strip()
X_PROFILE_URL = os.getenv("X_PROFILE_URL", "https://x.com/catiqofficial").strip()
X_POST_URL = os.getenv("X_POST_URL", "https://x.com/catiqofficial").strip()
PRESALE_URL = os.getenv("PRESALE_URL", "https://catiq.xyz/").strip()
TGE_DATE = os.getenv("TGE_DATE", "TBA").strip()
REQUIRED_REFERRALS = int(os.getenv("REQUIRED_REFERRALS", "3"))
AIRDROP_REWARD = os.getenv("AIRDROP_REWARD", "200 $CIQ").strip()
REFERRAL_REWARD = os.getenv("REFERRAL_REWARD", "300 $CIQ").strip()

# Optional presale tiers — Phase 4: Presale Buyers [VIP]
TIER_BRONZE_USD = os.getenv("TIER_BRONZE_USD", "20").strip()
TIER_BRONZE_BONUS = os.getenv("TIER_BRONZE_BONUS", "1000").strip()
TIER_SILVER_USD = os.getenv("TIER_SILVER_USD", "50").strip()
TIER_SILVER_BONUS = os.getenv("TIER_SILVER_BONUS", "3000").strip()
TIER_GOLD_USD = os.getenv("TIER_GOLD_USD", "100").strip()
TIER_GOLD_BONUS = os.getenv("TIER_GOLD_BONUS", "7000").strip()
TIER_VIP_USD = os.getenv("TIER_VIP_USD", "200").strip()
TIER_VIP_BONUS = os.getenv("TIER_VIP_BONUS", "15000").strip()
TIER_BONUS_UNIT = os.getenv("TIER_BONUS_UNIT", "$CIQ").strip() or "$CIQ"

_admin_raw = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = {int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()}

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
# Tolerate accidental /rest/v1 suffix from dashboard copy
if SUPABASE_URL.endswith("/rest/v1"):
    SUPABASE_URL = SUPABASE_URL[: -len("/rest/v1")]


def chat_target(preferred_id: str, username: str) -> str | int:
    """Prefer numeric chat id for getChatMember; fall back to @username."""
    if preferred_id and preferred_id.lstrip("-").isdigit():
        return int(preferred_id)
    return username


def group_join_url() -> str:
    """Public URL for the Group button."""
    if GROUP_INVITE_LINK:
        return GROUP_INVITE_LINK
    return f"https://t.me/{GROUP_USERNAME.lstrip('@')}"
