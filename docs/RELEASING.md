# Releasing a new version

1. **Test** – the scenarios and replays in [tests/](../tests/README.md), and
   `tests/device_check.py` on a real clock.
2. **Raise the version** – the `# @version` line at the top of
   `KlipperDashboard.ax`, for example `1.0.1`.
3. **Describe the changes** – a new `## 1.0.1 – <date>` section at the top
   of [CHANGELOG.md](../CHANGELOG.md), written for the people who use the
   script. It becomes the release notes.
4. **Keep the settings** – don't rename or remove a `# @config` key unless
   you must: everyone who updates loses that setting's value. The release
   warns when a key from the previous release is gone.
5. **Commit, then tag and push:**

   ```bash
   git tag v1.0.1
   git push origin main v1.0.1
   ```

   The [release workflow](../.github/workflows/release.yml) checks that the
   tag matches `@version` and publishes a GitHub Release with
   `KlipperDashboard.ax` attached. If the check fails, fix it, delete the
   tag (`git tag -d v1.0.1 && git push origin :v1.0.1`) and tag again.
6. **Update the AWTRIX Hub** – the Hub has no upload API, so open the
   script's [Hub page](https://awtrix.de/script/XyFadk4KssPL), edit it, and paste the new `KlipperDashboard.ax`.
   Clocks that installed it from the Hub then offer **Update available**
   under **Scripts → Check for updates**. If the pictures changed,
   `python3 tools/make_media.py hub` remakes `docs/hub/`.
