## Download

| Computer | File | How to start |
|---|---|---|
| Windows | `LocalTTS-windows.exe` | Double-click. Windows may warn ("Windows protected your PC"): click **More info**, then **Run anyway**. |
| Mac | `LocalTTS-macos.zip` | Unzip, move **LocalTTS** to Applications. The first time: right-click it, choose **Open**, then **Open** again (the app is not signed by Apple). |
| Linux | `LocalTTS-linux.tar.gz` | Unpack, then run `./LocalTTS`. |

The first start takes a few seconds. Offline voices (Kokoro, Piper) download once the first time you
use them, then work without internet. See the README for how to use the app.

**About "Edge (online)" voices:** they are meant for quick previews only. They use an unofficial
Microsoft service: your text is sent to Microsoft, the service may stop working, and the voices are
not licensed for publishing. Use Kokoro or Piper voices for finished material.

## Licenses and source code

LocalTTS is free software under the GNU GPL v3 (`LICENSE.txt`). The source code for exactly this
version is attached below ("Source code"). The app contains third-party components under their own
licenses, listed in `THIRD_PARTY_NOTICES.md`.

Each voice has its own license; see THIRD_PARTY_NOTICES.md before using generated audio commercially.
