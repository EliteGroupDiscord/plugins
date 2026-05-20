// commands/nuke.js
const { PermissionFlagsBits, ChannelType } = require("discord.js");

module.exports = {
  name: "nuke",
  description: "Deletes and recreates the current channel.",

  async execute(message) {
    const channel = message.channel;

    if (!message.member.permissions.has(PermissionFlagsBits.ManageChannels)) {
      return message.reply("❌ You need **Manage Channels** to use this.");
    }

    if (!message.guild.members.me.permissions.has(PermissionFlagsBits.ManageChannels)) {
      return message.reply("❌ I need **Manage Channels** permission.");
    }

    if (
      channel.type !== ChannelType.GuildText &&
      channel.type !== ChannelType.GuildAnnouncement
    ) {
      return message.reply("❌ This can only be used in text channels.");
    }

    const position = channel.position;

    const newChannel = await channel.clone({
      name: channel.name,
      reason: `Channel nuked by ${message.author.tag}`,
    });

    await newChannel.setPosition(position);

    await channel.delete(`Channel nuked by ${message.author.tag}`);

    await newChannel.send(`💥 Channel nuked by **${message.author.tag}**`);
  },
};
