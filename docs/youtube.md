# YouTube Live

This sends the stream to YouTube Live so that it can be embedded on the club website. It's
separate from the stream on the local network, which carries on if YouTube or the internet
fails.

## Setting up

1. In [YouTube Studio](https://studio.youtube.com/), open **Go live** and turn on live
   streaming. YouTube may take up to 24 hours to enable this on a new channel.
2. Open **Stream**, and copy the stream key and the stream URL. Use a saved, reusable
   stream key, so that it stays the same between race days.
3. Add the key to `secrets.json`. Treat it like a password, because anyone with it can
   stream to the channel:

   ```json
   { "youtube_stream_key": "your-key" }
   ```

4. In `config.json`, turn on the push. If YouTube's stream URL isn't the default
   `rtmps://a.rtmp.youtube.com/live2`, set `youtube_ingest_url` to it as well:

   ```json
   { "version": 1, "stream": { "youtube_enabled": true } }
   ```

5. In YouTube Studio's stream settings, turn on **Enable Auto-start** and
   **Enable Auto-stop**, so the broadcast follows the app. Set the audience and visibility,
   and allow embedding.

Run `startcam` as usual. It logs "Sending video to YouTube" once data is flowing. Video can
take up to a minute to appear in YouTube Studio.

To use a club account later, change the key in `secrets.json`, and the stream URL if it
differs.

## Embedding on the website

Replace `CHANNEL_ID` with the channel's ID, which starts with `UC`. It's listed in YouTube
under **Settings**, **Advanced settings**.

```html
<iframe
  width="1280"
  height="720"
  style="width: 100%; height: auto; aspect-ratio: 16 / 9"
  src="https://www.youtube.com/embed/live_stream?channel=CHANNEL_ID&mute=1"
  title="Start line camera"
  allow="autoplay; encrypted-media; picture-in-picture"
  allowfullscreen
></iframe>
```

This address stays the same between race days and shows whatever the channel has live. When
nothing is live, it shows YouTube's own message. YouTube doesn't document this address, and
reports differ on whether it still works, so test it on the channel. The stream must be
public and allow embedding.

If it doesn't work, give the broadcasts a playlist and embed that instead, or link to
`https://www.youtube.com/channel/CHANNEL_ID/live`.

## What to expect

- **Delay:** YouTube adds its own delay, typically several seconds to around half a
  minute, so don't use this stream for timing decisions.
- **Audio:** the stream has a silent audio track, and the camera's microphone isn't sent.
- **Internet failures:** it keeps retrying, up to every 30 seconds, and also restarts if no
  data has been sent for 30 seconds. If the outage is long, YouTube may end the broadcast,
  and a new broadcast can have a new video address.
- **Upload speed:** the stream needs about `stream.bitrate_kbps` plus 128 kbps, which is
  about 2.6 Mbps by default.

## Protecting the stream key

The key isn't written to the logs. It does appear in the `ffmpeg` command line, which other
users logged in to the same computer can see, so run this on a computer you control.
