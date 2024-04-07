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

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)
from bot_helpers import build_menu, send_safe

from guess_track_title import guess_track_title
from queue_processor import QueueProcessor
from yt_dlp_processor import YTDlPProcessor

# User commands
BOT_COMMAND_START = "start"
BOT_COMMAND_HELP = "help"


class BotHandler:
    def __init__(self, config: Dict, messages: Dict):
        self.config = config
        self.messages = messages

        logging.info("Creating YTDlPProcessor instance")
        self.yt_dlp_processor = YTDlPProcessor(config)

        logging.info("Creating QueueProcessor instance")
        self.queue_processor = QueueProcessor(
            config, messages, self.yt_dlp_processor, self._database_get_data, self._database_set_data
        )

        # Temporary users database that allows markup, rename actions and downloading to be possible
        # NOTE: maybe in a future it should be replaced with a proper file-based database
        self._temp_database = {}

        self._application = None
        self._event_loop = None

    def _database_get_data(self, chat_id: int, *args) -> Any or None:
        """Retrieves temp data from self._temp_database and creates a new user if needed

        Args:
            chat_id (int): chat ID
            *args: sequence of keys to retrieve

        Returns:
            Any or None: user's temp data or None if not exists
        """
        if not chat_id in self._temp_database:
            self._temp_database[chat_id] = {}

        data_temp = self._temp_database[chat_id]
        for key in args:
            if data_temp is None or key not in data_temp:
                return None
            data_temp = data_temp[key]

        return data_temp

    def _database_set_data(self, chat_id: int, *args) -> None:
        """Recursively sets data in self._temp_database
        Ex:
        >>> _database_set_data(123, "a", "aa", "aaa", "text")
        {123: {'a': {'aa': {'aaa': 'text'}}}}

        Args:
            chat_id (int): chat ID
            *args: sequence of keys to set. The last one is the value to set
        """
        if not chat_id in self._temp_database:
            self._temp_database[chat_id] = {}

        if len(args) == 0:
            return
        value = args[-1]
        args = [chat_id] + list(args[:-1])

        data_temp = self._temp_database
        for i, key in enumerate(args):
            if i == len(args) - 1:
                data_temp[key] = value
                break
            if key not in data_temp or data_temp[key] is None:
                data_temp[key] = {}
            data_temp = data_temp[key]

    def start_bot(self):
        """Starts download queue listener as thread and bot (blocking)
        Press CTRL+C to stop it
        """
        try:
            # Start queue listener
            self.queue_processor.start()

            # Close previous event loop (just in case)
            try:
                loop = asyncio.get_running_loop()
                if loop and loop.is_running():
                    logging.info("Stopping current event loop before starting a new one")
                    loop.stop()
            except Exception as e:
                logging.warning(f"Error stopping current event loop: {e}. You can ignore this message")

            # Create new event loop
            logging.info("Creating a new event loop")
            self._event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._event_loop)

            # Build bot
            builder = ApplicationBuilder().token(self.config.get("bot_token"))
            self._application = builder.build()

            # Commands
            logging.info("Adding command handlers")
            self._application.add_handler(CommandHandler(BOT_COMMAND_START, self.bot_command_start))
            self._application.add_handler(CommandHandler(BOT_COMMAND_HELP, self.bot_command_help))

            # Handle messages
            logging.info("Adding message handlers")
            self._application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.bot_message))
            self._application.add_handler(MessageHandler(filters.PHOTO & (~filters.COMMAND), self.bot_message))

            # Unknown command -> send help
            logging.info("Adding unknown command handler")
            self._application.add_handler(MessageHandler(filters.COMMAND, self.bot_command_help))

            # Add buttons handler
            logging.info("Adding markup handler")
            self._application.add_handler(CallbackQueryHandler(self.query_callback))

            # Start telegram bot polling
            logging.info("Starting bot polling")
            self._application.run_polling(close_loop=False, stop_signals=[])

        # Exit requested
        except (KeyboardInterrupt, SystemExit):
            logging.warning("KeyboardInterrupt or SystemExit @ bot_start")

        # Bot error?
        except Exception as e:
            if "Event loop is closed" in str(e):
                logging.warning("Stopping telegram bot")
            else:
                logging.error("Telegram bot error", exc_info=e)

        # Stop queue listener
        self.queue_processor.stop()

        # If we're here, exit requested
        logging.warning("Telegram bot stopped")

    async def query_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Buttons (reply_markup) callback

        Args:
            update (Update): update object from bot's callback
            context (ContextTypes.DEFAULT_TYPE): context object from bot's callback

        Raises:
            Exception: _description_
        """
        chat_id = update.effective_chat.id
        try:
            data_ = update.callback_query.data
            if data_ is None:
                raise Exception("No callback data")

            # Extract and parse data
            message_id = update.effective_message.message_id
            data_parts = data_.split("|")
            action = data_parts[0]
            edit_message_id = int(data_parts[1])
            arguments = data_parts[2:]
            logging.info(f"'{action}' markup action from user: {chat_id}")

            # Check message IDs (we couldn't proceed if it's not equal or not exist)
            if message_id is None or edit_message_id < 0 or edit_message_id != message_id:
                await send_safe(chat_id=chat_id, text=self.messages["error_expired"], context=context)
                return

            # Reset renaming
            if action != "rdn" and action != "rnm" and action != "dwn":
                self._database_set_data(chat_id, "rename", None)

            ###################
            # Request formats #
            ###################
            if action == "fmt":
                if len(arguments) < 2:
                    raise Exception("Not enoughs arguments")
                extractor = arguments[0]
                id_ = arguments[1]
                await self.markup_fmt(chat_id, edit_message_id, extractor, id_, context)

            #################################
            # Download stream / rename file #
            #################################
            elif action == "dwn" or action == "rnm":
                if len(arguments) < 5:
                    raise Exception("Not enoughs arguments")
                extractor = arguments[0]
                id_ = arguments[1]
                format_id = arguments[2]
                is_video = arguments[3] == "1" or arguments[3].lower() == "t"
                try:
                    bitrate = float(arguments[4])
                    if bitrate < 0:
                        bitrate = None
                except:
                    bitrate = None
                if action == "dwn":
                    await self.markup_dwn(
                        chat_id, edit_message_id, extractor, id_, format_id, is_video, bitrate, context
                    )
                else:
                    await self.markup_rnm(
                        chat_id, edit_message_id, extractor, id_, format_id, is_video, bitrate, context
                    )

            #######################
            # Rename and download #
            #######################
            elif action == "rdn":
                if len(arguments) < 6:
                    raise Exception("Not enoughs arguments")
                extractor = arguments[0]
                id_ = arguments[1]
                format_id = arguments[2]
                is_video = arguments[3] == "1" or arguments[3].lower() == "t"
                name_index = int(arguments[4])
                try:
                    bitrate = float(arguments[5])
                    if bitrate < 0:
                        bitrate = None
                except:
                    bitrate = None
                if name_index >= 0 and name_index >= len(self._database_get_data(chat_id, "rename", "names") or []):
                    raise Exception(f"Index {name_index} is out of range")

                author, title = self._database_get_data(chat_id, "rename", "names")[name_index]
                self._database_set_data(chat_id, "rename", "final", "author", author)
                self._database_set_data(chat_id, "rename", "final", "title", title)

                await self.markup_dwn(chat_id, edit_message_id, extractor, id_, format_id, is_video, bitrate, context)

            ########################
            # Back button (search) #
            ########################
            elif action == "sch":
                await self.markup_sch(chat_id, edit_message_id, context)

        # Error?
        except Exception as e:
            logging.error("Query callback error", exc_info=e)
            await send_safe(chat_id=chat_id, text=self.messages["error_common"].format(error=str(e)), context=context)

        # Submit answer
        try:
            await context.bot.answer_callback_query(update.callback_query.id)
        except Exception as e:
            logging.warning(f"Answer callback query error: {e}")

    async def markup_fmt(
        self,
        chat_id: int,
        edit_message_id: int,
        extractor: str,
        id_: str,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Markup callback for format request action: "fmt|ID of edited message|extractor|media ID"

        Args:
            chat_id (int): ID of current user
            edit_message_id (int): parsed ID of message to edit or <0 if not exists
            extractor(str): extractor name (ex. "youtube")
            id_ (str): media ID
            context (ContextTypes.DEFAULT_TYPE): context object from bot's callback

        Raises:
            Exception: in case of error
        """
        logging.info(f"Search formats markup action from {chat_id}")

        try:
            data = self.yt_dlp_processor.get_formats(extractor, id_)
            audio_streams, video_streams, title, channel, from_metadata = data

        # Send error message with back button on error
        except Exception as e:
            logging.error("Error extracting formats", exc_info=e)
            buttons = []
            if (
                self._database_get_data(chat_id, "search_result") is not None
                and self._database_get_data(chat_id, "search_result", "edit_message_id") == edit_message_id
            ):
                buttons.append(
                    InlineKeyboardButton(
                        self.messages["button_back"],
                        callback_data=f"sch|{edit_message_id}",
                    )
                )
            if "expired" in str(e):
                message = self.messages["error_expired"]
            else:
                message = self.messages["error_common"].format(error=str(e))

            await send_safe(
                chat_id=chat_id,
                text=message,
                reply_markup=InlineKeyboardMarkup(build_menu(buttons)) if len(buttons) != 0 else None,
                context=context,
                edit_message_id=edit_message_id,
            )
            return

        if len(audio_streams) == 0 and len(video_streams) == 0:
            message = self.messages["format_no_streams"]
        else:
            message = self.messages["format_select"]
        buttons = []

        # Save data for rename action
        self._database_set_data(chat_id, "rename", "edit_message_id", edit_message_id)
        self._database_set_data(chat_id, "rename", "title", title)
        self._database_set_data(chat_id, "rename", "channel", channel)
        self._database_set_data(chat_id, "rename", "names", [])
        self._database_set_data(chat_id, "rename", "renamed", False)

        # We already have a proper track title and artist name
        if from_metadata:
            self._database_set_data(chat_id, "rename", "final", "title", title)
            self._database_set_data(chat_id, "rename", "final", "author", channel)
            action = "dwn"

        # We need to ask user for proper name
        else:
            self._database_set_data(chat_id, "rename", "final", "title", None)
            self._database_set_data(chat_id, "rename", "final", "author", None)
            action = "rnm"

        # Audio stream select button (rename audio file)
        for audio_stream in audio_streams:
            format_id = audio_stream["id"]
            bitrate_str = f"{audio_stream['bitrate']:.2f}"
            buttons.append(
                InlineKeyboardButton(
                    self.messages["button_format_audio"].format(
                        audio_format=self.config["target_format_audio"].upper(), info=audio_stream["name"]
                    ),
                    # action|ID of message to edit|extractor|media ID|stream ID|is video?|audio bitrate
                    callback_data=f"{action}|{edit_message_id}|{extractor}|{id_}|{format_id}|0|{bitrate_str}",
                )
            )

        # Video stream select button
        for video_stream in video_streams:
            format_id = video_stream["id"]
            buttons.append(
                InlineKeyboardButton(
                    self.messages["button_format_video"].format(
                        video_format=self.config["target_format_video"].upper(), info=video_stream["name"]
                    ),
                    # action|ID of message to edit|extractor|media ID|stream ID|is video?|audio bitrate
                    callback_data=f"dwn|{edit_message_id}|{extractor}|{id_}|{format_id}|1|",
                )
            )

        # Back button (search)
        if (
            self._database_get_data(chat_id, "search_result") is not None
            and self._database_get_data(chat_id, "search_result", "edit_message_id") == edit_message_id
        ):
            buttons.append(
                InlineKeyboardButton(
                    self.messages["button_back"],
                    callback_data=f"sch|{edit_message_id}",
                )
            )

        await send_safe(
            chat_id=chat_id,
            text=message,
            reply_markup=InlineKeyboardMarkup(build_menu(buttons)) if len(buttons) != 0 else None,
            context=context,
            edit_message_id=edit_message_id,
        )

    async def markup_rnm(
        self,
        chat_id: int,
        edit_message_id: int,
        extractor: str,
        id_: str,
        format_id: str,
        is_video: bool,
        bitrate: float or None,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Markup callback for rename action: "rnm|ID of edited message|extractor|media ID|download video?|bitrate"

        Args:
            chat_id (int): ID of current user
            edit_message_id (int): parsed ID of message to edit or None if not exists
            extractor (str): extractor name (ex. "youtube")
            id_ (str): media ID
            format_id (str): ID of format (stream)
            is_video (bool): True if it's video stream, false if it's audio
            bitrate (float or None): audio bitrate (only required if video is True)
            context (ContextTypes.DEFAULT_TYPE): context object from bot's callback
        """
        if self._database_get_data(chat_id, "rename") is None:
            await send_safe(chat_id=chat_id, text=self.messages["error_expired"], context=context)
            return
        if not bitrate:
            bitrate = 0.0

        title = self._database_get_data(chat_id, "rename", "title") or "Untitled"
        channel = self._database_get_data(chat_id, "rename", "channel") or "Unknown"
        guessed_names = guess_track_title(title, channel)

        # Add suggestions
        buttons = []
        is_video = "1" if is_video else "0"
        for i, (author, title) in enumerate(guessed_names):
            buttons.append(
                InlineKeyboardButton(
                    self.messages["button_rename_guess"].format(author=author, title=title),
                    # action|ID of message to edit|extractor|media ID|stream ID|is video?|name index|audio bitrate
                    callback_data=(
                        f"rdn|{edit_message_id}|{extractor}|{id_}|{format_id}|{is_video}|{i}|{bitrate:.2f}"
                    ),
                )
            )

        # Save data
        self._database_set_data(chat_id, "rename", "names", guessed_names[:])
        self._database_set_data(chat_id, "rename", "callback", "edit_message_id", edit_message_id)
        self._database_set_data(chat_id, "rename", "callback", "extractor", extractor)
        self._database_set_data(chat_id, "rename", "callback", "id", id_)
        self._database_set_data(chat_id, "rename", "callback", "format_id", format_id)
        self._database_set_data(chat_id, "rename", "callback", "bitrate", bitrate)
        self._database_set_data(chat_id, "rename", "renamed", False)

        # Add back button
        buttons.append(
            InlineKeyboardButton(
                self.messages["button_back"],
                callback_data=f"fmt|{edit_message_id}|{extractor}|{id_}",
            )
        )

        await send_safe(
            chat_id=chat_id,
            text=self.messages["rename_audio"],
            context=context,
            edit_message_id=edit_message_id,
            reply_markup=InlineKeyboardMarkup(build_menu(buttons)),
            markdown=True,
        )

    async def markup_dwn(
        self,
        chat_id: int,
        edit_message_id: int,
        extractor: str,
        id_: str,
        format_id: str,
        is_video: bool,
        bitrate: float or None,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Markup callback for download action: "dwn|ID of edited message|extractor|media ID|download video?|bitrate"

        Args:
            chat_id (int): ID of current user
            edit_message_id (int): parsed ID of message to edit or None if not exists
            extractor (str): extractor name (ex. "youtube")
            id_ (str): media ID
            format_id (str): ID of format (stream)
            is_video (bool): True if it's video stream, false if it's audio
            bitrate (float or None): audio bitrate (only required if video is True)
            context (ContextTypes.DEFAULT_TYPE): context object from bot's callback
        """
        logging.info(f"Download request of {id_} from {chat_id}")

        # Check queue and send back button
        if self.queue_processor.queue.full():
            logging.warning("Queue is full")
            message = self.messages["error_queue_full"]
            reply_markup = None
            if edit_message_id:
                buttons = [
                    (
                        InlineKeyboardButton(
                            self.messages["button_back"],
                            callback_data=f"fmt|{edit_message_id}|{extractor}|{id_}",
                        )
                    )
                ]
                reply_markup = InlineKeyboardMarkup(build_menu(buttons))
            await send_safe(
                chat_id=chat_id,
                text=message,
                context=context,
                edit_message_id=edit_message_id,
                reply_markup=reply_markup,
            )
            return

        # Extract rename data from database (can be Nones)
        author = self._database_get_data(chat_id, "rename", "final", "author")
        title = self._database_get_data(chat_id, "rename", "final", "title")
        self._database_set_data(chat_id, "rename", None)

        # Add to the queue
        data = {
            "action": "download",
            "chat_id": chat_id,
            "edit_message_id": edit_message_id,
            "extractor": extractor,
            "id": id_,
            "format_id": format_id,
            "is_video": is_video,
            "bitrate": bitrate,
            "author": author,
            "title": title,
        }
        self.queue_processor.queue.put(data)

        # Send initial message
        edit_message_id = await send_safe(
            chat_id=chat_id,
            text=self.messages["download_started"],
            context=context,
            edit_message_id=edit_message_id if edit_message_id >= 0 else None,
            markdown=True,
        )

    async def markup_sch(
        self,
        chat_id: int,
        edit_message_id: int,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Markup callback for search action: "sch|ID of edited message"

        Args:
            chat_id (int): ID of current user
            edit_message_id (int): parsed ID of message to edit or <0 if not exists
            context (ContextTypes.DEFAULT_TYPE): context object from bot's callback
        """
        # Check in database
        text = self._database_get_data(chat_id, "search_result", "text")
        markup = self._database_get_data(chat_id, "search_result", "markup")
        edit_message_id_ = self._database_get_data(chat_id, "search_result", "edit_message_id")

        if text and edit_message_id_ is not None and edit_message_id_ >= 0 and edit_message_id_ == edit_message_id:
            await send_safe(
                chat_id=chat_id,
                text=text,
                context=context,
                edit_message_id=edit_message_id,
                reply_markup=markup,
                markdown=True,
            )
            return

        # Not in database
        await send_safe(chat_id=chat_id, text=self.messages["error_expired"], context=context)

    async def _rename(self, chat_id: int, request: str, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Accepts "Author - Title"

        Args:
            chat_id (int): Chat ID
            request (str): "Author - Title"
            context (ContextTypes.DEFAULT_TYPE): context object from bot's callback
        """
        # Try to parse author and title and check them
        try:
            request_parts = request.split("-")
            author = request_parts[0].strip()
            title = "-".join(request_parts[1:]).strip()
            if not author or not title:
                raise Exception("Empty author or title")
        except Exception as e:
            logging.warning(f"Unable to rename: {e}")
            edit_message_id = self._database_get_data(chat_id, "rename", "edit_message_id")
            await send_safe(
                chat_id=chat_id,
                text=self.messages["rename_error"],
                context=context,
                edit_message_id=edit_message_id,
            )
            return

        self._database_set_data(chat_id, "rename", "final", "author", author)
        self._database_set_data(chat_id, "rename", "final", "title", title)
        self._database_set_data(chat_id, "rename", "renamed", True)

    async def _search(self, chat_id: int, request: str, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Searches using "request" and sends search results to the user

        Args:
            chat_id (int): parsed ID of user
            request (str): query
            context (ContextTypes.DEFAULT_TYPE): context object from bot's callback
        """
        logging.info(f"Search request: {request}")

        # Check queue
        if self.queue_processor.queue.full():
            logging.warning("Queue is full")
            await send_safe(chat_id=chat_id, text=self.messages["error_queue_full"], context=context)
            return

        # Send initial message
        edit_message_id = await send_safe(
            chat_id=chat_id,
            text=self.messages["searching"].format(query=request),
            context=context,
            markdown=True,
        )

        # Check
        if edit_message_id is None or edit_message_id < 0:
            await send_safe(chat_id=chat_id, text=self.messages["error_send"], context=context)
            return

        # Add to the queue
        data = {"action": "search", "chat_id": chat_id, "edit_message_id": edit_message_id, "request": request}
        self.queue_processor.queue.put(data)

    async def bot_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Text messages callback

        Args:
            update (Update): update object from bot's callback
            context (ContextTypes.DEFAULT_TYPE): context object from bot's callback
        """
        chat_id = update.effective_chat.id
        user_name = str(update.effective_chat.effective_name)
        request = update.message.text.strip().split("\n")[0].strip()
        if not request or len(request) < 2:
            logging.warning(f"Empty message from {user_name} ({chat_id})")
            await send_safe(chat_id=chat_id, text=self.messages["empty_message"], context=context)
            return

        logging.info(f"Text message from from {user_name} ({chat_id})")

        # Rename and download
        if (
            self._database_get_data(chat_id, "rename") is not None
            and self._database_get_data(chat_id, "rename", "final", "title") is None
            and self._database_get_data(chat_id, "rename", "final", "channel") is None
            and self._database_get_data(chat_id, "rename", "renamed")
        ):
            await self._rename(chat_id, request, context)
            await self.markup_dwn(
                chat_id,
                self._database_get_data(chat_id, "rename", "callback", "edit_message_id"),
                self._database_get_data(chat_id, "rename", "callback", "extractor"),
                self._database_get_data(chat_id, "rename", "callback", "id"),
                self._database_get_data(chat_id, "rename", "callback", "format_id"),
                False,
                self._database_get_data(chat_id, "rename", "callback", "bitrate"),
                context,
            )

        # Search request if no renaming active
        else:
            await self._search(chat_id, request, context)

    async def bot_command_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/help command callback

        Args:
            update (Update): update object from bot's callback
            context (ContextTypes.DEFAULT_TYPE): context object from bot's callback
        """
        chat_id = update.effective_chat.id
        user_name = str(update.effective_chat.effective_name)
        logging.info(f"/help command from {user_name} ({chat_id})")

        # Send help message
        await send_safe(chat_id=chat_id, text=self.messages["help"], context=context)

    async def bot_command_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/start command callback

        Args:
            update (Update): update object from bot's callback
            context (ContextTypes.DEFAULT_TYPE): context object from bot's callback
        """
        chat_id = update.effective_chat.id
        user_name = str(update.effective_chat.effective_name)
        logging.info(f"/start command from {user_name} ({chat_id})")

        # Send start and help messages
        await send_safe(chat_id=chat_id, text=self.messages["start"], context=context)
        await send_safe(chat_id=chat_id, text=self.messages["help"], context=context)
