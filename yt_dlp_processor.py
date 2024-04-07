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

import datetime
import logging
import math
import os
import shutil
import tempfile
from typing import Callable, Dict, List, Tuple

import yt_dlp
from PIL import Image

# How make raw information dictionaries to keep in memory to not waste time retrieving them again
_SEARCH_CACHE_ENTRIES = 100


def human_format(num) -> str:
    """Formats number to human-readable format
    See <https://stackoverflow.com/a/45846841> for more info

    Args:
        num: any large or small number

    Returns:
        str: formatted number
    """
    num = float(f"{num:.3g}")
    magnitude = 0
    while abs(num) >= 1000:
        magnitude += 1
        num /= 1000.0
    return f"{num:f}".rstrip("0").rstrip(".") + ["", "K", "M", "B", "T"][magnitude]


def bitrate_to_str(bitrate: float) -> str:
    """Converts bitrate to 00.0kbps or 00.0Mbps format

    Args:
        bitrate (float): bitrate in kbps

    Returns:
        str: formatted bitrate
    """
    if bitrate < 1000:
        return f"{bitrate:.1f}kbps"
    else:
        return f"{bitrate / 1000.0:.1f}Mbps"


class AlbumArtPP(yt_dlp.postprocessor.PostProcessor):
    def run(self, information):
        """Extracts thumbnail, crops to 1:1 and saves it as JPEG file replacing the original one"""
        # Find thumbnail path
        thumb_index = next((-i for i, t in enumerate(information["thumbnails"][::-1], 1) if t.get("filepath")), None)
        if thumb_index is None:
            logging.warning("Unable to find thumbnail")
            return [], information
        thumb_name = information["thumbnails"][thumb_index]["filepath"]

        # Open it with Pillow
        thumbnail_path = os.path.join(information["__finaldir"], thumb_name)
        logging.info(f"Processing thumbnail: {thumbnail_path}")
        thumbnail = Image.open(thumbnail_path)

        # Crop to 1:1
        left_crop = math.ceil((thumbnail.width - thumbnail.height) / 2)
        thumbnail = thumbnail.crop((left_crop, 0, thumbnail.width - left_crop, thumbnail.height))
        logging.info(f"Thumbnail final size: {thumbnail.width}x{thumbnail.height}")

        # Generate new path as JPEG
        thumb_name_new = os.path.splitext(thumb_name)[0] + ".jpg"
        thumbnail_path_new = os.path.join(information["__finaldir"], thumb_name_new)

        # Save as JPEG
        logging.info(f"Saving thumbnail as {thumbnail_path_new}")
        thumbnail.save(thumbnail_path_new)

        # Delete original one
        if thumbnail_path_new != thumbnail_path:
            logging.info(f"Deleting old thumbnail {thumbnail_path}")
            os.remove(thumbnail_path)

        # Update info
        information["thumbnails"][thumb_index]["filepath"] = thumb_name_new
        information["thumbnails"][thumb_index]["width"] = int(thumbnail.width)
        information["thumbnails"][thumb_index]["height"] = int(thumbnail.height)
        information["thumbnails"][thumb_index]["resolution"] = f"{thumbnail.width}x{thumbnail.height}"

        # Keep only current thumbnail
        thumb_info = information["thumbnails"][thumb_index]
        information["thumbnails"] = [thumb_info]

        return [], information


class ArtistTrackInjectorPP(yt_dlp.postprocessor.PostProcessor):
    def __init__(self, downloader=None, track=None, artist=None):
        self._track = track
        self._artist = artist
        super().__init__(downloader)

    def run(self, information):
        """Sets information["track"] and information["artist"]"""
        logging.info(f"Using {self._track} as a track name and {self._artist} as an artist name")
        information["track"] = self._track
        information["artist"] = self._artist
        return [], information


