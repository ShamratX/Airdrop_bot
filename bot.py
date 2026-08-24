"""
X Bot — Free airdrop (steps 1–4 mandatory) + optional presale bonus (step 5).
All user / TX data is stored in CSV under ./data/
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    Update,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
from storage import (
    acreate_user,
    aget_user,
    aincrement_referral,
    aupdate_user,
    create_user,
    export_paths,
    flag,
    get_user,
    increment_referral,
    save_presale_proof,
    update_user,
)
from supabase_store import insert_buy_proof, upsert_airdrop_claim

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("xbot")

# Conversation states for optional step 5
WAIT_WALLET, WAIT_TXID = range(2)

# Callback data
CB_X_DONE = "x_done"
CB_RT_DONE = "rt_done"
CB_LIKE_DONE = "like_done"
CB_X_WAIT = "x_wait"
CB_REFERRAL = "referral"
CB_BUY = "buy"
CB_OPEN_WALLET = "open_wallet"
CB_SKIP_STEP5 = "skip_step5"
CB_TIER_BRONZE = "tier_bronze"
CB_TIER_SILVER = "tier_silver"
CB_TIER_GOLD = "tier_gold"
CB_TIER_VIP = "tier_vip"
CB_MENU = "menu"
CB_REFRESH = "refresh_status"

# After Follow / Retweet / Like click — wait long enough to finish on X, then hide button
X_FOLLOW_DELAY_SEC = 15.0
X_RETWEET_DELAY_SEC = 18.0
X_LIKE_DELAY_SEC = 12.0

WALLET_RE = re.compile(r"^(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")
TXID_RE = re.compile(r"^(0x)?[a-fA-F0-9]{64}$")

MEMBER_OK = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
}

# Soft capacity target: ~300 concurrent users
MAX_CONCURRENT_UPDATES = 300
HTTP_POOL_SIZE = 64
DB_THREAD_WORKERS = 64
# Cache Telegram membership checks (getChatMember is expensive under load)
_MEMBER_CACHE: dict[int, tuple[float, bool, bool]] = {}
_MEMBER_CACHE_TTL_SEC = 75.0


def _progress(user: dict) -> str:
    checks = [
        ("Follow X", flag(user.get("x_done"))),
        ("Live Retweet", flag(user.get("rt_done"))),
        ("Like X Pin", flag(user.get("like_done"))),
        ("Channel joined", flag(user.get("channel_joined"))),
        ("Group joined", flag(user.get("group_joined"))),
        (
            "👥 Invite friends",
            flag(user.get("referrals_done")),
        ),
    ]
    lines = ["📌 Progress:"]
    for label, done in checks:
        lines.append(f"{'✅' if done else '✓'} {label}")
    return "\n".join(lines)


def _airdrop_ready(user: dict) -> bool:
    """Core tasks only — referral is optional bonus (wallet unlocks without it)."""
    return all(
        flag(user.get(k))
        for k in (
            "channel_joined",
            "group_joined",
            "x_done",
            "rt_done",
            "like_done",
        )
    )


async def sync_telegram_joins(
    bot, user_id: int, *, force: bool = False, user: dict | None = None
) -> dict[str, str] | None:
    """Upgrade channel/group flags when user is already a member (no spam messages)."""
    # Returning users who already passed both joins — skip TG API on /start
    if (
        not force
        and user
        and flag(user.get("channel_joined"))
        and flag(user.get("group_joined"))
    ):
        return None

    now = time.monotonic()
    cached = _MEMBER_CACHE.get(user_id)
    if (
        not force
        and cached
        and (now - cached[0]) < _MEMBER_CACHE_TTL_SEC
    ):
        ch_ok, gr_ok = cached[1], cached[2]
    else:
        ch = config.chat_target(config.CHANNEL_ID, config.CHANNEL_USERNAME)
        gr = config.chat_target(config.GROUP_ID, config.GROUP_USERNAME)
        ch_ok, gr_ok = await asyncio.gather(
            _is_member(bot, ch, user_id),
            _is_member(bot, gr, user_id),
        )
        _MEMBER_CACHE[user_id] = (now, ch_ok, gr_ok)
        if len(_MEMBER_CACHE) > 4000:
            _MEMBER_CACHE.clear()

    kwargs: dict[str, str] = {}
    if ch_ok:
        kwargs["channel_joined"] = "1"
    if gr_ok:
        kwargs["group_joined"] = "1"
    # On forced refresh, clear flags if user left
    if force:
        if not ch_ok:
            kwargs["channel_joined"] = "0"
        if not gr_ok:
            kwargs["group_joined"] = "0"
    if kwargs:
        return await aupdate_user(user_id, **kwargs)
    return None


async def _is_member(bot, chat_id, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in MEMBER_OK
    except (BadRequest, Forbidden) as exc:
        logger.warning("Membership check failed for %s: %s", chat_id, exc)
        return False


def main_keyboard(
    user: dict, pending: set[str] | None = None
) -> InlineKeyboardMarkup:
    pending = pending or set()
    site = config.PROJECT_WEBSITE.rstrip("/")
    channel_url = f"https://t.me/{config.CHANNEL_USERNAME.lstrip('@')}"
    rows: list[list[InlineKeyboardButton]] = []

    # Tasks stay visible: incomplete 🔹 / completed ✅ (X opens link on one tap)
    x_row: list[InlineKeyboardButton] = []
    if flag(user.get("x_done")):
        x_row.append(InlineKeyboardButton("✅ Followed X", url=config.X_PROFILE_URL))
    else:
        x_row.append(InlineKeyboardButton("🔹 Follow X", url=config.X_PROFILE_URL))
    if flag(user.get("rt_done")):
        x_row.append(InlineKeyboardButton("✅ Retweeted", url=config.X_POST_URL))
    else:
        x_row.append(InlineKeyboardButton("🔹 Retweet", url=config.X_POST_URL))
    rows.append(x_row)

    if flag(user.get("like_done")):
        rows.append(
            [InlineKeyboardButton("✅ Liked X Pin", url=config.X_POST_URL)]
        )
    else:
        rows.append(
            [InlineKeyboardButton("🔹 Like X Pin", url=config.X_POST_URL)]
        )

    cg_row: list[InlineKeyboardButton] = []
    if flag(user.get("channel_joined")):
        cg_row.append(InlineKeyboardButton("✅ Joined Channel", url=channel_url))
    else:
        cg_row.append(InlineKeyboardButton("🔹 Channel", url=channel_url))
    if flag(user.get("group_joined")):
        cg_row.append(
            InlineKeyboardButton("✅ Joined Group", url=config.group_join_url())
        )
    else:
        cg_row.append(
            InlineKeyboardButton("🔹 Group", url=config.group_join_url())
        )
    rows.append(cg_row)

    if flag(user.get("referrals_done")):
        rows.append(
            [InlineKeyboardButton("✅ Invite 3 Friends", callback_data=CB_REFERRAL)]
        )
    else:
        rows.append(
            [InlineKeyboardButton("🔹 Invite 3 Friends", callback_data=CB_REFERRAL)]
        )

    rows.append(
        [InlineKeyboardButton("👇 Submit Wallet After Finished all the tasks", callback_data=CB_OPEN_WALLET)]
    )
    rows.append([InlineKeyboardButton("🛒 Buy", callback_data=CB_BUY)])
    rows.append([InlineKeyboardButton("🔹 Website", url=site)])
    rows.append([InlineKeyboardButton("🔄 Refresh status", callback_data=CB_REFRESH)])
    return InlineKeyboardMarkup(rows)


def back_to_start_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("⬅️ Back", callback_data=CB_MENU)]


def referral_link_for(bot_username: str, user_id: int) -> str:
    return f"https://t.me/{bot_username}?start=ref_{user_id}"


def referral_keyboard(link: str, user: dict | None = None) -> InlineKeyboardMarkup:
    share = (
        "https://t.me/share/url?url="
        + quote(link, safe="")
        + "&text="
        + quote("Join CatIQ ($CIQ) airdrop 🐾", safe="")
    )
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "📋 Copy link",
                api_kwargs={"copy_text": {"text": link}},
            ),
            InlineKeyboardButton("📤 Share", url=share),
        ],
        [InlineKeyboardButton("🔄 Refresh", callback_data=CB_REFERRAL)],
        back_to_start_row(),
    ]
    return InlineKeyboardMarkup(rows)


def buy_page_text() -> str:
    return (
        "🛒 Buy CatIQ ($CIQ)\n\n"
        "💎 Phase 4: Presale Buyers [VIP]\n\n"
        f"• {_tier_line(config.TIER_BRONZE_USD, config.TIER_BRONZE_BONUS)}\n"
        f"• {_tier_line(config.TIER_SILVER_USD, config.TIER_SILVER_BONUS)}\n"
        f"• {_tier_line(config.TIER_GOLD_USD, config.TIER_GOLD_BONUS)}\n"
        f"• {_tier_line(config.TIER_VIP_USD, config.TIER_VIP_BONUS)}\n\n"
        "How to buy:\n\n"
        "1. Tap to Buy\n"
        "2. Complete purchase on the website\n"
    )


def buy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒 Buy", url=config.PRESALE_URL)],
            [InlineKeyboardButton("🔄 Refresh", callback_data=CB_BUY)],
            back_to_start_row(),
        ]
    )


def welcome_text(user: dict, bot_username: str, first_name: str | None = None) -> str:
    name = (first_name or user.get("first_name") or "").strip()
    hi = f"Hi {name}!\n" if name else ""

    return (
        f"{hi}"
        f"Welcome to CatIQ ($CIQ) 🐾\n\n"
        f"Tap below to complete tasks and claim free $CIQ.\n\n"
        f"Submit all tasks and get more than $15 of $CIQ tokens.\n\n"
        f"Complete the tasks and Refresh status.\n\n"
        f"{_progress(user)}"
    )


def _tier_line(usd: str, bonus: str) -> str:
    return f"Buy ${usd}+ get free {bonus} {config.TIER_BONUS_UNIT}"


def _tier_buttons() -> list[list[InlineKeyboardButton]]:
    return [
        [
            InlineKeyboardButton(
                _tier_line(config.TIER_BRONZE_USD, config.TIER_BRONZE_BONUS),
                callback_data=CB_TIER_BRONZE,
            )
        ],
        [
            InlineKeyboardButton(
                _tier_line(config.TIER_SILVER_USD, config.TIER_SILVER_BONUS),
                callback_data=CB_TIER_SILVER,
            )
        ],
        [
            InlineKeyboardButton(
                _tier_line(config.TIER_GOLD_USD, config.TIER_GOLD_BONUS),
                callback_data=CB_TIER_GOLD,
            )
        ],
        [
            InlineKeyboardButton(
                _tier_line(config.TIER_VIP_USD, config.TIER_VIP_BONUS),
                callback_data=CB_TIER_VIP,
            )
        ],
    ]


def _presale_tiers_text() -> str:
    return (
        "💎 Phase 4: Presale Buyers [VIP]\n\n"
        f"• {_tier_line(config.TIER_BRONZE_USD, config.TIER_BRONZE_BONUS)}\n"
        f"• {_tier_line(config.TIER_SILVER_USD, config.TIER_SILVER_BONUS)}\n"
        f"• {_tier_line(config.TIER_GOLD_USD, config.TIER_GOLD_BONUS)}\n"
        f"• {_tier_line(config.TIER_VIP_USD, config.TIER_VIP_BONUS)}\n\n"
        "Proof required:\n"
        "1) Wallet address\n"
        "2) Transaction Hash (TXID)\n"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    tg_user = update.effective_user
    referrer_id = None
    if context.args:
        raw = context.args[0]
        if raw.startswith("ref_") and raw[4:].isdigit():
            candidate = int(raw[4:])
            if candidate != tg_user.id and await aget_user(candidate):
                referrer_id = str(candidate)

    user = await aget_user(tg_user.id)
    is_new = user is None
    if is_new:
        user = await acreate_user(
            tg_user.id,
            tg_user.username,
            tg_user.first_name,
            referrer_id=referrer_id,
        )
        if referrer_id:
            ref = await aincrement_referral(referrer_id, config.REQUIRED_REFERRALS)
            if ref:
                count = int(ref.get("referral_count") or 0)
                wallet = (ref.get("wallet_address") or "").strip()
                if wallet:
                    await asyncio.to_thread(
                        lambda: upsert_airdrop_claim(
                            int(referrer_id),
                            wallet,
                            referral_count=count,
                        )
                    )
                try:
                    if flag(ref.get("referrals_done")):
                        msg = (
                            f"🎉 New referral! Total: {count}/"
                            f"{config.REQUIRED_REFERRALS}\n"
                            f"✅ Bonus target hit — +{config.REFERRAL_REWARD} each counted friend.\n"
                            "Supabase payout amount updated if wallet was saved."
                        )
                    else:
                        msg = (
                            f"➕ New referral! {count}/{config.REQUIRED_REFERRALS}\n"
                            f"Bonus so far: counted friends × {config.REFERRAL_REWARD}"
                        )
                    await context.bot.send_message(
                        chat_id=int(referrer_id),
                        text=msg,
                    )
                except Exception:
                    pass
    else:
        # Skip DB write when profile unchanged (same UX, faster /start)
        un = tg_user.username or ""
        fn = tg_user.first_name or ""
        if (user.get("username") or "") != un or (user.get("first_name") or "") != fn:
            user = (
                await aupdate_user(
                    tg_user.id,
                    username=un,
                    first_name=fn,
                )
                or user
            )

    synced = await sync_telegram_joins(context.bot, tg_user.id, user=user)
    if synced:
        user = synced

    # Remove any leftover reply keyboard, then show welcome + task buttons
    await update.message.reply_text(
        welcome_text(user, config.BOT_USERNAME, tg_user.first_name),
        reply_markup=main_keyboard(user),
        disable_web_page_preview=True,
    )


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    try:
        await query.answer()
    except BadRequest:
        pass
    # No membership API here — keeps Back / menu buttons fast (check on Refresh only)
    user = await aget_user(update.effective_user.id)
    if not user:
        user = await acreate_user(
            update.effective_user.id,
            update.effective_user.username,
            update.effective_user.first_name,
        )
    bot_username = config.BOT_USERNAME or "bot"
    text = welcome_text(user, bot_username, update.effective_user.first_name)
    markup = main_keyboard(user, pending=_x_pending(context))
    try:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        # Old message / same content — send a fresh menu so Group URL updates
        if "not modified" in str(exc).lower():
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )
        else:
            raise


async def refresh_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sync channel/group; confirm X tasks after user returns from links → show ✅."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    try:
        await query.answer("Updating…")
    except BadRequest:
        pass
    uid = update.effective_user.id
    synced = await sync_telegram_joins(context.bot, uid, force=True)
    user = synced or await aget_user(uid)
    if not user:
        user = await acreate_user(
            uid,
            update.effective_user.username,
            update.effective_user.first_name,
        )
    # Blind-trust confirm for X — skip write if already set (same end state)
    if not (
        flag(user.get("x_done"))
        and flag(user.get("rt_done"))
        and flag(user.get("like_done"))
    ):
        user = (
            await aupdate_user(uid, x_done="1", rt_done="1", like_done="1") or user
        )
    if _airdrop_ready(user) and not flag(user.get("airdrop_complete")):
        user = await aupdate_user(uid, airdrop_complete="1") or user
    bot_username = config.BOT_USERNAME or "bot"
    text = welcome_text(user, bot_username, update.effective_user.first_name)
    markup = main_keyboard(user)
    try:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )
        else:
            raise


def _x_pending(context: ContextTypes.DEFAULT_TYPE) -> set[str]:
    return set(context.user_data.get("x_pending_flags") or [])


def _x_pending_add(context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    s = _x_pending(context)
    s.add(key)
    context.user_data["x_pending_flags"] = list(s)


def _x_pending_remove(context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    s = _x_pending(context)
    s.discard(key)
    context.user_data["x_pending_flags"] = list(s)


async def _edit_home_menu(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    message_id: int,
    user_id: int,
    first_name: str | None,
    pending: set[str] | None = None,
) -> None:
    user = await aget_user(user_id)
    if not user:
        return
    text = welcome_text(user, config.BOT_USERNAME, first_name)
    markup = main_keyboard(user, pending=pending)
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.warning("edit home menu failed: %s", exc)


async def _x_task_click(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    flag_key: str,
    open_url: str,
    delay_sec: float,
) -> None:
    """Arm task → button stays openable, then after delay mark done (button hides)."""
    query = update.callback_query
    if not query or not update.effective_user or not query.message:
        return
    uid = update.effective_user.id
    user = await aget_user(uid)
    if not user:
        await query.answer("Please /start first", show_alert=True)
        return
    if flag(user.get(flag_key)):
        await query.answer("Already done ✅")
        return
    if flag_key in _x_pending(context):
        await query.answer("Wait a moment…")
        return

    _x_pending_add(context, flag_key)
    chat_id = query.message.chat_id
    message_id = query.message.message_id
    first_name = update.effective_user.first_name

    # Some Telegram clients open external URL from callback answer
    try:
        await query.answer(url=open_url)
    except BadRequest:
        try:
            await query.answer("Tap again to open — finishes in a few sec")
        except BadRequest:
            pass

    # Button switches to URL (⏳) so user can open / finish on X; then hides
    await _edit_home_menu(
        context,
        chat_id=chat_id,
        message_id=message_id,
        user_id=uid,
        first_name=first_name,
        pending=_x_pending(context),
    )

    async def _finish() -> None:
        try:
            await asyncio.sleep(delay_sec)
            await aupdate_user(uid, **{flag_key: "1"})
            await amark_airdrop_complete(uid)
            _x_pending_remove(context, flag_key)
            await _edit_home_menu(
                context,
                chat_id=chat_id,
                message_id=message_id,
                user_id=uid,
                first_name=first_name,
                pending=_x_pending(context),
            )
        except Exception:
            _x_pending_remove(context, flag_key)
            logger.exception("X task confirm failed (%s)", flag_key)

    context.application.create_task(_finish())


async def x_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _x_task_click(
        update,
        context,
        flag_key="x_done",
        open_url=config.X_PROFILE_URL,
        delay_sec=X_FOLLOW_DELAY_SEC,
    )


async def rt_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _x_task_click(
        update,
        context,
        flag_key="rt_done",
        open_url=config.X_POST_URL,
        delay_sec=X_RETWEET_DELAY_SEC,
    )


async def like_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _x_task_click(
        update,
        context,
        flag_key="like_done",
        open_url=config.X_POST_URL,
        delay_sec=X_LIKE_DELAY_SEC,
    )


async def x_wait(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        try:
            await query.answer("Almost done…")
        except BadRequest:
            pass


async def buy_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    user = await aget_user(update.effective_user.id)
    if not user:
        try:
            await query.answer("Please /start first", show_alert=True)
        except BadRequest:
            pass
        return
    try:
        await query.answer()
    except BadRequest:
        pass

    text = buy_page_text()
    markup = buy_keyboard()
    try:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def referral_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    user = await aget_user(update.effective_user.id)
    if not user:
        try:
            await query.answer("Please /start first", show_alert=True)
        except BadRequest:
            pass
        return
    try:
        await query.answer()
    except BadRequest:
        pass
    bot_username = config.BOT_USERNAME
    link = referral_link_for(bot_username, update.effective_user.id)
    count = int(user.get("referral_count") or 0)
    need = config.REQUIRED_REFERRALS
    done = flag(user.get("referrals_done")) or count >= need

    if done:
        text = (
            f"👥 Invite friends (bonus)\n\n"
            f"Target: Invite {need} friends\n"
            f"Reward: {config.REFERRAL_REWARD} per friend who opens the bot\n\n"
            f"✅ Bonus complete!"
        )
        rows: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton("🔄 Refresh", callback_data=CB_REFERRAL)],
            back_to_start_row(),
        ]
        markup = InlineKeyboardMarkup(rows)
    else:
        text = (
            f"👥 Invite friends (bonus)\n\n"
            f"Target: Invite {need} friends\n"
            f"Reward: {config.REFERRAL_REWARD} per friend who opens the bot\n\n"
            f"Only friends who tap your link + Start count.\n\n"
            f"How to invite:\n\n"
            f"1. Tap Copy link (or Share)\n"
            f"2. Send to friends\n"
            f"3. When they Start, bonus updates (even later)"
        )
        markup = referral_keyboard(link, user)

    try:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


def step5_offer_text() -> str:
    return "⬇️ Send your MetaMask wallet address now:"


async def _live_telegram_ok(
    bot, user_id: int
) -> tuple[bool, str | None]:
    """Re-check channel + group membership. Returns (ok, error_toast)."""
    # Bypass short cache — wallet submit must be fresh
    _MEMBER_CACHE.pop(user_id, None)
    ch = config.chat_target(config.CHANNEL_ID, config.CHANNEL_USERNAME)
    gr = config.chat_target(config.GROUP_ID, config.GROUP_USERNAME)
    ch_ok, gr_ok = await asyncio.gather(
        _is_member(bot, ch, user_id),
        _is_member(bot, gr, user_id),
    )
    _MEMBER_CACHE[user_id] = (time.monotonic(), ch_ok, gr_ok)
    if not ch_ok:
        await aupdate_user(user_id, channel_joined="0")
        return False, "Join the channel again — then submit wallet."
    if not gr_ok:
        await aupdate_user(user_id, group_joined="0")
        return False, "Join the group again — then submit wallet."
    return True, None


async def open_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not update.effective_user:
        return ConversationHandler.END

    user = await aget_user(update.effective_user.id)
    if not user or not _airdrop_ready(user):
        try:
            await query.answer(
                "First finish your tasks then click and submit wallet address.",
                show_alert=True,
            )
        except BadRequest:
            pass
        return ConversationHandler.END

    live_ok, err = await _live_telegram_ok(context.bot, update.effective_user.id)
    if not live_ok:
        try:
            await query.answer(
                "First finish your tasks then click and submit wallet address.",
                show_alert=True,
            )
        except BadRequest:
            pass
        return ConversationHandler.END

    # Always allow opening submit flow; duplicate check happens after they send address
    try:
        await query.answer()
    except BadRequest:
        pass

    await query.edit_message_text(
        "⬇️ Send your MetaMask wallet address now:",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([back_to_start_row()]),
    )
    return WAIT_WALLET


async def skip_step5(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not update.effective_user:
        return ConversationHandler.END
    user = await aget_user(update.effective_user.id)
    if not user or not _airdrop_ready(user):
        await query.answer(
            "❌ Finish all tasks first. Wallet unlocks only after full verify.",
            show_alert=True,
        )
        await show_menu(update, context)
        return ConversationHandler.END
    live_ok, err = await _live_telegram_ok(context.bot, update.effective_user.id)
    if not live_ok:
        await query.answer(err or "Verify channel/group first", show_alert=True)
        await show_menu(update, context)
        return ConversationHandler.END

    user = await aupdate_user(update.effective_user.id, step5_skipped="1") or user
    await query.answer("Step 5 skipped — optional offer closed.", show_alert=True)
    await query.edit_message_text(
        welcome_text(user or {}, config.BOT_USERNAME)
        + "\n\n✅ Airdrop registration complete (optional offer skipped).",
        reply_markup=main_keyboard(user or {}),
        disable_web_page_preview=True,
    )
    return ConversationHandler.END


def wallet_claim_message(wallet: str) -> str:
    site = config.PROJECT_WEBSITE.rstrip("/")
    return (
        f"✅ Wallet saved: {wallet}\n\n"
        f"When presale ends, claim your tokens on: {site}\n"
        f"Stay on our Channel, X & Group for updates.\n"
        f"Thanks! 🐾"
    )


async def receive_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return WAIT_WALLET
    user = await aget_user(update.effective_user.id)
    if not user or not _airdrop_ready(user):
        await update.message.reply_text("Finish required tasks first. /start")
        return ConversationHandler.END

    # One Telegram ID → one airdrop wallet only
    if (user.get("wallet_address") or "").strip():
        await update.message.reply_text(
            "This wallet has been submitted.",
            reply_markup=InlineKeyboardMarkup([back_to_start_row()]),
            disable_web_page_preview=True,
        )
        return ConversationHandler.END

    wallet = (update.message.text or "").strip()
    if not WALLET_RE.match(wallet):
        await update.message.reply_text(
            "❌ Invalid wallet. Send EVM 0x... (40 hex) or Solana-style address."
        )
        return WAIT_WALLET

    await aupdate_user(update.effective_user.id, wallet_address=wallet)

    ref_count = int((user.get("referral_count") or 0) or 0)
    ok = await asyncio.to_thread(
        lambda: upsert_airdrop_claim(
            update.effective_user.id,
            wallet,
            referral_count=ref_count,
        )
    )
    if not ok:
        logger.warning(
            "Wallet saved locally but Supabase airdrop_claims failed for %s",
            update.effective_user.id,
        )

    await update.message.reply_text(
        "This wallet has been submitted.",
        reply_markup=InlineKeyboardMarkup([back_to_start_row()]),
        disable_web_page_preview=True,
    )
    return ConversationHandler.END


def _tier_meta(callback_data: str | None) -> tuple[str, str]:
    mapping = {
        CB_TIER_BRONZE: (
            "buy20",
            _tier_line(config.TIER_BRONZE_USD, config.TIER_BRONZE_BONUS),
        ),
        CB_TIER_SILVER: (
            "buy50",
            _tier_line(config.TIER_SILVER_USD, config.TIER_SILVER_BONUS),
        ),
        CB_TIER_GOLD: (
            "buy100",
            _tier_line(config.TIER_GOLD_USD, config.TIER_GOLD_BONUS),
        ),
        CB_TIER_VIP: (
            "buy200",
            _tier_line(config.TIER_VIP_USD, config.TIER_VIP_BONUS),
        ),
    }
    return mapping.get(callback_data or "", ("unspecified", "Unknown"))


async def select_tier(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not update.effective_user:
        return ConversationHandler.END
    tier, label = _tier_meta(query.data)
    await aupdate_user(update.effective_user.id, presale_tier=tier)
    context.user_data["presale_tier"] = tier
    user = await aget_user(update.effective_user.id) or {}
    wallet = user.get("wallet_address") or "—"
    await query.answer(f"Selected: {label}")
    await query.edit_message_text(
        (
            f"✅ Selected: {label}\n\n"
            f"Proof 1 — Wallet: {wallet}\n"
            f"Buy on: {config.PRESALE_URL}\n\n"
            "Proof 2 — paste Transaction Hash (TXID) here."
        ),
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([back_to_start_row()]),
    )
    return WAIT_TXID


async def receive_txid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return WAIT_TXID
    user = await aget_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Start with /start")
        return ConversationHandler.END

    txid = (update.message.text or "").strip()
    if not TXID_RE.match(txid):
        await update.message.reply_text(
            "❌ Invalid TXID. Send 64-character hex hash (optional 0x prefix)."
        )
        return WAIT_TXID

    wallet = user.get("wallet_address") or ""
    if not wallet:
        await update.message.reply_text("Send wallet first. /start → Drop Wallet")
        return ConversationHandler.END

    await asyncio.to_thread(
        save_presale_proof,
        update.effective_user.id,
        update.effective_user.username,
        wallet,
        "",
        txid,
    )
    ok = await asyncio.to_thread(
        insert_buy_proof,
        update.effective_user.id,
        wallet,
        txid,
    )
    if not ok:
        logger.warning(
            "TXID saved locally but Supabase buy_proofs failed for %s",
            update.effective_user.id,
        )
    await update.message.reply_text(
        (
            "✅ Proof submitted!\n\n"
            f"Wallet: {wallet}\n"
            f"TXID: {txid}\n\n"
            "Saved for review.\n"
            f"TGE alert: {config.TGE_DATE}"
        ),
        reply_markup=InlineKeyboardMarkup([back_to_start_row()]),
    )
    return ConversationHandler.END


async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Cancelled. Use /start to return.")
    return ConversationHandler.END


async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if update.effective_user.id not in config.ADMIN_IDS:
        await update.message.reply_text("Admin only.")
        return
    paths = export_paths()
    await update.message.reply_text(f"📤 Exporting {len(paths)} sheet file(s)…")
    for path in paths:
        with path.open("rb") as doc:
            await update.message.reply_document(document=doc, filename=path.name)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if update.effective_user.id not in config.ADMIN_IDS:
        await update.message.reply_text("Admin only.")
        return
    from storage import _read_users, sheet_info  # local admin helper

    rows = _read_users()
    total = len(rows)
    complete = sum(1 for r in rows if flag(r.get("airdrop_complete")) or _airdrop_ready(r))
    wallets = sum(1 for r in rows if r.get("wallet_address"))
    txids = sum(1 for r in rows if r.get("txid"))
    skipped = sum(1 for r in rows if flag(r.get("step5_skipped")))
    await update.message.reply_text(
        (
            f"📊 Users: {total}\n"
            f"✅ Airdrop ready: {complete}\n"
            f"💎 Wallets: {wallets}\n"
            f"🧾 TXIDs: {txids}\n"
            f"⏭ Step5 skipped: {skipped}\n\n"
            f"{sheet_info()}"
        )
    )


def mark_airdrop_complete(user_id: int) -> None:
    user = get_user(user_id)
    if user and _airdrop_ready(user) and not flag(user.get("airdrop_complete")):
        update_user(user_id, airdrop_complete="1")


async def amark_airdrop_complete(user_id: int) -> None:
    await asyncio.to_thread(mark_airdrop_complete, user_id)


def build_app() -> Application:
    if not config.BOT_TOKEN or config.BOT_TOKEN.startswith("your_"):
        raise SystemExit("Set BOT_TOKEN in .env (see .env.example)")

    async def post_init(application: Application) -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=DB_THREAD_WORKERS))
        desc = config.BOT_DESCRIPTION
        if desc:
            try:
                await application.bot.set_my_description(description=desc)
                # Short description max 120 chars (profile / share card)
                short = desc if len(desc) <= 120 else desc[:117] + "..."
                await application.bot.set_my_short_description(short_description=short)
                logger.info("Bot description set: %s", desc)
            except Exception as exc:
                logger.warning("Could not set bot description: %s", exc)
        try:
            public_cmds = [BotCommand("start", "Open main menu")]
            admin_cmds = public_cmds + [
                BotCommand("stats", "Admin stats"),
                BotCommand("export", "Admin CSV export"),
            ]
            # Everyone sees only /start
            await application.bot.set_my_commands(
                public_cmds,
                scope=BotCommandScopeDefault(),
            )
            # Each admin sees /start + admin commands in their private chat
            for admin_id in config.ADMIN_IDS:
                try:
                    await application.bot.set_my_commands(
                        admin_cmds,
                        scope=BotCommandScopeChat(chat_id=admin_id),
                    )
                except Exception as exc:
                    logger.warning(
                        "Could not set admin commands for %s: %s", admin_id, exc
                    )
            await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
            logger.info(
                "Bot commands set (public=/start, admin extras for %s)",
                sorted(config.ADMIN_IDS) or "none",
            )
            logger.info(
                "Capacity tuned for ~%s concurrent updates (pool=%s, db_workers=%s)",
                MAX_CONCURRENT_UPDATES,
                HTTP_POOL_SIZE,
                DB_THREAD_WORKERS,
            )
        except Exception as exc:
            logger.warning("Could not set bot commands: %s", exc)

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .concurrent_updates(MAX_CONCURRENT_UPDATES)
        .connection_pool_size(HTTP_POOL_SIZE)
        .connect_timeout(10.0)
        .read_timeout(20.0)
        .write_timeout(20.0)
        .pool_timeout(10.0)
        .post_init(post_init)
        .build()
    )

    step5 = ConversationHandler(
        entry_points=[CallbackQueryHandler(open_wallet, pattern=f"^{CB_OPEN_WALLET}$")],
        states={
            WAIT_WALLET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_wallet),
                CallbackQueryHandler(skip_step5, pattern=f"^{CB_SKIP_STEP5}$"),
                CallbackQueryHandler(show_menu, pattern=f"^{CB_MENU}$"),
            ],
            WAIT_TXID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_txid),
                CallbackQueryHandler(skip_step5, pattern=f"^{CB_SKIP_STEP5}$"),
                CallbackQueryHandler(show_menu, pattern=f"^{CB_MENU}$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conv),
            CommandHandler("start", start),
        ],
        allow_reentry=True,
    )

    app.add_handler(step5)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("export", export_csv))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(referral_info, pattern=f"^{CB_REFERRAL}$"))
    app.add_handler(CallbackQueryHandler(buy_info, pattern=f"^{CB_BUY}$"))
    app.add_handler(CallbackQueryHandler(skip_step5, pattern=f"^{CB_SKIP_STEP5}$"))
    app.add_handler(CallbackQueryHandler(x_done, pattern=f"^{CB_X_DONE}$"))
    app.add_handler(CallbackQueryHandler(rt_done, pattern=f"^{CB_RT_DONE}$"))
    app.add_handler(CallbackQueryHandler(like_done, pattern=f"^{CB_LIKE_DONE}$"))
    app.add_handler(CallbackQueryHandler(x_wait, pattern=f"^{CB_X_WAIT}$"))
    app.add_handler(CallbackQueryHandler(refresh_status, pattern=f"^{CB_REFRESH}$"))
    app.add_handler(CallbackQueryHandler(show_menu, pattern=f"^{CB_MENU}$"))
    return app


def main() -> None:
    from storage import ensure_csv

    ensure_csv()
    app = build_app()
    logger.info("CATIQ Airdrop Bot (@%s) starting…", config.BOT_USERNAME)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
