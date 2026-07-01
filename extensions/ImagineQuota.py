from __future__ import annotations

import os
from collections.abc import Mapping

import discord
from discord.ext import commands

from utils.EmbedMessage import SakuraEmbedMsg
from utils.imagine_rate_limit_store import ImagineQuotaStatus, ImagineRateLimiter

IMAGINE_QUOTA_ADMIN_USER_ID_ENV = "IMAGINE_QUOTA_ADMIN_USER_ID"


class ImagineQuotaResetAllView(discord.ui.View):
    def __init__(self, limiter: ImagineRateLimiter):
        super().__init__(timeout=180)
        self.limiter = limiter

    @discord.ui.button(label="重置所有繪圖額度", style=discord.ButtonStyle.danger)
    async def reset_all_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.handle_reset_all(interaction)

    async def handle_reset_all(self, interaction):
        if not is_imagine_quota_admin(getattr(getattr(interaction, "user", None), "id", 0)):
            await interaction.response.send_message("你沒有權限重置繪圖額度。", ephemeral=True)
            return
        reset_count = self.limiter.reset_all()
        await interaction.response.edit_message(
            embed=SakuraEmbedMsg(
                title="繪圖額度已重置",
                description=f"已重置 {reset_count} 筆繪圖額度紀錄。",
            ),
            view=None,
        )


class ImagineQuota(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.limiter = ImagineRateLimiter()

    @commands.slash_command(name="imagine_quota", description="查看你的繪圖額度")
    async def imagine_quota(self, ctx: discord.ApplicationContext):
        await ctx.respond(embed=build_imagine_quota_embed(self.limiter.check(ctx.author.id)), ephemeral=True)

    @commands.slash_command(name="imagine_quota_reset_all", description="重置所有使用者的繪圖額度")
    async def imagine_quota_reset_all(self, ctx: discord.ApplicationContext):
        if not is_imagine_quota_admin(getattr(ctx.author, "id", 0)):
            await ctx.respond(content="你沒有權限重置繪圖額度。", ephemeral=True)
            return
        await ctx.respond(
            embed=SakuraEmbedMsg(
                title="重置所有繪圖額度",
                description="按下按鈕後，所有使用者的繪圖請求次數都會被重置。",
            ),
            view=ImagineQuotaResetAllView(self.limiter),
            ephemeral=True,
        )


def build_imagine_quota_embed(status: ImagineQuotaStatus) -> SakuraEmbedMsg:
    if status.unlimited:
        description = "你的帳號不套用繪圖使用限制。"
    elif status.reset_at is None:
        description = (
            f"剩餘 {status.remaining} / {status.limit} 次\n"
            "首次成功繪圖後會開始計算 24 小時使用窗口。"
        )
    else:
        description = (
            f"剩餘 {status.remaining} / {status.limit} 次\n"
            f"重置時間：<t:{status.reset_at}:R>（<t:{status.reset_at}:f>）"
        )
    return SakuraEmbedMsg(title="繪圖額度", description=description)


def is_imagine_quota_admin(user_id, env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    configured = str(values.get(IMAGINE_QUOTA_ADMIN_USER_ID_ENV, "")).strip()
    return configured.isdigit() and str(user_id) == configured


def setup(bot: discord.Bot):
    bot.add_cog(ImagineQuota(bot))
