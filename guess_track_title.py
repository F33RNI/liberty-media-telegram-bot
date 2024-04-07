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

import logging
import re
from typing import List, Tuple


def guess_track_title(title: str, channel: str or None = None) -> List[Tuple[str, str]]:
    """Tries to guess track's author and title from title and channel

    Args:
        title (str): full title of video
        channel (str or None, optional): name of channel / uploader. Defaults to None

    Returns:
        List[Tuple[str, str]]: List of (author, title) where the most possible one is the 1st
        [("Author", "Possible Track Name"), ("Author", "Another Possible Track Name")]

    >>> guess_track_title("F3RNI - What's Next?", "F3RNI")
    [('F3RNI', "What's Next?"), ('F3RNI', "F3RNI - What's Next?"), ('Unknown', "F3RNI - What's Next?")]
    >>> guess_track_title("What's Next? - F3RNI (slowed + reverb)", "F3RNI")
    [('F3RNI', "What's Next? (slowed + reverb)"), ('F3RNI', "What's Next? - F3RNI (slowed + reverb)"), ('Unknown', "What's Next? - F3RNI (slowed + reverb)")]
    >>> guess_track_title("F3RNI - What's Next?", "Someone")
    [('F3RNI', "What's Next?"), ("What's Next?", 'F3RNI'), ('Someone', "F3RNI - What's Next?"), ('Unknown', "F3RNI - What's Next?")]
    >>> guess_track_title("F3RNI - What's Next?")
    [('F3RNI', "What's Next?"), ("What's Next?", 'F3RNI'), ('Unknown', "F3RNI - What's Next?")]
    >>> guess_track_title("What's Next? - F3RNI")
    [('F3RNI', "What's Next?"), ("What's Next?", 'F3RNI'), ('Unknown', "What's Next? - F3RNI")]
    >>> guess_track_title("What's Next? - F3RNI", "F3RNI")
    [('F3RNI', "What's Next?"), ('F3RNI', "What's Next? - F3RNI"), ('Unknown', "What's Next? - F3RNI")]
    >>> guess_track_title("What's Next? - F3RNI", "Someone")
    [('F3RNI', "What's Next?"), ("What's Next?", 'F3RNI'), ('Someone', "What's Next? - F3RNI"), ('Unknown', "What's Next? - F3RNI")]
    """

    results = []

    try:
        # Remove " - Topic"
        if channel and channel.endswith(" - Topic"):
            channel = channel[:-8]

        # Try to use name of channel (if it's also in title, we can use it as author)
        if channel:
            if channel.lower() in title.lower():
                # Remove channel name from the title
                track_name = re.sub(re.escape(channel), "", title, flags=re.IGNORECASE).strip()

                # Remove delimiters and double spaces
                for delimiter in ["-", "—", ":", "_"]:
                    track_name = track_name.replace(delimiter, " ").strip()
                track_name = re.sub(" +", " ", track_name).strip()

                if track_name:
                    results.append((channel, track_name))

        # No channel or it's not in title -> use regex to extract possible author and track name
        if len(results) == 0:
            match = re.match(r"(.+?)\s*[-—:,_]\s*(.+)", title)
            if match:
                author, track_name = match.groups()
                author = author.strip()
                track_name = track_name.strip()
                if author and track_name:
                    if len(track_name.split(" ")) > len(author.split(" ")):
                        if (author, track_name) not in results:
                            results.append((author, track_name))
                        if (track_name, author) not in results:
                            results.append((track_name, author))
                    else:
                        if (track_name, author) not in results:
                            results.append((track_name, author))
                        if (author, track_name) not in results:
                            results.append((author, track_name))

        # Use the name of channel
        if channel and (channel, title) not in results:
            results.append((channel, title))

    except Exception as e:
        logging.warning(f"Cannot guess author and track name from '{title}': {e}")

    # The worst case
    if ("Unknown", title) not in results:
        results.append(("Unknown", title))

    # Default or unknown
    return results
