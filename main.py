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

import argparse
import json
import logging
import os
import sys

from _version import __version__
import bot_handler

# Default config file path
CONFIG_FILE = "config.json"


def parse_args() -> argparse.Namespace:
    """Parses cli arguments

    Returns:
        argparse.Namespace: parsed arguments
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=os.getenv("CONFIG_FILE", CONFIG_FILE),
        required=False,
        help=f"path to config.json file (Default: {os.getenv('CONFIG_FILE', CONFIG_FILE)})",
    )
    parser.add_argument("-v", "--version", action="version", version=__version__)
    return parser.parse_args()


def logging_setup() -> None:
    """Sets up logging format and level"""
    # Logs formatter
    log_formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # Setup logging into console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)

    # Add all handlers and setup level
    root_logger = logging.getLogger()
    root_logger.addHandler(console_handler)
    root_logger.setLevel(logging.INFO)

    # Log test message
    logging.info("Logging setup is complete")


def main() -> None:
    """Main entry (blocking)
    Everything should be done in a single
    """
    # Parse arguments
    args = parse_args()

    # Initialize logging
    logging_setup()

    # Log software version and GitHub link
    logging.info(f"liberty-media-telegram-bot version: {__version__}")
    logging.info("https://github.com/F33RNI/liberty-media-telegram-bot")

    # Load configs
    logging.info(f"Loading config file {args.config}")
    with open(args.config, "r", encoding="utf-8") as file:
        config = json.loads(file.read())

    # Load messages
    logging.info(f"Loading messages file {config['messages_file']}")
    with open(config["messages_file"], "r", encoding="utf-8") as file:
        messages = json.loads(file.read())

    # Initialize and start telegram bot handler (blocking)
    logging.info("Initializing Telegram bot handler")
    bot_handler_ = bot_handler.BotHandler(config, messages)
    bot_handler_.start_bot()

    # Done
    logging.info("liberty-media-telegram-bot exited")


if __name__ == "__main__":
    main()
