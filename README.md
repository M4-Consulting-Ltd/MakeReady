# Watchtower-SetReady

Virtual **Set Ready** buttons for a [Watchtower](https://watchtower3d.com) printer farm.
One web page lists every printer Watchtower knows about, shows its state, and gives each
one a button that tells Watchtower the plate has been cleared. It does the job of the
[ClearCue](https://github.com/ThePrintBench/ClearCue) hardware buttons without buying or
building any: open the page on a phone, tablet or wall-mounted screen in the farm.

## Running it

Needs Python 3.8+ and nothing else.

1. In Watchtower, create an API key (starts with `wt_`) with the `control` scope,
   allowed to send `set_ready` and (for the Resume button) `resume`.
2. Run `python3 server.py` and open `http://localhost:8080`.
3. The **Settings** dialog opens on first use. Enter:
   - **Watchtower address**: the IP address or name of the Watchtower computer, e.g.
     `192.168.1.50`. `https://` and `/api/public/v1` are added for you; type
     `http://…` or a `:port` if yours needs one.
   - **API key**: the key from step 1.
   - **Skip the certificate check**: tick this for `https://` unless you have set
     `WATCHTOWER_CA` (below). Watchtower signs its certificate with its own local CA,
     which Python doesn't trust by default.

The **Settings** button in the page header changes these later. They are saved in
`config.json` next to `server.py`. The API key is never shown again, and has to be
re-entered whenever the address changes.

### Cameras (optional)

To show a camera picture on each printer's card, fill in **Cameras** in the Settings
dialog with a Watchtower username and password. Each card then shows a snapshot that
refreshes every 5 seconds (a still image, not live video). A camera named "chamber" is
preferred when a printer has several; printers with no online camera just show no picture.

Cameras aren't part of Watchtower's public API, so the API key can't fetch them. The
server logs in to Watchtower's own web interface instead, which has two consequences:

- The password is stored in `config.json` on the computer running `server.py`, and it
  grants much more than the API key does. Use a dedicated Watchtower user with as few
  rights as possible if you can.
- That interface is undocumented and can change with a Watchtower update. If it does,
  the pictures stop; the buttons keep working.

Other options go in `config.json` (see `config.example.json`) or environment variables
of the same name:

- `HOST`: `127.0.0.1` (default) serves the page only on this computer; `0.0.0.0` makes
  it reachable from phones and tablets on the network.
- `PORT`: default `8080`.
- `WATCHTOWER_CA`: path to Watchtower's CA certificate (PEM), to verify `https://`
  properly instead of skipping the check.
- `WATCHTOWER_URL`, `WATCHTOWER_API_KEY`, `WATCHTOWER_USERNAME`, `WATCHTOWER_PASSWORD`:
  the same values as the Settings dialog.

**Anyone who can open the page can press the buttons and change the settings.** There
is no login; with
`HOST` set to `0.0.0.0`, keep it on a network you trust.

## Docker and Unraid

The image listens on port 8080 and keeps `config.json` in `/config`.

```bash
docker run -d --name watchtower-setready --restart unless-stopped \
  -p 8090:8080 -v /path/to/settings:/config \
  ghcr.io/m4-consulting-ltd/watchtower-setready:latest
```

The image is built and pushed to GitHub's container registry by
`.github/workflows/docker.yml` on every push to `main`. To build it yourself instead:
`docker build -t watchtower-setready .` in this folder.

### Unraid

1. Copy `unraid/watchtower-setready.xml` to the flash drive as
   `/boot/config/plugins/dockerMan/templates-user/my-watchtower-setready.xml`
   (over the network that is the `flash` share, folder
   `config/plugins/dockerMan/templates-user`).
2. In Unraid: **Docker → Add Container → Template → watchtower-setready**, check the
   port (default 8090) and settings folder (default
   `/mnt/user/appdata/watchtower-setready`), then **Apply**.
3. Open the container's **WebUI** and fill in Settings.

To update: **Docker → Check for Updates**, then update the container.

If the image is private (GitHub makes it private when the repository is), Unraid
can't pull it until you either make the package public (GitHub → the package →
Package settings → Change visibility) or log in on Unraid with
`docker login ghcr.io` using a GitHub token that has `read:packages`.

## Using it

| Card | Meaning | Button |
|---|---|---|
| Green | Ready for the next job | Nothing to do |
| Solid red, filled button | Plate waiting to be cleared | **Press once the plate is clear** |
| Red, slow pulse | Printing, with progress, time left and finish time when Watchtower reports them | Disabled |
| Red, fast blink | Paused or error | **Resume print**: tap, then tap again within 4 seconds to confirm |
| Amber | Watchtower reports the printer offline | Disabled |

The page refreshes every 5 seconds. The browser tab title shows how many plates are
waiting, e.g. `(2) Watchtower SetReady`.

## How it works

`server.py` serves `index.html` and forwards these calls to Watchtower, adding the API key:

| Page calls | Watchtower call |
|---|---|
| `GET /api/printers` | `GET {base}/printers` |
| `POST /api/printers/{name}/set-ready` | `POST {base}/printers/{name}/commands` with `{"command":"set_ready"}` |
| `POST /api/printers/{name}/resume` | the same call with `{"command":"resume"}` |
| `GET /api/cameras`, `GET /api/camera/{name}` | (camera login only) `GET /api/system/streams`, `GET /api/go2rtc/frame.jpeg?src=…` |

The page talks to this small server rather than straight to Watchtower so the API key
never reaches the browser, and so the browser doesn't have to trust Watchtower's
certificate or be allowed by its cross-origin rules.

The `set_ready` call and the state rules are taken from ClearCue: offline when
`online` is false; printing when `busy` (paused/error if a status string says so);
ready when `ready`; otherwise the plate is waiting and the button is live.
