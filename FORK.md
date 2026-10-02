# Fork notes

Personal fork of [Shaffer-Softworks/hyperhdr-ha](https://github.com/Shaffer-Softworks/hyperhdr-ha),
maintained at `jincheng95/hyperhdr-ha` and installed through HACS as a custom repository.

Pushing to `upstream` is disabled at the git level (`remote.upstream.pushurl = DISABLED`).
Only `origin` is writable.

## Versioning scheme

Fork releases append a **fourth numeric segment** to the upstream version they are based on:

```
v<UPSTREAM_MAJOR>.<MINOR>.<PATCH>.<FORK_ITERATION>
```

The fork iteration starts at `1` and increments for each fork-only release built on the
same upstream base. It resets to `1` whenever the fork is rebuilt on a newer upstream tag.

```
v1.0.0        upstream
v1.0.0.1      fork, first change on top of upstream 1.0.0
v1.2.1        upstream
v1.2.1.1      fork, rebuilt on upstream 1.2.1               <- current
v1.2.1.2      fork, second change on the same base
```

Ordering is monotonic under HACS's `AwesomeVersion` comparison, which compares segment by
segment: `1.0.0 < 1.0.0.1 < 1.0.0.2 < 1.0.1 < 1.1.0 < 1.1.0.1`. A new upstream release
therefore always outranks every fork build on the previous base, so HACS will never offer a
stale fork build as an upgrade over a newer upstream one, and never mistake an upstream
version for a downgrade.

`custom_components/hyperhdr/manifest.json` carries the same string without the leading `v`.

## Cutting a fork release

Upstream's `release.yml` workflow is `workflow_dispatch`-only and creates a release branch
plus a pull request. That ceremony is unnecessary here, so releases are cut locally instead.
`hacs.json` keeps upstream's `zip_release: true` / `filename: hyperhdr.zip`, so the release
must carry a `hyperhdr.zip` asset whose contents sit at the **zip root** (`manifest.json`,
`__init__.py`, …) — HACS unpacks that into `custom_components/hyperhdr/`.

```sh
VERSION=1.2.1.2                       # bump the fourth segment
# edit custom_components/hyperhdr/manifest.json -> "version": "$VERSION"
git commit -am "chore: bump manifest to $VERSION"
rm -f hyperhdr.zip
( cd custom_components/hyperhdr && zip -r ../../hyperhdr.zip . -x "*__pycache__/*" -x "*.pyc" )
git add hyperhdr.zip && git commit -m "build: hyperhdr.zip for v$VERSION"
git tag "v$VERSION" && git push origin master "v$VERSION"
gh release create "v$VERSION" hyperhdr.zip --title "v$VERSION" --notes "..."
```

## Picking up a new upstream release

```sh
git fetch upstream --tags
git merge upstream/master        # resolve manifest.json version by hand
# set manifest version to <new upstream>.1, then cut a release as above
```

Conflict-prone files:

- `manifest.json` conflicts on the `version` field every time. The fork's `name`, `codeowners`
  and URL changes sit beside it.
- `hacs.json` can conflict on `name`.
- `smoothing_config.py` conflicts if upstream touches `async_patch_smoothing_config`. Keep the
  full-config guard unless upstream now sends whole configs itself.

## Local changes vs upstream

### Branding

`manifest.json` and `hacs.json` carry the name `HyperHDR (jincheng95 fork)`, `@jincheng95` is
added to `codeowners` (HACS renders codeowners as the repository authors), and
`documentation` / `issue_tracker` point at this fork rather than upstream.

### `light.py` — no fork delta

Upstream [#119](https://github.com/Shaffer-Softworks/hyperhdr-ha/pull/119) (433d7d2) makes the
same deletion as the fork's d4170d7. Both stop the second un-scale that walked solid colours
toward white on every brightness change. #119 also caches the full-brightness RGB for the
session and floors solid-colour brightness at 12. The fork took upstream's `light.py` verbatim
in v1.2.1.1 and now carries no change to it.

### `smoothing_config.py` — smoothing writes send the whole config

Upstream v1.1.0 (#115) added nine smoothing entities, disabled by default. Each one writes
through `async_patch_smoothing_config`. Upstream calls the library's
`async_update_smoothing_config`, which sends `config/setconfig` with a smoothing-only fragment.

HyperHDR does not merge that fragment. `InstanceConfig::saveSettings(config, correct=true)`
validates it against the full instance schema and fails, so it auto-corrects the config
(`sources/base/InstanceConfig.cpp`). The `leds`, `device`, `network` and `general` schemas are
all required. The schema checker re-creates each missing required section from its defaults
(`sources/json-utils/jsonschema/QJsonSchemaChecker.cpp`). The corrected config is then saved.
One smoothing toggle would therefore reset the 241-LED layout, the device settings and the network auth.
A partial write did exactly that on 2026-08-23.

The entities register on this install because the server has `localAdminAuth: false`. HyperHDR
grants LAN clients admin rights, so `getconfig` succeeds without a password.

The fork replaces the body of `async_patch_smoothing_config` with a read-modify-write:

1. Call `getconfig` and take the full config from `info`.
2. Refuse the write and log a warning if `leds`, `device`, `network`, `general` or `smoothing`
   is missing.
3. Merge the new fields into `smoothing` on a deep copy.
4. Send the whole config back with `setconfig`.
5. Re-read smoothing, or keep the merged values if the re-read fails, and notify the entities.

The signature and the `bool` return are unchanged, so the callers in `number.py`, `select.py`
and `switch.py` work as before. No other code path in the integration sends `setconfig`.

The HyperHDR web UI also saves whole configs. An API write and a UI save can overwrite each
other, so the last writer wins.

### Deliberately NOT changed

`async_turn_on` still writes the server-side adjustment (`brightness` % / `luminanceGain`) in
addition to scaling the RGB. If that adjustment *does* apply to colour priorities on a given
HyperHDR build, brightness is effectively applied twice and the dimming curve is squared —
50 % renders as 25 %. Upstream asserts it does *not* apply to colour priorities (that assertion
is the entire premise of issue #99), but upstream never verified it against physical LED
output; their notes list "Physical LED appearance" under "Not automated".

Removing the adjustment write is therefore **not** safe without measuring it first, since it
would reintroduce #99 on any build where upstream's premise is wrong. To settle it on a given
server: set a solid colour, then change *only* `luminanceGain` via the HyperHDR JSON-RPC API
(`{"command":"adjustment","adjustment":{"luminanceGain":0.2}}`) without touching the colour
priority, and watch whether the strip dims. If it does, the adjustment write can be dropped
from the solid-colour path in a future fork release.

Note also that `luminanceGain` is an **instance-global** setting, so it dims a running
video-grabber ambilight too. Any automation handing the instance over to capture should set
brightness back to 100 % as it does so.