class RenamePP(yt_dlp.postprocessor.PostProcessor):
    def __init__(self, downloader=None, is_audio: bool = False):
        self._is_audio = is_audio
        super().__init__(downloader)

    def run(self, information):
        """Renames final file
        Renames audio files into Artist - Track (if self._is_audio is True) and removes '/', '\\' and '?' symbols
        """

        # Extract path and filename
        filepath = information["filepath"]
        filename = os.path.basename(filepath)

        filename_new = filename
        if self._is_audio:
            if "track" in information and "artist" in information:
                track = information["track"]
                artist = information["artist"]
                ext = os.path.splitext(information["filepath"])[1]
                filename_new = f"{artist} - {track}{ext}"
            else:
                logging.warning("No metadata")
                return [], information

        # Remove path delimiters from name
        filename_new = filename_new.replace("/", "-")
        filename_new = filename_new.replace("\\", "-")

        # Remove windows-unfriendly characters from name
        filename_new = filename_new.replace("?", "")

        logging.info(f"Renaming to {filename_new}")
        if not os.path.exists(os.path.join(information["__finaldir"], filename_new)):
            os.rename(
                os.path.join(information["__finaldir"], filename),
                os.path.join(information["__finaldir"], filename_new),
            )
            if information["__finaldir"] in filepath:
                filepath_new = os.path.join(information["__finaldir"], filename_new)
            else:
                filepath_new = filename_new

            logging.info(f"Replacing filepath {filepath} to {filepath_new}")
            information["filepath"] = filepath_new
        else:
            logging.warning("File already exists, skipping")

        return [], information


