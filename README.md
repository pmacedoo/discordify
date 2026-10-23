# Discordify

### Your video. Small enough to send.

Ever been ready to share a clip, only for Discord to tell you **the file is too large**?
Now you’re opening a video editor, guessing bitrates, exporting again… all just to send one video.

**Discordify takes care of it. Drop your video in. Get a Discord-ready MP4 out.**

No settings to figure out. No uploads. No account. Just drag, drop, and send.

**[Download for Windows](https://github.com/pmacedoo/discordify/releases/latest)** · Free and open source · **by pedromacedo**

![Discordify](assets/preview.png)

## Less fiddling. More sharing.

- **Under 19 MB, automatically.** Compression and resolution are adjusted for you, and the file size is checked before saving.
- **Keep your original FPS.** Your 60 FPS clip stays 60 FPS. Variable frame rates are supported, too.
- **Keep the whole clip.** No trimming the ending just to fit a size limit.
- **Keep your original file.** The compressed copy is saved right next to it. Existing files stay untouched.
- **Keep it on your computer.** Processing is local. Your videos aren’t uploaded anywhere.
- **Out of the way when you’re done.** Close the window and Discordify stays in your system tray, ready for the next clip.

Already have an MP4 under 19 MB? Discordify leaves it as it is, avoiding unnecessary quality loss.

## Drag. Drop. Send.

1. **Open Discordify.** No installation required.
2. **Drop in your video.** Or click the drop area to choose a file.
3. **Share the result.** Find your `_discordify.mp4` beside the original, or click the notification to open its folder.

You can also drop a video directly onto `Discordify.exe`. Right-click the tray icon to cancel a conversion or quit.

## Get Discordify

**[Download the latest release →](https://github.com/pmacedoo/discordify/releases/latest)**

Choose `Discordify.exe` to get started, or grab the ZIP with documentation and license information included.
Requires **Windows 10 or 11, 64-bit**. Python and FFmpeg are bundled. The executable is currently unsigned.

The target is **19,000,000 bytes**. Discord’s upload limits depend on your account and context; check the limit shown in your client.
Very long videos may not fit with usable quality. Discordify reports an error rather than saving an incomplete result.
One video is processed at a time. Output uses H.264 video and AAC audio.

<details>
<summary><strong>For developers</strong></summary>

### Build from source

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python build.py
.venv\Scripts\python tests\test_compression.py
```

The executable is written to `dist/Discordify.exe`. The build bundles FFmpeg and generates the application icon.
After building, run `python src/discordify.py` to work on the interface.
The **Build Windows** workflow can also build the executable manually through GitHub Actions.

### Tested with real video conversion

The packaged executable reduced a **26.2 MB clip to 18.7 MB**, preserving **60 FPS, all 480 frames, and audio**.
Additional tests cover 29.97 FPS, variable frame rates, cancellation, and invalid input.
Frame timings are preserved within container precision. See [VALIDACAO.txt](VALIDACAO.txt) for the test record.

</details>

## Made by pedromacedo

Free and open source under [GPL-3.0-or-later](LICENSE). Copyright © 2026 pedromacedo.
See [THIRD_PARTY.md](THIRD_PARTY.md) for dependencies and their source code.

Discordify is an independent project and is not affiliated with Discord.
