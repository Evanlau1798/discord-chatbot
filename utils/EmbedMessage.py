from datetime import datetime

import discord


class SakuraEmbedMsg(discord.Embed):
    def __init__(self, title: str = None, description: str = None, loading: bool = False):
        super().__init__(
            title=title,
            description=description,
            colour=discord.Color.from_rgb(r=217, g=140, b=144),
        )
        self.set_footer(text="願四季如春，櫻花永不凋零")
        self.timestamp = datetime.now()
        if loading:
            self.set_author(
                name="請稍後...",
                icon_url="https://media.tenor.com/On7kvXhzml4AAAAi/loading-gif.gif",
            )
