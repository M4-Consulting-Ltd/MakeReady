# Watchtower-SetReady

Virtual **Set Ready** buttons for a [Watchtower](https://watchtower3d.com) printer farm.
One web page lists every printer Watchtower knows about, shows its state, and gives each
one a button that tells Watchtower the plate has been cleared. It does the job of the
[ClearCue](https://github.com/ThePrintBench/ClearCue) hardware buttons without buying or
building any: open the page on a phone, tablet or wall-mounted screen in the farm.

## Running it

Needs Python 3.8+ and nothing else.

1. In Watchtower, create an API key (starts with `wt_`) with the `control` scope and
   `set_ready`.
2. Copy `config.example.json` to `config.json` and fill in:
   - `WATCHTOWER_URL`: your Watchtower API base URL, ending in `/api/public/v1`
   - `WATCHTOWER_API_KEY`: the key from step 1
   - `WATCHTOWER_CA`: if the URL is `https://`, the path to Watchtower's CA certificate
     (PEM). Watchtower signs its certificate with its own local CA, so Python won't
     trust it otherwise. (`"WATCHTOWER_INSECURE": "1"` skips the check instead.)
   - `HOST`: leave `127.0.0.1` to use the page only on this computer; set `0.0.0.0` to
     reach it from phones and tablets on the network.
3. Run `python3 server.py` and open `http://<this-computer>:8080`.

Every setting can also be given as an environment variable of the same name.

**Anyone who can open the page can press the buttons.** There is no login; with
`HOST` set to `0.0.0.0`, keep it on a network you trust.

## Using it

| Card | Meaning | Button |
|---|---|---|
| Green | Ready for the next job | Nothing to do |
| Solid red, filled button | Plate waiting to be cleared | **Press once the plate is clear** |
| Red, slow pulse | Printing | Disabled |
| Red, fast blink | Paused or error | Disabled |
| Amber | Watchtower reports the printer offline | Disabled |

The page refreshes every 5 seconds. The browser tab title shows how many plates are
waiting, e.g. `(2) Watchtower SetReady`.

## How it works

`server.py` serves `index.html` and forwards two calls to Watchtower, adding the API key:

| Page calls | Watchtower call |
|---|---|
| `GET /api/printers` | `GET {base}/printers` |
| `POST /api/printers/{name}/set-ready` | `POST {base}/printers/{name}/commands` with `{"command":"set_ready"}` |

The page talks to this small server rather than straight to Watchtower so the API key
never reaches the browser, and so the browser doesn't have to trust Watchtower's
certificate or be allowed by its cross-origin rules.

The `set_ready` call and the state rules are taken from ClearCue: offline when
`online` is false; printing when `busy` (paused/error if a status string says so);
ready when `ready`; otherwise the plate is waiting and the button is live.
