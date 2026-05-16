Factorio Server — Operator Guide
================================

Welcome. This folder is your control surface for the Factorio server. You
manage the server entirely through files in this directory — no SSH, no
sudo, no Discord pings (hopefully).


WHAT'S WHERE
------------

  /factorio/
  |
  |-- README.txt          You are here.
  |
  |-- mods/               Mod .zip files. Drop new ones in, delete old ones,
  |                       trigger a restart (see below).
  |
  |-- saves/              Save files (autosaves + manual). Latest is always
  |                       loaded on server start. Old autosaves rotate
  |                       automatically.
  |
  |-- config/             Server configuration. Edit server-settings.json
  |                       to change server name, password, etc.
  |                       server-adminlist.json / server-banlist.json /
  |                       server-whitelist.json are optional.
  |
  |-- scenarios/          Scenario .zip files (if you use them).
  |
  |-- script-output/      Mod-generated output (most mods don't write here).
  |
  |-- logs/               Server logs. Read-only for you.
  |   |-- factorio.log         The game's stdout. Player joins, chat,
  |   |                        errors. This is what you want to read
  |   |                        when something seems wrong.
  |   |-- reconcile.log        State-reconciler log. Less interesting day
  |   |                        to day; useful when version changes fail.
  |   |-- *.log.1, *.log.2     Rotated old logs.
  |
  `-- control/            Server control surface.
      |-- factorio-control.json   Your desired state — EDIT THIS.
      |-- factorio-status.json    Current observed state — READ-ONLY.
      `-- restart-now             Touch this to restart immediately.


HOW TO DO COMMON THINGS
-----------------------

  Restart the server NOW:
      Upload an empty file named 'restart-now' into the control/ folder.
      The file vanishes within ~1 second; that's the signal it worked.
      The server is back within ~10 seconds.
      (Has no effect if state is set to "stopped" — see below.)

  Restart the server at the next reconcile tick (within 30s):
      Edit control/factorio-control.json:
          "restart": true
      The reconciler will restart factorio and set restart back to false.
      Slower than restart-now but useful if you're batching other edits.
      (Has no effect if state is set to "stopped".)

  Stop the server for maintenance / take it down for a while:
      Edit control/factorio-control.json:
          "state": "stopped"
      The reconciler stops factorio at the next tick (within 30s).
      While state is "stopped":
        - restart-now does nothing
        - the restart flag has no effect
        - version changes still install (in the background) but factorio
          stays stopped — useful if you want to upgrade and start fresh
      To bring it back up, set "state": "running". The reconciler will
      start it within 30s.

  Add or update mods:
      1. SFTP-upload new mod .zip files into mods/.
      2. (Optional) delete old versions of those mods from mods/.
      3. Touch control/restart-now to apply.

  Add or update mods safely (server down during upload):
      1. Edit control/factorio-control.json: "state": "stopped". Wait 30s.
      2. SFTP-upload / delete mods as you like.
      3. Edit control/factorio-control.json: "state": "running".
      4. Server is back within 30s.

  Change the Factorio version:
      Edit control/factorio-control.json:
          "version": "stable"        — always run the latest stable
          "version": "experimental"  — always run the latest experimental
          "version": "2.0.76"        — pin to a specific version
      Save the file. The reconciler will download + install + restart the
      server within ~30s + however long download takes.

  Change server name / description / password:
      Edit config/server-settings.json. Then touch control/restart-now.

  Roll back to the previous Factorio version:
      Edit control/factorio-control.json and set version to the previous
      one. The reconciler keeps the most recent previous install on disk,
      so the rollback is a fast symlink swap (no re-download).


CHECK CURRENT STATE
-------------------

  Read control/factorio-status.json. It tells you:

    installed_version       Which Factorio version is actually live
    service_active          Whether the server is currently running
    last_reconcile_utc      When the reconciler last ran
    last_reconcile_status   "ok" or "error: <reason>"
    last_restart_utc        When the server last (re)started
    available               What the latest stable + experimental
                            versions are upstream

  Status updates every ~30 seconds. If you flip a flag in
  factorio-control.json and want to see if it was processed, wait 30s
  and re-download status.


SOMETHING'S WRONG?
------------------

  1. Read control/factorio-status.json — does last_reconcile_status say
     anything other than "ok"?

  2. Read logs/reconcile.log — recent entries show what the reconciler
     did and what failed.

  3. Read logs/factorio.log — the game's own log. If the game crashed
     on startup, the error is here.

  4. If service_active is false but factorio-control.json is valid and
     the game just won't start: ping the homelab admin. Probably an LXC
     or networking issue.


WHAT YOU CAN'T DO FROM HERE
---------------------------

  - Restart the LXC itself (only the factorio service).
  - Change network ports.
  - Install Factorio mods that don't have official .zip releases.
  - Recover from a corrupted save without a backup. PBS backs up the
    whole LXC nightly, but in-flight saves are your responsibility.

  For any of those: ping the homelab admin.


HOW THIS WORKS, BRIEFLY
-----------------------

A small Python script (factorio-reconcile) runs every 30 seconds via
systemd. It reads control/factorio-control.json, compares it against
reality, and converges reality toward your stated intent. Then it writes
control/factorio-status.json with what it sees.

The restart-now file is watched by a separate systemd path unit that
restarts the service the instant the file appears.

The reconciler script is in /usr/local/bin/factorio-reconcile on the
host. The game runs as systemd unit 'factorio.service'.
