"""
Copyright (C) 2024 Fern Lane

This file is part of the liberty-media-telegram-bot distribution
(see <https://github.com/F33RNI/liberty-media-telegram-bot>)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
"""

import asyncio
import logging
from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import telegram
from telegram.ext import ContextTypes
import md2tgmd


async def send_safe(
    chat_id: int,
    text: str,
    context: ContextTypes.DEFAULT_TYPE or None = None,
    api_token: str or None = None,
    reply_to_message_id: int or None = None,
    edit_message_id: int or None = None,
    reply_markup: InlineKeyboardMarkup or None = None,
    markdown: bool = False,
) -> int or None:
    """Sends or edits text message without raising any error

    Args:
        chat_id (int): ID of user (chat)
        text (str): text to send
        context (ContextTypes.DEFAULT_TYPE or None, optional): context object from bot's callback instead of api_token
        api_token (str or None, optional): bot's token to use instead of context. Defaults to None
        reply_to_message_id (int or None, optional): ID of message to reply on. Defaults to None
        edit_message_id(int or None, optional): ID of message to edit instead of sending a new one. Defaults to None
        reply_markup (InlineKeyboardMarkup or None, optional): buttons. Defaults to None
        markdown (bool, optional): True to parse as markdown. Defaults to False

    Returns:
        int or None: ID of sent message or None in case of error
    """
    try:
        # Try to get bot's instance from context or api token
        if context is not None:
            bot = context.bot
        elif api_token:
            bot = telegram.Bot(api_token)
        else:
            raise Exception("No context object or bot token provided")

        # Try to escape markdown
        if markdown:
            try:
                text = md2tgmd.escape(text)
            except Exception as e_:
                logging.warning(f"Unable to escape markdown. Sending without parse_mode: {e_}")
                markdown = False

        # Edit message
        if edit_message_id is not None:
            return (
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=edit_message_id,
                    text=text,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True,
                    parse_mode="MarkdownV2" if markdown else None,
                )
            ).message_id

        # Send a new one
        else:
            return (
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_to_message_id=reply_to_message_id,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True,
                    parse_mode="MarkdownV2" if markdown else None,
                )
            ).message_id
    except Exception as e:
        logging.error(f"Error sending {text} to {chat_id}: {e}", exc_info=e)

    return None


def build_menu(buttons: List[InlineKeyboardButton], n_cols: int = 1, header_buttons=None, footer_buttons=None) -> List:
    """Returns a list of inline buttons used to generate inlinekeyboard responses

    Args:
        buttons (List[InlineKeyboardButton]): list of InlineKeyboardButton
        n_cols (int, optional): number of columns (number of list of buttons). Defaults to 1
        header_buttons (optional): first button value. Defaults to None
        footer_buttons (optional): last button value. Defaults to None

    Returns:
        List: list of inline buttons
    """
    buttons = [button for button in buttons if button is not None]
    menu = [buttons[i : i + n_cols] for i in range(0, len(buttons), n_cols)]
    if header_buttons:
        menu.insert(0, header_buttons)
    if footer_buttons:
        menu.append(footer_buttons)
    return menu


def async_helper(awaitable_) -> None:
    """Runs async function inside sync

    Args:
        awaitable_ (_type_): coroutine
    """
    # Try to get current event loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    # Check it
    if loop and loop.is_running():
        loop.create_task(awaitable_)

    # We need new event loop
    else:
        asyncio.run(awaitable_)