class YTDlPProcessor:
    def __init__(self, config: Dict) -> None:
        self.config = config

        self.temp_dir = None

        self._filename = None
        self._progress_callback = None
        self._progress_callback_args = None
        self._progress = 0
        self._timed_out = False

        # Used for get_formats() and download()
        self._search_cache = []

        # Idk how to to this gracefully, so here it is. Result will be: {"youtube:search": "YoutubeSearch", ...}
        self._key_to_ie_key = {}
        logging.info("Parsing extractors")
        for extractor in yt_dlp.list_extractors():
            try:
                name = extractor.IE_NAME
                ie_key = extractor.ie_key()
                if not name or not ie_key:
                    continue
                self._key_to_ie_key[name] = ie_key
            except:
                pass
        logging.info(f"Found {len(self._key_to_ie_key)} extractors")

    def _add_extractors(self, ydl: yt_dlp.YoutubeDL, extractor_name: str or None = None) -> List[Tuple[str, str]]:
        """Adds extractors to ydl. Used in search() and download() functions

        Args:
            ydl (yt_dlp.YoutubeDL): ydl instance
            extractor_name (str or None, optional): use only specific extractor (ex. "youtube") and skip other

        Raises:
            Exception: in case of something goes wrong

        Returns:
            List[Tuple[str, str]]: [("ExtractorIeKey", "search formatter")]
        """
        extractors_ = []
        for extractor in self.config["extractors"]:
            if (
                not extractor.get("enabled")
                or "name" not in extractor
                or "keys" not in extractor
                or "icon" not in extractor
                or "full_name" not in extractor
            ):
                logging.info(f"Skipping {extractor.get('name')} extractors")
                continue
            if extractor_name and not extractor["name"].startswith(extractor_name):
                continue

            logging.info(f"Adding {extractor['name']} extractors")
            added = []
            try:
                for key in extractor["keys"]:
                    # Multiple extractors
                    if key.endswith(":*"):
                        key = key[:-2]
                        for key_, ie_key in self._key_to_ie_key.items():
                            if not key_.startswith(key):
                                continue
                            if (ie_key, extractor.get("search_query_formatter")) in extractors_:
                                continue
                            # ydl.add_info_extractor(ydl.get_info_extractor(ie_key))
                            ydl.get_info_extractor(ie_key)
                            extractors_.append((ie_key, extractor.get("search_query_formatter")))
                            added.append(ie_key)

                    # Only one extractor
                    else:
                        ie_key = self._key_to_ie_key.get(key)
                        if not ie_key:
                            continue
                        if (ie_key, extractor.get("search_query_formatter")) in extractors_:
                            continue
                        # ydl.add_info_extractor(ydl.get_info_extractor(ie_key))
                        ydl.get_info_extractor(ie_key)
                        extractors_.append((ie_key, extractor.get("search_query_formatter")))
                        added.append(ie_key)

            except Exception as e_:
                logging.warning(f"Cannot add {extractor.get('name')} extractors. Skipping it. Error: {e_}")
            logging.info(f"{extractor['name']} extractors: {' ,'.join(added)}")

        ydl.add_default_info_extractors()

        return extractors_

    def _build_ydl_opts(self, extractor_name: str or None = None) -> Dict:
        """Build ydl_opts based on dictionary from config. Used in search() and download() functions

        Args:
            extractor_name (str or None, optional). use only specific extractor (ex. "youtube") and skip other

        Returns:
            Dict: ydl_opts
        """
        # Get base from config
        ydl_opts = self.config["ydl_opts"]

        # Add arguments for each extractor
        if "extractor_args" not in ydl_opts:
            ydl_opts["extractor_args"] = {}
        for extractor in self.config["extractors"]:
            if not extractor.get("enabled") or "name" not in extractor or "args" not in extractor:
                continue
            if extractor_name and not extractor["name"].startswith(extractor_name):
                continue
            keys = extractor.get("keys", [])
            for key in keys:
                ydl_opts["extractor_args"][key] = extractor["args"]

        return ydl_opts

    def extractor_name_to_full_name_and_icon(self, extractor: str) -> Tuple[str, str]:
        """Converts extractor name into extract's full name and emoji icon

        Args:
            extractor (str): name of extractor (ex. "youtube")

        Returns:
            Tuple[str, str]: ("Extractor's full name", "Icon as emoji")
        """
        for extractor_ in self.config["extractors"]:
            if "name" not in extractor_ or "icon" not in extractor_ or "full_name" not in extractor_:
                continue
            if extractor_["name"] == extractor:
                return extractor_["full_name"], extractor_["icon"]
        return extractor, f"[{extractor}]"

    def fix_id(self, extractor: str or None, id_: str) -> str or None:
        """Changes video / audio ID to link for some extractors
        Used in get_formats() and download()

        Args:
            extractor (str or None): (ex. "youtube")
            id_ (str): video / audio ID

        Returns:
            str or None: "fixed" ID (URL in most cases) or None if it's impossible to fix
        """
        if not extractor:
            return None

        # Firstly try to fix using cache
        info = self._cache_get(extractor_name=extractor, id_=id_)
        if info is not None:
            link = info.get("webpage_url")
            if link:
                return link

        # Now the chances are very low...
        if extractor == "soundcloud" or extractor.lower() == "bandcamp":
            return None
        elif extractor == "vk":
            return "https://vk.com/vkvideo?z=video" + id_
        elif extractor.lower() == "pornhub":
            return "https://rt.pornhub.com/view_video.php?viewkey=" + id_

        return id_

    def search(self, query: str, extractor_name: str or None = None) -> List[Dict]:
        """Searches videos / tracks based on query without raising any error
        Raw results will be saved into self._search_cache

        Args:
            query (str): text or link to search
            extractor_name (str or None, optional). use only specific extractor (ex. "youtube") and skip other

        Returns:
            List[Dict]: [
                {
                    "extractor": "name of extractor (ex. 'youtube')",
                    "extractor_icon": "extractor's icon as emoji",
                    "extractor_full_name": "full name of extractor (with icon)",
                    "id": "video / track identifier",
                    "title": "full title of the video / track",
                    "link": "link to the original video / track",
                    "channel": "name of channel / uploader",
                    "metadata": [
                        "Some formatted metadata (see 'metadata_formatters' in config.json for more info)",
                    ]
                },
                ...
            ]
        """
        results = []
        try:
            # Build options
            ydl_opts = self._build_ydl_opts(extractor_name=extractor_name)

            # Add extractors and begin searching
            search_results = []
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Add extractors
                extractors_ = self._add_extractors(ydl, extractor_name=extractor_name)

                # Try to search using each each extractor
                for ie_key, search_query_formatter in extractors_:
                    search_results_temp = []
                    try:
                        # Retrieve extractor, format query if needed and find if it's suitable
                        extractor = ydl.get_info_extractor(ie_key)
                        suitable_formatted = False
                        if search_query_formatter:
                            query_formatted = search_query_formatter.format(query=query)
                            suitable_formatted = extractor.suitable(query_formatted)
                        suitable_raw = extractor.suitable(query)
                        if not suitable_formatted and not suitable_raw:
                            continue

                        # Search formatted (ex. "ytsearch5:....") and fallback to a raw query
                        if suitable_formatted:
                            try:
                                logging.info(f"Trying to search formatted query using {ie_key} extractor")
                                search_results_temp = ydl.extract_info(query_formatted, download=False, ie_key=ie_key)
                                search_results_temp = search_results_temp["entries"]
                            except:
                                if suitable_raw:
                                    logging.info(f"Trying to search directly using {ie_key} extractor")
                                    search_results_temp = [ydl.extract_info(query, download=False, ie_key=ie_key)]

                        # Search raw only (URLs)
                        else:
                            logging.info(f"Trying to search directly using {ie_key} extractor")
                            search_results_temp = [ydl.extract_info(query, download=False, ie_key=ie_key)]

                    except Exception as e_:
                        logging.error(f"Error searching using {ie_key} extractor: {e_}")

                    # Merge results
                    for search_result_temp in search_results_temp:
                        search_results.append(search_result_temp)

            # Search done
            logging.info(f"Found {len(search_results)} raw search results")
            if len(search_results) == 0:
                return []

            # Parse
            logging.info("Parsing results")
            for i, search_result in enumerate(search_results):
                # Check for basic data
                id_ = search_result.get("id")
                extractor = search_result.get("extractor")
                title = search_result.get("fulltitle")
                if title is None:
                    title = search_result.get("title")
                if title is None:
                    title = "Untitled"
                link = search_result.get("webpage_url")
                channel = search_result.get("channel")
                if channel is None:
                    channel = search_result.get("uploader")
                if channel is None:
                    channel = "Unnamed"
                formats = search_result.get("formats", [])
                is_live = search_result.get("is_live", False)
                if not id_ or not extractor or not title or not link or not channel or len(formats) == 0 or is_live:
                    logging.warning(f"Skipping search result {i}")
                    continue

                # Strip and check title and channel / uploader (just in case)
                title = title.strip()
                channel = channel.strip()
                if not title or not channel:
                    logging.warning(f"Skipping search result {i}. No title or channel name / uploader")
                    continue

                # Format and add metadata
                metadata = []
                for metadata_key, metadata_formatter in self.config["metadata_formatters"].items():
                    metadata_info = search_result.get(metadata_key)
                    if metadata_info is None:
                        continue
                    try:
                        if metadata_key == "upload_date":
                            upload_datetime = datetime.datetime.strptime(metadata_info, "%Y%m%d")
                            metadata.append(metadata_formatter.format(upload_datetime))
                        else:
                            metadata.append(metadata_formatter.format(metadata_info))
                    except Exception as e_:
                        logging.warning(f"Cannot format metadata {metadata_key}: {e_}")

                # Save to the cache
                self._cache_put(search_result)

                # Append to the final (parsed) results
                full_name, icon = self.extractor_name_to_full_name_and_icon(extractor)
                results.append(
                    {
                        "extractor": extractor,
                        "extractor_icon": icon,
                        "extractor_full_name": full_name,
                        "id": id_,
                        "title": title,
                        "channel": channel,
                        "link": link,
                        "metadata": metadata,
                    }
                )

        # Log error
        except Exception as e:
            logging.error(f"Error searching using '{query}' query", exc_info=e)

        logging.info(f"Found {len(results)} usable results")
        return results

    def get_formats(self, extractor: str, id_: str) -> Tuple[List[Dict], List[Dict], str, str, bool]:
        """Retries audio and video streams

        Args:
            extractor (str): extractor name (key) (ex. "youtube")
            id_ (str): video / audio ID or link

        Raises:
            Exception: expired / other error

        Returns:
            Tuple[List[Dict], List[Dict]]: (audio formats, video formats, title, channel, from_metadata) where:
            audio formats: [
                {
                    "extractor": "same as extractor argument",
                    "id": "ID of format",
                    "name": "123.4kbps",
                    "bitrate": bitrate value in kbps as float
                }
            ]
            video formats: [
                {
                    "extractor": "same as extractor argument",
                    "id": "ID of format",
                    "resolution": "1920x1080",
                    "name": "1920x1080 @ 1.23Mbps",
                    "bitrate": bitrate value in kbps as float
                }
            ]
            from_metadata: True if we don't need to guess author (artist) and track name (title)
        """

        audio_formats = []
        video_formats = []

        title = "Untitled"
        channel = "Unknown"
        from_metadata = False

        # Try to find in cache
        from_cache = self._cache_get(extractor, id_)
        if from_cache is not None:
            info = from_cache
            logging.info(f"Formats for {extractor} media {id_} found in cache")

        # Search
        else:
            logging.info(f"Not found in cache. Extracting formats from {extractor} media: {id_}")
            id_ = self.fix_id(extractor, id_)
            if id_ is None:
                raise Exception("Unable to recover search query. Request is expired. Please make a new one")
            self.search(id_, extractor_name=extractor)
            info = self._cache_get(extractor, id_)

        # Check again (just in case)
        if info is None:
            raise Exception("Unable to extract formats")

        # Try to find real author and title
        if "track" in info and "artist" in info:
            title = info["track"]
            channel = info["artist"]
            from_metadata = True

        # Try to use title and name of channel / uploader instead
        else:
            title = info.get("fulltitle", title)
            channel = info.get("channel", info.get("uploader", channel))
            from_metadata = False

        # Parse formats
        logging.info("Parsing formats")
        audio_formats_ = []
        video_formats_ = []
        formats = info["formats"]
        for format_ in formats:
            # Make sure it has format_id and filesize and filesize didn't exceed max_file_size
            format_id = format_.get("format_id")
            filesize = format_.get("filesize")
            if filesize is None or not isinstance(filesize, int):
                filesize = 0
            if not format_id or filesize > self.config.get("max_file_size", 1073741824):
                continue

            # Retrieve file extensions and make sure that we have at least one of them
            audio_ext = format_.get("audio_ext")
            if audio_ext.lower() == "none":
                audio_ext = None
            video_ext = format_.get("video_ext")
            if video_ext.lower() == "none":
                video_ext = None
            if not audio_ext and not video_ext:
                continue

            # Retrieve video resolution and bitrates
            width = format_.get("width")
            height = format_.get("height")
            resolution = None
            if width and height:
                resolution = f"{width}x{height}"
            resolution = format_.get("resolution", resolution)
            if resolution.lower() == "audio only":
                resolution = None
            vbr = format_.get("vbr")
            if vbr is None:
                vbr = format_.get("tbr")
            abr = format_.get("abr")
            if abr is None:
                abr = format_.get("tbr")

            # Resolution and vbr must exist for video and abr must exist for audio
            if (audio_ext and not abr) or (video_ext and (not vbr or not resolution)):
                continue

            # Append
            if audio_ext:
                audio_formats_.append({"id": format_id, "bitrate": abr, "ext": audio_ext})
            else:
                video_formats_.append({"id": format_id, "resolution": resolution, "bitrate": vbr, "ext": video_ext})
        logging.info(f"Found {len(audio_formats_)} audio formats and {len(video_formats_)} video formats")

        logging.info("Sorting formats")

        # Sort video formats by bitrate
        video_formats_ = sorted(video_formats_, key=lambda format_: format_["bitrate"], reverse=True)

        # Keep only the best audio format
        if len(audio_formats_) != 0:
            audio_formats_ = sorted(audio_formats_, key=lambda format_: format_["bitrate"], reverse=True)
            bitrate = float(audio_formats_[0]["bitrate"])
            audio_formats = [
                {
                    "extractor": extractor,
                    "id": audio_formats_[0]["id"],
                    "name": bitrate_to_str(bitrate),
                    "bitrate": bitrate,
                    "title": title,
                    "channel": channel,
                }
            ]

        # If no audio formats -> replace audio format with a video because we can convert it to audio anyway
        elif len(video_formats_) != 0:
            video_formats_middle_index = len(video_formats_) // 2
            bitrate = float(video_formats_[video_formats_middle_index]["bitrate"])
            audio_formats = [
                {
                    "extractor": extractor,
                    "id": video_formats_[video_formats_middle_index]["id"],
                    "name": "Extract from video",
                    "bitrate": bitrate,
                    "title": title,
                    "channel": channel,
                }
            ]

        # Keep only formats with unique resolution
        video_formats = []
        for format_ in video_formats_:
            discard = False
            for existing_format_ in video_formats:
                if existing_format_["resolution"] == format_["resolution"]:
                    discard = True
                    break
            if discard:
                continue

            audio_format = None
            if len(audio_formats) != 0:
                audio_format = audio_formats[0]["id"]

            additional_format = f"+{audio_format}" if (audio_format and audio_format != format_["id"]) else ""
            video_formats.append(
                {
                    "extractor": extractor,
                    "id": format_["id"] + additional_format,
                    "resolution": format_["resolution"],
                    "name": f"{format_['resolution']} @ {bitrate_to_str(format_['bitrate'])}",
                    "bitrate": format_["bitrate"],
                    "title": title,
                    "channel": channel,
                }
            )

        logging.info(f"Found: {len(audio_formats)} best audio formats and {len(video_formats)} best video formats")
        return (audio_formats, video_formats, title, channel, from_metadata)

    def download(
        self,
        extractor: str,
        id_: str,
        format_id: str,
        is_audio: bool,
        target_format: str,
        audio_target_bitrate: float or None = None,
        audio_author: str or None = None,
        audio_title: str or None = None,
        progress_callback: Callable or None = None,
        progress_callback_args: Tuple or None = None,
    ) -> str or None:
        """Downloads specific stream and converts it into mp3 / mp4

        Args:
            extractor (str): extractor name (key) (ex. "youtube")
            id_ (str): video / audio ID or link
            format_id (str): ID of format to download
            is_audio (bool): True if it's audio, False if it's video
            target_format (str): target_format_audio ot target_format_video from main config (ex. "mp3")
            audio_target_bitrate (float or None, optional): target mp3 bitrate (only used if is_audio is True)
            audio_author (str or None, optional): author of the audio (only used if is_audio is True)
            audio_title (str or None, optional): title of the audio (only used if is_audio is True)
            progress_callback (Callable or None, optional): non-blocking function to handle progress data
                The following data will be passed into progress_callback:
                    (
                        {
                            "finished": True or False,
                            "progress": 0.0 - 1.0,
                            "elapsed": the number of seconds since download started (if available)
                            "eta: the estimated time in seconds (if available)
                            "speed: the download speed in bytes/second (if available),
                            "postprocessor": name of post-processor (if available)
                        },
                        progress_callback_args
                    )
            progress_callback_args (Tuple, optional): custom arguments to pass to progress_callback after dictionary

        Raises:
            Exception: in case of something goes wrong

        Returns:
            str or None: path to saved file
        """
        self._filename = None

        def _filename_post_hook(filename_):
            logging.info(f"Received downloaded path: {filename_}")
            self._filename = filename_

        logging.info("Creating temp directory. Please delete it manually after downloading is finished")
        self.temp_dir = tempfile.mkdtemp()

        ydl_opts = self._build_ydl_opts(extractor_name=extractor)
        ydl_opts["outtmpl"] = os.path.join(self.temp_dir, "%(title)s.%(ext)s")
        ydl_opts["format"] = format_id

        ydl_opts["post_hooks"] = [_filename_post_hook]
        ydl_opts["progress_hooks"] = [self._download_progress_hook]
        ydl_opts["postprocessor_hooks"] = [self._download_progress_hook]

        self._progress_callback = progress_callback
        self._progress_callback_args = progress_callback_args
        self._timed_out = False

        logging.info(f"Downloading {extractor} media {id_} into {self.temp_dir} directory")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Add extractors
            extractors_ = self._add_extractors(ydl, extractor_name=extractor)

            # Convert to MP3 -> Inject artist - track into info -> Embed metadata -> Prepare thumbnail -> Embed it
            if is_audio:
                ydl.add_post_processor(
                    yt_dlp.postprocessor.FFmpegExtractAudioPP(
                        ydl, preferredcodec=target_format, preferredquality=audio_target_bitrate
                    )
                )
                ydl.add_post_processor(ArtistTrackInjectorPP(ydl, track=audio_title, artist=audio_author))
                ydl.add_post_processor(yt_dlp.postprocessor.FFmpegMetadataPP(ydl))
                ydl.add_post_processor(AlbumArtPP(ydl))
                ydl.add_post_processor(yt_dlp.postprocessor.EmbedThumbnailPP(ydl))

            # Convert to MP4 -> Embed thumbnail
            else:
                ydl.add_post_processor(yt_dlp.postprocessor.FFmpegVideoConvertorPP(ydl, preferedformat=target_format))
                try:
                    ydl.add_post_processor(yt_dlp.postprocessor.EmbedThumbnailPP(ydl))
                except Exception as e:
                    logging.warning(f"Unable to embed thumbnail to video file: {e}")

            # Rename into safe name
            ydl.add_post_processor(RenamePP(ydl, is_audio))

            # Try to recover ID
            id_ = self.fix_id(extractor, id_)
            if id_ is None:
                raise Exception("Unable to recover search query. Request is expired. Please make a new one")

            # Try each enabled extractors until downloaded
            for extractor_ie_key, _ in extractors_:
                if self._filename is not None:
                    break
                try:
                    logging.info(f"Trying to download using {extractor_ie_key} extractor")
                    ydl.extract_info(id_, download=True, ie_key=extractor_ie_key)
                except Exception as e:
                    logging.warning(f"Unable to download using {extractor_ie_key} extractor: {e}")
                if self._timed_out:
                    raise Exception(f"Timed out downloading with {extractor_ie_key} extractor")

        logging.info("Downloading finished")
        return self._filename

    def cleanup(self) -> None:
        """Removes temp directory if exists without raising any error
        Must be called after download
        """
        if self.temp_dir is not None and os.path.exists(self.temp_dir):
            try:
                logging.info(f"Trying to delete {self.temp_dir} directory")
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                logging.error(f"Error deleting {self.temp_dir} directory: {e}")

    def _download_progress_hook(self, progress_info: Dict) -> None:
        """Redirects some callback data into self._progress_callback without raising any error
        and aborts downloading on timeout by raising an error (also it will set self._timed_out flag)

        Raises:
            Exception: Exception("Downloading timed out...")

        Args:
            progress_info (Dict): data from YoutubeDL
        """
        if self._progress_callback is None:
            return

        # Status should always be here
        status = progress_info.get("status")
        if not status:
            return

        # Check for timeout
        elapsed = progress_info.get("elapsed", 0)
        if status != "finished" and elapsed > self.config["download_timeout"]:
            self._timed_out = True
            raise Exception(f"Downloading timed out ({elapsed} > {self.config['download_timeout']})")

        progress_info_ = {"finished": status == "finished"}

        # Calculated progress using bytes or fragments
        progress = -1
        try:
            if "downloaded_bytes" in progress_info and "total_bytes" in progress_info:
                progress = progress_info.get("downloaded_bytes") / progress_info.get("total_bytes")
            elif "fragment_index" in progress_info and "fragment_count" in progress_info:
                progress = progress_info.get("fragment_index") / progress_info.get("fragment_count")
        except Exception as e:
            logging.warning(f"Cannot calculate progress: {e}")

        # Use stored progress if progress is not available
        if progress < 0:
            progress = self._progress
        else:
            self._progress = progress

        progress_info_["progress"] = progress

        # Add some extra keys
        for key in ["elapsed", "eta", "speed", "postprocessor"]:
            if key in progress_info:
                progress_info_[key] = progress_info[key]

        # Redirect
        try:
            self._progress_callback(progress_info_, self._progress_callback_args)
        except Exception as e:
            logging.warning(f"progress_callback() error: {e}")

    def _cache_put(self, entry: Dict) -> None:
        """Saves entry to the self._search_cache and discards old entries

        Args:
            entry (Dict): raw information dictionary
        """
        if len(self._search_cache) >= _SEARCH_CACHE_ENTRIES:
            self._search_cache.pop(0)
        self._search_cache.append(entry)

    def _cache_get(self, extractor_name: str, id_: str) -> Dict or None:
        """Searches entry by video / track ID and extractor key in self._search_cache

        Args:
            extractor_name (str): extractor name (key) to search (ex. "youtube")
            id_ (str): video / track ID to search in cache

        Returns:
            Dict or None: raw information dictionary or None if not found
        """
        for cache_entry in self._search_cache:
            if cache_entry.get("extractor") == extractor_name and cache_entry.get("id") == id_:
                return cache_entry
