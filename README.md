# 🦐 liberty-media-telegram-bot

| ![Project logo](logo.jpg) | <h3>YT_DLP based Telegram bot for downloading media files from popular stream platforms</h3> |
| ------------------------- | :------------------------------------------------------------------------------------------: |

<div style="width:100%;text-align:center;">
    <p align="center">
        <img src="https://badges.frapsoft.com/os/v1/open-source.png?v=103" >
    </p>
</div>
<div style="width:100%;text-align:center;">
    <p align="center">
        <a href="https://www.youtube.com/@F3RNI"><img alt="YouTube" src="https://img.shields.io/badge/-YouTube-red" ></a>
        <a href="https://f3rni.bandcamp.com"><img alt="Bandcamp" src="https://img.shields.io/badge/-Bandcamp-cyan" ></a>
        <a href="https://open.spotify.com/artist/22PQ62alehywlYiksbtzsm"><img alt="Spotify" src="https://img.shields.io/badge/-Spotify-green" ></a>
        <a href="https://soundcloud.com/f3rni"><img alt="SoundCloud" src="https://img.shields.io/badge/-SoundCloud-orange" ></a>
    </p>
</div>

----------

## 😋 Support project

> 💜 Please support the project so that I can continue to develop it

- BTC: `bc1qd2j53p9nplxcx4uyrv322t3mg0t93pz6m5lnft`
- ETH: `0x284E6121362ea1C69528eDEdc309fC8b90fA5578`
- ZEC: `t1Jb5tH61zcSTy2QyfsxftUEWHikdSYpPoz`

- Or by my music on [🔷 bandcamp](https://f3rni.bandcamp.com/)

- Or [message me](https://t.me/f33rni) if you would like to donate in other way 💰

----------

## 🐧 Running as service on linux

1. Install Python **3.10** / **3.11** *(not tested on other versions)*, `venv` and `pip`
2. Clone repo
   1. `git clone https://github.com/F33RNI/liberty-media-telegram-bot.git`
   2. `cd liberty-media-telegram-bot`
3. Create venv `python -m venv venv` / `python3 -m venv venv` / `python3.10 -m venv venv` / `python3.11 -m venv venv`
4. Carefully change all the settings in `config.json` file
5. Install systemd
   1. `sudo apt-get install -y systemd`
6. Create new service file
   1. `sudo nano /etc/systemd/system/liberty-media-telegram-bot.service`

      ```ini
      [Unit]
      Description=liberty-media-telegram-bot service
      After=multi-user.target
      
      [Service]
      Type=simple
      Restart=on-failure
      RestartSec=5
      
      WorkingDirectory=YOUR DIRECTORY HERE/liberty-media-telegram-bot
      ExecStart=YOUR DIRECTORY HERE/liberty-media-telegram-bot/run.sh
      
      [Install]
      WantedBy=multi-user.target
      
      ```

7. Reload systemctl daemon
   1. `sudo systemctl daemon-reload`
8. Enable and start service
   1. `sudo systemctl enable liberty-media-telegram-bot`
   2. `sudo systemctl start liberty-media-telegram-bot`

----------

## 🚧 README coming soon
