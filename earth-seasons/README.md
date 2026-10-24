# Seasons Globe

An interactive 3D Earth showing where the Sun's rays fall through the year.
Live at `/earth-seasons/` on the GitHub Pages site.

- Drag to rotate, scroll on the globe to zoom, click a spot for its season, daylight and noon sun height.
- Scroll or drag the timeline, scroll the month calendar, click the orbit diagram, or press ▶ to play the year.
- The Sun's position comes from a low-precision solar ephemeris, so the solstices, equinoxes and the latitude
  where the Sun is overhead are accurate for any date.

Static files only: no build step. Three.js r170 (MIT) is vendored in `vendor/`. Earth textures are NASA
Blue Marble / Black Marble imagery (public domain), via the `three-globe` example images.
