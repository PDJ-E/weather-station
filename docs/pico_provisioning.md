# Pico 2 — repeatable, traceable provisioning (Weather Station)

**Tool:** [`tools/picoctl.py`](../tools/picoctl.py). **Canonical source:** [`firmware/pico/`](../firmware/pico/) (profiles and pin maps are described in its [README](../firmware/pico/README.md)).

The Pico already runs the known-good Pimoroni MicroPython 1.29.0. `picoctl` **never flashes, erases or deletes** anything on it: it copies Python files with `mpremote`, checks every copy by SHA-256, and records what it installed.

## What gets recorded

Every write to the Pico (`deploy` or `push`) leaves two records:

| Where | What |
|---|---|
| Pico `/ws_deploy.json` | Profile, Git commit and branch, which sent files were not committed, the SHA-256 and source path of every installed file, and a list of later pushes with their notes |
| PC `.pico-deployments/log.jsonl` (gitignored) | One line per deploy/push: time, port, profile, commit, backup folder, file hashes |

`picoctl status` reads the Pico back and answers two questions: *is the Pico still what was recorded?* (catches files edited by hand, for example from Thonny) and *does the record still match the repository?* (catches repository changes not yet deployed).

## Host requirements

- Python 3.10+ on the PC, run from the repository root.
- `mpremote==1.29.0` and `pyserial==3.5` (both in `requirements.txt`). The version of `mpremote` is pinned because `picoctl` parses its output. The interactive installer installs it if it is missing or has a different version.
- Close Thonny and any serial monitor before running `picoctl`: only one program can hold the Pico's port.

## 1. New or replacement Pico: one command

Plug the Pico 2 into USB and run:

```powershell
python tools/picoctl.py
```

The installer:

1. Installs the pinned `mpremote` if needed.
2. Lists serial ports and marks Raspberry Pi devices. If exactly one is found, Enter selects it; otherwise you choose.
3. Asks for the **profile** (there is no default): `socket`, `bench` or `uart`. Choose the one that matches how the Pico is wired.
4. Caches the pinned Pimoroni UF2 on the PC as a recovery copy. If that fails (no network), it only warns: the UF2 is never flashed.
5. Checks the board is an RP2350 with the Pimoroni BME68X drivers.
6. Backs up every file on the Pico to `.pico-backups/<UTC timestamp>/`, checking sizes. **If the Pico is empty** (a new board), it asks for confirmation before continuing without a backup.
7. Installs the SHA-256-pinned `micropython-modbus==2.3.7` to `/lib/umodbus/`.
8. Copies the modules, then `main.py` **last**, reading each one back and checking SHA-256.
9. Runs an on-board import test of Modbus, the Pimoroni drivers and all application modules.
10. Writes `/ws_deploy.json` and the PC log line, and lists files on the Pico it does not manage (they are left untouched).

It does **not** reset the Pico. Reset or power-cycle it once the sensors and RS-485 wiring are connected.

## 2. The same steps as separate commands

```powershell
# See the plan; nothing is contacted or written.
python tools/picoctl.py deploy --profile socket

# Back up, install, verify and record.
python tools/picoctl.py deploy --profile socket --port COM12 --apply
```

- `--port` is required for every command that talks to the Pico. `auto` is refused: each `mpremote` call would resolve it separately, so with two boards attached the backup and the install could go to different devices. List ports with `python -m mpremote connect list`.
- `--skip-backup` is only for a Pico known to be empty.
- `--include-tests` also copies `common_interface_test.py`.
- `--main PATH` installs a specific `main.py` instead of the profile's; the record marks it as an override.
- `--offline` uses only the cached UF2 and `micropython-modbus` archive. `--archive PATH` supplies the archive.
- `--reset` starts `main.py` after deploying.
- If any deployed file has uncommitted changes, the deploy still runs but warns, and the record lists those files. The commit alone then does not reproduce the deployment; the recorded SHA-256 values do.

Other commands:

```powershell
python tools/picoctl.py doctor --port COM12   # board model and drivers
python tools/picoctl.py backup --port COM12   # full backup to .pico-backups/
python tools/picoctl.py firmware              # cache/verify the UF2 on the PC only
python tools/picoctl.py status --port COM12   # compare the Pico with its record and the repo
```

`status` exits with `0` when the Pico matches its record and `1` when it does not (or has no record).

## 3. Experiments: `push` instead of copying by hand

For bench work (a diagnostic script, a modified `main.py` from `experiments/`), use `push`. It replaces manual copying with the same guarantees as a deploy:

```powershell
# Plan only.
python tools/picoctl.py push experiments/rs485/rs485_turnaround_diag_pico.py --as main.py

# Copy, verify and record, with a reason.
python tools/picoctl.py push experiments/rs485/rs485_turnaround_diag_pico.py --as main.py `
    --port COM12 --apply --note "turnaround diagnostic"
```

- Files go to the Pico root. `--as` renames a single file (usually to `main.py`).
- Any Pico file about to be overwritten is first backed up to `.pico-backups/<timestamp>-push/`.
- A new `main.py` is copied after the other files of the same push.
- The push is appended to `/ws_deploy.json` (which file came from which repository path and commit, plus the note) and to the PC log. `status` then shows it.
- To return to a profile, run `deploy` again.

## 4. Check the RS-485 link from `weather-core`

The Pi-side test must match the profile on the Pico:

| Pico profile | Pi test | Expects |
|---|---|---|
| `socket` | `python experiments/rs485/p4_socket_environmental_test_pi.py` | Map `0x0102`, DHT11 `NOT_INSTALLED`, BME688 OK, no `FAULT` |
| `bench` | `python experiments/rs485/rs485_environmental_test_pi.py` | Map `0x0101`, DHT11 and BME688 OK |

Both use `/dev/ttyAMA0`, RTS on GPIO17, Unit ID 1, and require `run_enable = 0` (stopped). **Rewire the bench before deploying `socket`:** RTS moves from Pico pin 9 to pin 4, and the BME688 from pins 4/5 to pins 9/10. The P4 long-run runner (`p4_rs485_long_run.py`) still expects map `0x0101`.

## 5. Host-only tests

```powershell
python -m unittest discover -s tests -p "test_picoctl.py" -v
```

They run without a Pico: the lock, downloads, backup and the full deploy → status → push sequence are exercised against a simulated Pico filesystem. GitHub Actions runs them, plus an `mpy-cross` compile of every firmware file, on pull requests and on pushes to `main`. They are not hardware tests.

## Dependency lock

Third-party artifacts are pinned in [`firmware/pico/dependencies.lock.json`](../firmware/pico/dependencies.lock.json):

- **Pimoroni UF2:** board, distribution and version, filename, official HTTPS release URL, exact size, SHA-256 and `action: download_only`.
- **micropython-modbus:** package and version, sdist filename, PyPI metadata URL, SHA-256 and on-device path.

Expected UF2 SHA-256 (`.pico-firmware/rpi_pico2-v1.29.0-micropython.uf2`, gitignored):

```text
2246a7daaf436d39f8c6810342def221c87aa4d50386942b32b9fadd10471dd3
```

The hashes are pins, not checks against "latest": every Pico provisioned from the same commit gets the same bits, and altered or corrupt downloads are rejected. Update the lock in its own reviewed pull request, after checking compatibility, running these tests and a hardware check. An invalid lock (structure, hash, URL, board, version/filename or flash policy) stops the installer.

## Recovery

`picoctl` never deletes, rolls back or resets automatically. A failure stops the process and keeps the backup. To restore, copy the files from the backup folder back with `mpremote fs cp` (or `push` them).
