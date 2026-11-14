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
- The port is detected once, from the Raspberry Pi USB ID, and that same port is used for every step. `mpremote`'s own `auto` is never used, because it is resolved again on every call and could switch boards between the backup and the install.

## 1. Everyday use: one command

Plug the Pico 2 into USB and run:

```powershell
python tools/picoctl.py
```

In the normal case it **asks nothing**:

- **Port:** detected from the Raspberry Pi USB ID. It only asks if several Picos are connected, and fails with a clear message if none is found (unplugged, or held by Thonny).
- **Profile:** the one already on the Pico, read from `/ws_deploy.json` or, for a Pico installed before records existed, recognized by the SHA-256 of its `main.py`. It only asks for a Pico whose firmware it does not recognize.
- **Empty Pico (new board):** there is nothing to back up, so it continues without asking.

Then it:

1. Installs the pinned `mpremote` if it is missing or has another version.
2. Caches the pinned Pimoroni UF2 on the PC as a recovery copy. If that fails (no network), it only warns: the UF2 is never flashed.
3. Checks the board is an RP2350 with the Pimoroni BME68X drivers.
4. Backs up every file on the Pico to `.pico-backups/<UTC timestamp>/`, checking sizes.
5. Installs the SHA-256-pinned `micropython-modbus==2.3.7` to `/lib/umodbus/`.
6. Copies the modules, then `main.py` **last**, reading each one back and checking SHA-256.
7. Runs an on-board import test of Modbus, the Pimoroni drivers and all application modules.
8. Writes `/ws_deploy.json` and the PC log line, and lists files on the Pico it does not manage (they are left untouched).

It does **not** reset the Pico unless you add `--reset`.

### Switching the wiring profile

```powershell
python tools/picoctl.py --profile socket
```

When the requested profile differs from the one on the Pico, it warns and asks **once** whether the Pico is already wired for it. In a non-interactive run, add `--yes`. After the switch, plain `python tools/picoctl.py` keeps using the new profile.

Other top-level options: `--port COMx` (only if detection cannot work), `--dry-run` (show the plan, write nothing), `--reset`.

## 2. Experiments: `push` instead of copying by hand

For bench work (a diagnostic script, a modified `main.py` from `experiments/`):

```powershell
python tools/picoctl.py push experiments/rs485/rs485_turnaround_diag_pico.py --as main.py --note "turnaround diagnostic"
```

- Files go to the Pico root. `--as` renames a single file (usually to `main.py`). `--note` is optional.
- Any Pico file about to be overwritten is first backed up to `.pico-backups/<timestamp>-push/`.
- A new `main.py` is copied after the other files of the same push.
- The push is appended to `/ws_deploy.json` (which file came from which repository path and commit, plus the note) and to the PC log.
- `--dry-run` shows what would be copied. To return to the profile's firmware, run `python tools/picoctl.py` again.

## 3. Checking and advanced commands

```powershell
python tools/picoctl.py status     # is the Pico still what was recorded? does the repo still match?
python tools/picoctl.py doctor     # board model and drivers
python tools/picoctl.py backup     # full backup to .pico-backups/
python tools/picoctl.py firmware   # cache/verify the UF2 on the PC only
```

`status` exits with `0` when the Pico matches its record and `1` when it does not (or has no record). It catches files edited by hand (for example from Thonny) and repository changes not yet installed.

All of them detect the port like the installer; `--port COMx` overrides it.

`deploy` is the scriptable form of the installer, with every option explicit. It is a dry run unless `--apply` is given:

```powershell
python tools/picoctl.py deploy --profile socket --apply
```

- `--skip-backup`, `--backup-dir PATH`.
- `--include-tests` also copies `common_interface_test.py`.
- `--main PATH` installs a specific `main.py` instead of the profile's; the record marks it as an override, and the installer then asks for the profile next time.
- `--offline` uses only the cached UF2 and `micropython-modbus` archive; `--archive PATH` supplies the archive.
- If any deployed file has uncommitted changes, the install still runs but warns, and the record lists those files.

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
