import asyncio
import json
import re
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from checker import (
    COMMENT_RE,
    POST_RE,
    SHARE_RE,
    RedditSession,
    check,
    load_snapshots,
    save_snapshots,
    walk,
    classify_post,
    classify_comment,
)

# ===== CONFIGURATION =====
# IMPORTANT: Set your bot token as an environment variable DISCORD_TOKEN
# (e.g. in PowerShell: $env:DISCORD_TOKEN="your-token-here")
import os
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
if not DISCORD_TOKEN:
    raise SystemExit("ERROR: DISCORD_TOKEN environment variable not set. "
                     "Set it before running: e.g. $env:DISCORD_TOKEN='your-token'")
# =========================

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# Shared session and snapshots
session = None
snapshots = {}
loop = None


def _init_session():
    """Run in thread — Camoufox sync API."""
    s = RedditSession()
    return s


def _check(url, sess, snaps):
    """Run in thread."""
    return check(url, sess, snaps)


def _warm(sess):
    """Run in thread."""
    sess._warm()


def _fetch(sess, url):
    """Run in thread."""
    return sess.fetch(url)


def _close_session(sess):
    """Run in thread."""
    sess.close()


@bot.event
async def on_ready():
    global session, snapshots, loop
    loop = bot.loop
    print(f"Logged in as {bot.user}")
    snapshots = load_snapshots()
    print("Warming up Reddit session...")
    session = await asyncio.to_thread(_init_session)
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    print("Ready!")


@bot.tree.command(name="check", description="Check if a Reddit post/comment is live, deleted, or edited")
@app_commands.describe(url="Reddit post or comment URL")
async def check_cmd(interaction: discord.Interaction, url: str):
    global session, snapshots
    await interaction.response.defer()
    try:
        status, reason, target = await asyncio.to_thread(_check, url, session, snapshots)
        if status == "BLOCKED":
            await asyncio.to_thread(_warm, session)
            status, reason, target = await asyncio.to_thread(_check, url, session, snapshots)

        color_map = {
            "LIVE": discord.Color.green(),
            "DELETED": discord.Color.red(),
            "REMOVED": discord.Color.red(),
            "NOT_FOUND": discord.Color.orange(),
            "BLOCKED": discord.Color.dark_red(),
            "INVALID": discord.Color.grey(),
            "UNKNOWN": discord.Color.grey(),
        }
        emoji_map = {
            "LIVE": "✅",
            "DELETED": "❌",
            "REMOVED": "🚫",
            "NOT_FOUND": "⚠️",
            "BLOCKED": "🔴",
            "INVALID": "❓",
            "UNKNOWN": "❓",
        }

        embed = discord.Embed(
            title=f"{emoji_map.get(status, '?')} {status}",
            description=reason,
            color=color_map.get(status, discord.Color.grey()),
        )
        embed.add_field(name="URL", value=url, inline=False)
        if target and target != url:
            embed.add_field(name="Resolved to", value=target, inline=False)

        save_snapshots(snapshots)
        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")


@bot.tree.command(name="checkmulti", description="Check multiple Reddit URLs at once")
@app_commands.describe(urls="Space-separated Reddit URLs")
async def checkmulti_cmd(interaction: discord.Interaction, urls: str):
    global session, snapshots
    await interaction.response.defer()
    try:
        url_list = [u.strip() for u in urls.split() if u.strip()]
        if not url_list:
            await interaction.followup.send("No valid URLs provided.")
            return

        results = []
        live = removed = deleted = not_found = blocked = 0

        for url in url_list:
            status, reason, target = await asyncio.to_thread(_check, url, session, snapshots)
            if status == "BLOCKED":
                await asyncio.to_thread(_warm, session)
                status, reason, target = await asyncio.to_thread(_check, url, session, snapshots)

            if status == "LIVE":
                live += 1
            elif status == "REMOVED":
                removed += 1
            elif status == "DELETED":
                deleted += 1
            elif status in ("NOT_FOUND", "INVALID"):
                not_found += 1
            else:
                blocked += 1

            emoji = {"LIVE": "✅", "DELETED": "❌", "REMOVED": "🚫", "NOT_FOUND": "⚠️", "BLOCKED": "🔴"}.get(status, "❓")
            results.append(f"{emoji} **{status}** — {reason}")

        summary = f"**{live}** live | **{removed}** removed | **{deleted}** deleted | **{not_found}** not found | **{blocked}** blocked"

        embed = discord.Embed(
            title=f"Check Results ({len(url_list)} URLs)",
            description="\n".join(results),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=summary)

        save_snapshots(snapshots)
        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")


@bot.tree.command(name="profile", description="View a Reddit user's profile info")
@app_commands.describe(username="Reddit username (without u/)")
async def profile_cmd(interaction: discord.Interaction, username: str):
    global session
    await interaction.response.defer()
    try:
        url = f"https://www.reddit.com/user/{username}/about.json"
        body, code = await asyncio.to_thread(_fetch, session, url)
        if code != 200:
            await interaction.followup.send(f"❌ Could not fetch profile (HTTP {code})")
            return

        data = json.loads(body)
        d = data.get("data", {})

        embed = discord.Embed(
            title=f"u/{username}",
            url=f"https://reddit.com/u/{username}",
            color=discord.Color.orange(),
        )

        created = d.get("created_utc", 0)
        if created:
            embed.add_field(name="Cake Day", value=datetime.fromtimestamp(created).strftime("%Y-%m-%d"), inline=True)

        embed.add_field(name="Post Karma", value=f"{d.get('link_karma', 0):,}", inline=True)
        embed.add_field(name="Comment Karma", value=f"{d.get('comment_karma', 0):,}", inline=True)

        total = d.get("link_karma", 0) + d.get("comment_karma", 0)
        embed.add_field(name="Total Karma", value=f"{total:,}", inline=True)

        if d.get("is_gold"):
            embed.add_field(name="Reddit Gold", value="✅", inline=True)
        if d.get("is_mod"):
            embed.add_field(name="Moderator", value="✅", inline=True)

        save_snapshots(snapshots)
        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")


bot.run(DISCORD_TOKEN)
