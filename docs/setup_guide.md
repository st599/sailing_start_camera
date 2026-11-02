# Setup guide

This takes you from nothing to a test sequence, in about an hour. It's for whoever sets the system up
once for the club, and you'll need to be comfortable typing commands. Race officers only need the
[race-day guide](race_day_guide.md).

There are two ways to run it:

- **A laptop** shows the picture and has all the settings. Use this to set everything up in any case.
- **A Raspberry Pi 5** has no screen, and runs from a configuration made on the laptop. Race officers
  control it from a phone. **This hasn't been tried on a real Pi yet**, so see
  [the section below](#5-optional-a-raspberry-pi).

## 1. Set up the camera

1. Set the Sonoff Cam-S3W up in the eWeLink app, on the same network as the computer.
2. Find its address, from your router's list of devices or the eWeLink app.
3. Open `http://<camera-address>/` in a browser, and set a login password if it asks.
4. Under **ONVIF/RTSP**, turn it on and set a username and password. These are the camera login.
5. Turn **Watermark** off, otherwise the camera adds its own clock and logo.

More detail is in [the camera notes](camera.md).

## 2. Install the software

These steps are for Ubuntu or Debian, on a computer with internet access. The first run downloads
one more program, so run it once before taking a computer anywhere without internet.

```bash
sudo apt install git python3-venv ffmpeg libxcb-cursor0
git clone https://github.com/st599/sailing_start_camera.git
cd sailing_start_camera
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt -r requirements-gui.txt
venv/bin/python -m pip install --no-deps .
```

Check the clock is being set automatically, because the overlays rely on it:

```bash
timedatectl
```

It should say `System clock synchronized: yes`. If it doesn't, try
`sudo systemctl restart systemd-timesyncd`, then look again.

## 3. Add your settings

Create `secrets.json` in the `sailing_start_camera` folder, with your camera login, and a password
of your choice for the phone page:

```json
{
  "camera_username": "the camera login username",
  "camera_password": "the camera login password",
  "control_password": "a long password for race officers"
}
```

Then protect it, find the camera and check it all:

```bash
chmod 600 secrets.json
echo '{ "version": 1 }' > config.json
venv/bin/startcam --scan --config config.json --secrets secrets.json --write
venv/bin/startcam --check-config --config config.json --secrets secrets.json
```

The scan should end with `Camera is at`, and the check should say `is valid`. If the camera's on a
different subnet, such as behind a WiFi extender, see [the camera notes](camera.md#finding-the-camera).

## 4. Run a test sequence

```bash
venv/bin/startcam-gui --config config.json --secrets secrets.json
```

Closing the window stops everything. In the window:

1. Check the camera's picture appears. Your settings are under **File, Settings**.
2. Open **File, Start sequences**. Choose **Rule 26, one class**, press **Use template**, then **OK**.
3. Open **File, Start line**. Click the two ends of the line, then any points between them, then
   press **OK**.
4. Press **File, Save**.
5. Press **Start now**. The top right should show `Class -05:00`, with the class flag beneath it.
6. Press **Postpone**, then **Resume**, and check the picture follows.
7. Press **Start streaming**, and open the first address it shows in a browser on another device.

That's the system working. To start again, press **Abandon**.

### Set up your club

- **Sequences:** open **File, Start sequences** again and choose your template. Tamesis Club's
  Summer Series are built in. **Check them against the current notice.** See
  [the GUI guide](gui.md#settings-and-files) for making your own.
- **Flags:** the international code flags are built in. Add others, such as class symbols, to a
  `custom_flags` folder, which is explained in [the flags guide](flags.md).
- **Start line:** place it with the camera in its final position, and use a separate configuration file
  for each starting box. If the camera moves, the line has to be placed again.
- **Camera delay:** with the camera in its final position, stop the window and run the
  [delay measurement](camera.md#delay), so the times on the picture are accurate.
- **Firewall:** if devices can't connect, open the ports in
  [the streaming guide](streaming.md#firewalls).
- **TV:** open the first address on the TV's web browser, and bookmark it. Reserve the computer's IP
  address on your router, so that the bookmark keeps working. See
  [the streaming guide](streaming.md#watching-on-a-smart-tv).
- **YouTube:** follow [the YouTube guide](youtube.md).

### A desktop icon for race officers

So that nobody needs a terminal, create `~/Desktop/start-camera.desktop`, changing the folder in both
places to where you installed it:

```ini
[Desktop Entry]
Type=Application
Name=Start Camera
Exec=/home/YOU/sailing_start_camera/venv/bin/startcam-gui --config config.json --secrets secrets.json --log-file startcam.log
Path=/home/YOU/sailing_start_camera
Terminal=false
```

Then make it runnable, and allow it to launch if your desktop asks:

```bash
chmod +x ~/Desktop/start-camera.desktop
gio set ~/Desktop/start-camera.desktop metadata::trusted true
```

### Fill in the race-day guide

Print [the race-day guide](race_day_guide.md), and write in a contact name, the phone page's address
(`https://<computer-address>:8443/`) and the TV's address (`http://<computer-address>:8080/`). Give
race officers the phone page's password, and show them the first-time security warning on their
phones, which is expected. Then run through a test sequence with them.

## 5. (Optional) A Raspberry Pi

**This hasn't been tried on a real Pi yet**, and these steps may change. Use a 64-bit OS and a Pi 5.

1. Install as in step 2, leaving out `libxcb-cursor0` and `-r requirements-gui.txt`.
2. Copy `config.json`, `secrets.json` and any `custom_flags` folder from the laptop, and run the
   check from step 3.
3. Run `venv/bin/startcam-benchmark --config config.json --secrets secrets.json --output results.md`.
   [The performance guide](performance.md) explains the results.
4. Start it with `venv/bin/startcam --config config.json --secrets secrets.json`.

Starting by itself at power-on is still to come. The commands are in
[the command-line guide](command_line.md).

## If something goes wrong

Messages appear in the window's log, or `startcam.log` if you use the desktop icon.

| Message | Meaning |
|---|---|
| `could not connect to display`, or a Qt `xcb` error | Check you're at the computer's desktop, and that `libxcb-cursor0` is installed |
| `No camera found` | Check the camera has power and ONVIF/RTSP is on, and that it's on the same network |
| `rejected the login` | The camera login in `secrets.json` doesn't match the camera's ONVIF/RTSP settings |
| `sent no stream` | ONVIF/RTSP is off on the camera |
| `Address already in use` | Another copy is running. Close it, then try again |
| `The system clock isn't synchronised` | The times shown may be wrong. See step 2 |
| `Can't keep up with` | This computer's too slow for the settings. Lower the frame rate or resolution |
| `Can't download MediaMTX` | The first run needs internet access |
| `No control_password in the secrets` | The phone page stays off until there's a password in `secrets.json` |
