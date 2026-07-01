from __future__ import annotations

import discord

from utils.EmbedMessage import SakuraEmbedMsg


class PersonaSelect(discord.ui.Select):
    def __init__(self, cog, user_id: int):
        self.cog = cog
        self.user_id = user_id
        options = []
        for persona in cog.persona_store.list_personas()[:24]:
            options.append(
                discord.SelectOption(
                    label=persona.name[:100],
                    value=persona.key[:100],
                    description=f"檔名: {persona.key}"[:100],
                )
            )
        super().__init__(
            placeholder="選擇要切換的人設",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("這個選單不是給你使用的。", ephemeral=True)
            return
        selected = self.values[0]
        self.cog.user_settings.modify(user=interaction.user, persona=selected)
        await interaction.response.edit_message(
            embed=SakuraEmbedMsg(
                title="人設已切換",
                description=self.cog._format_current_settings(interaction.user),
            ),
            view=self.view,
        )


class PersonaSelectView(discord.ui.View):
    def __init__(self, cog, user_id: int):
        super().__init__(timeout=180)
        self.add_item(PersonaSelect(cog, user_id))
