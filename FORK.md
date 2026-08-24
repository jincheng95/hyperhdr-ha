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
v1.0.0.1      fork, first change on top of upstream 1.0.0   <- current
v1.0.0.2      fork, second change on the same base
v1.0.1        upstream (hypothetical)
v1.0.1.1      fork, rebuilt on upstream 1.0.1
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
VERSION=1.0.0.2                       # bump the fourth segment
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

Conflict-prone files: `manifest.json` (the `version` field, every time, plus the fork's `name`
/ `codeowners` / URL changes) and `hacs.json` (`name`). `light.py` will conflict only if
upstream touches the same brightness code — which is the outcome to hope for, since it would
mean the bug was fixed upstream and this fork can retire.

## Local changes vs upstream

### Branding

`manifest.json` and `hacs.json` carry the name `HyperHDR (jincheng95 fork)`, `@jincheng95` is
added to `codeowners` (HACS renders codeowners as the repository authors), and
`documentation` / `issue_tracker` point at this fork rather than upstream.

### `custom_components/hyperhdr/light.py` — brightness no longer washes colours out

Upstream [PR #100](https://github.com/Shaffer-Softworks/hyperhdr-ha/pull/100) fixed
[issue #99](https://github.com/Shaffer-Softworks/hyperhdr-ha/issues/99) ("Brightness does not
affect Solid color, but works for effects") by scaling the RGB triple client-side by
`brightness / 255` before sending it to the colour priority, and un-scaling it again when
syncing priorities back from HyperHDR. That mechanism is sound and is **kept here**.

The defect is that the un-scaling happens **twice**. `_update_priorities` already un-scales
the colour it reads back from the server before storing it in `self._rgb_color`, so that field
always holds a full-brightness colour. A later brightness-only `async_turn_on` then un-scaled
that already-un-scaled value a second time against `stored_brightness`:

```python
base_rgb = rgb_color
if ATTR_HS_COLOR not in kwargs:
    base_rgb = self._unscale_rgb_from_brightness(rgb_color, stored_brightness)
```

Because `_unscale_rgb_from_brightness` clamps each channel independently at 255, any channel
that overshoots gets pinned there. That changes the *ratios* between channels, so hue shifts
and saturation collapses — the colour walks toward white on every brightness change. Starting
from a deep blue of RGB(13, 77, 255) and moving the slider three times:

| brightness | sent to HyperHDR | reported back to HA |
| ---------- | ---------------- | ------------------- |
| 255        | (13, 77, 255)    | (13, 77, 255)       |
| 51         | (3, 15, 51)      | (15, 75, 255)       |
| 102        | (30, 102, 102)   | (75, 255, 255)      |
| 204        | (150, 204, 204)  | (188, 255, 255)     |

Pure primaries such as (0, 0, 255) or (255, 0, 0) are immune, because a zero channel can never
overshoot the clamp. Upstream's E2E tests (`scripts/docker_test_solid_brightness.py`) exercise
only solid red and solid blue, which is why the regression was not caught.

The fix deletes the second un-scale and sends `self._rgb_color` scaled exactly once. Sweeping
the slider now holds hue and saturation steady; the only residual movement is ±7/255 of 8-bit
quantisation at the very bottom of the range, which is inherent to sending a dimmed 8-bit
triple and does not accumulate.

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
