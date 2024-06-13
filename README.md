# TheBumApp
A jokey Discord bot who speaks the word of our Lord (aka a document produced by an absurd machine learning algorithm).

Run bot.py to run the bot. I have no idea what will happen if multiple people try to run it concurrently.

DM the bot 'logout' to get it to logout. Or kill the shell while it's running.

Get the .env file from John. This contains the secrets. Maybe we should figure out how to upload the secrets securely.

# Setup

1. Put [ffmpeg.exe](https://github.com/yt-dlp/FFmpeg-Builds/releases/tag/latest) in the directory or on the PATH.
    curl -Lv -o ffmpeg.tar.xz https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz
    tar -xf ./ffmpeg.tar.xz
    sudo mv ffmpeg-master-latest-linux64-gpl/bin/ff* /usr/local/bin/
    cd ../..

2. Setup Python (Linux but Windows is similar)
    sudo apt install python3-pip
    sudo apt install python3-venv
    python3 -m venv .bumenv
    pip install -r ./requirements.txt

3. Setup libopus (Linux only, can be skipped on Windows)
    sudo apt install libopus-dev
    # Find the libopus.so file with find / -type f -name libopus* 2>/dev/null if needed

4. Setup service (If desired)
    sudo cp ./christotron.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl start christotron.service
    systemctl status christotron.service
    sudo systemctl enable christotron.service
    