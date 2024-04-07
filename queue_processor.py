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

from datetime import timedelta
import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Bot

from bot_helpers import async_helper, build_menu, send_safe
from yt_dlp_processor import YTDlPProcessor


class QueueProcessor:
    def __init__(
        self,
        config: Dict,
        messages: Dict,
        yt_dlp_processor_: YTDlPProcessor,
        database_data_getter: Callable[[int, Tuple], Any],
        database_data_setter: Callable[[int, Tuple], None],
    ) -> None:
        self.config = config
        self.messages = messages
        self.yt_dlp_processor = yt_dlp_processor_
        self.database_data_getter = database_data_getter
        self.database_data_setter = database_data_setter

        self.queue = queue.Queue(self.config["queue_size"])

        self._queue_listener_thread = None
        self._progress_timer = None
        self._progress_message = None

    def start(self) -> None:
        """Starts _queue_handler_loop() as thread
        Everything in this class must be called from the one process
        """
        if self._queue_listener_thread is not None and self._queue_listener_thread.is_alive():
            return
        logging.info("Starting _queue_handler_loop() as thread")
        self._queue_listener_thread = threading.Thread(target=self._queue_handler_loop)
        self._queue_listener_thread.start()

    def stop(self) -> None:
        """Stops _queue_handler_loop() thread and joins it
        Everything in this class must be called from the one process
        """
        if self._queue_listener_thread is None or not self._queue_listener_thread.is_alive():
            return
        logging.info("Stopping queue listener thread")
        self.queue.put(None)
        try:
            self._queue_listener_thread.join()
        except Exception as e:
            logging.warning(f"Cannot join queue listener thread: {e}")

    def _queue_handler_loop(self) -> None:
        """Gets request from queue and processes it

        For search request, queue data must be: {
            "action": "search",
            "chat_id": ID of chat (user) as integer,
            "edit_message_id": if of current message as integer,
            "request": "Search query"
        }

        For download request, queue data must be: {
            "action": "download",
            "chat_id": ID of chat (user) as integer,
            "edit_message_id": if of current message as integer,
            "extractor": "name of extractor (ex. 'youtube')",
            "id": "video/audio ID",
            "format_id": "format to download",
            "is_video": True or False,
            "bitrate": target bitrate (for audio only) as float,
            "author": track's artist (for audio only),
            "title": track's name (for audio only)
        }
        """
        logging.info("_queue_handler_loop() started")
        while True:
            # Read from queue (blocking) and exit on None
            data = self.queue.get(block=True)
            if data is None:
                logging.warning("Received None from self._download_queue. Stopping listener")
                break
            logging.info(f"Received {data['action']} task from the queue")

            try:
                ##########
                # Search #
                ##########
                if data["action"] == "search":
                    message = ""
                    counter = 0
                    buttons = []

                    search_results = self.yt_dlp_processor.search(data["request"])

                    # Format results
                    extractor_name_temp = ""
                    for search_result in search_results:
                        # Add name of extractor (Header)
                        if search_result["extractor"] != extractor_name_temp:
                            extractor_name_temp = search_result["extractor"]
                            message += f"\n{search_result['extractor_full_name']}\n\n"

                        # Format and add search result
                        counter += 1
                        message += self.messages["search_result"].format(
                            position=counter,
                            title=search_result["title"],
                            link=search_result["link"],
                            channel=search_result["channel"],
                            metadata=" | ".join(search_result.get("metadata", [])),
                        )

                        # Add markup button for each search result
                        callback_data = (
                            f"fmt|{data['edit_message_id']}|{search_result['extractor']}|{search_result['id']}"
                        )
                        buttons.append(
                            InlineKeyboardButton(
                                self.messages["button_search_result_text"].format(
                                    extractor_icon=search_result["extractor_icon"],
                                    position=counter,
                                    title=search_result["title"],
                                ),
                                callback_data=callback_data,
                            )
                        )

                    # Add footer
                    if message:
                        message = message + self.messages["search_result_suffix"]

                    # Handle empty message
                    else:
                        message = self.messages["search_no_results"]

                    # Save into database for back button
                    self.database_data_setter(data["chat_id"], "search_result", "text", message)
                    if len(buttons) != 0:
                        self.database_data_setter(
                            data["chat_id"], "search_result", "markup", InlineKeyboardMarkup(build_menu(buttons))
                        )
                    else:
                        self.database_data_setter(data["chat_id"], "search_result", "markup", None)
                    self.database_data_setter(
                        data["chat_id"], "search_result", "edit_message_id", data["edit_message_id"]
                    )

                    # Edit message
                    async_helper(
                        send_safe(
                            chat_id=data["chat_id"],
                            text=self.database_data_getter(data["chat_id"], "search_result", "text"),
                            api_token=self.config["bot_token"],
                            edit_message_id=data["edit_message_id"],
                            reply_markup=self.database_data_getter(data["chat_id"], "search_result", "markup"),
                            markdown=True,
                        )
                    )

                ############
                # Download #
                ############
                elif data["action"] == "download":
                    try:
                        filepath = None
                        error_text = None
                        self._progress_timer = None
                        self._progress_message = None
                        try:
                            filepath = self.yt_dlp_processor.download(
                                extractor=data["extractor"],
                                id_=data["id"],
                                format_id=data["format_id"],
                                is_audio=not data["is_video"],
                                target_format=(
                                    self.config["target_format_video"]
                                    if data["is_video"]
                                    else self.config["target_format_audio"]
                                ),
                                audio_author=data.get("author"),
                                audio_title=data.get("title"),
                                audio_target_bitrate=data.get("bitrate"),
                                progress_callback=self._progress_callback,
                                progress_callback_args=(data["chat_id"], data["edit_message_id"]),
                            )
                        except Exception as e:
                            logging.error("Downloading error", exc_info=e)
                            error_text = str(e)

                        # Send file
                        if filepath:
                            logging.info(f"Sending {filepath}")
                            self._progress_timer = None
                            self._progress_callback(
                                {"finished": False, "progress": 1.0, "postprocessor": "Sending file"},
                                (data["chat_id"], data["edit_message_id"]),
                            )
                            time.sleep(1)
                            async_helper(
                                Bot(self.config["bot_token"]).send_document(
                                    chat_id=data["chat_id"],
                                    document=open(filepath, "rb"),
                                    read_timeout=self.config["send_file_timeout"],
                                    write_timeout=self.config["send_file_timeout"],
                                )
                            )
                            message = self.messages["download_finished"]

                        # Send error message
                        else:
                            message = self.messages["download_error"].format(error=error_text)

                        # Add back button
                        buttons = [
                            (
                                InlineKeyboardButton(
                                    self.messages["button_back"],
                                    callback_data=f"fmt|{data['edit_message_id']}|{data['extractor']}|{data['id']}",
                                )
                            )
                        ]
                        reply_markup = InlineKeyboardMarkup(build_menu(buttons))

                        # Send final message (may also be error message)
                        async_helper(
                            send_safe(
                                chat_id=data["chat_id"],
                                text=message,
                                api_token=self.config["bot_token"],
                                edit_message_id=data["edit_message_id"],
                                reply_markup=reply_markup,
                            )
                        )

                    # Try to clean things up
                    finally:
                        self.yt_dlp_processor.cleanup()

            # Try to send unhandled error message without any markup
            except Exception as e:
                logging.error("Error processing queue request", exc_info=e)
                async_helper(
                    send_safe(
                        chat_id=data["chat_id"],
                        text=self.messages[f"{data['action']}_error"].format(error=str(e)),
                        api_token=self.config["bot_token"],
                        edit_message_id=data["edit_message_id"],
                    )
                )

        # Done
        logging.info("_download_queue_listener() finished")

    def _progress_callback(self, progress_data: Dict, args: Tuple[int, int]) -> None:
        """Updates progress message every progress_edit_interval

        Args:
            progress_data (Dict):
            {
                "finished": True or False, (currently not used)
                "progress": 0.0 - 1.0,
                "elapsed": the number of seconds since download started (if available)
                "eta: the estimated time in seconds (if available)
                "speed: the download speed in bytes/second (if available),
                "postprocessor": name of post-processor (if available)
            }
            args (Tuple[int, int]): (chat_id, edit_message_id)
        """
        chat_id, edit_message_id = args

        if self._progress_timer is None:
            self._progress_timer = 0.0

        # We cannot send messages too fast
        if time.time() - self._progress_timer > self.config["progress_edit_interval"]:
            self._progress_timer = time.time()

            stage = progress_data.get("postprocessor", "")
            logging.info(f"{stage if stage else 'Downloading'}: {(progress_data['progress'] * 100.0):.2f}%")

            # Check if we can proceed (if we have message to edit)
            if edit_message_id is None or edit_message_id < 0:
                return

            # Create progress bar out of emojis
            progress_bar_ones = max(1, int(progress_data["progress"] * self.config["progress_bar_length"]))
            progress_bar_zeros = self.config["progress_bar_length"] - progress_bar_ones
            progress_bar = self.messages["download_progress_1"] * progress_bar_ones
            progress_bar += self.messages["download_progress_0"] * progress_bar_zeros

            # Speed (original one is in bytes per second)
            speed = progress_data.get("speed")
            if speed is not None:
                if speed > 1024 * 1024:
                    speed = f"{(speed / 1024.0 / 1024.0):.2f} MiB/s"
                elif speed > 1024:
                    speed = f"{(speed / 1024.0):.2f} KiB/s"
                else:
                    speed = f"{speed:.0f} B/s"
            else:
                speed = "-- -/-"

            # Time passed
            elapsed = progress_data.get("elapsed")
            if elapsed is not None:
                elapsed = str(timedelta(seconds=int(elapsed)))
            else:
                elapsed = "-:--:--"

            # Time left
            eta = progress_data.get("eta")
            if eta is not None:
                eta = str(timedelta(seconds=int(eta)))
            else:
                eta = "-:--:--"

            # Format everything
            message = self.messages["download_progress"].format(
                progress_bar=progress_bar,
                stage=stage,
                progress=progress_data["progress"] * 100.0,
                speed=speed,
                elapsed=elapsed,
                eta=eta,
            )

            # Send only if message changed to prevent "Message is not modified" error
            if message != self._progress_message:
                async_helper(
                    send_safe(
                        chat_id=chat_id,
                        text=message,
                        api_token=self.config["bot_token"],
                        edit_message_id=edit_message_id,
                    )
                )

            # Save message for future check
            self._progress_message = message
