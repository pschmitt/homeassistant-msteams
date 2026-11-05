# Services

The integration provides the `msteams` service domain.

## `msteams.send_message`

Send Markdown text to a conversation or channel thread.

| Field | Required | Description |
| --- | --- | --- |
| `message` | Yes | Message content. |
| `conversation_id` | No | Target conversation or channel thread. If omitted, uses the configured default conversation. |
| `reply_to_message_id` | No | Message ID to reply to. |
| `subject` | No | Optional subject header. |
| `entry_id` | No | Select a specific configured Teams entry when more than one is present. |

## `msteams.reply_message`

Reply to an existing message. Supply `conversation_id`, `message_id`, and
`message`. The service sends the message as a reply to the given message ID.
Use `entry_id` to select a specific configured Teams entry when needed.

## `msteams.add_reaction`

Add a reaction to a message. Supply `conversation_id`, `message_id`, and
`reaction`. Supported reactions are `like`, `heart`, `laugh`, `surprised`,
`sad`, and `angry`. The required reaction field is prefilled with `like` in the
UI. `entry_id` selects a
specific configured Teams entry when needed.

## Notify entity

The **Microsoft Teams** notify entity sends to the configured default
conversation. If no default is configured, it sends to the account's Teams
notes conversation. Pass `conversation_id` under the notification's `data` to
choose a conversation for one notification. A `reply_to_message_id` under
`data` replies to an existing message. A title is prepended to chat messages;
for a channel thread, it is sent as the subject.

Configure the default conversation under integration **Options**. See
[Configuration](configuration.md).
